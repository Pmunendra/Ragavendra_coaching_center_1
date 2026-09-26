import secrets
import string
import random
from datetime import datetime, timedelta
import bcrypt
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


def gen_token(length=10):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --------------------------------------------------------------------------
# Users (Admins / Staff)
# --------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="admin")  # admin / staff / teacher
    is_active_flag = db.Column(db.Boolean, default=True)
    is_super_admin = db.Column(db.Boolean, default=False)  # see admin/routes.py super_admin_required - the one
    # account tier no other admin can edit, demote, or delete; protects against any admin sabotaging another
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self):
        return self.is_active_flag


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------
class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    questions = db.relationship("Question", backref="subject", lazy="dynamic")


# --------------------------------------------------------------------------
# Exams
# --------------------------------------------------------------------------
class Exam(db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, nullable=False, default=60)
    total_marks = db.Column(db.Float, default=0)
    passing_percentage = db.Column(db.Float, default=40)
    negative_marking_enabled = db.Column(db.Boolean, default=False)
    negative_marks_per_wrong = db.Column(db.Float, default=0)
    shuffle_questions = db.Column(db.Boolean, default=True)
    shuffle_options = db.Column(db.Boolean, default=True)
    instructions = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    allow_retake = db.Column(db.Boolean, default=False)
    # Module 6 - opt-in strict anti-cheat: when enabled, ANY tab switch /
    # fullscreen exit / window blur immediately auto-submits the exam.
    # Defaults to False so existing exams keep today's 3-warning behavior.
    strict_anti_cheat = db.Column(db.Boolean, default=False)
    # Module 7 - optional scheduled start time + syllabus, used for email reminders
    scheduled_at = db.Column(db.DateTime, nullable=True)
    syllabus = db.Column(db.Text, nullable=True)
    # Phase 5 - test-series cadence (Daily/Weekly/Monthly Grand Test) shown
    # on the student's Mock Tests page. None = a regular/custom exam,
    # unaffected and still worked exactly as before.
    test_category = db.Column(db.String(20), nullable=True)  # daily / weekly / monthly / grand
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=True)  # optional: scope to one course
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ------------------------------------------------------------------
    # Phase 2 - Subscription + Secure Exam Features
    # ------------------------------------------------------------------
    # What kind of test this is. "demo" = always free (no plan needed),
    # "mock" = requires an eligible active plan (unless free_without_plan),
    # "main" = the real/graded test series, also plan-gated unless
    # free_without_plan is set. Defaults to "mock" so every exam that
    # existed before Phase 2 keeps behaving exactly as it did (gated by
    # the existing course/tier purchase check, nothing free).
    test_type = db.Column(db.String(10), nullable=False, default="mock")  # demo / mock / main
    # Lets an admin explicitly mark a mock/main test as free even without a
    # plan (spec #7: "Main Tests require an active eligible subscription
    # unless explicitly configured as free"). Demo tests ignore this flag -
    # they are always free.
    free_without_plan = db.Column(db.Boolean, default=False)

    # Flexible (available whenever published + subscription active) vs
    # Scheduled (only inside an admin-configured start/end window).
    availability_mode = db.Column(db.String(12), nullable=False, default="flexible")  # flexible / scheduled
    available_from = db.Column(db.DateTime, nullable=True)
    available_until = db.Column(db.DateTime, nullable=True)

    # Attempt limit. None = unlimited (governed only by allow_retake, kept
    # for backward compatibility). When set, this is the hard server-side cap.
    max_attempts = db.Column(db.Integer, nullable=True)

    # Secure exam flow / proctoring toggles - all default OFF so existing
    # exams are completely unaffected until an admin opts in per-exam.
    camera_required = db.Column(db.Boolean, default=False)
    mic_required = db.Column(db.Boolean, default=False)
    face_detection_enabled = db.Column(db.Boolean, default=False)
    multi_face_detection_enabled = db.Column(db.Boolean, default=False)
    looking_away_detection_enabled = db.Column(db.Boolean, default=False)
    voice_detection_enabled = db.Column(db.Boolean, default=False)

    # Unified 3-warning system - configurable per exam, defaults to 3.
    max_warnings = db.Column(db.Integer, nullable=False, default=3)

    # Per-exam leaderboard visibility (spec #28: "Test leaderboard" toggle,
    # distinct from the global leaderboard's Admin > Settings toggle). None
    # = inherit the global on/off setting; True/False = explicit override
    # for this exam specifically.
    show_leaderboard = db.Column(db.Boolean, nullable=True, default=None)

    def is_within_schedule(self, when=None):
        """Scheduled tests can only be attempted inside their window;
        flexible tests are always considered within schedule."""
        when = when or datetime.utcnow()
        if self.availability_mode != "scheduled":
            return True
        if self.available_from and when < self.available_from:
            return False
        if self.available_until and when > self.available_until:
            return False
        return True

    def requires_plan(self):
        """Demo tests and explicitly free mock/main tests never require a plan."""
        if self.test_type == "demo":
            return False
        return not self.free_without_plan

    questions = db.relationship(
        "Question", backref="exam", lazy="dynamic", cascade="all, delete-orphan"
    )
    links = db.relationship(
        "ExamLink", backref="exam", lazy="dynamic", cascade="all, delete-orphan"
    )
    attempts = db.relationship("StudentAttempt", backref="exam", lazy="dynamic")
    course = db.relationship("Course", backref=db.backref("exams", lazy="dynamic"))

    def question_count(self):
        return self.questions.count()

    def recompute_total_marks(self):
        total = sum(q.marks or 0 for q in self.questions)
        self.total_marks = total
        return total


class ExamLink(db.Model):
    """The public shareable link: https://domain.com/exam/<token>"""

    __tablename__ = "exam_links"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    token = db.Column(db.String(32), unique=True, nullable=False, default=lambda: gen_token(10))
    is_active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    max_attempts = db.Column(db.Integer, nullable=True)  # None = unlimited
    password_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw) if raw else None

    def check_password(self, raw):
        if not self.password_hash:
            return True
        return check_password_hash(self.password_hash, raw or "")

    def is_expired(self):
        return bool(self.expires_at and datetime.utcnow() > self.expires_at)

    def attempts_used(self):
        return StudentAttempt.query.filter_by(exam_link_id=self.id).count()

    def is_usable(self):
        if not self.is_active or self.is_expired():
            return False
        if self.max_attempts and self.attempts_used() >= self.max_attempts:
            return False
        return True

    def public_url(self, base_url):
        return f"{base_url.rstrip('/')}/exam/{self.token}"


