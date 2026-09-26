import csv
import io
import os
import uuid
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    jsonify, Response, current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Exam, Subject, Question, Option, ExamLink, Student, StudentAttempt,
    Result, EmailLog, ActivityLog, log_activity, QUESTION_TYPES, TEST_CATEGORIES,
    StudentAccount, Course, CoursePurchase, notify_student, CourseMaterial, ExamEnrollment,
    MaterialSubject, MaterialChapter, CREATABLE_MATERIAL_TYPES, ACCESS_DURATION_CHOICES,
    MaterialAccessLog, PerformanceAnalysis, CourseViewLog, IssuedCertificate, HomeSlide,
    SubscriptionPlan, SecurityViolation, DURATION_UNITS, Coupon, CouponRedemption, Category, SiteSetting, User,
    log_payment_transaction, TickerItem,
    WhatsAppConversation, WhatsAppMessage, WHATSAPP_CONVERSATION_STATUSES, WHATSAPP_CATEGORIES,
)
from app.services.qr_service import generate_qr_base64
from app.services.device_service import get_client_ip
from app.services.pdf_service import generate_students_list_pdf, generate_invoice_pdf
from app.services.payment_service import generate_invoice_number
from app.services import whatsapp_service

admin_bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="../templates")


# --------------------------------------------------------------------------
# Teacher role - a real, narrower permission tier, not just a label. Deny-
# by-default: only endpoints explicitly listed here are reachable by a
# 'teacher' account, so a future admin route added later is blocked for
# teachers automatically unless someone deliberately opts it in - failing
# closed rather than open. Scope: exams, question bank, bulk import, exam
# links, results, and course materials/subjects (content) - explicitly NOT
# payments, coupons, pricing, categories, users, settings, or the audit log.
# --------------------------------------------------------------------------
TEACHER_ALLOWED_ENDPOINTS = {
    "admin.exams_list", "admin.exam_new", "admin.exam_detail", "admin.exam_edit", "admin.exam_delete",
    "admin.question_new", "admin.question_edit", "admin.question_delete",
    "admin.question_import", "admin.question_import_confirm", "admin.question_import_template",
    "admin.link_new", "admin.link_toggle", "admin.link_qr",
    "admin.results_list", "admin.results_export_csv", "admin.results_export_xlsx",
    "admin.results_download_pdf", "admin.results_export_pdf",
    "admin.subjects_list", "admin.exam_enroll", "admin.exam_enroll_delete",
    "admin.students_list", "admin.college_students", "admin.students_export",
    "admin.course_subjects", "admin.subject_chapters", "admin.course_materials",
    "admin.material_subject_delete", "admin.material_chapter_delete", "admin.material_delete",
}


@admin_bp.before_request
def _restrict_teacher_role():
    if not current_user.is_authenticated:
        return  # let @login_required on the view handle unauthenticated access
    if current_user.role == "teacher" and request.endpoint not in TEACHER_ALLOWED_ENDPOINTS:
        flash("Your Teacher account doesn't have access to that section.", "warning")
        return redirect(url_for("admin.exams_list"))


