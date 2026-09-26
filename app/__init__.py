import contextlib
import os
import time
from flask import Flask
from app.config import Config
from app.extensions import db, login_manager, csrf, limiter


@contextlib.contextmanager
def _startup_db_lock(app):
    """Guards db.create_all() + the auto-migrate/seed steps below against
    a real race: gunicorn (and Render, via WEB_CONCURRENCY) normally
    starts several WORKER PROCESSES that each independently import this
    module and call create_app() at ~the same instant. Without a lock,
    two workers can both see "no tables yet", both issue CREATE TABLE,
    and whichever loses the race crashes on startup with "table already
    exists" - gunicorn then refuses to boot that worker at all (this was
    reproduced directly: `gunicorn -c gunicorn_conf.py run:app` with 2+
    workers against a fresh SQLite file crashes exactly this way without
    this lock). The same risk exists for any database, not just SQLite,
    since the race is at the Python/process level, not the DB engine
    level - so this lock is unconditional, not SQLite-specific.

    Implemented as a plain lock *file*, created with O_CREAT|O_EXCL
    (atomic on POSIX and Windows alike per Python's os.open docs), so it
    needs no extra dependency and no platform-specific code (`fcntl` is
    POSIX-only and would break Windows local development, which this
    project explicitly needs to support). Losing workers simply wait for
    the winner to finish and release the lock, then proceed normally -
    by then the tables/seed data already exist, so their own
    create_all()/seed calls are no-ops.
    """
    lock_path = os.path.join(app.instance_path, ".startup.lock")
    os.makedirs(app.instance_path, exist_ok=True)
    fd = None
    deadline = time.time() + 60
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.time() > deadline:
                # Another process's lock is stuck (e.g. it crashed before
                # cleaning up) - proceed rather than hang the worker
                # forever; create_all()/the seed checks are re-entrant
                # enough to tolerate this.
                break
            time.sleep(0.2)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _configure_sqlite_pragmas(app):
    """SQLite concurrency setup (spec: exam answers/attempts/autosave,
    payment callbacks and notifications all write to the DB, sometimes at
    the same time - a bare SQLite file handles that far worse than MySQL
    by default). No-op if DATABASE_URL points at a non-SQLite database.

    - WAL (Write-Ahead Logging) mode lets readers and a single writer work
      concurrently instead of the default SQLite behaviour of blocking all
      readers for the duration of a write - this is the single biggest
      concurrency win available for SQLite and is safe to enable always.
    - `synchronous=NORMAL` is the standard, safe pairing with WAL (fsyncs
      at WAL checkpoints rather than every transaction) - still durable
      against application crashes, only a WAL-mode-aware power-loss corner
      case is traded away, which is an acceptable trade for this project.
    - `busy_timeout` makes a writer that finds the database locked retry
      for up to this many milliseconds instead of failing immediately with
      "database is locked" - turns brief lock contention (two students'
      autosaves landing in the same instant) into a short wait instead of
      a 500 error.
    - `foreign_keys=ON` is required on every single SQLite connection
      because, unlike MySQL, SQLite ignores foreign-key constraints by
      default even though the schema declares them - without this, a bug
      elsewhere could silently insert orphaned rows (e.g. an Answer
      pointing at a deleted Attempt) that MySQL would have rejected.

    IMPORTANT - practical SQLite limits worth knowing (see also the
    deployment report): WAL mode allows many concurrent readers plus ONE
    concurrent writer; a second writer waits (up to `busy_timeout`) rather
    than running in parallel. For this project's expected load (a single
    web process/small number of workers, not a distributed cluster of
    app servers) that is a reasonable, well-understood limit - but SQLite
    is not a drop-in replacement for MySQL under heavy write concurrency
    (e.g. thousands of students autosaving in the same second). If that
    ever becomes the bottleneck, switch DATABASE_URL to a managed
    Postgres/MySQL instance; nothing else in the app assumes SQLite.
    """
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
        return

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _auto_migrate_new_columns(app):
    """`db.create_all()` only creates brand-new tables; it never adds
    columns to tables that already exist (see MIGRATION_NOTES.md). This
    adds the small set of columns introduced by the tiered-pricing /
    Razorpay payments feature so upgrading an existing deployment doesn't
    require a manual ALTER TABLE. Safe to run on every startup - each
    column is only added if it isn't already there."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    wanted = {
        "users": {
            "is_super_admin": "BOOLEAN DEFAULT 0",
        },
        "courses": {
            "price_exam_only": "FLOAT DEFAULT 0",
            "price_exam_pdf": "FLOAT DEFAULT 0",
            "price_class_only": "FLOAT DEFAULT 0",
            "course_video_url": "VARCHAR(400)",
            "category_id": "INTEGER",
        },
        "course_purchases": {
            "tier": "VARCHAR(20) DEFAULT 'combined'",
            "razorpay_order_id": "VARCHAR(80)",
            "razorpay_payment_id": "VARCHAR(80)",
            "razorpay_signature": "VARCHAR(200)",
            # Phase 2 - Subscription Plans
            "plan_id": "INTEGER",
            # Coupons - see Coupon/CouponRedemption in models.py
            "coupon_id": "INTEGER",
            "original_amount": "FLOAT",
            "discount_amount": "FLOAT DEFAULT 0",
            "tax_amount": "FLOAT DEFAULT 0",
            "platform_fee_amount": "FLOAT DEFAULT 0",
            "refund_amount": "FLOAT",
            "refund_platform_fee_amount": "FLOAT",
            "refund_reason": "VARCHAR(300)",
            "refunded_at": "DATETIME",
            "refunded_by": "INTEGER",
            "razorpay_refund_id": "VARCHAR(80)",
            "expiry_reminder_sent": "BOOLEAN DEFAULT 0",
        },
        "payment_transactions": {
            "method_detail": "VARCHAR(200)",
        },
        # Phase 2 - Subscription + Secure Exam Features
        "exams": {
            "test_type": "VARCHAR(10) DEFAULT 'mock'",
            "free_without_plan": "BOOLEAN DEFAULT FALSE",
            "availability_mode": "VARCHAR(12) DEFAULT 'flexible'",
            "available_from": "DATETIME",
            "available_until": "DATETIME",
            "max_attempts": "INTEGER",
            "camera_required": "BOOLEAN DEFAULT FALSE",
            "mic_required": "BOOLEAN DEFAULT FALSE",
            "face_detection_enabled": "BOOLEAN DEFAULT FALSE",
            "multi_face_detection_enabled": "BOOLEAN DEFAULT FALSE",
            "looking_away_detection_enabled": "BOOLEAN DEFAULT FALSE",
            "voice_detection_enabled": "BOOLEAN DEFAULT FALSE",
            "max_warnings": "INTEGER DEFAULT 3",
            "show_leaderboard": "BOOLEAN",
        },
        "student_attempts": {
            "warning_count": "INTEGER DEFAULT 0",
            "auto_submitted": "BOOLEAN DEFAULT FALSE",
            "student_account_id": "INTEGER",
        },
    }

    for table, columns in wanted.items():
        if table not in existing_tables:
            continue
        existing_columns = {c["name"] for c in inspector.get_columns(table)}
        for col_name, col_def in columns.items():
            if col_name in existing_columns:
                continue
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                app.logger.info(f"Auto-migration: added column {table}.{col_name}")
            except Exception as exc:  # pragma: no cover - best-effort, never blocks startup
                app.logger.warning(f"Auto-migration skipped for {table}.{col_name}: {exc}")


def _auto_create_admin(app):
    """Creates the admin account from ADMIN_EMAIL / ADMIN_PASSWORD in .env
    the first time the app starts, if no admin exists yet. This means the
    admin login/username/password lives only in `.env`, never in code or
    in a setup wizard. Existing admins are left untouched; this never
    overwrites a password."""
    from app.models import User

    email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()
    password = app.config.get("ADMIN_PASSWORD") or ""
    if not email or not password:
        return  # not configured - skip silently, use `flask create-admin` instead
    if User.query.filter_by(role="admin").first():
        return  # an admin already exists somewhere - don't touch it
    if User.query.filter_by(email=email).first():
        return  # a user with this email already exists

    admin = User(name=app.config.get("ADMIN_NAME", "Admin"), email=email, role="admin", is_super_admin=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    app.logger.info(f"Auto-created admin account for {email} from .env")


def _ensure_super_admin_exists(app):
    """Migration safety net: on a database that already had admin users
    before is_super_admin existed, that column defaults to False for all of
    them - which would leave the platform with zero super admins and no way
    for anyone to manage other admin accounts at all. Promotes the oldest
    active admin automatically so that never happens silently."""
    from app.models import User

    if User.query.filter_by(role="admin", is_super_admin=True).first():
        return  # already have one - nothing to do
    oldest_admin = (
        User.query.filter_by(role="admin", is_active_flag=True)
        .order_by(User.created_at.asc()).first()
    )
    if oldest_admin:
        oldest_admin.is_super_admin = True
        db.session.commit()
        app.logger.info(f"No Super Admin existed - auto-promoted {oldest_admin.email}")


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["GENERATED_PDF_FOLDER"], exist_ok=True)

    db.init_app(app)

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
        # Fresh checkout / first Render deploy won't have instance/ yet -
        # SQLite raises "unable to open database file" rather than
        # creating missing parent directories itself. Read the directory
        # back from db.engine.url (inspecting the configured URL only -
        # this does not open a connection) instead of re-deriving it from
        # config, because Flask-SQLAlchemy 3.x resolves a *relative*
        # sqlite:/// URI against app.instance_path, not the process's cwd
        # or the value SQLITE_DB_PATH was built from - re-deriving it here
        # would create the wrong directory for anyone who overrides
        # DATABASE_URL with a relative path instead of using the default.
        with app.app_context():
            db_path = db.engine.url.database
        if db_path and db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    _configure_sqlite_pragmas(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ---- Security headers ----
    @app.after_request
    def set_secure_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            # fonts.googleapis.com serves the Google Fonts stylesheet (Fraunces/
            # IBM Plex, used site-wide by the design system); cdnjs.cloudflare.com
            # serves pdf.js for the study-material PDF viewer.
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://checkout.razorpay.com "
            "https://cdnjs.cloudflare.com; "
            # fonts.gstatic.com is where the actual Google Fonts .woff2 FILES are
            # hosted (googleapis.com only serves the @font-face CSS that points
            # there) - allowing style-src for googleapis.com without also
            # allowing font-src for gstatic.com would still silently fail to
            # load any glyphs.
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
            # Stripe (Module 10c) is a pure server-side redirect to Stripe's own
            # hosted checkout page - no js.stripe.com script or iframe is ever
            # loaded on our own pages, so nothing needs adding here for it.
            "connect-src 'self' https://cdn.jsdelivr.net https://api.razorpay.com https://lumberjack.razorpay.com; "
            # openstreetmap.org embeds the "our location" map on the homepage
            # and contact page.
            "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com "
            "https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com "
            "https://www.openstreetmap.org; "
        )
        return response

    # ---- Blueprints ----
    from app.auth.routes import auth_bp
    from app.admin.routes import admin_bp
    from app.public.routes import public_bp
    from app.api.routes import api_bp
    from app.student.routes import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(student_bp)
    csrf.exempt(api_bp)  # autosave endpoints use JWT-based attempt tokens, not cookie CSRF

    # ---- Global template context (available on every blueprint's templates,
    # not just student_bp's own views) ----
    from datetime import datetime as _dt
    from app.student.routes import current_student as _current_student

    @app.context_processor
    def _inject_global_context():
        student = _current_student()
        unread_count = 0
        if student:
            from app.models import Notification
            unread_count = Notification.query.filter_by(
                student_account_id=student.id, is_read=False
            ).count()
        from app.models import TickerItem, SiteSetting
        ticker_items = []
        if SiteSetting.get_bool("ticker_enabled", True):
            ticker_items = TickerItem.query.filter_by(is_active=True).order_by(
                TickerItem.sort_order, TickerItem.created_at
            ).all()
        whatsapp_number = SiteSetting.get("whatsapp_number", "").strip()
        whatsapp_visible = SiteSetting.get_bool("whatsapp_enabled", False) and bool(whatsapp_number)

        # Sidebar badge for Admin > WhatsApp Support (requirement 14 -
        # there's no separate admin notification system in this project to
        # integrate with, so this live count IS the notification signal,
        # rather than building a second, parallel notification mechanism).
        # Cheap enough to run on every admin page load (one indexed COUNT),
        # and only runs at all for a logged-in admin, never for
        # student/public pages.
        whatsapp_new_count = 0
        from flask_login import current_user as _current_admin
        if getattr(_current_admin, "is_authenticated", False):
            # Only admin Users ever authenticate via Flask-Login in this
            # app (students use a separate session-based login - see
            # student/routes.py current_student()), so this is safely
            # admin-only without an extra type check.
            from app.models import WhatsAppConversation
            whatsapp_new_count = WhatsAppConversation.query.filter(
                WhatsAppConversation.whatsapp_number != ""
            ).filter_by(status="NEW").count()

        return {
            "current_student": student,
            "now_year": _dt.utcnow().year,
            "unread_notification_count": unread_count,
            "ticker_items": ticker_items,
            "whatsapp_visible": whatsapp_visible,
            "whatsapp_number": whatsapp_number,
            "whatsapp_message": SiteSetting.get("whatsapp_message", "Hi! I have a question about Kalyani Exam Hub."),
            "whatsapp_new_count": whatsapp_new_count,
        }

    # ---- CLI: seed database ----
    from app.seed import register_cli
    register_cli(app)

    # ---- CLI: exam reminder emails (Module 7) ----
    from app.cli import register_reminder_cli
    register_reminder_cli(app)

    with app.app_context(), _startup_db_lock(app):
        db.create_all()
        _auto_migrate_new_columns(app)
        _auto_create_admin(app)
        _ensure_super_admin_exists(app)
        from app.models import HomeSlide
        if HomeSlide.query.count() == 0:
            defaults = [
                dict(badge_text="NEW BATCH  2026-27", title="Crack APPSC & TSPSC with a Plan Built for You",
                     subtitle="Live mock tests, structured material & an AI engine that finds your weak topics.",
                     button_text="Explore Now", button_link="#courses",
                     image_filename="slide1_govt_exams.jpg", sort_order=1),
                dict(badge_text="SMART ANALYTICS", title="AI-Powered Performance Analysis, After Every Test",
                     subtitle="Know your accuracy, exam readiness and expected rank - instantly.",
                     button_text="Explore Now", button_link="#ai-analysis",
                     image_filename="slide2_ai_analysis.jpg", sort_order=2),
                dict(badge_text="TEST SERIES", title="1,200+ Mock Tests, Practice Like the Real Exam",
                     subtitle="Daily, weekly & monthly tests designed to keep you exam-ready year round.",
                     button_text="Explore Now", button_link="#mock-tests",
                     image_filename="slide3_mock_tests.jpg", sort_order=3),
                dict(badge_text="18+ EXAM CATEGORIES", title="Structured Study Material, Chapter by Chapter",
                     subtitle="Notes, practice sheets and previous papers organised chapter-by-chapter.",
                     button_text="Explore Now", button_link="#courses",
                     image_filename="slide4_study_material.jpg", sort_order=4),
            ]
            for d in defaults:
                db.session.add(HomeSlide(**d, is_active=True))
            db.session.commit()

        # Default exam-category taxonomy (spec section 6/35) - seeded once on
        # first run, same pattern as HomeSlide above. Admin can rename,
        # reorder, deactivate, or add more from Admin > Categories; this only
        # runs when no category exists yet at all.
        from app.models import Category
        if Category.query.count() == 0:
            default_categories = [
                ("SSC", "bi-file-earmark-text", "Staff Selection Commission exams - CGL, CHSL, MTS, GD"),
                ("Banking & Insurance", "bi-bank", "IBPS, SBI, RBI, LIC and other banking/insurance exams"),
                ("Railways", "bi-train-front", "RRB NTPC, Group D, ALP and other railway recruitment exams"),
                ("UPSC", "bi-building", "Civil Services and other Union Public Service Commission exams"),
                ("APPSC", "bi-geo-alt", "Andhra Pradesh Public Service Commission exams"),
                ("TSPSC", "bi-geo-alt-fill", "Telangana State Public Service Commission exams"),
                ("Teaching", "bi-easel", "TET, DSC and other teacher-eligibility exams"),
                ("Police", "bi-shield-check", "Police constable, SI and allied recruitment exams"),
                ("Defence", "bi-shield-shaded", "NDA, CDS, AFCAT and other defence entrance exams"),
                ("Technical & Engineering", "bi-cpu", "GATE, junior engineer and other technical exams"),
                ("Software Jobs", "bi-code-slash", "Placement and technical-assessment preparation"),
                ("Other Competitive Exams", "bi-mortarboard", "Everything else that doesn't fit the categories above"),
            ]
            for i, (name, icon, desc) in enumerate(default_categories):
                db.session.add(Category(
                    name=name, slug=Category.make_slug(name), icon=icon,
                    description=desc, display_order=i, is_active=True,
                ))
            db.session.commit()

        # Ticker banner (the scrolling strip under the navbar) - seeded
        # once on first run, same pattern as HomeSlide/Category above, so
        # the strip isn't confusingly empty out of the box. Admin can
        # edit/replace/disable these at any time from Admin > Ticker
        # Banner - this only runs when no ticker item exists yet at all.
        from app.models import TickerItem
        if TickerItem.query.count() == 0:
            default_ticker_items = [
                ("Welcome to Kalyani Exam Hub - your AI-powered exam prep partner", None, "bi-mortarboard"),
                ("New: Fresh mock tests added every week across all categories", None, "bi-lightning-charge-fill"),
                ("Explore our test series for SSC, Banking, Railways, APPSC, TSPSC & more", "/courses", "bi-gift"),
            ]
            for i, (text, link, icon) in enumerate(default_ticker_items):
                db.session.add(TickerItem(text=text, link=link, icon=icon, sort_order=i, is_active=True))
            db.session.commit()

        # Phase 2 (spec #5) - seed the 3 free demo tests on first run so
        # there's always something free/unlocked for a student to try
        # immediately, with no admin setup required. Admin can still
        # freely edit/publish/unpublish/delete these like any other exam -
        # this only runs once, when no demo test exists yet at all.
        from app.models import Exam, ExamLink, Question, Option
        if Exam.query.filter_by(test_type="demo").count() == 0:
            for i in range(1, 4):
                exam = Exam(
                    title=f"Demo Mock Test {i}",
                    description="A free sample test - no plan or purchase required.",
                    duration_minutes=20, total_marks=10, passing_percentage=40,
                    negative_marking_enabled=False, instructions=(
                        "This is a free demo test. Attempt all questions within the time limit. "
                        "Right-click, copy/paste, and tab-switching are monitored just like a real test."
                    ),
                    is_active=True, test_type="demo", availability_mode="flexible", max_warnings=3,
                )
                db.session.add(exam)
                db.session.flush()

                q = Question(
                    exam_id=exam.id, question_type="single_choice",
                    question_text=f"Demo Question 1 for Demo Mock Test {i}: What is 2 + 2?",
                    marks=10, negative_marks=0, order_index=1,
                )
                db.session.add(q)
                db.session.flush()
                db.session.add_all([
                    Option(question_id=q.id, option_text="3", is_correct=False, order_index=1),
                    Option(question_id=q.id, option_text="4", is_correct=True, order_index=2),
                    Option(question_id=q.id, option_text="5", is_correct=False, order_index=3),
                    Option(question_id=q.id, option_text="22", is_correct=False, order_index=4),
                ])
                exam.total_marks = 10
                db.session.add(ExamLink(exam_id=exam.id, is_active=True))
            db.session.commit()

    return app