# --------------------------------------------------------------------------
# Questions & Options
# --------------------------------------------------------------------------
QUESTION_TYPES = (
    "single_choice",
    "multiple_choice",
    "true_false",
    "integer",
    "fill_blank",
    "image_based",
    "paragraph",
    "case_study",
)

TEST_CATEGORIES = (
    ("", "Custom / One-off Exam"),
    ("daily", "Daily Mock Test"),
    ("weekly", "Weekly Test"),
    ("monthly", "Monthly Grand Test"),
    ("grand", "Grand Test"),
)


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=True)
    question_type = db.Column(db.String(30), nullable=False, default="single_choice")
    question_text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(300), nullable=True)
    passage_text = db.Column(db.Text, nullable=True)  # for paragraph / case study groups
    marks = db.Column(db.Float, default=1)
    negative_marks = db.Column(db.Float, default=0)
    correct_integer_answer = db.Column(db.Integer, nullable=True)
    correct_text_answer = db.Column(db.String(300), nullable=True)  # fill in blank
    explanation = db.Column(db.Text, nullable=True)
    # Module 5 - richer post-exam explanation content (all optional/nullable,
    # existing questions are unaffected until an admin fills these in)
    detailed_explanation = db.Column(db.Text, nullable=True)
    explanation_reference = db.Column(db.String(300), nullable=True)
    explanation_youtube_url = db.Column(db.String(400), nullable=True)
    explanation_voice_url = db.Column(db.String(400), nullable=True)
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    options = db.relationship(
        "Option", backref="question", lazy="joined", cascade="all, delete-orphan",
        order_by="Option.order_index",
    )

    def correct_option_ids(self):
        return [o.id for o in self.options if o.is_correct]


class Option(db.Model):
    __tablename__ = "options"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    option_text = db.Column(db.String(500), nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
    order_index = db.Column(db.Integer, default=0)


# --------------------------------------------------------------------------
# Students
# --------------------------------------------------------------------------
class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(160), nullable=False, index=True)
    college = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempts = db.relationship("StudentAttempt", backref="student", lazy="dynamic")


# --------------------------------------------------------------------------
# Attempts / Answers / Results
# --------------------------------------------------------------------------
class StudentAttempt(db.Model):
    __tablename__ = "student_attempts"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    exam_link_id = db.Column(db.Integer, db.ForeignKey("exam_links.id"), nullable=False)

    status = db.Column(
        db.String(20), default="details_submitted"
    )  # details_submitted, in_progress, submitted, auto_submitted
    started_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    due_at = db.Column(db.DateTime, nullable=True)  # started_at + duration -> for auto submit

    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(500))
    browser = db.Column(db.String(100))
    os = db.Column(db.String(100))
    device = db.Column(db.String(100))

    tab_switch_count = db.Column(db.Integer, default=0)
    fullscreen_exit_count = db.Column(db.Integer, default=0)
    devtools_warning_count = db.Column(db.Integer, default=0)
    # Module 6 - set when strict anti-cheat auto-submits the attempt
    security_violation_reason = db.Column(db.String(100), nullable=True)
    security_violation_at = db.Column(db.DateTime, nullable=True)

    # Phase 2 - unified 3-warning system. warning_count is the single
    # server-side counter that drives every violation type (tab switch,
    # fullscreen exit, face missing, multiple faces, looking away, voice
    # detected, ...). Never reset by refresh / localStorage / client state.
    warning_count = db.Column(db.Integer, nullable=False, default=0)
    auto_submitted = db.Column(db.Boolean, nullable=False, default=False)

    # Which registered StudentAccount (if any) this attempt belongs to -
    # nullable so the existing anonymous/link-only flow is untouched, but
    # required for plan-gated (mock/main) tests so access + attempt-limit
    # checks can be enforced server-side. Populated at exam_landing time
    # when the visitor is logged in as a StudentAccount.
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=True)

    question_order = db.Column(db.Text, nullable=True)  # JSON list of question ids, shuffled per-student

    answers = db.relationship(
        "Answer", backref="attempt", lazy="dynamic", cascade="all, delete-orphan"
    )
    result = db.relationship(
        "Result", backref="attempt", uselist=False, cascade="all, delete-orphan"
    )
    violations = db.relationship(
        "SecurityViolation", backref="attempt", lazy="dynamic", cascade="all, delete-orphan"
    )
    student_account = db.relationship("StudentAccount", foreign_keys=[student_account_id])

    def elapsed_seconds(self):
        if not self.started_at:
            return 0
        end = self.submitted_at or datetime.utcnow()
        return int((end - self.started_at).total_seconds())


# --------------------------------------------------------------------------
# Phase 2 - Security Violations (unified 3-warning system)
# One row per recorded violation (tab switch, fullscreen exit, face
# missing, multiple faces, looking away, voice detected, ...), persisted
# server-side against the attempt so refresh / localStorage tampering /
# direct API calls can never reset or bypass the warning count.
# --------------------------------------------------------------------------
class SecurityViolation(db.Model):
    __tablename__ = "security_violations"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("student_attempts.id"), nullable=False)
    violation_type = db.Column(db.String(30), nullable=False)
    # tab_switch / fullscreen_exit / blur / devtools / face_missing /
    # multiple_faces / looking_away / voice_detected
    warning_number = db.Column(db.Integer, nullable=False)  # 1, 2, or 3 at the time this was recorded
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def record_violation(attempt, violation_type, max_warnings=None):
    """Single entry point for the unified 3-warning system (spec #15-17).
    Persists the violation, increments the ONE server-side warning
    counter for this attempt, and reports whether the exam should now be
    auto-submitted. Never trusts/accepts a client-supplied count.
    """
    max_warnings = max_warnings or (attempt.exam.max_warnings if attempt.exam else 3) or 3
    attempt.warning_count = (attempt.warning_count or 0) + 1
    db.session.add(SecurityViolation(
        attempt_id=attempt.id, violation_type=violation_type, warning_number=attempt.warning_count,
    ))
    should_auto_submit = attempt.warning_count >= max_warnings
    return attempt.warning_count, should_auto_submit


