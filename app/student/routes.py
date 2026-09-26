import os
import re
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, current_app, send_from_directory, abort, jsonify,
)
from werkzeug.utils import secure_filename
from sqlalchemy.exc import IntegrityError

from app.extensions import db, limiter
from app.models import (
    StudentAccount, StudentAttempt, Student, log_activity,
    Course, CoursePurchase, Notification, notify_student, CourseMaterial,
    MaterialSubject, MaterialAccessLog, log_material_access, PerformanceAnalysis,
    Exam, ExamLink, CourseViewLog, log_course_view, IssuedCertificate,
    SubscriptionPlan, Coupon, log_payment_transaction,
)
from app.services.device_service import get_client_ip
from app.services.email_service import send_otp_email
from app.services.payment_service import build_upi_qr, generate_invoice_number, activate_paid_purchase
from app.services import razorpay_service
from app.services import stripe_service
from app.services import coupon_service
from app.services import pricing_service
from app.utils import create_material_token, decode_material_token

student_bp = Blueprint("student", __name__, url_prefix="/student", template_folder="../templates")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s]{7,15}$")
ALLOWED_PHOTO_EXT = {"png", "jpg", "jpeg", "webp"}


# --------------------------------------------------------------------------
# Session-based student auth helpers (independent of the admin Flask-Login
# setup in app/extensions.py, so the existing admin auth is left untouched).
# --------------------------------------------------------------------------
def _login_student(account):
    session["student_id"] = account.id
    session.permanent = True


def _logout_student():
    session.pop("student_id", None)


def current_student():
    sid = session.get("student_id")
    if not sid:
        return None
    return StudentAccount.query.get(sid)


def student_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_student()
        if not account or not account.is_active:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("student.login", next=request.path))
        return view(account, *args, **kwargs)
    return wrapped


@student_bp.context_processor
def inject_current_student():
    return {"current_student": current_student()}


# --------------------------------------------------------------------------
# File upload helpers
# --------------------------------------------------------------------------
def _photo_upload_dir():
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], "student_photos")
    os.makedirs(path, exist_ok=True)
    return path


def _save_photo(file_storage):
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_PHOTO_EXT:
        return None, "Photo must be a PNG, JPG, or WEBP image."
    new_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(_photo_upload_dir(), new_name))
    return new_name, None


@student_bp.route("/media/photo/<path:filename>")
def photo(filename):
    # Serve stored student photos from the (non-static) instance uploads folder.
    return send_from_directory(_photo_upload_dir(), filename)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
@student_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def register():
    if current_student():
        return redirect(url_for("student.home"))

    error = None
    form = request.form

    if request.method == "POST":
        full_name = form.get("full_name", "").strip()
        college_name = form.get("college_name", "").strip()
        address = form.get("address", "").strip()
        email = form.get("email", "").strip().lower()
        phone = form.get("phone", "").strip()
        password = form.get("password", "")
        confirm_password = form.get("confirm_password", "")
        photo_file = request.files.get("photo")

        if not all([full_name, college_name, address, email, phone, password, confirm_password]):
            error = "Please fill in all required fields."
        elif not EMAIL_RE.match(email):
            error = "Please enter a valid email address."
        elif not PHONE_RE.match(phone):
            error = "Please enter a valid mobile number."
        elif len(password) < 8:
            error = "Password must be at least 8 characters long."
        elif password != confirm_password:
            error = "Passwords do not match."
        elif not photo_file or not photo_file.filename:
            error = "Please upload a student photo."
        elif StudentAccount.query.filter_by(email=email).first():
            error = "An account with this email already exists."
        elif StudentAccount.query.filter_by(phone=phone).first():
            error = "An account with this mobile number already exists."

        photo_name = None
        if not error:
            photo_name, photo_err = _save_photo(photo_file)
            if photo_err:
                error = photo_err

        if not error:
            account = StudentAccount(
                full_name=full_name, college_name=college_name, address=address,
                email=email, phone=phone, photo_path=photo_name,
            )
            account.set_password(password)
            db.session.add(account)
            try:
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                error = "An account with this email or mobile number already exists."
                return render_template("student/register.html", error=error, form=form)

            otp_code = account.generate_otp(purpose="verify")
            db.session.commit()

            log_activity(f"student_account:{account.id}", "student_registered",
                          ip_address=get_client_ip(request))
            db.session.commit()

            send_otp_email(account, otp_code, purpose="verify")

            _login_student(account)
            flash("Registration successful! We've emailed you a verification code.", "success")
            return redirect(url_for("student.verify_otp"))

    return render_template("student/register.html", error=error, form=form)


# --------------------------------------------------------------------------
# Email OTP verification
# --------------------------------------------------------------------------
@student_bp.route("/verify-otp", methods=["GET", "POST"])
@student_login_required
def verify_otp(account):
    if account.is_verified:
        return redirect(url_for("student.home"))

    error = None
    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        if account.check_otp(code, purpose="verify"):
            account.is_verified = True
            account.clear_otp()
            db.session.commit()
            flash("Email verified successfully!", "success")
            return redirect(url_for("student.home"))
        db.session.commit()  # persist incremented attempt count
        error = "Invalid or expired code. Please try again."

    return render_template("student/verify_otp.html", error=error, account=account)


@student_bp.route("/resend-otp", methods=["POST"])
@student_login_required
@limiter.limit("5 per hour")
def resend_otp(account):
    if not account.is_verified:
        otp_code = account.generate_otp(purpose="verify")
        db.session.commit()
        send_otp_email(account, otp_code, purpose="verify")
        flash("A new verification code has been sent to your email.", "info")
    return redirect(url_for("student.verify_otp"))