def admin_role_required(fn):
    """Restricts a route to role == 'admin' (not 'staff'). Used only for
    user-management, since granting/revoking admin access is the one action
    a lower-privileged staff account must never be able to do to itself or
    others - everything else in this admin panel is intentionally open to
    both roles, matching the app's existing behavior."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user.role != "admin":
            flash("Only Admin-role accounts can manage users.", "danger")
            return redirect(url_for("admin.dashboard"))
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@admin_bp.route("/login")
def login_alias():
    """Spec requires the admin login entrypoint to be /admin/login; the
    actual Flask-Login form still lives at auth.login, untouched."""
    return redirect(url_for("auth.login", next=request.args.get("next")))


@admin_bp.route("/")
@login_required
def dashboard():
    total_exams = Exam.query.count()
    total_students = Student.query.count()
    total_attempts = StudentAttempt.query.count()
    submitted_attempts = StudentAttempt.query.filter(
        StudentAttempt.status.in_(["submitted", "auto_submitted"])
    ).count()

    avg_pct = db.session.query(func.avg(Result.percentage)).scalar() or 0
    max_pct = db.session.query(func.max(Result.percentage)).scalar() or 0
    min_pct = db.session.query(func.min(Result.percentage)).scalar() or 0
    pass_count = Result.query.filter_by(status="pass").count()
    fail_count = Result.query.filter_by(status="fail").count()
    pass_pct = round((pass_count / (pass_count + fail_count) * 100), 1) if (pass_count + fail_count) else 0

    recent_attempts = (
        StudentAttempt.query.filter(StudentAttempt.status.in_(["submitted", "auto_submitted"]))
        .order_by(StudentAttempt.submitted_at.desc())
        .limit(10)
        .all()
    )

    # Phase 2-4 module summary
    registered_accounts = StudentAccount.query.count()
    active_courses = Course.query.filter_by(is_active=True).count()
    pending_payments = CoursePurchase.query.filter_by(status="pending").count()
    revenue_collected = db.session.query(func.sum(CoursePurchase.amount)).filter_by(status="paid").scalar() or 0

    return render_template(
        "admin/dashboard.html",
        total_exams=total_exams, total_students=total_students,
        total_attempts=total_attempts, submitted_attempts=submitted_attempts,
        avg_pct=round(avg_pct, 1), max_pct=round(max_pct, 1), min_pct=round(min_pct, 1),
        pass_count=pass_count, fail_count=fail_count, pass_pct=pass_pct,
        recent_attempts=recent_attempts,
        registered_accounts=registered_accounts, active_courses=active_courses,
        pending_payments=pending_payments, revenue_collected=round(revenue_collected, 2),
    )


@admin_bp.route("/analytics")
@login_required
def analytics_dashboard():
    from sqlalchemy import extract

    total_students = StudentAccount.query.count()
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    active_student_ids = set(
        r[0] for r in db.session.query(MaterialAccessLog.student_account_id)
        .filter(MaterialAccessLog.created_at >= thirty_days_ago).distinct().all()
    ) | set(
        r[0] for r in db.session.query(CourseViewLog.student_account_id)
        .filter(CourseViewLog.viewed_at >= thirty_days_ago).distinct().all()
    )
    active_students = len(active_student_ids)

    total_revenue = db.session.query(func.sum(CoursePurchase.amount)).filter_by(status="paid").scalar() or 0
    total_platform_fees = db.session.query(func.sum(CoursePurchase.platform_fee_amount)).filter_by(status="paid").scalar() or 0
    total_discounts = db.session.query(func.sum(CoursePurchase.discount_amount)).filter_by(status="paid").scalar() or 0
    # "Plan revenue" per the brief (section 8) - the total minus the fee we
    # charge on top of it, so admins don't mistake the whole ₹183 as plan
    # price when ₹3 of it was the platform/payment convenience fee.
    plan_revenue = total_revenue - total_platform_fees
    # Refunded purchases are excluded from `total_revenue` above (they're
    # status="refunded", not "paid"), so "Total Collected" already reflects
    # money actually retained - this is shown separately purely so an admin
    # can see how much was given back, not to subtract it again.
    total_refunded = db.session.query(func.sum(CoursePurchase.refund_amount)).filter_by(status="refunded").scalar() or 0

    course_revenue = (
        db.session.query(Course.title, func.sum(CoursePurchase.amount), func.count(CoursePurchase.id))
        .join(CoursePurchase, CoursePurchase.course_id == Course.id)
        .filter(CoursePurchase.status == "paid")
        .group_by(Course.id).order_by(func.sum(CoursePurchase.amount).desc()).all()
    )
    course_revenue_rows = [{"course": t, "revenue": round(r or 0, 2), "sales": c} for t, r, c in course_revenue]

    popular_courses = sorted(course_revenue_rows, key=lambda r: r["sales"], reverse=True)[:5]

    monthly_revenue_raw = (
        db.session.query(
            extract("year", CoursePurchase.verified_at), extract("month", CoursePurchase.verified_at),
            func.sum(CoursePurchase.amount),
        )
        .filter(CoursePurchase.status == "paid", CoursePurchase.verified_at.isnot(None))
        .group_by(extract("year", CoursePurchase.verified_at), extract("month", CoursePurchase.verified_at))
        .order_by(extract("year", CoursePurchase.verified_at), extract("month", CoursePurchase.verified_at))
        .all()
    )
    monthly_revenue = [{"label": f"{int(m):02d}/{int(y)}", "amount": round(a or 0, 2)} for y, m, a in monthly_revenue_raw]

    student_growth_raw = (
        db.session.query(
            extract("year", StudentAccount.created_at), extract("month", StudentAccount.created_at),
            func.count(StudentAccount.id),
        )
        .group_by(extract("year", StudentAccount.created_at), extract("month", StudentAccount.created_at))
        .order_by(extract("year", StudentAccount.created_at), extract("month", StudentAccount.created_at))
        .all()
    )
    student_growth = [{"label": f"{int(m):02d}/{int(y)}", "count": c} for y, m, c in student_growth_raw]

    mock_test_attempts = StudentAttempt.query.filter(
        StudentAttempt.status.in_(["submitted", "auto_submitted"])
    ).count()

    pdf_views = (
        db.session.query(func.count(MaterialAccessLog.id))
        .join(CourseMaterial, CourseMaterial.id == MaterialAccessLog.material_id)
        .filter(CourseMaterial.material_type != "video", MaterialAccessLog.action == "view")
        .scalar() or 0
    )
    video_views = (
        db.session.query(func.count(MaterialAccessLog.id))
        .join(CourseMaterial, CourseMaterial.id == MaterialAccessLog.material_id)
        .filter(CourseMaterial.material_type == "video", MaterialAccessLog.action == "view")
        .scalar() or 0
    )
    total_material_usage = MaterialAccessLog.query.count()

    ai_trend_raw = (
        db.session.query(
            extract("year", PerformanceAnalysis.created_at), extract("month", PerformanceAnalysis.created_at),
            func.avg(PerformanceAnalysis.exam_readiness_percent),
        )
        .group_by(extract("year", PerformanceAnalysis.created_at), extract("month", PerformanceAnalysis.created_at))
        .order_by(extract("year", PerformanceAnalysis.created_at), extract("month", PerformanceAnalysis.created_at))
        .all()
    )
    ai_readiness_trend = [{"label": f"{int(m):02d}/{int(y)}", "avg_readiness": round(a or 0, 1)} for y, m, a in ai_trend_raw]

    return render_template(
        "admin/analytics.html",
        total_students=total_students, active_students=active_students,
        total_revenue=round(total_revenue, 2), plan_revenue=round(plan_revenue, 2),
        total_platform_fees=round(total_platform_fees, 2), total_discounts=round(total_discounts, 2),
        total_refunded=round(total_refunded, 2),
        course_revenue_rows=course_revenue_rows,
        popular_courses=popular_courses, monthly_revenue=monthly_revenue,
        student_growth=student_growth, mock_test_attempts=mock_test_attempts,
        pdf_views=pdf_views, video_views=video_views, total_material_usage=total_material_usage,
        ai_readiness_trend=ai_readiness_trend,
    )


# --------------------------------------------------------------------------
# Exams
# --------------------------------------------------------------------------
@admin_bp.route("/exams")
@login_required
def exams_list():
    exams = Exam.query.order_by(Exam.created_at.desc()).all()
    return render_template("admin/exams_list.html", exams=exams)


def _parse_tri_state_bool(value):
    """For the leaderboard-override select: '' -> None (inherit global),
    '1' -> True, '0' -> False."""
    if value == "1":
        return True
    if value == "0":
        return False
    return None


@admin_bp.route("/exams/new", methods=["GET", "POST"])
@login_required
def exam_new():
    if request.method == "POST":
        exam = Exam(
            title=request.form["title"],
            description=request.form.get("description", ""),
            duration_minutes=int(request.form.get("duration_minutes", 60)),
            passing_percentage=float(request.form.get("passing_percentage", 40)),
            negative_marking_enabled=bool(request.form.get("negative_marking_enabled")),
            negative_marks_per_wrong=float(request.form.get("negative_marks_per_wrong", 0) or 0),
            shuffle_questions=bool(request.form.get("shuffle_questions")),
            shuffle_options=bool(request.form.get("shuffle_options")),
            instructions=request.form.get("instructions", ""),
            allow_retake=bool(request.form.get("allow_retake")),
            strict_anti_cheat=bool(request.form.get("strict_anti_cheat")),
            scheduled_at=(
                datetime.strptime(request.form.get("scheduled_at"), "%Y-%m-%dT%H:%M")
                if request.form.get("scheduled_at") else None
            ),
            syllabus=request.form.get("syllabus") or None,
            test_category=request.form.get("test_category") or None,
            course_id=request.form.get("course_id", type=int) or None,
            created_by=current_user.id,
            test_type=request.form.get("test_type") or "mock",
            free_without_plan=bool(request.form.get("free_without_plan")),
            max_attempts=request.form.get("max_attempts", type=int) or None,
            availability_mode=request.form.get("availability_mode") or "flexible",
            available_from=(
                datetime.strptime(request.form.get("available_from"), "%Y-%m-%dT%H:%M")
                if request.form.get("available_from") else None
            ),
            available_until=(
                datetime.strptime(request.form.get("available_until"), "%Y-%m-%dT%H:%M")
                if request.form.get("available_until") else None
            ),
            camera_required=bool(request.form.get("camera_required")),
            mic_required=bool(request.form.get("mic_required")),
            face_detection_enabled=bool(request.form.get("face_detection_enabled")),
            multi_face_detection_enabled=bool(request.form.get("multi_face_detection_enabled")),
            looking_away_detection_enabled=bool(request.form.get("looking_away_detection_enabled")),
            voice_detection_enabled=bool(request.form.get("voice_detection_enabled")),
            max_warnings=request.form.get("max_warnings", type=int) or 3,
            show_leaderboard=_parse_tri_state_bool(request.form.get("show_leaderboard", "")),
        )
        db.session.add(exam)
        log_activity(current_user.email, "exam_created", meta=exam.title, ip_address=get_client_ip(request))
        db.session.commit()

        # Test-series exams (Daily/Weekly/Monthly/Grand) get a default,
        # always-open link automatically, so they show up ready-to-take on
        # the student's Mock Tests page without an extra admin step.
        if exam.test_category:
            db.session.add(ExamLink(exam_id=exam.id))
            db.session.commit()

        flash("Exam created. Now add some questions.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))

    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    return render_template("admin/exam_form.html", exam=None, test_categories=TEST_CATEGORIES, courses=courses)


@admin_bp.route("/exams/<int:exam_id>")
@login_required
def exam_detail(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    questions = exam.questions.order_by(Question.order_index).all()
    links = exam.links.order_by(ExamLink.created_at.desc()).all()
    enrollments = ExamEnrollment.query.filter_by(exam_id=exam.id).order_by(ExamEnrollment.created_at.desc()).all()
    return render_template("admin/exam_detail.html", exam=exam, questions=questions, links=links,
                            enrollments=enrollments, base_url=current_app.config["APP_BASE_URL"])


@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@login_required
def exam_edit(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    if request.method == "POST":
        exam.title = request.form["title"]
        exam.description = request.form.get("description", "")
        exam.duration_minutes = int(request.form.get("duration_minutes", 60))
        exam.passing_percentage = float(request.form.get("passing_percentage", 40))
        exam.negative_marking_enabled = bool(request.form.get("negative_marking_enabled"))
        exam.negative_marks_per_wrong = float(request.form.get("negative_marks_per_wrong", 0) or 0)
        exam.shuffle_questions = bool(request.form.get("shuffle_questions"))
        exam.shuffle_options = bool(request.form.get("shuffle_options"))
        exam.instructions = request.form.get("instructions", "")
        exam.allow_retake = bool(request.form.get("allow_retake"))
        exam.strict_anti_cheat = bool(request.form.get("strict_anti_cheat"))
        exam.scheduled_at = (
            datetime.strptime(request.form.get("scheduled_at"), "%Y-%m-%dT%H:%M")
            if request.form.get("scheduled_at") else None
        )
        exam.syllabus = request.form.get("syllabus") or None
        exam.test_category = request.form.get("test_category") or None
        exam.course_id = request.form.get("course_id", type=int) or None
        exam.is_active = bool(request.form.get("is_active"))
        exam.test_type = request.form.get("test_type") or "mock"
        exam.free_without_plan = bool(request.form.get("free_without_plan"))
        exam.max_attempts = request.form.get("max_attempts", type=int) or None
        exam.availability_mode = request.form.get("availability_mode") or "flexible"
        exam.available_from = (
            datetime.strptime(request.form.get("available_from"), "%Y-%m-%dT%H:%M")
            if request.form.get("available_from") else None
        )
        exam.available_until = (
            datetime.strptime(request.form.get("available_until"), "%Y-%m-%dT%H:%M")
            if request.form.get("available_until") else None
        )
        exam.camera_required = bool(request.form.get("camera_required"))
        exam.mic_required = bool(request.form.get("mic_required"))
        exam.face_detection_enabled = bool(request.form.get("face_detection_enabled"))
        exam.multi_face_detection_enabled = bool(request.form.get("multi_face_detection_enabled"))
        exam.looking_away_detection_enabled = bool(request.form.get("looking_away_detection_enabled"))
        exam.voice_detection_enabled = bool(request.form.get("voice_detection_enabled"))
        exam.max_warnings = request.form.get("max_warnings", type=int) or 3
        exam.show_leaderboard = _parse_tri_state_bool(request.form.get("show_leaderboard", ""))
        log_activity(current_user.email, "exam_updated", meta=exam.title, ip_address=get_client_ip(request))
        db.session.commit()

        if exam.test_category and exam.links.count() == 0:
            db.session.add(ExamLink(exam_id=exam.id))
            db.session.commit()

        flash("Exam updated.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))

    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    return render_template("admin/exam_form.html", exam=exam, test_categories=TEST_CATEGORIES, courses=courses)


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@login_required
def exam_delete(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    log_activity(current_user.email, "exam_deleted", meta=exam.title, ip_address=get_client_ip(request))
    db.session.delete(exam)
    db.session.commit()
    flash("Exam deleted.", "info")
    return redirect(url_for("admin.exams_list"))


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------
@admin_bp.route("/exams/<int:exam_id>/questions/new", methods=["GET", "POST"])
@login_required
def question_new(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    subjects = Subject.query.order_by(Subject.name).all()

    if request.method == "POST":
        q = Question(
            exam_id=exam.id,
            subject_id=request.form.get("subject_id") or None,
            question_type=request.form["question_type"],
            question_text=request.form["question_text"],
            image_url=request.form.get("image_url") or None,
            passage_text=request.form.get("passage_text") or None,
            marks=float(request.form.get("marks", 1)),
            negative_marks=float(request.form.get("negative_marks", 0) or 0),
            correct_integer_answer=_to_int(request.form.get("correct_integer_answer")),
            correct_text_answer=request.form.get("correct_text_answer") or None,
            explanation=request.form.get("explanation") or None,
            detailed_explanation=request.form.get("detailed_explanation") or None,
            explanation_reference=request.form.get("explanation_reference") or None,
            explanation_youtube_url=request.form.get("explanation_youtube_url") or None,
            explanation_voice_url=request.form.get("explanation_voice_url") or None,
            order_index=exam.question_count(),
        )
        db.session.add(q)
        db.session.flush()

        option_texts = request.form.getlist("option_text[]")
        correct_flags = request.form.getlist("option_correct[]")  # values = index positions marked correct
        for idx, text in enumerate(option_texts):
            if not text.strip():
                continue
            db.session.add(Option(
                question_id=q.id, option_text=text.strip(),
                is_correct=str(idx) in correct_flags, order_index=idx,
            ))

        exam.recompute_total_marks()
        db.session.commit()
        flash("Question added.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))

    return render_template("admin/question_form.html", exam=exam, question=None,
                            subjects=subjects, question_types=QUESTION_TYPES)


def _to_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


@admin_bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required
def question_edit(question_id):
    q = Question.query.get_or_404(question_id)
    exam = q.exam
    subjects = Subject.query.order_by(Subject.name).all()

    if request.method == "POST":
        q.subject_id = request.form.get("subject_id") or None
        q.question_type = request.form["question_type"]
        q.question_text = request.form["question_text"]
        q.image_url = request.form.get("image_url") or None
        q.passage_text = request.form.get("passage_text") or None
        q.marks = float(request.form.get("marks", 1))
        q.negative_marks = float(request.form.get("negative_marks", 0) or 0)
        q.correct_integer_answer = _to_int(request.form.get("correct_integer_answer"))
        q.correct_text_answer = request.form.get("correct_text_answer") or None
        q.explanation = request.form.get("explanation") or None
        q.detailed_explanation = request.form.get("detailed_explanation") or None
        q.explanation_reference = request.form.get("explanation_reference") or None
        q.explanation_youtube_url = request.form.get("explanation_youtube_url") or None
        q.explanation_voice_url = request.form.get("explanation_voice_url") or None

        # Replace options
        Option.query.filter_by(question_id=q.id).delete()
        option_texts = request.form.getlist("option_text[]")
        correct_flags = request.form.getlist("option_correct[]")
        for idx, text in enumerate(option_texts):
            if not text.strip():
                continue
            db.session.add(Option(
                question_id=q.id, option_text=text.strip(),
                is_correct=str(idx) in correct_flags, order_index=idx,
            ))

        exam.recompute_total_marks()
        log_activity(current_user.email, "question_updated", meta=f"exam={exam.id} q={q.id}", ip_address=get_client_ip(request))
        db.session.commit()
        flash("Question updated.", "success")
        return redirect(url_for("admin.exam_detail", exam_id=exam.id))

    return render_template("admin/question_form.html", exam=exam, question=q,
                            subjects=subjects, question_types=QUESTION_TYPES)


@admin_bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@login_required
def question_delete(question_id):
    q = Question.query.get_or_404(question_id)
    exam = q.exam
    log_activity(current_user.email, "question_deleted", meta=f"exam={exam.id} q={q.id}", ip_address=get_client_ip(request))
    db.session.delete(q)
    exam.recompute_total_marks()
    db.session.commit()
    flash("Question deleted.", "info")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


# --------------------------------------------------------------------------
# Bulk question import (CSV / XLSX) - preview-then-confirm flow. Nothing
# is written to the database until the admin explicitly confirms, and only
# rows that pass validation are ever imported - see bulk_import_service.py.
# --------------------------------------------------------------------------
def _import_tmp_dir():
    path = os.path.join(current_app.instance_path, "tmp_imports")
    os.makedirs(path, exist_ok=True)
    return path


@admin_bp.route("/exams/<int:exam_id>/questions/import", methods=["GET", "POST"])
@login_required
def question_import(exam_id):
    from app.services import bulk_import_service as svc
    exam = Exam.query.get_or_404(exam_id)

    if request.method == "GET":
        return render_template("admin/question_import.html", exam=exam)

    file = request.files.get("import_file")
    if not file or not file.filename:
        flash("Please choose a .csv or .xlsx file to upload.", "warning")
        return redirect(url_for("admin.question_import", exam_id=exam.id))

    filename = file.filename.lower()
    if not (filename.endswith(".csv") or filename.endswith(".xlsx")):
        flash("Unsupported file type - please upload a .csv or .xlsx file.", "warning")
        return redirect(url_for("admin.question_import", exam_id=exam.id))

    raw_bytes = file.read()
    token = uuid.uuid4().hex + (".xlsx" if filename.endswith(".xlsx") else ".csv")
    tmp_path = os.path.join(_import_tmp_dir(), token)
    with open(tmp_path, "wb") as f:
        f.write(raw_bytes)

    try:
        with open(tmp_path, "rb") as f:
            from werkzeug.datastructures import FileStorage
            raw_rows = svc.parse_upload(FileStorage(stream=f, filename=filename))
    except ValueError as e:
        os.remove(tmp_path)
        flash(str(e), "danger")
        return redirect(url_for("admin.question_import", exam_id=exam.id))

    if not raw_rows:
        os.remove(tmp_path)
        flash("That file has no data rows - nothing to import.", "warning")
        return redirect(url_for("admin.question_import", exam_id=exam.id))

    validated = svc.validate_rows(raw_rows, exam)
    valid_count = sum(1 for r in validated if r.is_valid)
    invalid_count = len(validated) - valid_count

    return render_template(
        "admin/question_import_preview.html", exam=exam, rows=validated,
        valid_count=valid_count, invalid_count=invalid_count, token=token,
    )


@admin_bp.route("/exams/<int:exam_id>/questions/import/confirm", methods=["POST"])
@login_required
def question_import_confirm(exam_id):
    from app.services import bulk_import_service as svc
    exam = Exam.query.get_or_404(exam_id)
    token = request.form.get("token", "")
    tmp_path = os.path.join(_import_tmp_dir(), os.path.basename(token))  # basename: never trust a path from the client

    if not token or not os.path.isfile(tmp_path):
        flash("This import session has expired - please upload the file again.", "warning")
        return redirect(url_for("admin.question_import", exam_id=exam.id))

    filename = token
    with open(tmp_path, "rb") as f:
        from werkzeug.datastructures import FileStorage
        raw_rows = svc.parse_upload(FileStorage(stream=f, filename=filename))

    # Re-validate at confirm time too, not just at preview time - the set of
    # questions already in this exam (or another admin's concurrent import)
    # may have changed since the preview was shown.
    validated = svc.validate_rows(raw_rows, exam)
    imported = svc.commit_valid_rows(validated, exam)
    skipped = len(validated) - imported

    log_activity(current_user.email, "questions_bulk_imported",
                  meta=f"exam={exam.id} imported={imported} skipped={skipped}",
                  ip_address=get_client_ip(request))
    db.session.commit()

    try:
        os.remove(tmp_path)
    except OSError:
        pass

    if skipped:
        flash(f"Imported {imported} question(s). {skipped} row(s) were skipped - re-upload a corrected file if you'd like to add them.", "info")
    else:
        flash(f"Imported {imported} question(s) successfully.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@admin_bp.route("/questions/import/template.csv")
@login_required
def question_import_template():
    from app.services import bulk_import_service as svc
    csv_text = svc.generate_template_csv()
    return Response(
        csv_text, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=question_import_template.csv"},
    )


# --------------------------------------------------------------------------
# Public links
# --------------------------------------------------------------------------
@admin_bp.route("/exams/<int:exam_id>/links/new", methods=["POST"])
@login_required
def link_new(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    link = ExamLink(exam_id=exam.id)

    expiry = request.form.get("expires_at")
    if expiry:
        link.expires_at = datetime.fromisoformat(expiry)
    max_attempts = request.form.get("max_attempts")
    link.max_attempts = int(max_attempts) if max_attempts else None
    password = request.form.get("password")
    if password:
        link.set_password(password)

    db.session.add(link)
    log_activity(current_user.email, "link_created", meta=f"exam={exam.id}")
    db.session.commit()
    flash("Public exam link generated.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@admin_bp.route("/links/<int:link_id>/toggle", methods=["POST"])
@login_required
def link_toggle(link_id):
    link = ExamLink.query.get_or_404(link_id)
    link.is_active = not link.is_active
    db.session.commit()
    return redirect(url_for("admin.exam_detail", exam_id=link.exam_id))


@admin_bp.route("/links/<int:link_id>/qr")
@login_required
def link_qr(link_id):
    link = ExamLink.query.get_or_404(link_id)
    url = link.public_url(current_app.config["APP_BASE_URL"])
    return jsonify({"qr": generate_qr_base64(url), "url": url})


# --------------------------------------------------------------------------
# Students & Results
# --------------------------------------------------------------------------
@admin_bp.route("/students")
@login_required
def students_list():
    q = request.args.get("q", "").strip()
    query = Student.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Student.full_name.ilike(like)) | (Student.email.ilike(like)) | (Student.phone.ilike(like))
        )
    students = query.order_by(Student.created_at.desc()).limit(200).all()
    return render_template("admin/students_list.html", students=students, q=q)


@admin_bp.route("/results")
@login_required
def results_list():
    q = request.args.get("q", "").strip()
    exam_id = request.args.get("exam_id", type=int)
    course_id = request.args.get("course_id", type=int)
    plan_id = request.args.get("plan_id", type=int)
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    query = (
        StudentAttempt.query.join(Student).join(Exam)
        .filter(StudentAttempt.status.in_(["submitted", "auto_submitted"]))
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Student.full_name.ilike(like)) | (Student.email.ilike(like)) | (Student.phone.ilike(like))
        )
    if exam_id:
        query = query.filter(StudentAttempt.exam_id == exam_id)
    if course_id:
        query = query.filter(Exam.course_id == course_id)
    if plan_id:
        # Only attempts belonging to a StudentAccount who has purchased
        # this specific plan (any status paid, regardless of current
        # expiry - so historical results for a plan remain filterable).
        plan_holder_ids = [
            row[0] for row in db.session.query(CoursePurchase.student_account_id)
            .filter(CoursePurchase.plan_id == plan_id, CoursePurchase.status == "paid").distinct()
        ]
        query = query.filter(StudentAttempt.student_account_id.in_(plan_holder_ids or [-1]))
    if date_from:
        try:
            query = query.filter(StudentAttempt.submitted_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(StudentAttempt.submitted_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    attempts = query.order_by(StudentAttempt.submitted_at.desc()).limit(300).all()

    # Phase 2: true nth-attempt-for-this-student-on-this-exam number (not
    # just table row order), computed from full submission history so it's
    # correct even though `attempts` above is limited/filtered/sorted.
    attempt_numbers = {}
    for a in attempts:
        if a.id in attempt_numbers:
            continue
        siblings = (
            StudentAttempt.query.join(Student)
            .filter(Student.email == a.student.email, StudentAttempt.exam_id == a.exam_id,
                    StudentAttempt.status.in_(["submitted", "auto_submitted"]))
            .order_by(StudentAttempt.submitted_at.asc())
            .all()
        )
        for idx, sib in enumerate(siblings, start=1):
            attempt_numbers[sib.id] = idx

    # Phase 2: resolve "which plan/purchase was active for this student on
    # this exam's course" for display, computed here (not in the template -
    # Jinja can't walk SQLAlchemy relationship internals cleanly).
    attempt_plan_labels = {}
    for a in attempts:
        label = "-"
        if a.student_account_id and a.exam.course_id:
            purchase = (
                CoursePurchase.query.filter_by(
                    student_account_id=a.student_account_id, course_id=a.exam.course_id, status="paid",
                ).order_by(CoursePurchase.created_at.desc()).first()
            )
            if purchase:
                label = purchase.tier_label()
        attempt_plan_labels[a.id] = label

    exams = Exam.query.order_by(Exam.title).all()
    courses = Course.query.order_by(Course.title).all()
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.name).all()
    return render_template("admin/results_list.html", attempts=attempts, exams=exams, courses=courses, plans=plans,
                            q=q, exam_id=exam_id, course_id=course_id, plan_id=plan_id,
                            date_from=date_from, date_to=date_to,
                            attempt_plan_labels=attempt_plan_labels, attempt_numbers=attempt_numbers)


@admin_bp.route("/results/export.csv")
@login_required
def results_export_csv():
    exam_id = request.args.get("exam_id", type=int)
    query = StudentAttempt.query.filter(StudentAttempt.status.in_(["submitted", "auto_submitted"]))
    if exam_id:
        query = query.filter(StudentAttempt.exam_id == exam_id)
    attempts = query.order_by(StudentAttempt.submitted_at.desc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Phone", "Email", "Exam", "Score", "Total", "Percentage",
                      "Status", "Correct", "Wrong", "Skipped", "Submitted At"])
    for a in attempts:
        r = a.result
        writer.writerow([
            a.student.full_name, a.student.phone, a.student.email, a.exam.title,
            r.score if r else "", r.total_marks if r else "", r.percentage if r else "",
            r.status if r else "", r.correct if r else "", r.wrong if r else "",
            r.skipped if r else "", a.submitted_at,
        ])

    output = buf.getvalue()
    return Response(
        output, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=results.csv"},
    )


@admin_bp.route("/results/export.xlsx")
@login_required
def results_export_xlsx():
    from openpyxl import Workbook
    exam_id = request.args.get("exam_id", type=int)
    query = StudentAttempt.query.filter(StudentAttempt.status.in_(["submitted", "auto_submitted"]))
    if exam_id:
        query = query.filter(StudentAttempt.exam_id == exam_id)
    attempts = query.order_by(StudentAttempt.submitted_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(["Name", "Phone", "Email", "Exam", "Score", "Total", "Percentage",
               "Status", "Correct", "Wrong", "Skipped", "Submitted At"])
    for a in attempts:
        r = a.result
        ws.append([
            a.student.full_name, a.student.phone, a.student.email, a.exam.title,
            r.score if r else "", r.total_marks if r else "", r.percentage if r else "",
            r.status if r else "", r.correct if r else "", r.wrong if r else "",
            r.skipped if r else "", a.submitted_at.strftime("%Y-%m-%d %H:%M") if a.submitted_at else "",
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=results.xlsx"},
    )


@admin_bp.route("/results/<int:attempt_id>/pdf")
@login_required
def results_download_pdf(attempt_id):
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    from flask import send_file
    if attempt.result and attempt.result.pdf_path:
        return send_file(attempt.result.pdf_path, as_attachment=True)
    flash("PDF not generated yet for this attempt.", "warning")
    return redirect(url_for("admin.results_list"))


# --------------------------------------------------------------------------
# Subjects (simple CRUD, used to tag questions)
# --------------------------------------------------------------------------
@admin_bp.route("/subjects", methods=["GET", "POST"])
@login_required
def subjects_list():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            db.session.add(Subject(name=name, description=request.form.get("description", "")))
            db.session.commit()
            flash("Subject added.", "success")
        return redirect(url_for("admin.subjects_list"))

    subjects = Subject.query.order_by(Subject.name).all()
    return render_template("admin/subjects_list.html", subjects=subjects)


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------
@admin_bp.route("/exams/<int:exam_id>/enroll", methods=["POST"])
@login_required
def exam_enroll(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    link_id = request.form.get("exam_link_id", type=int)
    college = request.form.get("college", "").strip()
    emails_raw = request.form.get("emails", "").strip()

    accounts = []
    if emails_raw:
        wanted = {e.strip().lower() for e in emails_raw.split(",") if e.strip()}
        accounts = StudentAccount.query.filter(StudentAccount.email.in_(wanted)).all()
    elif college:
        matching_emails = [
            e[0] for e in db.session.query(Student.email).filter(Student.college.ilike(f"%{college}%")).distinct()
        ]
        accounts = StudentAccount.query.filter(StudentAccount.email.in_(matching_emails)).all()

    created = 0
    for account in accounts:
        exists = ExamEnrollment.query.filter_by(exam_id=exam.id, student_account_id=account.id).first()
        if not exists:
            db.session.add(ExamEnrollment(exam_id=exam.id, student_account_id=account.id, exam_link_id=link_id))
            created += 1
    db.session.commit()
    flash(f"Enrolled {created} student(s) for exam reminders.", "success")
    return redirect(url_for("admin.exam_detail", exam_id=exam.id))


@admin_bp.route("/exams/<int:exam_id>/enroll/<int:enrollment_id>/delete", methods=["POST"])
@login_required
def exam_enroll_delete(exam_id, enrollment_id):
    enrollment = ExamEnrollment.query.get_or_404(enrollment_id)
    db.session.delete(enrollment)
    db.session.commit()
    return redirect(url_for("admin.exam_detail", exam_id=exam_id))


@admin_bp.route("/activity")
@login_required
def activity_log():
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(300).all()
    return render_template("admin/activity_log.html", logs=logs)


# --------------------------------------------------------------------------
# College-wise Student Management (Module 3)
# --------------------------------------------------------------------------
def _student_rows_by_college(college_filter=None, emails=None):
    """Aggregates the anonymous per-attempt Student records by email (since
    that table has no persistent student identity), and enriches each row
    with the matching StudentAccount (photo/address/subscription) when one
    exists. Suitable for admin-scale result sets (capped below), not a
    high-volume analytics query.
    """
    query = db.session.query(
        Student.email.label("email"),
        func.max(Student.full_name).label("full_name"),
        func.max(Student.college).label("college"),
        func.max(Student.phone).label("phone"),
        func.min(Student.created_at).label("registered"),
    ).group_by(Student.email)

    if college_filter:
        query = query.filter(Student.college.ilike(f"%{college_filter}%"))
    if emails:
        query = query.filter(Student.email.in_(emails))

    grouped = query.order_by(func.max(Student.created_at).desc()).limit(500).all()

    rows = []
    for g in grouped:
        stats = (
            db.session.query(
                func.count(Result.id), func.avg(Result.percentage), func.max(Result.percentage)
            )
            .join(StudentAttempt, StudentAttempt.id == Result.attempt_id)
            .join(Student, Student.id == StudentAttempt.student_id)
            .filter(Student.email == g.email)
            .first()
        )
        exam_count = stats[0] or 0
        avg_score = round(stats[1], 2) if stats[1] else 0
        best_score = round(stats[2], 2) if stats[2] else 0

        account = StudentAccount.query.filter_by(email=g.email).first()
        subscription = "-"
        if account:
            active_purchase = (
                CoursePurchase.query.filter_by(student_account_id=account.id, status="paid")
                .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
                .order_by(CoursePurchase.expires_at.desc())
                .first()
            )
            if active_purchase:
                subscription = f"{active_purchase.course.title} (until {active_purchase.expires_at.strftime('%d %b %Y')})"

        rows.append({
            "email": g.email,
            "name": g.full_name,
            "college": g.college,
            "phone": g.phone,
            "address": account.address if account else "",
            "photo": account.photo_path if account else None,
            "registered": g.registered.strftime("%d %b %Y") if g.registered else "",
            "exam_count": exam_count,
            "avg_score": avg_score,
            "best_score": best_score,
            "subscription": subscription,
        })
    return rows


@admin_bp.route("/college-students")
@login_required
def college_students():
    college = request.args.get("college", "").strip()
    show_all = request.args.get("all") == "1"
    rows = []
    if college:
        rows = _student_rows_by_college(college_filter=college)
    elif show_all:
        rows = _student_rows_by_college()
    colleges = [
        c[0] for c in db.session.query(Student.college).filter(Student.college.isnot(None))
        .distinct().order_by(Student.college).all()
    ]
    return render_template("admin/college_students.html", rows=rows, college=college,
                            colleges=colleges, show_all=show_all)


# --------------------------------------------------------------------------
# Export (Module 4) - all / single college / selected students, xlsx/csv/pdf
# --------------------------------------------------------------------------
@admin_bp.route("/students/export")
@login_required
def students_export():
    fmt = request.args.get("format", "xlsx")
    college = request.args.get("college", "").strip() or None
    ids_param = request.args.get("emails", "").strip()
    emails = [e for e in ids_param.split(",") if e] if ids_param else None

    rows = _student_rows_by_college(college_filter=college, emails=emails)
    scope_label = (
        f"College - {college}" if college else ("Selected Students" if emails else "All Students")
    )

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Name", "College", "Email", "Phone", "Address", "Registered Date",
                          "Exam Count", "Average Score", "Best Score", "Current Subscription"])
        for r in rows:
            writer.writerow([r["name"], r["college"], r["email"], r["phone"], r["address"],
                              r["registered"], r["exam_count"], r["avg_score"], r["best_score"], r["subscription"]])
        return Response(buf.getvalue(), mimetype="text/csv",
                         headers={"Content-Disposition": "attachment; filename=students.csv"})

    if fmt == "pdf":
        from flask import send_file
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        generate_students_list_pdf(rows, path, title=scope_label)
        return send_file(path, as_attachment=True, download_name="students.pdf")

    # default: xlsx
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    ws.append(["Name", "College", "Email", "Phone", "Address", "Registered Date",
               "Exam Count", "Average Score", "Best Score", "Current Subscription"])
    for r in rows:
        ws.append([r["name"], r["college"], r["email"], r["phone"], r["address"],
                   r["registered"], r["exam_count"], r["avg_score"], r["best_score"], r["subscription"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(buf.read(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": "attachment; filename=students.xlsx"})


@admin_bp.route("/results/export.pdf")
@login_required
def results_export_pdf():
    """Adds a PDF export option alongside the existing CSV/XLSX exam-results exports."""
    from flask import send_file
    import tempfile

    exam_id = request.args.get("exam_id", type=int)
    query = StudentAttempt.query.filter(StudentAttempt.status.in_(["submitted", "auto_submitted"]))
    if exam_id:
        query = query.filter(StudentAttempt.exam_id == exam_id)
    attempts = query.order_by(StudentAttempt.submitted_at.desc()).limit(500).all()

    rows = []
    for a in attempts:
        r = a.result
        rows.append({
            "name": a.student.full_name, "college": a.exam.title, "email": a.student.email,
            "phone": a.student.phone, "address": "-",
            "registered": a.submitted_at.strftime("%d %b %Y") if a.submitted_at else "",
            "exam_count": r.correct if r else 0, "avg_score": r.percentage if r else 0,
            "best_score": r.score if r else 0, "subscription": (r.status if r else "-").upper(),
        })
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    title = "Exam Results" + (f" - {Exam.query.get(exam_id).title}" if exam_id else "")
    generate_students_list_pdf(rows, path, title=title)
    return send_file(path, as_attachment=True, download_name="results.pdf")


# --------------------------------------------------------------------------
# Courses (Module 9)
# --------------------------------------------------------------------------
@admin_bp.route("/courses")
@login_required
def courses_list():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("admin/courses_list.html", courses=courses)


@admin_bp.route("/courses/new", methods=["GET", "POST"])
@login_required
def course_new():
    if request.method == "POST":
        course = Course(
            title=request.form["title"],
            description=request.form.get("description", ""),
            faculty=request.form.get("faculty", ""),
            subjects=request.form.get("subjects", ""),
            category_id=request.form.get("category_id", type=int) or None,
            duration_label=request.form.get("duration_label", ""),
            duration_days=int(request.form.get("duration_days") or 180),
            price=float(request.form.get("price") or 0),
            price_exam_only=float(request.form.get("price_exam_only") or 0),
            price_exam_pdf=float(request.form.get("price_exam_pdf") or 0),
            # price_class_only / price_videos_only / demo_video_url / course_video_url /
            # videos_as_separate_package are NOT settable from this form anymore -
            # video/live-class purchase tiers were removed in the Phase 1 cleanup.
            # They stay at their column defaults (0/None/False) for new courses.
            price_mock_tests=float(request.form.get("price_mock_tests") or 0),
            discount_percent=float(request.form.get("discount_percent") or 0),
            features=request.form.get("features", ""),
            # Package configuration
            pdfs_as_separate_package=bool(request.form.get("pdfs_as_separate_package")),
            exams_as_separate_package=bool(request.form.get("exams_as_separate_package")),
            mock_tests_as_separate_package=bool(request.form.get("mock_tests_as_separate_package")),
            created_by=current_user.id,
        )
        db.session.add(course)
        db.session.flush()
        if request.form.get("notify_students"):
            for account in StudentAccount.query.filter_by(is_active_flag=True).all():
                notify_student(
                    account.id, "New Course Available",
                    f"'{course.title}' is now available. Check it out!", type="new_course",
                )
        log_activity(current_user.email, "course_created", meta=course.title, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Course created.", "success")
        return redirect(url_for("admin.courses_list"))
    categories = Category.query.filter_by(is_active=True).order_by(Category.display_order, Category.name).all()
    return render_template("admin/course_form.html", course=None, categories=categories)


@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
@login_required
def course_edit(course_id):
    course = Course.query.get_or_404(course_id)
    if request.method == "POST":
        old_price = (course.price, course.price_exam_only, course.price_exam_pdf, course.discount_percent)
        course.title = request.form["title"]
        course.description = request.form.get("description", "")
        course.faculty = request.form.get("faculty", "")
        course.subjects = request.form.get("subjects", "")
        course.category_id = request.form.get("category_id", type=int) or None
        course.duration_label = request.form.get("duration_label", "")
        course.duration_days = int(request.form.get("duration_days") or 180)
        course.price = float(request.form.get("price") or 0)
        course.price_exam_only = float(request.form.get("price_exam_only") or 0)
        course.price_exam_pdf = float(request.form.get("price_exam_pdf") or 0)
        # price_class_only / price_videos_only / demo_video_url / course_video_url /
        # videos_as_separate_package are intentionally left untouched here - the
        # video/live-class Admin UI fields were removed in the Phase 1 cleanup,
        # so any pre-existing values are preserved as-is but can no longer be
        # edited or newly set through this form.
        course.price_mock_tests = float(request.form.get("price_mock_tests") or 0)
        course.discount_percent = float(request.form.get("discount_percent") or 0)
        course.features = request.form.get("features", "")
        # Package configuration
        course.pdfs_as_separate_package = bool(request.form.get("pdfs_as_separate_package"))
        course.exams_as_separate_package = bool(request.form.get("exams_as_separate_package"))
        course.mock_tests_as_separate_package = bool(request.form.get("mock_tests_as_separate_package"))
        course.is_active = bool(request.form.get("is_active"))
        new_price = (course.price, course.price_exam_only, course.price_exam_pdf, course.discount_percent)
        if new_price != old_price:
            log_activity(current_user.email, "course_price_changed",
                          meta=f"course={course.id} {old_price}->{new_price}", ip_address=get_client_ip(request))
        else:
            log_activity(current_user.email, "course_updated", meta=course.title, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Course updated.", "success")
        return redirect(url_for("admin.courses_list"))
    categories = Category.query.filter_by(is_active=True).order_by(Category.display_order, Category.name).all()
    return render_template("admin/course_form.html", course=course, categories=categories)


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@login_required
def course_delete(course_id):
    course = Course.query.get_or_404(course_id)
    log_activity(current_user.email, "course_deleted", meta=course.title, ip_address=get_client_ip(request))
    db.session.delete(course)
    db.session.commit()
    flash("Course deleted.", "info")
    return redirect(url_for("admin.courses_list"))


# --------------------------------------------------------------------------
# Phase 2 - Subscription Plans (Module 15)
# --------------------------------------------------------------------------
@admin_bp.route("/plans")
@login_required
def plans_list():
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.course_id, SubscriptionPlan.created_at.desc()).all()
    return render_template("admin/plans_list.html", plans=plans)


def _plan_from_form(plan, form):
    plan.course_id = form.get("course_id", type=int)
    plan.name = form.get("name", "").strip()
    plan.duration_value = form.get("duration_value", type=int) or 1
    plan.duration_unit = form.get("duration_unit") if form.get("duration_unit") in DURATION_UNITS else "months"
    plan.price = form.get("price", type=float) or 0
    plan.description = form.get("description") or None
    plan.includes_materials = bool(form.get("includes_materials"))
    plan.includes_mock_tests = bool(form.get("includes_mock_tests"))
    plan.includes_main_tests = bool(form.get("includes_main_tests"))
    plan.is_active = bool(form.get("is_active"))
    return plan


@admin_bp.route("/plans/new", methods=["GET", "POST"])
@login_required
def plan_new():
    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    if request.method == "POST":
        plan = SubscriptionPlan(created_by=current_user.id, is_active=True)
        _plan_from_form(plan, request.form)
        if not plan.course_id or not plan.name:
            flash("Course and plan name are required.", "warning")
            return render_template("admin/plan_form.html", plan=None, courses=courses)
        db.session.add(plan)
        log_activity(current_user.email, "plan_created", meta=plan.name, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Subscription plan created.", "success")
        return redirect(url_for("admin.plans_list"))
    return render_template("admin/plan_form.html", plan=None, courses=courses)


@admin_bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@login_required
def plan_edit(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    if request.method == "POST":
        old_price = plan.price
        _plan_from_form(plan, request.form)
        log_activity(current_user.email,
                      "plan_price_changed" if plan.price != old_price else "plan_updated",
                      meta=f"{plan.name} {old_price}->{plan.price}", ip_address=get_client_ip(request))
        db.session.commit()
        flash("Subscription plan updated.", "success")
        return redirect(url_for("admin.plans_list"))
    return render_template("admin/plan_form.html", plan=plan, courses=courses)


@admin_bp.route("/plans/<int:plan_id>/toggle", methods=["POST"])
@login_required
def plan_toggle(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    plan.is_active = not plan.is_active
    db.session.commit()
    flash(f"Plan {'activated' if plan.is_active else 'deactivated'}.", "info")
    return redirect(url_for("admin.plans_list"))


@admin_bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
@login_required
def plan_delete(plan_id):
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    if plan.purchases.count() > 0:
        # Never delete a plan with purchase history - deactivate instead,
        # so existing students' subscription records stay intact/readable.
        plan.is_active = False
        db.session.commit()
        flash("This plan has purchases against it, so it was deactivated instead of deleted.", "info")
    else:
        db.session.delete(plan)
        db.session.commit()
        flash("Plan deleted.", "info")
    return redirect(url_for("admin.plans_list"))


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------
@admin_bp.route("/coupons")
@login_required
def coupons_list():
    coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
    return render_template("admin/coupons_list.html", coupons=coupons, now=datetime.utcnow())


def _coupon_from_form(coupon, form):
    coupon.code = (form.get("code") or "").strip().upper()
    coupon.description = form.get("description") or None
    coupon.discount_type = form.get("discount_type") if form.get("discount_type") in ("percent", "fixed") else "percent"
    coupon.discount_value = form.get("discount_value", type=float) or 0
    coupon.max_discount_amount = form.get("max_discount_amount", type=float) or None
    coupon.min_purchase_amount = form.get("min_purchase_amount", type=float) or 0
    coupon.starts_at = _parse_date(form.get("starts_at"))
    coupon.expires_at = _parse_date(form.get("expires_at"))
    coupon.max_usage_total = form.get("max_usage_total", type=int) or None
    coupon.max_usage_per_user = form.get("max_usage_per_user", type=int) or 1
    course_ids = request.form.getlist("course_ids")
    coupon.course_ids = ",".join(course_ids) if course_ids else None
    coupon.is_active = bool(form.get("is_active"))
    return coupon


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


@admin_bp.route("/coupons/new", methods=["GET", "POST"])
@login_required
def coupon_new():
    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    if request.method == "POST":
        coupon = Coupon(is_active=True)
        _coupon_from_form(coupon, request.form)
        if not coupon.code or not coupon.discount_value:
            flash("Coupon code and discount value are required.", "warning")
            return render_template("admin/coupon_form.html", coupon=None, courses=courses)
        if Coupon.query.filter(db.func.upper(Coupon.code) == coupon.code).first():
            flash("A coupon with this code already exists.", "warning")
            return render_template("admin/coupon_form.html", coupon=None, courses=courses)
        db.session.add(coupon)
        log_activity(current_user.email, "coupon_created", meta=coupon.code, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Coupon created.", "success")
        return redirect(url_for("admin.coupons_list"))
    return render_template("admin/coupon_form.html", coupon=None, courses=courses)


@admin_bp.route("/coupons/<int:coupon_id>/edit", methods=["GET", "POST"])
@login_required
def coupon_edit(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    courses = Course.query.filter_by(is_active=True).order_by(Course.title).all()
    if request.method == "POST":
        _coupon_from_form(coupon, request.form)
        db.session.commit()
        flash("Coupon updated.", "success")
        return redirect(url_for("admin.coupons_list"))
    return render_template("admin/coupon_form.html", coupon=coupon, courses=courses)


@admin_bp.route("/coupons/<int:coupon_id>/toggle", methods=["POST"])
@login_required
def coupon_toggle(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    coupon.is_active = not coupon.is_active
    db.session.commit()
    flash(f"Coupon {'activated' if coupon.is_active else 'deactivated'}.", "info")
    return redirect(url_for("admin.coupons_list"))


@admin_bp.route("/coupons/<int:coupon_id>/delete", methods=["POST"])
@login_required
def coupon_delete(coupon_id):
    coupon = Coupon.query.get_or_404(coupon_id)
    if coupon.usage_count() > 0:
        # Never delete a coupon with redemption history - deactivate instead,
        # so past purchases/discount records stay intact and auditable.
        coupon.is_active = False
        db.session.commit()
        flash("This coupon has been redeemed before, so it was deactivated instead of deleted.", "info")
    else:
        db.session.delete(coupon)
        db.session.commit()
        flash("Coupon deleted.", "info")
    return redirect(url_for("admin.coupons_list"))


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------
@admin_bp.route("/categories")
@login_required
def categories_list():
    categories = Category.query.order_by(Category.display_order, Category.name).all()
    return render_template("admin/categories_list.html", categories=categories)


@admin_bp.route("/categories/new", methods=["GET", "POST"])
@login_required
def category_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Category name is required.", "warning")
            return render_template("admin/category_form.html", category=None)
        slug = Category.make_slug(name)
        if Category.query.filter((Category.name == name) | (Category.slug == slug)).first():
            flash("A category with this name already exists.", "warning")
            return render_template("admin/category_form.html", category=None)
        category = Category(
            name=name, slug=slug,
            description=request.form.get("description") or None,
            icon=request.form.get("icon") or "bi-mortarboard",
            display_order=request.form.get("display_order", type=int) or 0,
            is_active=True,
        )
        db.session.add(category)
        log_activity(current_user.email, "category_created", meta=name, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Category created.", "success")
        return redirect(url_for("admin.categories_list"))
    return render_template("admin/category_form.html", category=None)


@admin_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
@login_required
def category_edit(category_id):
    category = Category.query.get_or_404(category_id)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Category name is required.", "warning")
            return render_template("admin/category_form.html", category=category)
        category.name = name
        category.slug = Category.make_slug(name)
        category.description = request.form.get("description") or None
        category.icon = request.form.get("icon") or "bi-mortarboard"
        category.display_order = request.form.get("display_order", type=int) or 0
        category.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Category updated.", "success")
        return redirect(url_for("admin.categories_list"))
    return render_template("admin/category_form.html", category=category)


@admin_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def category_delete(category_id):
    category = Category.query.get_or_404(category_id)
    if category.courses.count() > 0:
        # Never delete a category with courses assigned - deactivate instead,
        # so those courses don't silently lose their grouping.
        category.is_active = False
        db.session.commit()
        flash("This category has courses assigned, so it was deactivated instead of deleted.", "info")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted.", "info")
    return redirect(url_for("admin.categories_list"))


# --------------------------------------------------------------------------
# Ticker Banner - the scrolling announcement strip under the navbar
# ("Today's Offer...", "Our Specialty..."). Admin-managed list of short
# items, each individually toggleable; rendered as a single continuous
# scroll on the public site (see public/_base.html + landing.css).
# --------------------------------------------------------------------------
@admin_bp.route("/ticker")
@login_required
def ticker_list():
    items = TickerItem.query.order_by(TickerItem.sort_order, TickerItem.created_at).all()
    return render_template("admin/ticker_list.html", items=items,
                            ticker_enabled=SiteSetting.get_bool("ticker_enabled", True))


@admin_bp.route("/ticker/toggle-global", methods=["POST"])
@login_required
def ticker_toggle_global():
    current = SiteSetting.get_bool("ticker_enabled", True)
    SiteSetting.set("ticker_enabled", "false" if current else "true")
    db.session.commit()
    flash(f"Ticker banner {'disabled' if current else 'enabled'} site-wide.", "info")
    return redirect(url_for("admin.ticker_list"))


@admin_bp.route("/ticker/new", methods=["GET", "POST"])
@login_required
def ticker_new():
    if request.method == "POST":
        text = (request.form.get("text") or "").strip()
        if not text:
            flash("Ticker text is required.", "warning")
            return render_template("admin/ticker_form.html", item=None)
        item = TickerItem(
            text=text,
            link=request.form.get("link") or None,
            icon=request.form.get("icon") or "bi-megaphone",
            sort_order=request.form.get("sort_order", type=int) or 0,
            is_active=True,
        )
        db.session.add(item)
        log_activity(current_user.email, "ticker_item_created", meta=text, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Ticker item added.", "success")
        return redirect(url_for("admin.ticker_list"))
    return render_template("admin/ticker_form.html", item=None)


@admin_bp.route("/ticker/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def ticker_edit(item_id):
    item = TickerItem.query.get_or_404(item_id)
    if request.method == "POST":
        text = (request.form.get("text") or "").strip()
        if not text:
            flash("Ticker text is required.", "warning")
            return render_template("admin/ticker_form.html", item=item)
        item.text = text
        item.link = request.form.get("link") or None
        item.icon = request.form.get("icon") or "bi-megaphone"
        item.sort_order = request.form.get("sort_order", type=int) or 0
        item.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Ticker item updated.", "success")
        return redirect(url_for("admin.ticker_list"))
    return render_template("admin/ticker_form.html", item=item)


@admin_bp.route("/ticker/<int:item_id>/toggle", methods=["POST"])
@login_required
def ticker_toggle(item_id):
    item = TickerItem.query.get_or_404(item_id)
    item.is_active = not item.is_active
    db.session.commit()
    return redirect(url_for("admin.ticker_list"))


@admin_bp.route("/ticker/<int:item_id>/delete", methods=["POST"])
@login_required
def ticker_delete(item_id):
    item = TickerItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash("Ticker item deleted.", "info")
    return redirect(url_for("admin.ticker_list"))


# --------------------------------------------------------------------------
# Platform Settings
# --------------------------------------------------------------------------
@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        SiteSetting.set("leaderboard_enabled", "true" if request.form.get("leaderboard_enabled") else "false")
        SiteSetting.set("leaderboard_anonymous", "true" if request.form.get("leaderboard_anonymous") else "false")
        SiteSetting.set("gst_enabled", "true" if request.form.get("gst_enabled") else "false")
        gst_percent = request.form.get("gst_percent", type=float)
        if gst_percent is not None and 0 <= gst_percent <= 100:
            SiteSetting.set("gst_percent", gst_percent)
        SiteSetting.set("gst_inclusive", "true" if request.form.get("gst_inclusive") else "false")
        SiteSetting.set("platform_fee_enabled", "true" if request.form.get("platform_fee_enabled") else "false")
        platform_fee_type = request.form.get("platform_fee_type", "fixed")
        if platform_fee_type in ("fixed", "percent"):
            SiteSetting.set("platform_fee_type", platform_fee_type)
        platform_fee_value = request.form.get("platform_fee_value", type=float)
        if platform_fee_value is not None and platform_fee_value >= 0:
            SiteSetting.set("platform_fee_value", platform_fee_value)
        refund_fee_policy = request.form.get("refund_platform_fee_policy", "exclude")
        if refund_fee_policy in ("exclude", "include"):
            SiteSetting.set("refund_platform_fee_policy", refund_fee_policy)
        SiteSetting.set("whatsapp_number", (request.form.get("whatsapp_number") or "").strip())
        SiteSetting.set("whatsapp_message", (request.form.get("whatsapp_message") or "").strip())
        SiteSetting.set("whatsapp_enabled", "true" if request.form.get("whatsapp_enabled") else "false")
        for field in ("support_received", "support_resolved", "payment_success", "exam_reminder", "subscription_expiry"):
            SiteSetting.set(f"wa_template_{field}", (request.form.get(f"wa_template_{field}") or "").strip())
        log_activity(current_user.email, "settings_updated", meta="leaderboard+gst+platform_fee+refund_policy+whatsapp", ip_address=get_client_ip(request))
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        leaderboard_enabled=SiteSetting.get_bool("leaderboard_enabled", True),
        leaderboard_anonymous=SiteSetting.get_bool("leaderboard_anonymous", False),
        gst_enabled=SiteSetting.get_bool("gst_enabled", False),
        gst_percent=SiteSetting.get("gst_percent", "18"),
        gst_inclusive=SiteSetting.get_bool("gst_inclusive", True),
        platform_fee_enabled=SiteSetting.get_bool("platform_fee_enabled", False),
        platform_fee_type=SiteSetting.get("platform_fee_type", "fixed"),
        platform_fee_value=SiteSetting.get("platform_fee_value", "0"),
        refund_platform_fee_policy=SiteSetting.get("refund_platform_fee_policy", "exclude"),
        whatsapp_number=SiteSetting.get("whatsapp_number", ""),
        whatsapp_message=SiteSetting.get("whatsapp_message", "Hi! I have a question about Kalyani Exam Hub."),
        whatsapp_enabled=SiteSetting.get_bool("whatsapp_enabled", False),
        wa_template_support_received=SiteSetting.get("wa_template_support_received", ""),
        wa_template_support_resolved=SiteSetting.get("wa_template_support_resolved", ""),
        wa_template_payment_success=SiteSetting.get("wa_template_payment_success", ""),
        wa_template_exam_reminder=SiteSetting.get("wa_template_exam_reminder", ""),
        wa_template_subscription_expiry=SiteSetting.get("wa_template_subscription_expiry", ""),
    )


# --------------------------------------------------------------------------
# Admin / Staff user management - restricted to role == "admin" (see
# admin_role_required above). Every mutation here guards against three ways
# this feature could damage the platform: acting on your own account,
# removing the last active admin, and - the Super Admin tier below - one
# admin sabotaging or promoting-past another admin they don't outrank.
# --------------------------------------------------------------------------
def _active_admin_count(exclude_id=None):
    q = User.query.filter_by(role="admin", is_active_flag=True)
    if exclude_id:
        q = q.filter(User.id != exclude_id)
    return q.count()


def super_admin_required(fn):
    """A regular admin can freely manage staff accounts, but only a Super
    Admin can touch another admin-role account (create one, edit one,
    change anyone's role to/from admin, or delete one) - otherwise any
    single admin could quietly demote, deactivate, or delete every other
    admin, including the platform owner."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_super_admin:
            flash("Only a Super Admin can manage other Admin-role accounts.", "danger")
            return redirect(url_for("admin.users_list"))
        return fn(*args, **kwargs)
    return wrapper


@admin_bp.route("/users")
@login_required
@admin_role_required
def users_list():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users_list.html", users=users)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@admin_role_required
def user_new():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        requested_role = request.form.get("role") if request.form.get("role") in ("admin", "staff", "teacher") else "staff"

        # A regular (non-super) admin can only create staff accounts - see
        # super_admin_required's docstring for why.
        if requested_role == "admin" and not current_user.is_super_admin:
            flash("Only a Super Admin can create another Admin-role account.", "danger")
            return redirect(url_for("admin.users_list"))

        if not email or not name or len(password) < 8:
            flash("Name, email, and a password of at least 8 characters are required.", "warning")
            return render_template("admin/user_form.html", user=None)
        if User.query.filter_by(email=email).first():
            flash("A user with this email already exists.", "warning")
            return render_template("admin/user_form.html", user=None)

        u = User(name=name, email=email, role=requested_role, is_active_flag=True)
        u.set_password(password)
        db.session.add(u)
        log_activity(current_user.email, "user_created", meta=f"{email} role={requested_role}", ip_address=get_client_ip(request))
        db.session.commit()
        flash("User created.", "success")
        return redirect(url_for("admin.users_list"))
    return render_template("admin/user_form.html", user=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_role_required
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == "POST":
        new_role = request.form.get("role") if request.form.get("role") in ("admin", "staff", "teacher") else user.role
        new_active = bool(request.form.get("is_active_flag"))

        # Guard 1: don't let someone demote/deactivate their own account -
        # that's how an admin accidentally locks themselves out mid-edit.
        if user.id == current_user.id and (new_role != "admin" or not new_active):
            flash("You can't change your own role or deactivate your own account.", "warning")
            return redirect(url_for("admin.user_edit", user_id=user.id))

        # Guard 2 (Super Admin tier): a regular admin can freely manage
        # staff accounts, but editing an existing admin, or promoting
        # someone TO admin, requires Super Admin - otherwise any admin
        # could demote/deactivate/reassign any other admin at will.
        touches_admin_tier = user.role == "admin" or new_role == "admin"
        if touches_admin_tier and user.id != current_user.id and not current_user.is_super_admin:
            flash("Only a Super Admin can edit another Admin-role account.", "danger")
            return redirect(url_for("admin.users_list"))

        # Guard 3: don't let the last active admin be demoted/deactivated -
        # that would lock everyone out of user management entirely.
        losing_admin_status = user.role == "admin" and (new_role != "admin" or not new_active)
        if losing_admin_status and _active_admin_count(exclude_id=user.id) == 0:
            flash("This is the last active Admin-role account - promote another user to Admin first.", "warning")
            return redirect(url_for("admin.user_edit", user_id=user.id))

        user.name = (request.form.get("name") or user.name).strip()
        old_role = user.role
        user.role = new_role
        user.is_active_flag = new_active
        # Only a Super Admin can grant/revoke Super Admin status, and never
        # on their own account (self-guard above already blocks self role/
        # active changes; this keeps that consistent for this flag too).
        if current_user.is_super_admin and user.id != current_user.id:
            user.is_super_admin = bool(request.form.get("is_super_admin"))
        new_password = request.form.get("password") or ""
        if new_password:
            if len(new_password) < 8:
                flash("Password must be at least 8 characters.", "warning")
                return render_template("admin/user_form.html", user=user)
            user.set_password(new_password)

        if old_role != new_role:
            log_activity(current_user.email, "user_role_changed",
                          meta=f"{user.email} {old_role}->{new_role}", ip_address=get_client_ip(request))
        else:
            log_activity(current_user.email, "user_updated", meta=user.email, ip_address=get_client_ip(request))
        db.session.commit()
        flash("User updated.", "success")
        return redirect(url_for("admin.users_list"))
    return render_template("admin/user_form.html", user=user)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_role_required
def user_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "warning")
        return redirect(url_for("admin.users_list"))
    if user.role == "admin" and not current_user.is_super_admin:
        flash("Only a Super Admin can delete another Admin-role account.", "danger")
        return redirect(url_for("admin.users_list"))
    if user.role == "admin" and _active_admin_count(exclude_id=user.id) == 0:
        flash("This is the last active Admin-role account and can't be deleted.", "warning")
        return redirect(url_for("admin.users_list"))
    log_activity(current_user.email, "user_deleted", meta=user.email, ip_address=get_client_ip(request))
    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "info")
    return redirect(url_for("admin.users_list"))