class Answer(db.Model):
    __tablename__ = "answers"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("student_attempts.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)

    selected_option_ids = db.Column(db.String(200), nullable=True)  # comma separated ids
    integer_answer = db.Column(db.Integer, nullable=True)
    text_answer = db.Column(db.String(500), nullable=True)

    status = db.Column(db.String(20), default="not_answered")
    # not_visited, not_answered, answered, marked_for_review, answered_marked

    is_correct = db.Column(db.Boolean, nullable=True)
    marks_awarded = db.Column(db.Float, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    question = db.relationship("Question")

    def selected_ids_list(self):
        if not self.selected_option_ids:
            return []
        return [int(x) for x in self.selected_option_ids.split(",") if x]


class Result(db.Model):
    __tablename__ = "results"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("student_attempts.id"), nullable=False)

    total_questions = db.Column(db.Integer, default=0)
    attempted = db.Column(db.Integer, default=0)
    correct = db.Column(db.Integer, default=0)
    wrong = db.Column(db.Integer, default=0)
    skipped = db.Column(db.Integer, default=0)

    score = db.Column(db.Float, default=0)
    total_marks = db.Column(db.Float, default=0)
    percentage = db.Column(db.Float, default=0)
    rank = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(10), default="fail")  # pass / fail
    time_taken_seconds = db.Column(db.Integer, default=0)
    pdf_path = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("student_attempts.id"), nullable=True)
    to_address = db.Column(db.String(160))
    subject = db.Column(db.String(300))
    status = db.Column(db.String(20))  # sent / failed
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(160))  # admin email or "student:<id>" or "system"
    action = db.Column(db.String(200))
    meta = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def log_activity(actor, action, meta=None, ip_address=None):
    entry = ActivityLog(actor=actor, action=action, meta=meta, ip_address=ip_address)
    db.session.add(entry)


# --------------------------------------------------------------------------
# Student Accounts (registered/logged-in students)
# --------------------------------------------------------------------------
# NOTE: This is intentionally a separate table from `Student` above.
# `Student` is the lightweight, anonymous record created per exam-link
# attempt (no login) and stays untouched. `StudentAccount` is the new,
# persistent, login-capable profile added for the student portal
# (registration / profile / dashboard). The two are linked loosely by
# email so a student's dashboard can surface their past exam history
# without altering the existing exam-link flow.
class StudentAccount(UserMixin, db.Model):
    __tablename__ = "student_accounts"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    college_name = db.Column(db.String(200), nullable=False)
    photo_path = db.Column(db.String(300), nullable=True)
    address = db.Column(db.Text, nullable=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    is_verified = db.Column(db.Boolean, default=False)
    is_active_flag = db.Column(db.Boolean, default=True)

    # Single-purpose OTP slot, reused for email-verification and
    # password-reset flows (never both active at once).
    otp_hash = db.Column(db.String(255), nullable=True)
    otp_purpose = db.Column(db.String(20), nullable=True)  # 'verify' or 'reset'
    otp_expires_at = db.Column(db.DateTime, nullable=True)
    otp_attempts = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ---- password (bcrypt) ----
    def set_password(self, raw):
        self.password_hash = bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def check_password(self, raw):
        if not self.password_hash or not raw:
            return False
        try:
            return bcrypt.checkpw(raw.encode("utf-8"), self.password_hash.encode("utf-8"))
        except ValueError:
            return False

    @property
    def is_active(self):
        return self.is_active_flag

    # ---- OTP (email verification / password reset) ----
    def generate_otp(self, purpose, ttl_minutes=10):
        code = "".join(random.choices(string.digits, k=6))
        self.otp_hash = generate_password_hash(code)
        self.otp_purpose = purpose
        self.otp_expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        self.otp_attempts = 0
        return code

    def check_otp(self, code, purpose):
        if not self.otp_hash or self.otp_purpose != purpose:
            return False
        if not self.otp_expires_at or datetime.utcnow() > self.otp_expires_at:
            return False
        if self.otp_attempts is not None and self.otp_attempts >= 5:
            return False
        self.otp_attempts = (self.otp_attempts or 0) + 1
        return check_password_hash(self.otp_hash, code)

    def clear_otp(self):
        self.otp_hash = None
        self.otp_purpose = None
        self.otp_expires_at = None
        self.otp_attempts = 0


# --------------------------------------------------------------------------
# Courses (Module 9)
# --------------------------------------------------------------------------
class TickerItem(db.Model):
    """One item in the scrolling announcement banner shown under the
    navbar (e.g. "Today's Offer: 20% off SSC CGL Test Series"). Admin-
    managed, ordered, individually toggleable - see admin/routes.py's
    ticker_* routes and Admin > Ticker Banner."""
    __tablename__ = "ticker_items"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(200), nullable=False)
    link = db.Column(db.String(300), nullable=True)  # internal path or full URL; optional
    icon = db.Column(db.String(40), nullable=True, default="bi-megaphone")  # Bootstrap Icons class
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def resolved_link(self):
        return self.link or None


class SiteSetting(db.Model):
    """Simple key-value store for platform-wide toggles that don't warrant
    their own dedicated table (leaderboard visibility, etc.) - a deliberately
    small, reusable pattern rather than one column per setting scattered
    across unrelated tables."""
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(400), nullable=True)

    @staticmethod
    def get(key, default=None):
        row = SiteSetting.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def get_bool(key, default=False):
        val = SiteSetting.get(key, "true" if default else "false")
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def set(key, value):
        row = SiteSetting.query.filter_by(key=key).first()
        if not row:
            row = SiteSetting(key=key)
            db.session.add(row)
        row.value = str(value)