# --------------------------------------------------------------------------
# Login / logout
# --------------------------------------------------------------------------
@student_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def login():
    if current_student():
        return redirect(url_for("student.home"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        account = StudentAccount.query.filter_by(email=email).first()

        if account and account.check_password(password) and account.is_active:
            _login_student(account)
            log_activity(f"student_account:{account.id}", "student_login",
                          ip_address=get_client_ip(request))
            db.session.commit()
            next_url = request.args.get("next")
            return redirect(next_url or url_for("student.home"))

        error = "Invalid email or password."

    return render_template("student/login.html", error=error)


@student_bp.route("/logout")
@student_login_required
def logout(account):
    log_activity(f"student_account:{account.id}", "student_logout", ip_address=get_client_ip(request))
    db.session.commit()
    _logout_student()
    flash("You have been logged out.", "info")
    return redirect(url_for("student.login"))


# --------------------------------------------------------------------------
# Home / dashboard
# --------------------------------------------------------------------------
def _keyword_set(text):
    if not text:
        return set()
    return {w.strip().lower() for w in text.replace(",", " ").split() if len(w.strip()) > 2}


def _recommend_courses(account, limit=4):
    """Lightweight content-based recommender: score active, not-yet-purchased
    courses by keyword overlap with the student's purchased/viewed courses'
    `subjects` field, falling back to overall popularity for new students
    with no signal yet. No ML model - fully explainable from the data."""
    purchased_ids = {p.course_id for p in account.purchases}
    signal_courses = (
        Course.query.filter(Course.id.in_(purchased_ids)).all() if purchased_ids else []
    )
    recent_view_ids = [
        v.course_id for v in
        CourseViewLog.query.filter_by(student_account_id=account.id)
        .order_by(CourseViewLog.viewed_at.desc()).limit(10).all()
    ]
    if recent_view_ids:
        signal_courses += Course.query.filter(Course.id.in_(recent_view_ids)).all()

    keywords = set()
    for c in signal_courses:
        keywords |= _keyword_set(c.subjects)

    candidates = Course.query.filter_by(is_active=True).filter(~Course.id.in_(purchased_ids)).all()

    def popularity(c):
        return c.purchases.filter_by(status="paid").count()

    if keywords:
        scored = [(len(keywords & _keyword_set(c.subjects)), popularity(c), c) for c in candidates]
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        result = [c for score, _, c in scored if score > 0][:limit]
        if len(result) < limit:
            fallback = [c for c in sorted(candidates, key=popularity, reverse=True) if c not in result]
            result += fallback[: limit - len(result)]
        return result

    return sorted(candidates, key=popularity, reverse=True)[:limit]


@student_bp.route("/home")
@student_login_required
def home(account):
    # Loosely match this account's past exam-link attempts by email, since
    # the anonymous exam-attempt flow (Student/StudentAttempt) is separate
    # and untouched.
    attempts = (
        StudentAttempt.query.join(Student)
        .filter(Student.email == account.email, StudentAttempt.status.in_(["submitted", "auto_submitted"]))
        .order_by(StudentAttempt.submitted_at.desc())
        .all()
    )
    results = [a.result for a in attempts if a.result]

    exam_count = len(results)
    avg_score = round(sum(r.percentage for r in results) / exam_count, 2) if exam_count else 0
    best_score = round(max((r.percentage for r in results), default=0), 2)
    recent_results = list(zip(attempts, results))[:5]

    my_rank = None
    if exam_count:
        board = _leaderboard_rows(period="overall", limit=1000)
        my_rank = next((r["rank"] for r in board if r["email"] == account.email), None)

    # AI Performance Analysis - shown right on the dashboard, so a student
    # sees "what should I study next" the moment they land here, not only
    # on the separate Analytics page.
    latest_analysis = (
        PerformanceAnalysis.query.filter_by(student_account_id=account.id)
        .order_by(PerformanceAnalysis.created_at.desc()).first()
    )

    # Phase 6: Recently Viewed, AI Recommendations, Popular & New Courses.
    purchased_ids = {p.course_id for p in account.purchases}
    recent_view_course_ids = []
    for v in (CourseViewLog.query.filter_by(student_account_id=account.id)
              .order_by(CourseViewLog.viewed_at.desc()).limit(20).all()):
        if v.course_id not in recent_view_course_ids:
            recent_view_course_ids.append(v.course_id)
    recently_viewed = []
    if recent_view_course_ids:
        by_id = {c.id: c for c in Course.query.filter(Course.id.in_(recent_view_course_ids)).all()}
        recently_viewed = [by_id[cid] for cid in recent_view_course_ids[:5] if cid in by_id]

    recommended_courses = _recommend_courses(account, limit=4)
    popular_courses = (
        Course.query.filter_by(is_active=True).filter(~Course.id.in_(purchased_ids))
        .order_by(Course.created_at.desc()).limit(8).all()
    )
    popular_courses = sorted(popular_courses, key=lambda c: c.purchases.filter_by(status="paid").count(), reverse=True)[:4]
    new_courses = (
        Course.query.filter_by(is_active=True).filter(~Course.id.in_(purchased_ids))
        .order_by(Course.created_at.desc()).limit(4).all()
    )

    # Phase 2 - "My Active Plan" widget (spec #4): every currently-active
    # subscription this student holds, with plan/course/dates/days-left.
    my_active_plans = (
        CoursePurchase.query.filter_by(student_account_id=account.id, status="paid")
        .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
        .order_by(CoursePurchase.expires_at.asc())
        .all()
    )

    return render_template(
        "student/home.html", account=account,
        exam_count=exam_count, avg_score=avg_score, best_score=best_score,
        recent_results=recent_results, now=datetime.utcnow(), my_rank=my_rank,
        recently_viewed=recently_viewed, recommended_courses=recommended_courses,
        popular_courses=popular_courses, new_courses=new_courses,
        latest_analysis=latest_analysis, my_active_plans=my_active_plans,
    )


# --------------------------------------------------------------------------
# WhatsApp Support (Module 15) - student side. See services/
# whatsapp_bot_service.py for the identification/linking design.
# --------------------------------------------------------------------------
@student_bp.route("/whatsapp-support")
@student_login_required
@limiter.limit("20 per hour")
def whatsapp_support(account):
    """Creates (or reuses) a support conversation pre-linked to this
    logged-in student, then bounces the browser straight to a wa.me deep
    link with a prefilled message - opens the student's own WhatsApp app/
    web client with the official Kalyani Exam Hub number already filled
    in. Never exposes account.id or any other internal identifier - only
    the student's name and the short, random support_reference (see
    generate_support_reference() in models.py)."""
    from app.models import SiteSetting
    from app.services import whatsapp_bot_service
    from urllib.parse import quote

    official_number = SiteSetting.get("whatsapp_number", "").strip()
    if not official_number:
        flash("WhatsApp support isn't configured yet. Please use another support option, or contact us by email.", "warning")
        return redirect(url_for("student.home"))

    convo = whatsapp_bot_service.start_support_conversation(account)
    db.session.commit()

    message = (
        f"Hi Kalyani Exam Hub Management,\n"
        f"I am a student and I need support.\n\n"
        f"Name: {account.full_name}\n"
        f"Support Reference: {convo.support_reference}"
    )
    wa_url = f"https://wa.me/{official_number}?text={quote(message)}"
    return redirect(wa_url)


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
@student_bp.route("/profile/edit", methods=["GET", "POST"])
@student_login_required
def edit_profile(account):
    error = None
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        college_name = request.form.get("college_name", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        photo_file = request.files.get("photo")

        if not all([full_name, college_name, address, phone]):
            error = "Please fill in all required fields."
        elif not PHONE_RE.match(phone):
            error = "Please enter a valid mobile number."
        else:
            existing = StudentAccount.query.filter(
                StudentAccount.phone == phone, StudentAccount.id != account.id
            ).first()
            if existing:
                error = "Another account already uses this mobile number."

        new_photo_name = None
        if not error and photo_file and photo_file.filename:
            new_photo_name, photo_err = _save_photo(photo_file)
            if photo_err:
                error = photo_err

        if not error:
            account.full_name = full_name
            account.college_name = college_name
            account.address = address
            account.phone = phone
            if new_photo_name:
                account.photo_path = new_photo_name
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("student.home"))

    return render_template("student/edit_profile.html", account=account, error=error)


@student_bp.route("/change-password", methods=["GET", "POST"])
@student_login_required
def change_password(account):
    error = None
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not account.check_password(current_pw):
            error = "Current password is incorrect."
        elif len(new_pw) < 8:
            error = "New password must be at least 8 characters long."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."

        if not error:
            account.set_password(new_pw)
            db.session.commit()
            log_activity(f"student_account:{account.id}", "student_password_changed")
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("student.home"))

    return render_template("student/change_password.html", error=error)


# --------------------------------------------------------------------------
# Forgot / reset password (email OTP)
# --------------------------------------------------------------------------
@student_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        account = StudentAccount.query.filter_by(email=email).first()
        if account:
            otp_code = account.generate_otp(purpose="reset")
            db.session.commit()
            send_otp_email(account, otp_code, purpose="reset")
        # Always show the same message, regardless of whether the email
        # exists, to avoid leaking which emails are registered.
        session["reset_email"] = email
        flash("If that email is registered, a reset code has been sent.", "info")
        return redirect(url_for("student.reset_password"))

    return render_template("student/forgot_password.html")


@student_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password():
    error = None
    email = session.get("reset_email", "")

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("otp", "").strip()
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        account = StudentAccount.query.filter_by(email=email).first()

        if len(new_pw) < 8:
            error = "Password must be at least 8 characters long."
        elif new_pw != confirm_pw:
            error = "Passwords do not match."
        elif not account or not account.check_otp(code, purpose="reset"):
            if account:
                db.session.commit()
            error = "Invalid or expired reset code."

        if not error:
            account.set_password(new_pw)
            account.clear_otp()
            db.session.commit()
            log_activity(f"student_account:{account.id}", "student_password_reset")
            db.session.commit()
            session.pop("reset_email", None)
            flash("Password reset successfully. Please log in.", "success")
            return redirect(url_for("student.login"))

    return render_template("student/reset_password.html", error=error, email=email)


# --------------------------------------------------------------------------
# Courses (Module 9)
# --------------------------------------------------------------------------
@student_bp.route("/courses")
@student_login_required
def courses(account):
    course_list = Course.query.filter_by(is_active=True).order_by(Course.created_at.desc()).all()
    active_course_ids = {p.course_id for p in account.purchases if p.is_active_subscription()}
    return render_template("student/courses.html", courses=course_list, active_course_ids=active_course_ids)


@student_bp.route("/tests")
@student_login_required
def tests(account):
    """Phase 2 - Demo / Mock / Main Tests listing (spec #4-#7), grouped by
    Exam.test_type. Uses the same server-side can_access_exam() gate the
    exam-taking flow itself enforces, so what's shown here always matches
    what the student can actually open - Demo is always unlocked, Mock/
    Main show a Locked badge (with the reason) when there's no eligible
    active plan, the schedule window is closed, or attempts are used up.
    """
    from app.services.access_service import can_access_exam, ACCESS_DENIAL_MESSAGES

    candidates = (
        Exam.query.filter(Exam.is_active.is_(True))
        .order_by(Exam.created_at.desc())
        .all()
    )

    grouped = {"demo": [], "mock": [], "main": []}
    for e in candidates:
        link = e.links.filter_by(is_active=True).first()
        if not link or not link.is_usable():
            continue
        allowed, reason = can_access_exam(e, account)
        already_attempted = (
            StudentAttempt.query.join(Student)
            .filter(Student.email == account.email, StudentAttempt.exam_id == e.id,
                    StudentAttempt.status.in_(["submitted", "auto_submitted"]))
            .first()
        )
        grouped.setdefault(e.test_type or "mock", []).append({
            "exam": e, "link": link, "allowed": allowed,
            "locked_reason": ACCESS_DENIAL_MESSAGES.get(reason) if not allowed else None,
            "already_attempted": bool(already_attempted),
            "can_retake": e.allow_retake or not already_attempted,
        })

    return render_template("student/tests.html", grouped=grouped,
                            base_url=current_app.config["APP_BASE_URL"])


@student_bp.route("/mock-tests")
@student_login_required
def mock_tests(account):
    """Phase 5: Daily / Weekly / Monthly Grand Test listing, driven by
    Exam.test_category. A test scoped to a course (exam.course_id set)
    only shows to students with an active subscription to that course;
    a test left unscoped (course_id is None) shows to everyone."""
    active_course_ids = {p.course_id for p in account.purchases if p.grants_exam()}
    # candidates = (
    #     Exam.query.filter(Exam.test_category.isnot(None), Exam.is_active.is_(True))
    #     .order_by(Exam.scheduled_at.asc().nullslast(), Exam.created_at.desc())
    #     .all()
    # )
    active_course_ids = {
        p.course_id
        for p in account.purchases
        if p.grants_exam()
    }

    candidates = (
        Exam.query.filter(
            Exam.test_category.isnot(None),
            Exam.is_active.is_(True)
        )
        .order_by(
            Exam.scheduled_at.is_(None),
            Exam.scheduled_at.asc(),
            Exam.created_at.desc()
        )
        .all()
    )
    visible = [e for e in candidates if not e.course_id or e.course_id in active_course_ids]

    grouped = {"daily": [], "weekly": [], "monthly": [], "grand": []}
    for e in visible:
        link = e.links.filter_by(is_active=True).first()
        if not link or not link.is_usable():
            continue
        already_attempted = (
            StudentAttempt.query.join(Student)
            .filter(Student.email == account.email, StudentAttempt.exam_id == e.id,
                    StudentAttempt.status.in_(["submitted", "auto_submitted"]))
            .first()
        )
        grouped.setdefault(e.test_category, []).append({
            "exam": e, "link": link,
            "can_retake": e.allow_retake or not already_attempted,
            "already_attempted": bool(already_attempted),
        })

    return render_template("student/mock_tests.html", grouped=grouped,
                            base_url=current_app.config["APP_BASE_URL"])


@student_bp.route("/courses/<int:course_id>")
@student_login_required
def course_detail(account, course_id):
    course = Course.query.filter_by(id=course_id, is_active=True).first_or_404()

    # One card per tier the course actually offers, each with its own
    # independent active / expired purchase status.
    tier_status = {}
    for tier in course.available_tiers():
        active = (
            CoursePurchase.query.filter_by(
                student_account_id=account.id, course_id=course.id, status="paid", tier=tier
            )
            .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
            .first()
        )
        expired = None
        if not active:
            expired = (
                CoursePurchase.query.filter_by(
                    student_account_id=account.id, course_id=course.id, status="paid", tier=tier
                )
                .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at <= datetime.utcnow())
                .order_by(CoursePurchase.expires_at.desc()).first()
            )
        tier_status[tier] = {"active": active, "expired": expired}

    subscription_plans = SubscriptionPlan.query.filter_by(course_id=course.id, is_active=True).order_by(
        SubscriptionPlan.duration_value).all()
    plan_status = {}
    for plan in subscription_plans:
        active = (
            CoursePurchase.query.filter_by(student_account_id=account.id, plan_id=plan.id, status="paid")
            .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
            .first()
        )
        if active:
            plan_status[plan.id] = active

    log_course_view(account.id, course.id)
    db.session.commit()
    return render_template(
        "student/course_detail.html", course=course, tier_status=tier_status,
        subscription_plans=subscription_plans, plan_status=plan_status,
    )