# --------------------------------------------------------------------------
# Phase 2 - Student Subscriptions management (extend/renew/activate/deactivate)
# --------------------------------------------------------------------------
@admin_bp.route("/subscriptions")
@login_required
def subscriptions_list():
    q = request.args.get("q", "").strip()
    query = CoursePurchase.query.filter_by(status="paid")
    if q:
        like = f"%{q}%"
        query = query.join(StudentAccount).filter(
            (StudentAccount.full_name.ilike(like)) | (StudentAccount.email.ilike(like))
        )
    purchases = query.order_by(CoursePurchase.expires_at.desc()).limit(300).all()
    return render_template("admin/subscriptions_list.html", purchases=purchases, q=q, now=datetime.utcnow())


@admin_bp.route("/subscriptions/<int:purchase_id>/extend", methods=["POST"])
@login_required
def subscription_extend(purchase_id):
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    days = request.form.get("days", type=int) or 30
    base = purchase.expires_at if (purchase.expires_at and purchase.expires_at > datetime.utcnow()) else datetime.utcnow()
    purchase.expires_at = base + timedelta(days=days)
    purchase.status = "paid"
    purchase.expiry_reminder_sent = False
    log_activity(current_user.email, "subscription_extended",
                  meta=f"purchase={purchase.id} days={days}", ip_address=get_client_ip(request))
    notify_student(
        purchase.student_account_id, "Plan Extended",
        f"Your plan for '{purchase.course.title}' was extended by {days} day(s) by the admin. "
        f"New expiry: {purchase.expires_at.strftime('%d %b %Y')}.",
        type="general",
    )
    db.session.commit()
    flash(f"Subscription extended by {days} day(s).", "success")
    return redirect(request.referrer or url_for("admin.subscriptions_list"))