class Category(db.Model):
    """Exam category (SSC, Banking, Railways, UPSC, APPSC, TSPSC, Teaching,
    Police, Defence, Technical, Software Jobs, ...). Admin-managed taxonomy
    so new categories can be added without a code change - Course rows
    reference this via category_id (nullable, so ungrouped/legacy courses
    still work and just show under "Other")."""
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    slug = db.Column(db.String(90), unique=True, nullable=False)
    description = db.Column(db.String(300), nullable=True)
    icon = db.Column(db.String(40), nullable=True, default="bi-mortarboard")  # Bootstrap Icons class
    display_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    courses = db.relationship("Course", backref="category", lazy="dynamic")

    def active_course_count(self):
        return self.courses.filter_by(is_active=True).count()

    @staticmethod
    def make_slug(name):
        import re
        return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    faculty = db.Column(db.String(200), nullable=True)
    subjects = db.Column(db.String(300), nullable=True)  # comma-separated display text
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    duration_label = db.Column(db.String(60), nullable=True)  # e.g. "6 Months" (display only)
    duration_days = db.Column(db.Integer, nullable=False, default=180)  # used to compute subscription expiry
    price = db.Column(db.Float, nullable=False, default=0)  # legacy field == price of the "combined" tier
    discount_percent = db.Column(db.Float, nullable=False, default=0)  # applied to every tier below
    features = db.Column(db.Text, nullable=True)  # one feature per line
    # --- DEPRECATED (Phase 1 cleanup, Aug 2026): video/live-class fields ----
    # These columns are intentionally KEPT in the schema so existing data is
    # never lost, but the video/live-class feature itself has been disabled:
    # the Admin UI no longer lets anyone set them, and the student-facing
    # "watch" flow (routes + templates) has been removed. Do not build new
    # functionality on top of these until Phase 2 explicitly revisits video.
    demo_video_url = db.Column(db.String(400), nullable=True)  # [DEPRECATED] free preview - no longer editable/shown
    course_video_url = db.Column(db.String(400), nullable=True)  # [DEPRECATED] paid class video - watch route removed
    image_path = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ---- Tiered pricing (Payments Module 10b) ----------------------------
    # Four independent purchase tiers so a student can buy exactly what
    # they need instead of one bundled price:
    #   exam_only    -> mock tests / exams for this course only
    #   exam_pdf     -> exams + notes/PDF/assignment/practice materials
    #   class_only   -> recorded/live video classes only
    #   combined     -> everything (exam + pdf + class) - stored in `price` above
    # A tier price of 0/None means that tier is not sold for this course.
    price_exam_only = db.Column(db.Float, nullable=True, default=0)
    price_exam_pdf = db.Column(db.Float, nullable=True, default=0)
    price_class_only = db.Column(db.Float, nullable=True, default=0)  # [DEPRECATED] video tier, no longer sold
    price_mock_tests = db.Column(db.Float, nullable=True, default=0)  # Mock tests as separate package
    price_videos_only = db.Column(db.Float, nullable=True, default=0)  # [DEPRECATED] video tier, no longer sold
    
    # Package configuration: whether videos/pdfs/exams/mock_tests are separate packages
    videos_as_separate_package = db.Column(db.Boolean, default=False)  # [DEPRECATED] video packaging, unused now
    pdfs_as_separate_package = db.Column(db.Boolean, default=False)   # True = PDFs require separate purchase
    exams_as_separate_package = db.Column(db.Boolean, default=False)  # True = Exams require separate purchase
    mock_tests_as_separate_package = db.Column(db.Boolean, default=True)  # True = Mock tests require separate purchase

    purchases = db.relationship("CoursePurchase", backref="course", lazy="dynamic")

    # NOTE: "class_only" and "videos_only" are kept in TIER_CHOICES/TIER_LABELS
    # only so historical CoursePurchase rows with these tiers keep resolving
    # correctly (labels, access checks). They are intentionally excluded from
    # available_tiers() below, so they can no longer be newly purchased.
    TIER_CHOICES = ("exam_only", "exam_pdf", "class_only", "combined", "mock_tests", "videos_only")
    TIER_LABELS = {
        "exam_only": "Exam Only",
        "exam_pdf": "Exam + PDF / Notes",
        "class_only": "Class (Video) Only [Discontinued]",
        "combined": "All Access (Exam + PDF)",
        "mock_tests": "Mock Tests Only",
        "videos_only": "Videos Only [Discontinued]",
    }
    # Tiers whose sale is disabled as of the Phase 1 video/live-class cleanup.
    VIDEO_TIERS = ("class_only", "videos_only")

    def final_price(self):
        """Backward-compatible: final price of the combined/all-access tier."""
        return self.tier_final_price("combined")

    def tier_price(self, tier):
        if tier == "combined":
            return self.price or 0
        if tier == "exam_only":
            return self.price_exam_only or 0
        if tier == "exam_pdf":
            return self.price_exam_pdf or 0
        if tier == "class_only":
            return self.price_class_only or 0
        if tier == "mock_tests":
            return self.price_mock_tests or 0
        if tier == "videos_only":
            return self.price_videos_only or 0
        return 0

    def tier_final_price(self, tier):
        discount = self.discount_percent or 0
        return round(self.tier_price(tier) * (1 - discount / 100), 2)

    def available_tiers(self):
        """Tiers with a price > 0, i.e. actually offered for this course.
        Video/live-class tiers are excluded - see VIDEO_TIERS above."""
        return [t for t in self.TIER_CHOICES if t not in self.VIDEO_TIERS and self.tier_price(t) > 0]

    def starting_price(self):
        """Cheapest available plan's final price - used for 'From ₹X' listing display."""
        prices = [self.tier_final_price(t) for t in self.available_tiers()]
        return min(prices) if prices else 0

    def starting_price_original(self):
        """Same tier as starting_price(), but the pre-discount price - used
        for the strikethrough alongside starting_price() on listing cards."""
        tiers = self.available_tiers()
        if not tiers:
            return 0
        cheapest_tier = min(tiers, key=lambda t: self.tier_final_price(t))
        return self.tier_price(cheapest_tier)

    def feature_list(self):
        return [f.strip() for f in (self.features or "").splitlines() if f.strip()]

    def has_class_video(self):
        """Whether this course has a full/paid class video configured at all."""
        return bool(self.course_video_url)


# --------------------------------------------------------------------------
# Phase 2 - Subscription Plans (Module 15)
# Admin-configurable time-based plans (e.g. "3 Months", "6 Months",
# "1 Year", or any custom name/duration an admin creates). A plan is
# scoped to one Course and grants some combination of materials / mock
# tests / main tests for its duration. This sits ALONGSIDE the existing
# Course tier system (exam_only / exam_pdf / combined / mock_tests) -
# CoursePurchase.plan_id is nullable, so every purchase made before Phase
# 2 (plan_id is NULL) keeps working exactly as before, driven by `tier`.
# New purchases can optionally be made against a specific plan instead.
# --------------------------------------------------------------------------
DURATION_UNITS = ("days", "months", "years")


class SubscriptionPlan(db.Model):
    __tablename__ = "subscription_plans"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)

    name = db.Column(db.String(120), nullable=False)  # e.g. "3 Months", "6 Months", "1 Year"
    duration_value = db.Column(db.Integer, nullable=False, default=3)
    duration_unit = db.Column(db.String(10), nullable=False, default="months")  # days / months / years
    price = db.Column(db.Float, nullable=False, default=0)
    description = db.Column(db.Text, nullable=True)

    includes_materials = db.Column(db.Boolean, default=True)
    includes_mock_tests = db.Column(db.Boolean, default=True)
    includes_main_tests = db.Column(db.Boolean, default=True)

    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    course = db.relationship("Course", backref=db.backref(
        "subscription_plans", lazy="dynamic", cascade="all, delete-orphan"))

    def duration_days(self):
        if self.duration_unit == "years":
            return (self.duration_value or 0) * 365
        if self.duration_unit == "months":
            return (self.duration_value or 0) * 30
        return self.duration_value or 0

    def duration_label(self):
        unit = self.duration_unit
        val = self.duration_value or 0
        if val == 1:
            unit = unit[:-1]  # month/year/day (singular)
        return f"{val} {unit}"