# NOTE (Phase 1 cleanup): the "watch" route that used to serve the full/paid
# course video has been removed - video/live-class functionality is
# discontinued. course.course_video_url / demo_video_url are left in the
# database untouched but are no longer read or rendered anywhere.


@student_bp.route("/plans/<int:plan_id>/buy", methods=["POST"])
@student_login_required
def plan_buy(account, plan_id):
    """Phase 2 - buy a configurable SubscriptionPlan (3/6/12 months, or any
    other admin-created plan) for its course. Reuses the exact same
    CoursePurchase row/checkout/Razorpay flow as course_buy() above -
    the only difference is plan_id gets set, so grants_* and expiry use
    the plan's own duration/include-flags instead of the tier table."""
    plan = SubscriptionPlan.query.filter_by(id=plan_id, is_active=True).first_or_404()
    course = plan.course

    already_active = (
        CoursePurchase.query.filter_by(
            student_account_id=account.id, plan_id=plan.id, status="paid",
        )
        .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
        .first()
    )
    if already_active:
        flash("You already have this plan active on your account.", "info")
        return redirect(url_for("student.my_courses"))

    purchase = CoursePurchase.query.filter_by(
        student_account_id=account.id, plan_id=plan.id, status="pending",
    ).first()
    if not purchase:
        purchase = CoursePurchase(
            student_account_id=account.id, course_id=course.id, plan_id=plan.id,
            tier="combined",
        )
        pricing_service.apply_pricing(purchase, base_amount=plan.price, discount_amount=0)
        db.session.add(purchase)
        db.session.commit()

    return redirect(url_for("student.checkout", purchase_id=purchase.id))