@admin_bp.route("/subscriptions/<int:purchase_id>/renew", methods=["POST"])
@login_required
def subscription_renew(purchase_id):
    """Restart the subscription from today for its plan's/course's full duration."""
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    days = purchase.plan.duration_days() if purchase.plan_id and purchase.plan else purchase.course.duration_days
    purchase.status = "paid"
    purchase.expires_at = datetime.utcnow() + timedelta(days=days)
    purchase.expiry_reminder_sent = False
    log_activity(current_user.email, "subscription_renewed", meta=f"purchase={purchase.id}",
                  ip_address=get_client_ip(request))
    notify_student(
        purchase.student_account_id, "Plan Renewed",
        f"Your plan for '{purchase.course.title}' has been renewed. "
        f"New expiry: {purchase.expires_at.strftime('%d %b %Y')}.",
        type="general",
    )
    db.session.commit()
    flash("Subscription renewed.", "success")
    return redirect(request.referrer or url_for("admin.subscriptions_list"))


@admin_bp.route("/subscriptions/<int:purchase_id>/deactivate", methods=["POST"])
@login_required
def subscription_deactivate(purchase_id):
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    purchase.expires_at = datetime.utcnow()  # immediately expires -> access locks on next request
    log_activity(current_user.email, "subscription_deactivated", meta=f"purchase={purchase.id}",
                  ip_address=get_client_ip(request))
    db.session.commit()
    flash("Subscription deactivated - access is now locked.", "info")
    return redirect(request.referrer or url_for("admin.subscriptions_list"))