# --------------------------------------------------------------------------
# Course Purchases / Payments (Module 10)
# --------------------------------------------------------------------------
PAYMENT_STATUSES = ("pending", "paid", "failed", "cancelled", "refunded")
PAYMENT_METHODS = ("UPI", "Razorpay")


class PaymentTransaction(db.Model):
    """Append-only log of every payment attempt against a purchase.

    This is NOT a replacement for CoursePurchase, which remains the single
    Order/entitlement record (status, amount, expiry - everything already
    reading those fields keeps working unchanged). It exists because
    CoursePurchase only has ONE razorpay_order_id/payment_id slot: before
    this, a retry after a failed payment silently overwrote the previous
    attempt's transaction data, losing that history entirely. Every row
    here is additive - nothing ever updates or deletes a prior row.
    """
    __tablename__ = "payment_transactions"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("course_purchases.id"), nullable=False)
    gateway = db.Column(db.String(20), nullable=False)  # razorpay / upi / manual
    gateway_order_id = db.Column(db.String(100), nullable=True)
    gateway_payment_id = db.Column(db.String(100), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # created / success / failed
    failure_reason = db.Column(db.String(300), nullable=True)
    method_detail = db.Column(db.String(200), nullable=True)  # e.g. "UPI - likely PhonePe (x@ybl)", "Debit Card - Visa ****4242"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    purchase = db.relationship(
        "CoursePurchase",
        backref=db.backref("transactions", lazy="dynamic", order_by="PaymentTransaction.created_at.desc()"),
    )


def log_payment_transaction(purchase, gateway, status, order_id=None, payment_id=None, failure_reason=None, amount=None, method_detail=None):
    """Single entry point for recording a transaction attempt - keeps the
    "never overwrite, always append" rule in one place rather than repeated
    at every call site."""
    db.session.add(PaymentTransaction(
        purchase_id=purchase.id, gateway=gateway, status=status,
        gateway_order_id=order_id, gateway_payment_id=payment_id,
        amount=amount if amount is not None else purchase.amount,
        failure_reason=failure_reason, method_detail=method_detail,
    ))


class Coupon(db.Model):
    """Discount coupon - percentage or fixed amount off a course purchase.
    Every rule here (validity window, min purchase, usage caps, which
    courses it applies to) is enforced server-side in coupon_service.py at
    the moment a coupon is applied AND again right before a payment is
    verified - never trust a discount amount echoed back from the client.
    """
    __tablename__ = "coupons"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)

    discount_type = db.Column(db.String(10), nullable=False, default="percent")  # percent | fixed
    discount_value = db.Column(db.Float, nullable=False)
    max_discount_amount = db.Column(db.Float, nullable=True)  # cap for percent coupons, e.g. "20% off up to Rs.200"
    min_purchase_amount = db.Column(db.Float, nullable=True, default=0)

    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)

    max_usage_total = db.Column(db.Integer, nullable=True)      # None = unlimited
    max_usage_per_user = db.Column(db.Integer, nullable=False, default=1)

    # Comma-separated Course ids this coupon is restricted to. Empty/NULL = applies to every course.
    course_ids = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def course_id_list(self):
        if not self.course_ids:
            return []
        return [int(x) for x in self.course_ids.split(",") if x.strip().isdigit()]

    def applies_to_course(self, course_id):
        ids = self.course_id_list()
        return not ids or course_id in ids

    def is_currently_valid(self):
        now = datetime.utcnow()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True

    def usage_count(self):
        return CouponRedemption.query.filter_by(coupon_id=self.id).count()

    def compute_discount(self, amount):
        if self.discount_type == "percent":
            d = amount * (self.discount_value / 100.0)
            if self.max_discount_amount:
                d = min(d, self.max_discount_amount)
        else:
            d = self.discount_value
        return round(max(0.0, min(d, amount)), 2)


class CouponRedemption(db.Model):
    """One row per successfully *paid* purchase that used a coupon - created
    only in activate_paid_purchase(), never when a coupon is merely applied
    at checkout, so an abandoned/failed payment never consumes usage."""
    __tablename__ = "coupon_redemptions"

    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey("coupons.id"), nullable=False)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    purchase_id = db.Column(db.Integer, db.ForeignKey("course_purchases.id"), nullable=False, unique=True)
    discount_amount = db.Column(db.Float, nullable=False)
    redeemed_at = db.Column(db.DateTime, default=datetime.utcnow)

    coupon = db.relationship("Coupon", backref=db.backref("redemptions", lazy="dynamic"))
    student = db.relationship("StudentAccount")