@student_bp.route("/courses/<int:course_id>/buy", methods=["POST"])
@student_login_required
def course_buy(account, course_id):
    course = Course.query.filter_by(id=course_id, is_active=True).first_or_404()
    tier = request.form.get("tier", "combined")
    if tier not in course.available_tiers():
        # available_tiers() already excludes discontinued video tiers
        # (Course.VIDEO_TIERS) and anything priced at 0 for this course.
        flash("That plan isn't available for this course.", "warning")
        return redirect(url_for("student.course_detail", course_id=course.id))

    # Requirement 9 - prevent duplicate payments: if this tier is already
    # active (paid and not yet expired), don't let the student pay again.
    already_active = (
        CoursePurchase.query.filter_by(
            student_account_id=account.id, course_id=course.id, tier=tier, status="paid"
        )
        .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
        .first()
    )
    if already_active:
        flash("You already have an active purchase for this plan.", "info")
        return redirect(url_for("student.my_courses"))

    # Reuse an existing pending purchase for this course+tier instead of piling up duplicates.
    purchase = CoursePurchase.query.filter_by(
        student_account_id=account.id, course_id=course.id, tier=tier, status="pending"
    ).first()
    if not purchase:
        purchase = CoursePurchase(
            student_account_id=account.id, course_id=course.id, tier=tier,
        )
        pricing_service.apply_pricing(purchase, base_amount=course.tier_final_price(tier), discount_amount=0)
        db.session.add(purchase)
        db.session.commit()

    return redirect(url_for("student.checkout", purchase_id=purchase.id))


@student_bp.route("/checkout/<int:purchase_id>", methods=["GET", "POST"])
@student_login_required
def checkout(account, purchase_id):
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()

    if purchase.status == "paid":
        flash("This plan is already active on your account.", "info")
        return redirect(url_for("student.my_courses"))
    if purchase.status == "refunded":
        flash("This purchase was refunded and access was removed. Click Buy Now to start a new purchase.", "info")
        return redirect(url_for("student.course_detail", course_id=purchase.course_id))
    if purchase.status in ("failed", "cancelled"):
        flash("This payment attempt was not approved. Please click Buy Now to start a new one.", "warning")
        return redirect(url_for("student.course_detail", course_id=purchase.course_id))

    error = None
    if request.method == "POST":
        txn_id = request.form.get("txn_id", "").strip()
        if not txn_id:
            error = "Please enter the UPI reference / UTR number shown in your payment app after paying."
        else:
            purchase.student_txn_id = txn_id
            purchase.payment_method = "UPI"
            db.session.commit()
            flash("Thanks! We'll verify your payment and activate the course shortly.", "success")
            return redirect(url_for("student.my_courses"))

    upi_uri, qr_data_uri = build_upi_qr(
        current_app.config["UPI_PAYEE_VPA"], current_app.config["UPI_PAYEE_NAME"],
        purchase.amount, note=f"Course:{purchase.course_id}:{purchase.reference_code}",
    )
    return render_template(
        "student/checkout.html", purchase=purchase, qr_data_uri=qr_data_uri, upi_uri=upi_uri, error=error,
        razorpay_enabled=razorpay_service.is_configured(),
        razorpay_key_id=current_app.config.get("RAZORPAY_KEY_ID", ""),
        stripe_enabled=stripe_service.is_configured(),
        stripe_currency=current_app.config.get("STRIPE_CURRENCY", "usd"),
    )


@student_bp.route("/checkout/<int:purchase_id>/apply-coupon", methods=["POST"])
@student_login_required
def apply_coupon(account, purchase_id):
    """Server-side coupon pricing - see coupon_service.validate_and_price().
    Never trusts a discount amount from the client, only the code string."""
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status != "pending":
        flash("This payment is no longer pending.", "warning")
        return redirect(url_for("student.checkout", purchase_id=purchase.id))

    base_amount = purchase.original_amount or purchase.amount
    code = request.form.get("coupon_code", "")
    coupon, discount, final_amount, error = coupon_service.validate_and_price(
        code, account, purchase.course, base_amount
    )
    if error:
        flash(error, "warning")
    else:
        purchase.coupon_id = coupon.id
        pricing_service.apply_pricing(purchase, base_amount=base_amount, discount_amount=discount)
        db.session.commit()
        flash(f"Coupon applied — you saved Rs.{discount:.0f}.", "success")
    return redirect(url_for("student.checkout", purchase_id=purchase.id))