@admin_bp.route("/subscriptions/<int:purchase_id>/activate", methods=["POST"])
@login_required
def subscription_activate(purchase_id):
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    days = purchase.plan.duration_days() if purchase.plan_id and purchase.plan else purchase.course.duration_days
    purchase.status = "paid"
    purchase.expires_at = datetime.utcnow() + timedelta(days=days)
    purchase.expiry_reminder_sent = False
    purchase.verified_by = current_user.id
    purchase.verified_at = datetime.utcnow()
    purchase.invoice_number = purchase.invoice_number or generate_invoice_number()
    from app.services.coupon_service import record_redemption
    record_redemption(purchase)
    log_payment_transaction(purchase, gateway="manual", status="success", failure_reason=None)
    log_activity(current_user.email, "subscription_activated", meta=f"purchase={purchase.id}",
                  ip_address=get_client_ip(request))
    db.session.commit()
    flash("Subscription activated.", "success")
    return redirect(request.referrer or url_for("admin.subscriptions_list"))


# --------------------------------------------------------------------------
# Homepage Slider (Phase 6): admin-editable hero banner slides shown on "/".
# Images are stored under app/static/images/slides/ so the public landing
# page can serve them directly via url_for('static', ...).
# --------------------------------------------------------------------------
ALLOWED_SLIDE_IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}