class CoursePurchase(db.Model):
    __tablename__ = "course_purchases"

    id = db.Column(db.Integer, primary_key=True)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)

    # Phase 2: which configurable SubscriptionPlan this purchase is for.
    # Nullable - pre-Phase-2 purchases (and simple tier-only purchases)
    # leave this NULL and keep being driven entirely by `tier` below.
    plan_id = db.Column(db.Integer, db.ForeignKey("subscription_plans.id"), nullable=True)

    # Which access tier this purchase is for - see Course.TIER_CHOICES.
    # Defaults to "combined" so rows created before this column existed
    # keep granting full access, exactly like before.
    tier = db.Column(db.String(20), nullable=False, default="combined")

    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending / paid / failed / cancelled
    payment_method = db.Column(db.String(30), default="UPI")  # "UPI" or "Razorpay"

    # A short random token embedded in the UPI QR "note" field so a manually
    # entered UTR/reference can be matched back to this purchase record.
    reference_code = db.Column(db.String(20), unique=True, nullable=False, default=lambda: gen_token(8))
    student_txn_id = db.Column(db.String(120), nullable=True)  # UTR / UPI ref student enters after paying

    # Razorpay gateway fields (Module 10b - automatic verification).
    razorpay_order_id = db.Column(db.String(80), nullable=True)
    razorpay_payment_id = db.Column(db.String(80), nullable=True)
    razorpay_signature = db.Column(db.String(200), nullable=True)

    invoice_number = db.Column(db.String(40), unique=True, nullable=True)

    # Coupon system - see Coupon/CouponRedemption below. `original_amount`
    # preserves the pre-discount base price so the discount is always
    # re-derivable and auditable; `amount` remains the actual payable total
    # everywhere it was already used (Razorpay order, UPI QR, invoice).
    coupon_id = db.Column(db.Integer, db.ForeignKey("coupons.id"), nullable=True)
    original_amount = db.Column(db.Float, nullable=True)
    discount_amount = db.Column(db.Float, nullable=False, default=0.0)
    tax_amount = db.Column(db.Float, nullable=False, default=0.0)  # see pricing_service.py - GST, display/audit only; `amount` is always the final payable total
    platform_fee_amount = db.Column(db.Float, nullable=False, default=0.0)  # see pricing_service.py - Platform/Payment Convenience Fee, display/audit only; already folded into `amount`

    # Refunds/cancellations - see refund_service.py. A refund does NOT
    # delete or overwrite anything above (original_amount/discount_amount/
    # tax_amount/platform_fee_amount/amount stay exactly as originally
    # charged, for an accurate paid-vs-refunded audit trail). Setting
    # status="refunded" is what actually revokes access - is_active_
    # subscription() only grants access when status=="paid", so a refunded
    # purchase is locked out immediately without needing to touch
    # expires_at (which is deliberately left alone as a historical record
    # of what the expiry would have been).
    refund_amount = db.Column(db.Float, nullable=True)  # actual amount refunded to the student
    refund_platform_fee_amount = db.Column(db.Float, nullable=True)  # portion of platform_fee_amount that was (or wasn't) included in the refund - see refund_service.refund_policy()
    refund_reason = db.Column(db.String(300), nullable=True)
    refunded_at = db.Column(db.DateTime, nullable=True)
    refunded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # admin who processed it
    razorpay_refund_id = db.Column(db.String(80), nullable=True)  # set only when refunded automatically via the Razorpay API

    verified_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)  # subscription expiry once activated
    expiry_reminder_sent = db.Column(db.Boolean, default=False)  # see cli.py send-expiry-reminders

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("StudentAccount", backref="purchases")
    plan = db.relationship("SubscriptionPlan", backref=db.backref("purchases", lazy="dynamic"))
    coupon = db.relationship("Coupon", backref=db.backref("purchases", lazy="dynamic"))

    def is_active_subscription(self):
        return self.status == "paid" and self.expires_at and datetime.utcnow() < self.expires_at

    def days_remaining(self):
        if not self.expires_at:
            return 0
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)

    def tier_label(self):
        if self.plan_id and self.plan:
            return self.plan.name
        return Course.TIER_LABELS.get(self.tier, self.tier)

    # ---- Access grants ------------------------------------------------
    # When this purchase is tied to a Phase 2 SubscriptionPlan, access is
    # driven by that plan's include flags. Otherwise (plan_id is NULL,
    # i.e. every purchase made before Phase 2, or a plain tier purchase)
    # access falls back to the original tier-based logic below, unchanged.
    def grants_exam(self):
        """Main-test / general exam access."""
        if not self.is_active_subscription():
            return False
        if self.plan_id and self.plan:
            return bool(self.plan.includes_main_tests)
        if self.course.exams_as_separate_package:
            return self.tier in ("exam_only", "combined")
        return self.tier in ("exam_only", "exam_pdf", "combined")

    def grants_main_tests(self):
        return self.grants_exam()

    def grants_pdf_materials(self):
        if not self.is_active_subscription():
            return False
        if self.plan_id and self.plan:
            return bool(self.plan.includes_materials)
        if self.course.pdfs_as_separate_package:
            return self.tier in ("exam_pdf", "combined")
        return self.tier in ("exam_pdf", "combined")

    def grants_class_materials(self):
        if not self.is_active_subscription():
            return False
        if self.plan_id and self.plan:
            return False  # video/live classes were discontinued in Phase 1
        if self.course.videos_as_separate_package:
            return self.tier in ("class_only", "combined", "videos_only")
        return self.tier in ("class_only", "combined")

    def grants_mock_tests(self):
        if not self.is_active_subscription():
            return False
        if self.plan_id and self.plan:
            return bool(self.plan.includes_mock_tests)
        if self.course.mock_tests_as_separate_package:
            return self.tier in ("mock_tests", "combined")
        return self.tier in ("exam_only", "exam_pdf", "combined")


# --------------------------------------------------------------------------
# Notifications (Module 14)
# --------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    type = db.Column(db.String(40), default="general")
    # exam_reminder / course_expiry / new_course / new_test / payment_success / result_published / general
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("StudentAccount", backref="notifications")


def notify_student(student_account_id, title, message=None, type="general"):
    n = Notification(student_account_id=student_account_id, title=title, message=message, type=type)
    db.session.add(n)
    return n


# --------------------------------------------------------------------------
# WhatsApp Support (Module 15) - student <-> management support over the
# WhatsApp Business Cloud API. See services/whatsapp_service.py (raw Cloud
# API calls + webhook signature verification) and
# services/whatsapp_bot_service.py (the menu/auto-reply/handoff logic that
# actually uses these two models).
#
# Identification note (see whatsapp_bot_service.py docstring for the full
# reasoning): `student_account_id` is only ever set two ways - (a) the
# student tapped "WhatsApp Support" on their own dashboard while logged in
# (the most secure path - we already know exactly who they are), or (b) an
# admin manually links the conversation from the inbox after actually
# talking to the person. We deliberately do NOT auto-link a conversation to
# a StudentAccount just because its phone number happens to match one on
# file - phone numbers are self-reported, get reassigned/reused, and
# blindly trusting a WhatsApp sender ID as proof of identity would let
# anyone who has a former student's number see that student's support
# history. `phone_number_hint` still lets the admin inbox show "possibly
# Rahul Sharma (unconfirmed)" as a convenience without treating it as fact.
# --------------------------------------------------------------------------
WHATSAPP_CONVERSATION_STATUSES = ("NEW", "IN_PROGRESS", "WAITING_FOR_STUDENT", "RESOLVED")
WHATSAPP_CATEGORIES = (
    "exam_doubt", "payment_subscription", "login_account", "exam_attempt",
    "result_query", "exam_schedule", "management", "other",
)