@student_bp.route("/checkout/<int:purchase_id>/remove-coupon", methods=["POST"])
@student_login_required
def remove_coupon(account, purchase_id):
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status != "pending":
        return redirect(url_for("student.checkout", purchase_id=purchase.id))
    base_amount = purchase.original_amount or purchase.amount
    purchase.coupon_id = None
    pricing_service.apply_pricing(purchase, base_amount=base_amount, discount_amount=0)
    db.session.commit()
    flash("Coupon removed.", "info")
    return redirect(url_for("student.checkout", purchase_id=purchase.id))


# --------------------------------------------------------------------------
# Razorpay gateway (Module 10b) - automatic, webhook-free confirmation via
# Razorpay Checkout.js + server-side signature verification. Runs alongside
# the existing UPI-QR + manual-verification flow above, doesn't replace it.
# --------------------------------------------------------------------------
@student_bp.route("/checkout/<int:purchase_id>/razorpay/order", methods=["POST"])
@student_login_required
def razorpay_create_order(account, purchase_id):
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status != "pending":
        return jsonify({"error": "This payment is no longer pending."}), 400
    if not razorpay_service.is_configured():
        return jsonify({"error": "Razorpay isn't configured on this server."}), 400

    try:
        order = razorpay_service.create_order(
            purchase.amount, receipt=purchase.reference_code,
            notes={"course_id": purchase.course_id, "tier": purchase.tier, "student_account_id": account.id},
        )
    except Exception:
        current_app.logger.exception("Razorpay order creation failed for purchase %s", purchase.id)
        return jsonify({"error": "Couldn't reach Razorpay right now. Please try the UPI QR option below, "
                                  "or try again in a moment."}), 502
    purchase.razorpay_order_id = order["id"]
    log_payment_transaction(purchase, gateway="razorpay", status="created", order_id=order["id"])
    db.session.commit()
    return jsonify({
        "order_id": order["id"], "amount": order["amount"], "currency": order["currency"],
        "key_id": current_app.config["RAZORPAY_KEY_ID"],
        "name": current_app.config.get("UPI_PAYEE_NAME", "Exam Portal"),
        "description": f"{purchase.course.title} - {purchase.tier_label()}",
        "prefill_name": account.full_name, "prefill_email": account.email, "prefill_contact": account.phone,
    })