def _save_slide_image(file_storage):
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_SLIDE_IMAGE_EXT:
        return None, "Only JPG, PNG or WEBP images are allowed."
    folder = os.path.join(current_app.static_folder, "images", "slides")
    os.makedirs(folder, exist_ok=True)
    new_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(folder, new_name))
    return new_name, None


@admin_bp.route("/slides")
@login_required
def slides_list():
    slides = HomeSlide.query.order_by(HomeSlide.sort_order.asc(), HomeSlide.id.asc()).all()
    return render_template("admin/slides_list.html", slides=slides)


@admin_bp.route("/slides/new", methods=["GET", "POST"])
@login_required
def slide_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        file = request.files.get("image")
        errors = []
        if not title:
            errors.append("Title is required.")
        image_name = None
        if not file or not file.filename:
            errors.append("Please choose a banner image.")
        else:
            image_name, err = _save_slide_image(file)
            if err:
                errors.append(err)

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/slide_form.html", slide=None, form=request.form)

        max_order = db.session.query(func.max(HomeSlide.sort_order)).scalar() or 0
        slide = HomeSlide(
            badge_text=request.form.get("badge_text", "").strip() or None,
            title=title,
            subtitle=request.form.get("subtitle", "").strip() or None,
            button_text=request.form.get("button_text", "").strip() or "Explore Now",
            button_link=request.form.get("button_link", "").strip() or None,
            image_filename=image_name,
            sort_order=int(request.form.get("sort_order") or (max_order + 1)),
            is_active=bool(request.form.get("is_active")),
        )
        db.session.add(slide)
        log_activity(current_user.email, "home_slide_created", meta=slide.title, ip_address=get_client_ip(request))
        db.session.commit()
        flash("Slide added to the homepage.", "success")
        return redirect(url_for("admin.slides_list"))
    return render_template("admin/slide_form.html", slide=None, form=None)