class WhatsAppConversation(db.Model):
    __tablename__ = "whatsapp_conversations"

    id = db.Column(db.Integer, primary_key=True)
    # Short, human-shareable code (e.g. "KHE-SUP-48291") generated when a
    # logged-in student taps the dashboard button, and echoed back in the
    # prefilled WhatsApp message text - this, not the phone number alone,
    # is the trusted link back to student_account_id below.
    support_reference = db.Column(db.String(20), unique=True, nullable=False, index=True)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=True, index=True)
    whatsapp_number = db.Column(db.String(20), nullable=False, index=True)  # E.164-ish, digits only, as Meta sends it

    status = db.Column(db.String(20), default="NEW", nullable=False)
    category = db.Column(db.String(30), default="other")
    priority = db.Column(db.String(10), default="normal")  # low / normal / high
    assigned_to = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # Bot state machine - "menu" (default) means the bot is still handling
    # this conversation automatically; None means it's been handed off to
    # management and the bot must stay silent (see requirement: bot stops
    # sending irrelevant automated responses after "Talk to Management").
    bot_state = db.Column(db.String(30), default="menu", nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_message_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("StudentAccount", backref="whatsapp_conversations")
    assigned_admin = db.relationship("User", foreign_keys=[assigned_to])
    messages = db.relationship(
        "WhatsAppMessage", backref="conversation", lazy="dynamic",
        order_by="WhatsAppMessage.created_at", cascade="all, delete-orphan",
    )

    def category_label(self):
        return {
            "exam_doubt": "Exam Related Doubt", "payment_subscription": "Payment / Subscription",
            "login_account": "Login / Account", "exam_attempt": "Exam Attempt Problem",
            "result_query": "Result Query", "exam_schedule": "Exam Schedule",
            "management": "Talk to Management", "other": "Other Support",
        }.get(self.category, "Other Support")


class WhatsAppMessage(db.Model):
    """Append-only - a message is never edited or deleted once logged, same
    reasoning as PaymentTransaction above (accurate history for support/
    dispute review)."""
    __tablename__ = "whatsapp_messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("whatsapp_conversations.id"), nullable=False, index=True)
    direction = db.Column(db.String(10), nullable=False)  # "in" (from student) / "out" (to student)
    message_type = db.Column(db.String(20), default="text")  # text / bot_auto / template
    message_text = db.Column(db.Text, nullable=False)
    # Meta's own message id (inbound) - unique so a re-delivered webhook
    # event for the same message can be detected and skipped. Nullable
    # because bot-generated outbound messages don't have one until Meta's
    # send-API response comes back (we still store it once we have it).
    whatsapp_message_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    delivery_status = db.Column(db.String(20), nullable=True)  # sent / delivered / read / failed - updated by status webhooks
    is_internal_note = db.Column(db.Boolean, default=False)  # admin-only note, NEVER sent to the student
    sent_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # admin who sent this (direction="out" only)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


def generate_support_reference():
    """KHE-SUP-##### - short enough to type/read over WhatsApp, random
    enough (90000 possibilities) that guessing another student's reference
    isn't practical, and never derived from (so never leaks) the student's
    actual database id."""
    import random
    while True:
        candidate = f"KHE-SUP-{random.randint(10000, 99999)}"
        if not WhatsAppConversation.query.filter_by(support_reference=candidate).first():
            return candidate


# --------------------------------------------------------------------------
# Course Materials / restricted PDF viewer (Module 11)
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Study Materials CMS (Phase 2): Course -> Subject -> Chapter -> Material
# New, additive tables - independent from the exam question-bank Subject
# model above, since a course's study-material subject (e.g. "Indian
# Polity") is a different concept from an exam's question-tagging subject.
# --------------------------------------------------------------------------
class MaterialSubject(db.Model):
    __tablename__ = "material_subjects"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    course = db.relationship("Course", backref=db.backref(
        "material_subjects", order_by="MaterialSubject.order_index", cascade="all, delete-orphan"))

    def chapter_count(self):
        return self.chapters.count()

    def material_count(self):
        return sum(ch.materials.count() for ch in self.chapters)


class MaterialChapter(db.Model):
    __tablename__ = "material_chapters"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("material_subjects.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    order_index = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subject = db.relationship("MaterialSubject", backref=db.backref(
        "chapters", order_by="MaterialChapter.order_index", lazy="dynamic", cascade="all, delete-orphan"))


MATERIAL_TYPES = (
    ("video", "Video Class"),  # [DEPRECATED] kept only so existing video materials still render/label correctly
    ("notes", "Notes / PDF"),
    ("assignment", "Assignment"),
    ("practice_sheet", "Practice Sheet"),
    ("previous_paper", "Previous Paper"),
)

# Material types an admin can newly upload as of the Phase 1 video/live-class
# cleanup. "video" is intentionally excluded - no new video materials can be
# created, but existing ones (if any) are left untouched in the database.
CREATABLE_MATERIAL_TYPES = tuple(t for t in MATERIAL_TYPES if t[0] != "video")

# Access-duration presets offered on the admin upload form (Phase 2 spec):
# 30 / 60 / 90 / 180 / 365 days, or None = lifetime (tied to course validity).
ACCESS_DURATION_CHOICES = (30, 60, 90, 180, 365)


class CourseMaterial(db.Model):
    __tablename__ = "course_materials"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    chapter_id = db.Column(db.Integer, db.ForeignKey("material_chapters.id"), nullable=True)  # nullable: pre-Phase-2 rows
    title = db.Column(db.String(200), nullable=False)
    material_type = db.Column(db.String(30), nullable=False, default="notes")  # see MATERIAL_TYPES
    file_path = db.Column(db.String(300), nullable=True)  # stored under instance/uploads/course_materials (PDF/video types)
    video_url = db.Column(db.String(400), nullable=True)  # for material_type == 'video' (external YouTube/Vimeo)
    is_video_file_upload = db.Column(db.Boolean, default=False)  # True if video is uploaded file, not external URL
    access_duration_days = db.Column(db.Integer, nullable=True)  # None = lifetime (course-validity bound)
    order_index = db.Column(db.Integer, default=0)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Security options
    enable_watermark = db.Column(db.Boolean, default=True)  # Add student watermark to videos
    enable_download_protection = db.Column(db.Boolean, default=True)  # Prevent downloading
    enable_copy_protection = db.Column(db.Boolean, default=True)  # Disable right-click copy on PDFs
    disable_seek = db.Column(db.Boolean, default=False)  # Prevent video seeking (ultra-strict)

    course = db.relationship("Course", backref=db.backref("materials", order_by="CourseMaterial.order_index"))
    chapter = db.relationship("MaterialChapter", backref=db.backref(
        "materials", order_by="CourseMaterial.order_index", lazy="dynamic"))

    def material_type_label(self):
        return dict(MATERIAL_TYPES).get(self.material_type, self.material_type)

    def access_expiry_for(self, purchase):
        """None = no material-specific limit (governed by the course
        subscription's own expiry only). Otherwise, the earlier of the two."""
        if not self.access_duration_days or not purchase or not purchase.created_at:
            return None
        return purchase.created_at + timedelta(days=self.access_duration_days)

    def is_accessible_for(self, purchase):
        if not purchase or not purchase.is_active_subscription():
            return False
        # Tier gating: video classes require the class_only/combined tier;
        # every other material type (notes/pdf/assignment/practice/paper)
        # requires the exam_pdf/combined tier.
        if self.material_type == "video":
            if not purchase.grants_class_materials():
                return False
        else:
            if not purchase.grants_pdf_materials():
                return False
        material_expiry = self.access_expiry_for(purchase)
        if material_expiry and datetime.utcnow() > material_expiry:
            return False
        return True