# --------------------------------------------------------------------------
# Stripe gateway (Module 10c) - Checkout Session redirect, for students
# paying from outside India who can't use UPI/Razorpay (India-only rails).
# Confirmation happens via the webhook below, not the redirect itself -
# same "never trust the browser" rule as Razorpay: a student closing the
# tab right after paying must not be the only way the purchase gets marked
# paid.
# --------------------------------------------------------------------------
@student_bp.route("/checkout/<int:purchase_id>/stripe/start", methods=["POST"])
@student_login_required
def stripe_start(account, purchase_id):
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status != "pending":
        flash("This payment is no longer pending.", "warning")
        return redirect(url_for("student.checkout", purchase_id=purchase.id))
    if not stripe_service.is_configured():
        flash("Card payment isn't configured on this server.", "warning")
        return redirect(url_for("student.checkout", purchase_id=purchase.id))

    try:
        session = stripe_service.create_checkout_session(
            purchase,
            success_url=url_for("student.stripe_return", purchase_id=purchase.id, _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("student.checkout", purchase_id=purchase.id, _external=True),
        )
    except Exception:
        current_app.logger.exception("Stripe checkout session creation failed for purchase %s", purchase.id)
        flash("Couldn't reach Stripe right now. Please try again in a moment, or use Razorpay/UPI instead.", "danger")
        return redirect(url_for("student.checkout", purchase_id=purchase.id))

    log_payment_transaction(purchase, gateway="stripe", status="created", order_id=session.id)
    db.session.commit()
    return redirect(session.url)


@student_bp.route("/checkout/<int:purchase_id>/stripe/return")
@student_login_required
def stripe_return(account, purchase_id):
    """Landing page after Stripe Checkout closes. This does NOT activate
    the purchase - that only ever happens in the webhook below, so a
    student closing the tab before this page loads doesn't leave a paid
    order stuck as pending, and this page can't be used to fake a payment
    by simply visiting the URL."""
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    flash("Payment received - it's being confirmed and will unlock shortly." if purchase.status == "pending"
          else "Payment confirmed!", "info" if purchase.status == "pending" else "success")
    return redirect(url_for("student.my_courses"))


@student_bp.route("/checkout/<int:purchase_id>/razorpay/failed", methods=["POST"])
@student_login_required
def razorpay_payment_failed(account, purchase_id):
    """Called from Checkout.js's `payment.failed` event (Requirement 5/6) -
    records that a payment attempt failed so it shows correctly in Payment
    History / the admin Payments list, instead of just sitting silently as
    'pending' forever. Never activates anything."""
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status == "pending":
        purchase.status = "failed"
        purchase.razorpay_order_id = request.form.get("razorpay_order_id") or purchase.razorpay_order_id
        log_payment_transaction(
            purchase, gateway="razorpay", status="failed",
            order_id=purchase.razorpay_order_id, failure_reason="Payment failed in Razorpay checkout widget",
        )
        notify_student(
            account.id, "Payment Failed",
            f"Your payment attempt for '{purchase.course.title}' didn't go through. You can retry from My Courses.",
            type="payment_failed",
        )
        db.session.commit()
    return jsonify({"ok": True})


@student_bp.route("/checkout/<int:purchase_id>/razorpay/verify", methods=["POST"])
@student_login_required
def razorpay_verify(account, purchase_id):
    purchase = CoursePurchase.query.filter_by(id=purchase_id, student_account_id=account.id).first_or_404()
    if purchase.status != "pending":
        return jsonify({"error": "This payment is no longer pending."}), 400

    order_id = request.form.get("razorpay_order_id", "")
    payment_id = request.form.get("razorpay_payment_id", "")
    signature = request.form.get("razorpay_signature", "")

    if not (order_id and payment_id and signature) or order_id != purchase.razorpay_order_id:
        return jsonify({"error": "Payment details missing or don't match this order."}), 400

    try:
        signature_ok = razorpay_service.verify_payment_signature(order_id, payment_id, signature)
    except Exception:
        current_app.logger.exception("Razorpay signature verification errored for purchase %s", purchase.id)
        return jsonify({"error": "Couldn't verify the payment right now. If money was deducted, "
                                  "it will be confirmed automatically shortly, or contact support."}), 502

    if not signature_ok:
        purchase.status = "failed"
        log_payment_transaction(
            purchase, gateway="razorpay", status="failed",
            order_id=order_id, payment_id=payment_id, failure_reason="Signature verification failed",
        )
        notify_student(
            account.id, "Payment Failed",
            f"We couldn't verify your payment for '{purchase.course.title}'. If money was deducted, "
            f"it will be resolved automatically, or contact support.",
            type="payment_failed",
        )
        db.session.commit()
        return jsonify({"error": "Payment signature verification failed."}), 400

    # Signature verified -> genuinely paid. Activate immediately, no manual admin step.
    # (Idempotent: if the webhook already activated this purchase a moment
    # earlier, activate_paid_purchase() is a safe no-op here.)
    activate_paid_purchase(
        purchase, payment_id, signature=signature, order_id=order_id, method="Razorpay",
    )
    db.session.commit()
    return jsonify({"redirect_url": url_for("student.my_courses")})


@student_bp.route("/my-courses")
@student_login_required
def my_courses(account):
    purchases = CoursePurchase.query.filter_by(student_account_id=account.id).order_by(
        CoursePurchase.created_at.desc()
    ).all()
    return render_template("student/my_courses.html", purchases=purchases, now=datetime.utcnow())


@student_bp.route("/payment-history")
@student_login_required
def payment_history(account):
    """Requirement 7 - full transaction history: every payment attempt
    (paid / pending / failed / cancelled) for this student, with the
    Razorpay order/payment IDs and payment time, independent of whether
    the course access itself is still active."""
    purchases = CoursePurchase.query.filter_by(student_account_id=account.id).order_by(
        CoursePurchase.created_at.desc()
    ).all()
    return render_template("student/payment_history.html", purchases=purchases)


@student_bp.route("/invoice/<int:purchase_id>")
@student_login_required
def invoice(account, purchase_id):
    from flask import send_file
    import tempfile
    from app.services.pdf_service import generate_invoice_pdf

    purchase = CoursePurchase.query.filter_by(
        id=purchase_id, student_account_id=account.id, status="paid"
    ).first_or_404()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    generate_invoice_pdf(purchase, path)
    return send_file(path, as_attachment=True, download_name=f"invoice_{purchase.invoice_number}.pdf")


# --------------------------------------------------------------------------
# Certificates (Phase 5)
# --------------------------------------------------------------------------
CERTIFICATE_ELIGIBILITY_PERCENT = 50  # % of a course's materials viewed to unlock a certificate


def _completion_percent(account, course):
    total_materials = CourseMaterial.query.filter_by(course_id=course.id).count()
    if not total_materials:
        return 0.0
    viewed = (
        db.session.query(MaterialAccessLog.material_id)
        .join(CourseMaterial, CourseMaterial.id == MaterialAccessLog.material_id)
        .filter(MaterialAccessLog.student_account_id == account.id, CourseMaterial.course_id == course.id)
        .distinct().count()
    )
    return round(min(100.0, (viewed / total_materials) * 100), 1)


@student_bp.route("/certificates")
@student_login_required
def certificates(account):
    purchases = (
        CoursePurchase.query.filter_by(student_account_id=account.id, status="paid")
        .order_by(CoursePurchase.created_at.desc()).all()
    )
    issued = {c.course_id: c for c in IssuedCertificate.query.filter_by(student_account_id=account.id).all()}

    rows = []
    seen_courses = set()
    for p in purchases:
        if p.course_id in seen_courses:
            continue
        seen_courses.add(p.course_id)
        pct = _completion_percent(account, p.course)
        rows.append({
            "course": p.course,
            "completion_percent": pct,
            "eligible": pct >= CERTIFICATE_ELIGIBILITY_PERCENT,
            "certificate": issued.get(p.course_id),
        })

    return render_template("student/certificates.html", rows=rows,
                            eligibility_threshold=CERTIFICATE_ELIGIBILITY_PERCENT)


@student_bp.route("/certificates/<int:course_id>/generate", methods=["POST"])
@student_login_required
def certificate_generate(account, course_id):
    course = Course.query.get_or_404(course_id)
    has_purchase = CoursePurchase.query.filter_by(
        student_account_id=account.id, course_id=course.id, status="paid"
    ).first()
    if not has_purchase:
        flash("You need to have purchased this course to get a certificate.", "warning")
        return redirect(url_for("student.certificates"))

    pct = _completion_percent(account, course)
    if pct < CERTIFICATE_ELIGIBILITY_PERCENT:
        flash(f"Keep going! You've covered {pct}% of the material - reach {CERTIFICATE_ELIGIBILITY_PERCENT}% to unlock your certificate.", "info")
        return redirect(url_for("student.certificates"))

    cert = IssuedCertificate.query.filter_by(student_account_id=account.id, course_id=course.id).first()
    if not cert:
        cert = IssuedCertificate(student_account_id=account.id, course_id=course.id, completion_percent=pct)
        db.session.add(cert)
        log_activity(account.email, "certificate_issued", meta=f"course={course.id}", ip_address=get_client_ip(request))
        db.session.commit()
        flash("Certificate generated! You can download it now.", "success")
    return redirect(url_for("student.certificates"))


@student_bp.route("/certificates/<int:certificate_id>/download")
@student_login_required
def certificate_download(account, certificate_id):
    from flask import send_file
    import tempfile
    from app.services.pdf_service import generate_certificate_pdf

    cert = IssuedCertificate.query.filter_by(id=certificate_id, student_account_id=account.id).first_or_404()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    generate_certificate_pdf(cert, path)
    return send_file(path, as_attachment=True, download_name=f"certificate_{cert.certificate_number}.pdf")


# --------------------------------------------------------------------------
# Notifications (Module 14)
# --------------------------------------------------------------------------
@student_bp.route("/notifications")
@student_login_required
def notifications(account):
    items = Notification.query.filter_by(student_account_id=account.id).order_by(
        Notification.created_at.desc()
    ).limit(100).all()
    unread = [n for n in items if not n.is_read]
    for n in unread:
        n.is_read = True
    if unread:
        db.session.commit()
    return render_template("student/notifications.html", notifications=items)


# --------------------------------------------------------------------------
# Leaderboard (Module 12)
# --------------------------------------------------------------------------
def _leaderboard_rows(period="overall", limit=100, anonymize=False, viewer_email=None, exam_id=None):
    from sqlalchemy import func
    from app.models import Result

    query = (
        db.session.query(
            Student.email.label("email"),
            func.max(Student.full_name).label("name"),
            func.max(Student.college).label("college"),
            func.avg(Result.percentage).label("avg_pct"),
            func.sum(Result.correct).label("total_correct"),
            func.sum(Result.wrong).label("total_wrong"),
            func.avg(Result.time_taken_seconds).label("avg_time"),
        )
        .join(StudentAttempt, StudentAttempt.id == Result.attempt_id)
        .join(Student, Student.id == StudentAttempt.student_id)
    )
    if exam_id:
        query = query.filter(StudentAttempt.exam_id == exam_id)
    if period == "weekly":
        query = query.filter(StudentAttempt.submitted_at >= datetime.utcnow() - timedelta(days=7))
    elif period == "monthly":
        query = query.filter(StudentAttempt.submitted_at >= datetime.utcnow() - timedelta(days=30))

    query = query.group_by(Student.email).order_by(func.avg(Result.percentage).desc()).limit(limit)

    rows = []
    for idx, r in enumerate(query.all(), start=1):
        total = (r.total_correct or 0) + (r.total_wrong or 0)
        accuracy = round((r.total_correct or 0) / total * 100, 1) if total else 0
        account = StudentAccount.query.filter_by(email=r.email).first()
        is_self = viewer_email and r.email == viewer_email
        display_name, display_college, display_photo = r.name, r.college, (account.photo_path if account else None)
        if anonymize and not is_self:
            display_name = _anonymous_label(r.email)
            display_college = None
            display_photo = None
        rows.append({
            "rank": idx, "email": r.email, "name": display_name, "college": display_college,
            "score": round(r.avg_pct or 0, 2), "accuracy": accuracy,
            "avg_time": round(r.avg_time or 0),
            "photo": display_photo, "is_self": is_self,
        })
    return rows


def _anonymous_label(email):
    """Stable, non-reversible display label for a student who hasn't opted
    to be identified - same student always gets the same code, but the code
    can't be turned back into an email/name."""
    import hashlib
    code = hashlib.sha256((email or "").encode()).hexdigest()[:6].upper()
    return f"Student #{code}"


@student_bp.route("/leaderboard")
@student_login_required
def leaderboard(account):
    from app.models import SiteSetting
    if not SiteSetting.get_bool("leaderboard_enabled", True):
        return render_template("student/leaderboard.html", disabled=True, rows=[], period="overall", my_rank=None, my_exams=[])

    period = request.args.get("period", "overall")
    if period not in ("weekly", "monthly", "overall"):
        period = "overall"
    anonymize = SiteSetting.get_bool("leaderboard_anonymous", False)
    rows = _leaderboard_rows(period=period, anonymize=anonymize, viewer_email=account.email)
    my_rank = next((r["rank"] for r in rows if r["email"] == account.email), None)

    # Exams this student has actually attempted, for the "view this test's
    # leaderboard" picker - only exams where a per-exam board is actually
    # visible (see _exam_leaderboard_visible), so the list never links to a
    # page that immediately shows "unavailable".
    my_exams = (
        db.session.query(Exam.id, Exam.title)
        .join(StudentAttempt, StudentAttempt.exam_id == Exam.id)
        .join(Student, Student.id == StudentAttempt.student_id)
        .filter(Student.email == account.email, StudentAttempt.status.in_(("submitted", "auto_submitted")))
        .distinct().order_by(Exam.title).all()
    )
    my_exams = [(eid, title) for eid, title in my_exams if _exam_leaderboard_visible(Exam.query.get(eid))]

    return render_template("student/leaderboard.html", rows=rows, period=period, my_rank=my_rank,
                            anonymize=anonymize, disabled=False, my_exams=my_exams)


def _exam_leaderboard_visible(exam):
    """None on Exam.show_leaderboard means 'inherit the global setting';
    True/False is an explicit per-exam override either direction."""
    from app.models import SiteSetting
    if exam.show_leaderboard is not None:
        return exam.show_leaderboard
    return SiteSetting.get_bool("leaderboard_enabled", True)


@student_bp.route("/leaderboard/exam/<int:exam_id>")
@student_login_required
def exam_leaderboard(account, exam_id):
    from app.models import SiteSetting
    exam = Exam.query.get_or_404(exam_id)

    if not _exam_leaderboard_visible(exam):
        return render_template("student/leaderboard.html", disabled=True, rows=[], period="overall",
                                my_rank=None, my_exams=[], exam=exam)

    period = request.args.get("period", "overall")
    if period not in ("weekly", "monthly", "overall"):
        period = "overall"
    anonymize = SiteSetting.get_bool("leaderboard_anonymous", False)
    rows = _leaderboard_rows(period=period, anonymize=anonymize, viewer_email=account.email, exam_id=exam.id)
    my_rank = next((r["rank"] for r in rows if r["email"] == account.email), None)
    return render_template("student/leaderboard.html", rows=rows, period=period, my_rank=my_rank,
                            anonymize=anonymize, disabled=False, exam=exam, my_exams=[])


# --------------------------------------------------------------------------
# Analytics (Module 13)
# --------------------------------------------------------------------------
@student_bp.route("/analytics")
@student_login_required
def analytics(account):
    attempts = (
        StudentAttempt.query.join(Student)
        .filter(Student.email == account.email, StudentAttempt.status.in_(["submitted", "auto_submitted"]))
        .order_by(StudentAttempt.submitted_at.asc())
        .all()
    )

    subject_stats = {}
    total_correct = total_wrong = total_skipped = 0
    total_time, time_count = 0, 0
    daily_progress = []

    for a in attempts:
        r = a.result
        if r:
            total_correct += r.correct or 0
            total_wrong += r.wrong or 0
            total_skipped += r.skipped or 0
            if r.time_taken_seconds:
                total_time += r.time_taken_seconds
                time_count += 1
            if a.submitted_at:
                daily_progress.append({"date": a.submitted_at.strftime("%d %b"), "pct": r.percentage})

        for ans in a.answers:
            q = ans.question
            if not q:
                continue
            subj_name = q.subject.name if q.subject else "General"
            s = subject_stats.setdefault(subj_name, {"correct": 0, "wrong": 0, "skipped": 0, "marks": 0.0})
            if ans.is_correct is None:
                s["skipped"] += 1
            elif ans.is_correct:
                s["correct"] += 1
            else:
                s["wrong"] += 1
            s["marks"] += ans.marks_awarded or 0

    subject_rows = []
    for name, s in subject_stats.items():
        total_q = s["correct"] + s["wrong"] + s["skipped"]
        accuracy = round(s["correct"] / total_q * 100, 1) if total_q else 0
        subject_rows.append({"subject": name, "accuracy": accuracy, "marks": round(s["marks"], 2), "total": total_q})
    subject_rows.sort(key=lambda x: x["accuracy"], reverse=True)

    strong_topics = [s for s in subject_rows if s["total"] >= 1][:3]
    weak_topics = sorted([s for s in subject_rows if s["total"] >= 1], key=lambda x: x["accuracy"])[:3]

    total_questions = total_correct + total_wrong + total_skipped
    correct_pct = round(total_correct / total_questions * 100, 1) if total_questions else 0
    wrong_pct = round(total_wrong / total_questions * 100, 1) if total_questions else 0
    skipped_pct = round(total_skipped / total_questions * 100, 1) if total_questions else 0
    avg_time = round(total_time / time_count) if time_count else 0

    # Phase 4: AI Performance Analysis - latest engine-generated report plus
    # readiness trend across every analysed attempt.
    ai_history = (
        PerformanceAnalysis.query.filter_by(student_account_id=account.id)
        .order_by(PerformanceAnalysis.created_at.asc()).all()
    )
    latest_analysis = ai_history[-1] if ai_history else None
    readiness_trend = [{"date": a.created_at.strftime("%d %b"), "readiness": a.exam_readiness_percent} for a in ai_history]

    return render_template(
        "student/analytics.html", subject_rows=subject_rows, strong_topics=strong_topics,
        weak_topics=weak_topics, correct_pct=correct_pct, wrong_pct=wrong_pct, skipped_pct=skipped_pct,
        avg_time=avg_time, daily_progress=daily_progress, has_data=bool(attempts),
        latest_analysis=latest_analysis, ai_history=list(reversed(ai_history)),
        readiness_trend=readiness_trend,
    )


# --------------------------------------------------------------------------
# Restricted PDF viewer (Module 11)
# --------------------------------------------------------------------------
def _active_purchases_for(account, course_id):
    return (
        CoursePurchase.query.filter_by(student_account_id=account.id, course_id=course_id, status="paid")
        .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
        .all()
    )


def _purchase_granting(account, course_id, grant_method_name):
    """Returns the active purchase (if any) whose tier grants the given
    access - e.g. 'grants_pdf_materials' or 'grants_class_materials'. A
    student may hold several separate tier purchases for the same course
    (e.g. bought exam_only, then later class_only), so each grant is
    checked independently rather than assuming one purchase covers all."""
    for purchase in _active_purchases_for(account, course_id):
        if getattr(purchase, grant_method_name)():
            return purchase
    return None


# Kept for any older call-sites: any single active purchase for the course,
# regardless of tier (used only where the specific grant doesn't matter).
def _active_purchase_for(account, course_id):
    purchases = _active_purchases_for(account, course_id)
    return purchases[0] if purchases else None


@student_bp.route("/courses/<int:course_id>/materials")
@student_login_required
def course_materials(account, course_id):
    course = Course.query.get_or_404(course_id)
    pdf_purchase = _purchase_granting(account, course.id, "grants_pdf_materials")
    class_purchase = _purchase_granting(account, course.id, "grants_class_materials")
    active_purchase = pdf_purchase or class_purchase
    if not active_purchase:
        flash("You need an active Exam+PDF, Class, or All-Access plan for this course to view its materials.",
              "warning")
        return redirect(url_for("student.course_detail", course_id=course.id))

    # Phase 2: grouped Subject -> Chapter -> Materials, falling back to a
    # flat "Uploaded Materials" bucket for any pre-Phase-2 rows without a
    # chapter assignment, so nothing that existed before goes missing.
    subjects = (
        MaterialSubject.query.filter_by(course_id=course.id)
        .order_by(MaterialSubject.order_index).all()
    )
    unassigned = (
        CourseMaterial.query.filter_by(course_id=course.id, chapter_id=None)
        .order_by(CourseMaterial.order_index).all()
    )
    return render_template(
        "student/course_materials.html", course=course, subjects=subjects,
        unassigned=unassigned, active_purchase=active_purchase,
    )


@student_bp.route("/materials/<int:material_id>/view")
@student_login_required
def material_view(account, material_id):
    material = CourseMaterial.query.get_or_404(material_id)
    grant = "grants_class_materials" if material.material_type == "video" else "grants_pdf_materials"
    active_purchase = _purchase_granting(account, material.course_id, grant)
    if not active_purchase:
        flash("Your subscription for this course/material type has expired or isn't active.", "warning")
        return redirect(url_for("student.my_courses"))

    if not material.is_accessible_for(active_purchase):
        flash("Access to this specific material has expired. Renew to continue.", "warning")
        return redirect(url_for("student.course_detail", course_id=material.course_id))

    log_material_access(material.id, account.id, "view",
                         ip_address=get_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.session.commit()

    if material.material_type == "video":
        # Phase 1 cleanup: video/live-class functionality has been
        # discontinued. Any pre-existing "video" materials are left in the
        # database untouched (not deleted), but the video player templates
        # have been removed, so gracefully redirect instead of 500'ing.
        flash("Video classes are no longer available. Please contact support if you need this material.",
              "info")
        return redirect(url_for("student.course_materials", course_id=material.course_id))

    # For PDF/other materials
    token = create_material_token(material.id, account.id)
    stream_url = url_for("student.material_stream", material_id=material.id, token=token)
    watermark = {
        "name": account.full_name,
        "student_id": account.id,
        "email": account.email,
        "phone": account.phone,
        "stamp": datetime.utcnow().strftime("%d %b %Y %H:%M UTC"),
    }
    return render_template("student/material_view.html", material=material, stream_url=stream_url,
                            watermark=watermark)


@student_bp.route("/materials/<int:material_id>/stream")
@student_login_required
def material_stream(account, material_id):
    token = request.args.get("token", "")
    payload = decode_material_token(token)
    if not payload or payload.get("material_id") != material_id or payload.get("student_id") != account.id:
        abort(403)

    material = CourseMaterial.query.get_or_404(material_id)
    # Phase 3: always re-checked live (both the course subscription AND this
    # material's own access-duration window), so access is automatically
    # blocked the moment either expires - even mid-session.
    grant = "grants_class_materials" if material.material_type == "video" else "grants_pdf_materials"
    active_purchase = _purchase_granting(account, material.course_id, grant)
    if not material.is_accessible_for(active_purchase):
        abort(403)

    log_material_access(material.id, account.id, "stream",
                         ip_address=get_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.session.commit()

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "course_materials")
    
    # Determine MIME type based on material type
    if material.material_type == "video" and material.is_video_file_upload:
        mimetype = "video/mp4"
    else:
        mimetype = "application/pdf"
    
    response = send_from_directory(folder, material.file_path, mimetype=mimetype)
    
    # Add security headers for PDF/documents
    if material.material_type != "video" or not material.is_video_file_upload:
        response.headers["Content-Disposition"] = "inline"
    
    # Universal security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # For videos, add additional DRM-lite headers
    if material.material_type == "video" and material.is_video_file_upload:
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    
    return response