@admin_bp.route("/slides/<int:slide_id>/edit", methods=["GET", "POST"])
@login_required
def slide_edit(slide_id):
    slide = HomeSlide.query.get_or_404(slide_id)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        errors = []
        if not title:
            errors.append("Title is required.")

        file = request.files.get("image")
        new_image_name = None
        if file and file.filename:
            new_image_name, err = _save_slide_image(file)
            if err:
                errors.append(err)

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("admin/slide_form.html", slide=slide, form=request.form)

        slide.badge_text = request.form.get("badge_text", "").strip() or None
        slide.title = title
        slide.subtitle = request.form.get("subtitle", "").strip() or None
        slide.button_text = request.form.get("button_text", "").strip() or "Explore Now"
        slide.button_link = request.form.get("button_link", "").strip() or None
        slide.sort_order = int(request.form.get("sort_order") or slide.sort_order)
        slide.is_active = bool(request.form.get("is_active"))
        if new_image_name:
            old_path = os.path.join(current_app.static_folder, "images", "slides", slide.image_filename or "")
            slide.image_filename = new_image_name
            if slide.image_filename and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        db.session.commit()
        flash("Slide updated.", "success")
        return redirect(url_for("admin.slides_list"))
    return render_template("admin/slide_form.html", slide=slide, form=None)


@admin_bp.route("/slides/<int:slide_id>/delete", methods=["POST"])
@login_required
def slide_delete(slide_id):
    slide = HomeSlide.query.get_or_404(slide_id)
    img_path = os.path.join(current_app.static_folder, "images", "slides", slide.image_filename or "")
    db.session.delete(slide)
    db.session.commit()
    if slide.image_filename and os.path.exists(img_path):
        try:
            os.remove(img_path)
        except OSError:
            pass
    flash("Slide removed.", "info")
    return redirect(url_for("admin.slides_list"))


@admin_bp.route("/slides/<int:slide_id>/toggle", methods=["POST"])
@login_required
def slide_toggle(slide_id):
    slide = HomeSlide.query.get_or_404(slide_id)
    slide.is_active = not slide.is_active
    db.session.commit()
    flash(f"Slide {'shown on' if slide.is_active else 'hidden from'} the homepage.", "info")
    return redirect(url_for("admin.slides_list"))


@admin_bp.route("/slides/<int:slide_id>/move/<direction>", methods=["POST"])
@login_required
def slide_move(slide_id, direction):
    slide = HomeSlide.query.get_or_404(slide_id)
    if direction == "up":
        neighbor = (HomeSlide.query.filter(HomeSlide.sort_order < slide.sort_order)
                    .order_by(HomeSlide.sort_order.desc()).first())
    else:
        neighbor = (HomeSlide.query.filter(HomeSlide.sort_order > slide.sort_order)
                    .order_by(HomeSlide.sort_order.asc()).first())
    if neighbor:
        slide.sort_order, neighbor.sort_order = neighbor.sort_order, slide.sort_order
        db.session.commit()
    return redirect(url_for("admin.slides_list"))


# --------------------------------------------------------------------------
# Study Materials CMS (Phase 2): Course -> Subject -> Chapter -> Material,
# plus the secure PDF viewer access log (Phase 3).
# --------------------------------------------------------------------------
ALLOWED_MATERIAL_EXT = {"pdf"}
ALLOWED_MATERIAL_TYPES_NEEDING_FILE = {"notes", "assignment", "practice_sheet", "previous_paper"}