# --------------------------------------------------------------------------
# Exam Enrollments - who gets 24h/1h reminder emails for a scheduled exam
# --------------------------------------------------------------------------
class ExamEnrollment(db.Model):
    __tablename__ = "exam_enrollments"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    exam_link_id = db.Column(db.Integer, db.ForeignKey("exam_links.id"), nullable=True)

    reminder_24h_sent = db.Column(db.Boolean, default=False)
    reminder_1h_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    exam = db.relationship("Exam", backref="enrollments")
    student = db.relationship("StudentAccount", backref="exam_enrollments")
    exam_link = db.relationship("ExamLink")

    __table_args__ = (db.UniqueConstraint("exam_id", "student_account_id", name="uq_exam_student_enrollment"),)


# --------------------------------------------------------------------------
# Material Access Log (Phase 3) - every open of the secure PDF/video viewer
# is recorded here: who, what, when, from where.
# --------------------------------------------------------------------------
class MaterialAccessLog(db.Model):
    __tablename__ = "material_access_logs"

    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey("course_materials.id"), nullable=False)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    action = db.Column(db.String(20), default="view")  # view / stream
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    material = db.relationship("CourseMaterial", backref=db.backref("access_logs", lazy="dynamic"))
    student = db.relationship("StudentAccount", backref=db.backref("material_access_logs", lazy="dynamic"))


def log_material_access(material_id, student_account_id, action, ip_address=None, user_agent=None):
    entry = MaterialAccessLog(
        material_id=material_id, student_account_id=student_account_id, action=action,
        ip_address=ip_address, user_agent=(user_agent or "")[:400],
    )
    db.session.add(entry)
    return entry


# --------------------------------------------------------------------------
# AI Performance Analysis Engine (Phase 4)
# One row per graded StudentAttempt. Built by
# app/services/ai_analysis_service.py right after grading, alongside the
# existing Result row - purely additive and never blocks submission.
# --------------------------------------------------------------------------
class PerformanceAnalysis(db.Model):
    __tablename__ = "performance_analyses"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("student_attempts.id"), unique=True, nullable=False)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=True, index=True)

    accuracy_percent = db.Column(db.Float, default=0)          # correct / attempted
    attempt_rate_percent = db.Column(db.Float, default=0)      # attempted / total
    negative_marks_lost = db.Column(db.Float, default=0)
    avg_seconds_per_question = db.Column(db.Float, default=0)
    exam_readiness_percent = db.Column(db.Float, default=0)
    expected_percentile = db.Column(db.Float, default=0)       # 0-100, based on peers on the same exam
    expected_rank_label = db.Column(db.String(40), nullable=True)   # e.g. "Top 8%"
    trend_vs_previous = db.Column(db.Float, nullable=True)     # +/- percentage points vs the student's prior attempt

    # JSON-encoded (via Text) so the schema doesn't need to change as the
    # question bank's subject list grows.
    subject_breakdown_json = db.Column(db.Text, nullable=True)   # [{subject, total, correct, wrong, skipped, accuracy}]
    weak_topics_json = db.Column(db.Text, nullable=True)         # ["Fundamental Rights", ...]
    strong_topics_json = db.Column(db.Text, nullable=True)
    daily_plan_json = db.Column(db.Text, nullable=True)          # ["30 min - ...", ...]
    weekly_plan_json = db.Column(db.Text, nullable=True)
    monthly_plan_json = db.Column(db.Text, nullable=True)
    ai_feedback = db.Column(db.Text, nullable=True)              # short natural-language summary

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempt = db.relationship("StudentAttempt", backref=db.backref("performance_analysis", uselist=False))
    student_account = db.relationship("StudentAccount", backref=db.backref(
        "performance_history", lazy="dynamic", order_by="PerformanceAnalysis.created_at"))

    def _load(self, field):
        import json
        raw = getattr(self, field)
        try:
            return json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []

    def subject_breakdown(self):
        return self._load("subject_breakdown_json")

    def weak_topics(self):
        return self._load("weak_topics_json")

    def strong_topics(self):
        return self._load("strong_topics_json")

    def daily_plan(self):
        return self._load("daily_plan_json")

    def weekly_plan(self):
        return self._load("weekly_plan_json")

    def monthly_plan(self):
        return self._load("monthly_plan_json")


# --------------------------------------------------------------------------
# Recently Viewed Courses (Phase 6) - powers "Recently Viewed" and the
# content-based "AI Recommendations" strip on the student home page.
# --------------------------------------------------------------------------
class CourseViewLog(db.Model):
    __tablename__ = "course_view_logs"

    id = db.Column(db.Integer, primary_key=True)
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)

    student_account = db.relationship("StudentAccount", backref=db.backref("course_views", lazy="dynamic"))
    course = db.relationship("Course")


def log_course_view(student_account_id, course_id):
    entry = CourseViewLog(student_account_id=student_account_id, course_id=course_id)
    db.session.add(entry)
    return entry


# --------------------------------------------------------------------------
# Certificates (Phase 5)
# --------------------------------------------------------------------------
class IssuedCertificate(db.Model):
    __tablename__ = "issued_certificates"

    id = db.Column(db.Integer, primary_key=True)
    certificate_number = db.Column(db.String(40), unique=True, nullable=False, default=lambda: f"KEH-{gen_token(10)}")
    student_account_id = db.Column(db.Integer, db.ForeignKey("student_accounts.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    completion_percent = db.Column(db.Float, default=0)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    pdf_path = db.Column(db.String(300), nullable=True)

    student_account = db.relationship("StudentAccount", backref=db.backref("certificates", lazy="dynamic"))
    course = db.relationship("Course")

    __table_args__ = (db.UniqueConstraint("student_account_id", "course_id", name="uq_certificate_student_course"),)


# --------------------------------------------------------------------------
# Homepage Slider (Phase 6): admin-editable hero banner slides shown on "/"
# --------------------------------------------------------------------------
class HomeSlide(db.Model):
    __tablename__ = "home_slides"

    id = db.Column(db.Integer, primary_key=True)
    badge_text = db.Column(db.String(60), nullable=True)        # small pill label, e.g. "NEW BATCH"
    title = db.Column(db.String(200), nullable=False)
    subtitle = db.Column(db.String(400), nullable=True)
    button_text = db.Column(db.String(60), nullable=True, default="Explore Now")
    button_link = db.Column(db.String(300), nullable=True)      # internal path or full URL
    image_filename = db.Column(db.String(300), nullable=False)  # relative to static/images/slides/
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def resolved_link(self):
        """Returns a safe href for the slide's CTA button, falling back to '#'."""
        return self.button_link or "#"