@admin_bp.route("/courses/<int:course_id>/subjects", methods=["GET", "POST"])
@login_required
def course_subjects(course_id):
    """Step 1 of the CMS flow: manage a course's study-material subjects
    (e.g. "Indian Polity") - separate from the exam question-bank Subject."""
    course = Course.query.get_or_404(course_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Please enter a subject name.", "warning")
        else:
            subject = MaterialSubject(course_id=course.id, name=name, order_index=len(course.material_subjects))
            db.session.add(subject)
            log_activity(current_user.email, "material_subject_created",
                          meta=f"course={course.id} name={name}", ip_address=get_client_ip(request))
            db.session.commit()
            flash(f"Subject '{name}' added.", "success")
        return redirect(url_for("admin.course_subjects", course_id=course.id))

    subjects = MaterialSubject.query.filter_by(course_id=course.id).order_by(MaterialSubject.order_index).all()
    return render_template("admin/course_subjects.html", course=course, subjects=subjects)


@admin_bp.route("/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required
def material_subject_delete(subject_id):
    subject = MaterialSubject.query.get_or_404(subject_id)
    course_id = subject.course_id
    db.session.delete(subject)  # cascades to chapters; materials keep chapter_id=NULL orphaned (kept, not deleted)
    db.session.commit()
    flash("Subject deleted.", "info")
    return redirect(url_for("admin.course_subjects", course_id=course_id))


@admin_bp.route("/subjects/<int:subject_id>/chapters", methods=["GET", "POST"])
@login_required
def subject_chapters(subject_id):
    """Step 2: manage a subject's chapters (e.g. "Fundamental Rights")."""
    subject = MaterialSubject.query.get_or_404(subject_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Please enter a chapter name.", "warning")
        else:
            chapter = MaterialChapter(subject_id=subject.id, name=name, order_index=subject.chapters.count())
            db.session.add(chapter)
            log_activity(current_user.email, "material_chapter_created",
                          meta=f"subject={subject.id} name={name}", ip_address=get_client_ip(request))
            db.session.commit()
            flash(f"Chapter '{name}' added.", "success")
        return redirect(url_for("admin.subject_chapters", subject_id=subject.id))

    chapters = subject.chapters.all()
    return render_template("admin/subject_chapters.html", subject=subject, chapters=chapters)


@admin_bp.route("/chapters/<int:chapter_id>/delete", methods=["POST"])
@login_required
def material_chapter_delete(chapter_id):
    chapter = MaterialChapter.query.get_or_404(chapter_id)
    subject_id = chapter.subject_id
    db.session.delete(chapter)
    db.session.commit()
    flash("Chapter deleted.", "info")
    return redirect(url_for("admin.subject_chapters", subject_id=subject_id))


@admin_bp.route("/courses/<int:course_id>/materials", methods=["GET", "POST"])
@login_required
def course_materials(course_id):
    """Step 3: upload a material into Course -> Subject -> Chapter, choosing
    a material type and an access duration (30/60/90/180/365 days, or
    lifetime = tied to the course subscription itself)."""
    course = Course.query.get_or_404(course_id)

    if request.method == "POST":
        chapter_id = request.form.get("chapter_id", type=int)
        title = request.form.get("title", "").strip()
        material_type = request.form.get("material_type", "notes")
        access_duration = request.form.get("access_duration", "lifetime")
        file = request.files.get("pdf_file")

        chapter = MaterialChapter.query.filter_by(id=chapter_id).first() if chapter_id else None
        errors = []
        if not title:
            errors.append("Please provide a title.")
        if not chapter or chapter.subject.course_id != course.id:
            errors.append("Please select a valid Subject and Chapter for this course.")
        # Video Class is deliberately excluded here - the video/live-class
        # feature was disabled in the Phase 1 cleanup, so admins can no
        # longer create new video materials (existing ones, if any, are
        # left untouched in the database).
        if material_type not in dict(CREATABLE_MATERIAL_TYPES):
            errors.append("Please select a valid material type.")

        new_name = None
        if material_type in ALLOWED_MATERIAL_TYPES_NEEDING_FILE:
            if not file or not file.filename:
                errors.append("Please attach a PDF file.")
            else:
                ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
                if ext not in ALLOWED_MATERIAL_EXT:
                    errors.append("Only PDF files are allowed.")

        if errors:
            for e in errors:
                flash(e, "danger" if "valid" in e or "PDF" in e else "warning")
            return redirect(url_for("admin.course_materials", course_id=course.id))

        if file and file.filename:
            folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "course_materials")
            os.makedirs(folder, exist_ok=True)
            new_name = f"{uuid.uuid4().hex}.pdf"
            file.save(os.path.join(folder, new_name))

        material = CourseMaterial(
            course_id=course.id, chapter_id=chapter.id, title=title,
            material_type=material_type, file_path=new_name,
            access_duration_days=None if access_duration == "lifetime" else int(access_duration),
            order_index=len(course.materials), uploaded_by=current_user.id,
        )
        db.session.add(material)
        log_activity(current_user.email, "course_material_uploaded",
                      meta=f"course={course.id} chapter={chapter.id} title={title} type={material_type}",
                      ip_address=get_client_ip(request))
        db.session.commit()
        flash(f"{material.material_type_label()} '{title}' uploaded to {chapter.subject.name} → {chapter.name}.", "success")
        return redirect(url_for("admin.course_materials", course_id=course.id))

    subjects = (
        MaterialSubject.query.filter_by(course_id=course.id)
        .order_by(MaterialSubject.order_index).all()
    )
    materials = (
        CourseMaterial.query.filter_by(course_id=course.id)
        .order_by(CourseMaterial.order_index).all()
    )
    return render_template(
        "admin/course_materials.html", course=course, materials=materials, subjects=subjects,
        material_types=CREATABLE_MATERIAL_TYPES, access_choices=ACCESS_DURATION_CHOICES,
    )


@admin_bp.route("/materials/<int:material_id>/delete", methods=["POST"])
@login_required
def material_delete(material_id):
    material = CourseMaterial.query.get_or_404(material_id)
    course_id = material.course_id
    if material.file_path:
        folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "course_materials")
        try:
            os.remove(os.path.join(folder, material.file_path))
        except OSError:
            pass
    db.session.delete(material)
    db.session.commit()
    flash("Material deleted.", "info")
    return redirect(url_for("admin.course_materials", course_id=course_id))


@admin_bp.route("/materials/access-log")
@login_required
def material_access_log():
    """Phase 3: 'Log every PDF access.' - admin-visible audit trail."""
    page = request.args.get("page", 1, type=int)
    pagination = (
        MaterialAccessLog.query.order_by(MaterialAccessLog.created_at.desc())
        .paginate(page=page, per_page=50, error_out=False)
    )
    return render_template("admin/material_access_log.html", pagination=pagination, logs=pagination.items)


# --------------------------------------------------------------------------
# Payments (Module 10) - manual UPI reference verification
# --------------------------------------------------------------------------
@admin_bp.route("/payments")
@login_required
def payments_list():
    status = request.args.get("status", "pending")
    query = CoursePurchase.query
    if status and status != "all":
        query = query.filter_by(status=status)
    purchases = query.order_by(CoursePurchase.created_at.desc()).limit(300).all()
    return render_template("admin/payments_list.html", purchases=purchases, status=status)


@admin_bp.route("/payments/<int:purchase_id>/history")
@login_required
def payment_transactions(purchase_id):
    """Full append-only transaction history for one purchase - see
    PaymentTransaction in models.py for why this exists (a retry after a
    failed payment used to silently overwrite the previous attempt's
    order/payment IDs on CoursePurchase itself, losing that history)."""
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    transactions = purchase.transactions.all()
    return render_template("admin/payment_transactions.html", purchase=purchase, transactions=transactions)


@admin_bp.route("/payments/<int:purchase_id>/verify", methods=["POST"])
@login_required
def payment_verify(purchase_id):
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    if purchase.status != "paid":
        purchase.status = "paid"
        purchase.verified_by = current_user.id
        purchase.verified_at = datetime.utcnow()
        purchase.invoice_number = purchase.invoice_number or generate_invoice_number()
        purchase.expires_at = datetime.utcnow() + timedelta(
            days=(purchase.plan.duration_days() if purchase.plan_id and purchase.plan else purchase.course.duration_days)
        )
        purchase.expiry_reminder_sent = False
        from app.services.coupon_service import record_redemption
        record_redemption(purchase)
        log_payment_transaction(purchase, gateway="upi", status="success")
        notify_student(
            purchase.student_account_id,
            "Payment Confirmed",
            f"Your payment for '{purchase.course.title}' has been verified. Course activated!",
            type="payment_success",
        )
        log_activity(current_user.email, "payment_verified", meta=f"purchase={purchase.id}",
                      ip_address=get_client_ip(request))
        db.session.commit()
        flash("Payment verified and course activated for the student.", "success")
    return redirect(url_for("admin.payments_list"))


@admin_bp.route("/payments/<int:purchase_id>/refund")
@login_required
def payment_refund_form(purchase_id):
    """Confirmation screen before refunding - shows the computed default
    amount (per the configured platform-fee policy) so the admin sees
    exactly what's about to happen and can still adjust it for a genuine
    partial refund before confirming."""
    from app.services import refund_service
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    if purchase.status != "paid":
        flash("Only a paid purchase can be refunded.", "warning")
        return redirect(url_for("admin.payments_list"))
    return render_template(
        "admin/refund_form.html", purchase=purchase,
        default_amount=refund_service.default_refund_amount(purchase),
        policy=refund_service.refund_policy(),
    )


@admin_bp.route("/payments/<int:purchase_id>/refund", methods=["POST"])
@login_required
def payment_refund(purchase_id):
    from app.services import refund_service
    purchase = CoursePurchase.query.get_or_404(purchase_id)

    amount = request.form.get("amount", type=float)
    reason = request.form.get("reason", "")

    ok, error = refund_service.process_refund(purchase, amount, reason, current_user)
    if not ok:
        flash(error, "danger")
        return redirect(url_for("admin.payment_refund_form", purchase_id=purchase.id))

    log_activity(current_user.email, "payment_refunded",
                 meta=f"purchase={purchase.id} amount={amount}", ip_address=get_client_ip(request))
    db.session.commit()
    flash(f"Refunded Rs. {amount:.2f} and revoked access for this purchase.", "success")
    return redirect(url_for("admin.payments_list"))


@admin_bp.route("/payments/<int:purchase_id>/reject", methods=["POST"])
@login_required
def payment_reject(purchase_id):
    purchase = CoursePurchase.query.get_or_404(purchase_id)
    purchase.status = "failed"
    purchase.verified_by = current_user.id
    purchase.verified_at = datetime.utcnow()
    log_payment_transaction(purchase, gateway="upi", status="failed", failure_reason="Rejected by admin after manual review")
    notify_student(
        purchase.student_account_id, "Payment Failed",
        f"Your payment for '{purchase.course.title}' could not be verified. "
        f"Please contact support or try again.",
        type="payment_failed",
    )
    log_activity(current_user.email, "payment_rejected", meta=f"purchase={purchase.id}", ip_address=get_client_ip(request))
    db.session.commit()
    flash("Payment marked as failed.", "info")
    return redirect(url_for("admin.payments_list"))


# --------------------------------------------------------------------------
# WhatsApp Support Inbox (Module 15) - management side. Every route here
# is @login_required (same admin-auth guard used throughout this file -
# see "Do NOT create duplicate authentication" in the brief); there is no
# separate student-facing view of conversation contents anywhere in the
# app, so "a student must never access another student's WhatsApp
# conversation" is satisfied by there being no such route to misuse in
# the first place, not by an extra check bolted onto this one.
# --------------------------------------------------------------------------
@admin_bp.route("/whatsapp")
@login_required
def whatsapp_inbox():
    status = request.args.get("status", "").strip()
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()

    query = WhatsAppConversation.query.filter(WhatsAppConversation.whatsapp_number != "")
    if status and status in WHATSAPP_CONVERSATION_STATUSES:
        query = query.filter_by(status=status)
    if category and category in WHATSAPP_CATEGORIES:
        query = query.filter_by(category=category)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(StudentAccount, WhatsAppConversation.student_account_id == StudentAccount.id).filter(
            (WhatsAppConversation.whatsapp_number.ilike(like))
            | (WhatsAppConversation.support_reference.ilike(like))
            | (StudentAccount.full_name.ilike(like))
            | (StudentAccount.email.ilike(like))
        )

    conversations = query.order_by(WhatsAppConversation.last_message_at.desc()).limit(300).all()
    latest_by_conv_id = {
        c.id: (c.messages.order_by(WhatsAppMessage.created_at.desc()).first())
        for c in conversations
    }

    # Status counts for the summary cards - computed against ALL claimed
    # conversations (not the current filter), same as the Pending/Paid/
    # Failed counts pattern would if this page had one.
    counts = {
        s: WhatsAppConversation.query.filter(WhatsAppConversation.whatsapp_number != "").filter_by(status=s).count()
        for s in WHATSAPP_CONVERSATION_STATUSES
    }

    return render_template(
        "admin/whatsapp_inbox.html", conversations=conversations, counts=counts,
        latest_by_conv_id=latest_by_conv_id,
        status=status, category=category, q=q,
        statuses=WHATSAPP_CONVERSATION_STATUSES, categories=WHATSAPP_CATEGORIES,
    )


@admin_bp.route("/whatsapp/<int:conversation_id>")
@login_required
def whatsapp_conversation_detail(conversation_id):
    convo = WhatsAppConversation.query.get_or_404(conversation_id)
    messages = convo.messages.all()
    admins = User.query.filter_by(is_active_flag=True).order_by(User.name).all()
    # Same account data the bot itself is allowed to show the student (see
    # whatsapp_bot_service._account_status_summary) - showing it here too
    # means the admin doesn't have to context-switch to the Students page
    # just to see what plan/payment this conversation is actually about.
    student_purchases = []
    if convo.student:
        student_purchases = (
            CoursePurchase.query.filter_by(student_account_id=convo.student.id)
            .order_by(CoursePurchase.created_at.desc()).limit(5).all()
        )
    return render_template(
        "admin/whatsapp_conversation.html", convo=convo, messages=messages, admins=admins,
        statuses=WHATSAPP_CONVERSATION_STATUSES, categories=WHATSAPP_CATEGORIES,
        whatsapp_configured=whatsapp_service.is_configured(),
        student_purchases=student_purchases,
    )


@admin_bp.route("/whatsapp/<int:conversation_id>/reply", methods=["POST"])
@login_required
def whatsapp_conversation_reply(conversation_id):
    convo = WhatsAppConversation.query.get_or_404(conversation_id)
    text = (request.form.get("message") or "").strip()
    if not text:
        flash("Reply message can't be empty.", "warning")
        return redirect(url_for("admin.whatsapp_conversation_detail", conversation_id=convo.id))

    if not whatsapp_service.is_configured():
        flash("WhatsApp support is temporarily unavailable (not configured). Please use another channel to reach this student.", "danger")
        return redirect(url_for("admin.whatsapp_conversation_detail", conversation_id=convo.id))

    msg_id = whatsapp_service.send_text_message(convo.whatsapp_number, text)
    db.session.add(WhatsAppMessage(
        conversation_id=convo.id, direction="out", message_type="text",
        message_text=text, whatsapp_message_id=msg_id, sent_by=current_user.id,
    ))
    # A human has now actually engaged - stop any further bot auto-replies
    # for this conversation, and the ball is in the student's court.
    convo.bot_state = None
    convo.status = "WAITING_FOR_STUDENT"
    convo.last_message_at = datetime.utcnow()
    log_activity(current_user.email, "whatsapp_reply_sent", meta=f"conversation={convo.id}", ip_address=get_client_ip(request))
    db.session.commit()

    if msg_id:
        flash("Reply sent.", "success")
    else:
        flash("Reply was logged, but WhatsApp delivery failed - check the number/configuration and try again.", "warning")
    return redirect(url_for("admin.whatsapp_conversation_detail", conversation_id=convo.id))


@admin_bp.route("/whatsapp/<int:conversation_id>/note", methods=["POST"])
@login_required
def whatsapp_conversation_note(conversation_id):
    """Internal note - visible only in the admin inbox, NEVER sent to the
    student over WhatsApp (requirement 18: "Internal notes must NEVER be
    sent to the student")."""
    convo = WhatsAppConversation.query.get_or_404(conversation_id)
    text = (request.form.get("note") or "").strip()
    if text:
        db.session.add(WhatsAppMessage(
            conversation_id=convo.id, direction="out", message_type="text",
            message_text=text, is_internal_note=True, sent_by=current_user.id,
        ))
        db.session.commit()
    return redirect(url_for("admin.whatsapp_conversation_detail", conversation_id=convo.id))


@admin_bp.route("/whatsapp/<int:conversation_id>/update", methods=["POST"])
@login_required
def whatsapp_conversation_update(conversation_id):
    """Status / priority / category / assignment changes from the
    conversation detail page's sidebar controls."""
    convo = WhatsAppConversation.query.get_or_404(conversation_id)

    status = request.form.get("status")
    if status in WHATSAPP_CONVERSATION_STATUSES:
        # Best-effort closing message when management resolves a
        # conversation - uses the configured "Support Resolved" template
        # if one's set (see Admin > Settings > WhatsApp Message
        # Templates), since this may be sent outside the 24h customer-
        # service window the Cloud API requires for a plain text message.
        # Never raises and never blocks the status change either way -
        # see whatsapp_service.send_template_message()'s never-raises
        # contract.
        if status == "RESOLVED" and convo.status != "RESOLVED":
            template_name = SiteSetting.get("wa_template_support_resolved", "").strip()
            if template_name and whatsapp_service.is_configured():
                msg_id = whatsapp_service.send_template_message(convo.whatsapp_number, template_name)
                db.session.add(WhatsAppMessage(
                    conversation_id=convo.id, direction="out", message_type="template",
                    message_text=f"[template: {template_name}] Your support request has been resolved.",
                    whatsapp_message_id=msg_id, sent_by=current_user.id,
                ))
        convo.status = status

    priority = request.form.get("priority")
    if priority in ("low", "normal", "high"):
        convo.priority = priority

    category = request.form.get("category")
    if category in WHATSAPP_CATEGORIES:
        convo.category = category

    assigned_to = request.form.get("assigned_to", type=int)
    if assigned_to:
        if User.query.get(assigned_to):
            convo.assigned_to = assigned_to
    elif request.form.get("assigned_to") == "":
        convo.assigned_to = None

    log_activity(current_user.email, "whatsapp_conversation_updated", meta=f"conversation={convo.id}", ip_address=get_client_ip(request))
    db.session.commit()
    flash("Conversation updated.", "success")
    return redirect(url_for("admin.whatsapp_conversation_detail", conversation_id=convo.id))
