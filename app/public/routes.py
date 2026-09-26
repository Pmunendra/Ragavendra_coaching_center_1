import json
import random
import re
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify, current_app, abort,
)
from flask_login import login_required

from app.extensions import db, limiter, csrf
from app.models import (
    ExamLink, Exam, Student, StudentAttempt, Answer, log_activity,
    StudentAccount, notify_student, Course, CoursePurchase, HomeSlide,
    record_violation, Category,
)
from app.services.device_service import parse_user_agent, get_client_ip
from app.services.grading_service import finalize_attempt
from app.services.pdf_service import generate_result_pdf
from app.services.email_service import send_result_email
from app.services.access_service import can_access_exam, ACCESS_DENIAL_MESSAGES
from app.utils import create_attempt_token, decode_attempt_token


def _current_student_account():
    """Phase 2: mirrors student.current_student() without importing the
    student blueprint (avoids a circular import) - both read the same
    session["student_id"] set by student.login."""
    sid = session.get("student_id")
    if not sid:
        return None
    return StudentAccount.query.get(sid)

public_bp = Blueprint("public", __name__, template_folder="../templates")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+\-\s]{7,15}$")


# --------------------------------------------------------------------------
# Marketing site (Phase 1) - this is what "/" shows now, for everyone.
# Logged-in students get a personalised welcome strip above the same
# marketing sections; admins are never auto-redirected here or away from
# here - /admin/login is the only admin entrypoint.
# --------------------------------------------------------------------------
@public_bp.route("/")
def landing():
    slides = HomeSlide.query.filter_by(is_active=True).order_by(HomeSlide.sort_order.asc(), HomeSlide.id.asc()).all()

    featured_courses = (
        Course.query.filter_by(is_active=True).order_by(Course.created_at.desc()).limit(6).all()
    )

    account = None
    sid = session.get("student_id")
    if sid:
        account = StudentAccount.query.get(sid)

    active_course_ids = set()
    recent_purchase = None
    if account:
        active_course_ids = {
            p.course_id for p in account.purchases
            if p.status == "paid" and p.expires_at and p.expires_at > datetime.utcnow()
        }
        recent_purchase = (
            CoursePurchase.query.filter_by(student_account_id=account.id, status="paid")
            .order_by(CoursePurchase.created_at.desc()).first()
        )

    return render_template(
        "public/landing.html",
        slides=slides,
        featured_courses=featured_courses,
        active_course_ids=active_course_ids,
        account=account,
        recent_purchase=recent_purchase,
    )


@public_bp.route("/courses")
def courses_page():
    category_slug = request.args.get("category", "").strip()
    query = Course.query.filter_by(is_active=True)
    selected_category = None
    if category_slug:
        selected_category = Category.query.filter_by(slug=category_slug, is_active=True).first()
        if selected_category:
            query = query.filter(Course.category_id == selected_category.id)
    courses = query.order_by(Course.created_at.desc()).all()
    categories = Category.query.filter_by(is_active=True).order_by(Category.display_order, Category.name).all()

    account = None
    sid = session.get("student_id")
    if sid:
        account = StudentAccount.query.get(sid)

    active_course_ids = set()
    if account:
        active_course_ids = {
            p.course_id for p in account.purchases
            if p.status == "paid" and p.expires_at and p.expires_at > datetime.utcnow()
        }

    return render_template(
        "public/courses.html",
        courses=courses,
        account=account,
        active_course_ids=active_course_ids,
        categories=categories,
        selected_category=selected_category,
    )


@public_bp.route("/mock-tests")
def mock_tests_page():
    return render_template("public/mock_tests.html")


@public_bp.route("/study-materials")
def study_materials_page():
    return render_template("public/study_materials.html")


@public_bp.route("/about")
def about_page():
    return render_template("public/about.html")


@public_bp.route("/contact", methods=["GET"])
def contact_page():
    return render_template("public/contact.html")


@public_bp.route("/privacy-policy")
def privacy_policy():
    return render_template("public/privacy.html")


@public_bp.route("/refund-policy")
def refund_policy():
    return render_template("public/refund.html")


@public_bp.route("/terms")
def terms():
    return render_template("public/terms.html")


@public_bp.route("/contact", methods=["POST"])
@limiter.limit("10 per minute")
def contact_submit():
    """Lightweight contact-form handler (Phase 1: logs the enquiry; wire up
    email/CRM delivery later without changing the form or this endpoint)."""
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()
    if not name or not email or not message:
        flash("Please fill in your name, email and message.", "warning")
    else:
        log_activity("guest", "contact_enquiry", meta=f"name={name} email={email}")
        db.session.commit()
        flash("Thanks! Our counsellors will reach out to you shortly.", "success")
    return redirect(url_for("public.contact_page"))


def _get_active_link_or_404(token):
    link = ExamLink.query.filter_by(token=token).first()
    if not link:
        abort(404)
    return link


@public_bp.route("/exam/<token>", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def exam_landing(token):
    link = _get_active_link_or_404(token)
    exam = link.exam

    if not link.is_usable():
        return render_template("exam/link_unavailable.html", link=link, exam=exam)

    # Phase 2 - server-side subscription / schedule / attempt-limit gate.
    # This runs on EVERY visit to this URL, so a direct link after expiry,
    # a changed exam id, or simply not being logged in is denied here
    # regardless of what the client sends - never relies on frontend JS.
    account = _current_student_account()
    allowed, reason = can_access_exam(exam, account)
    if not allowed:
        if reason == "login_required":
            flash("Please log in to your student account to take this test.", "warning")
            return redirect(url_for("student.login", next=request.path))
        return render_template(
            "exam/link_unavailable.html", link=link, exam=exam,
            access_denied_message=ACCESS_DENIAL_MESSAGES.get(reason, "This test isn't available to you right now."),
        )

    error = None
    if request.method == "POST":
        # optional link password gate
        if link.password_hash and not link.check_password(request.form.get("link_password")):
            error = "Incorrect exam password."
        else:
            full_name = request.form.get("full_name", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip().lower()
            college = request.form.get("college", "").strip()
            city = request.form.get("city", "").strip()
            declaration = request.form.get("declaration")

            if not full_name or not phone or not email:
                error = "Full name, phone, and email are required."
            elif not EMAIL_RE.match(email):
                error = "Please enter a valid email address."
            elif not PHONE_RE.match(phone):
                error = "Please enter a valid phone number."
            elif not declaration:
                error = "You must accept the declaration to continue."

            if not error:
                student = Student(
                    full_name=full_name, phone=phone, email=email,
                    college=college or None, city=city or None,
                )
                db.session.add(student)
                db.session.flush()

                ua_info = parse_user_agent(request.headers.get("User-Agent"))
                attempt = StudentAttempt(
                    student_id=student.id, exam_id=exam.id, exam_link_id=link.id,
                    status="details_submitted",
                    ip_address=get_client_ip(request),
                    user_agent=request.headers.get("User-Agent", "")[:500],
                    browser=ua_info["browser"], os=ua_info["os"], device=ua_info["device"],
                    student_account_id=account.id if account else None,
                )
                db.session.add(attempt)
                log_activity(f"student:{student.id}", "details_submitted",
                              meta=f"exam={exam.id}", ip_address=get_client_ip(request))
                db.session.commit()

                session[f"attempt_{attempt.id}_verified"] = True
                return redirect(url_for("public.instructions", attempt_id=attempt.id))

    prefill = {"full_name": account.full_name, "email": account.email, "phone": account.phone,
               "college": account.college_name} if account else {}
    return render_template("exam/student_details.html", link=link, exam=exam, error=error, prefill=prefill)


def _require_attempt(attempt_id):
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    if not session.get(f"attempt_{attempt.id}_verified"):
        abort(403)
    return attempt


@public_bp.route("/exam/attempt/<int:attempt_id>/instructions")
def instructions(attempt_id):
    attempt = _require_attempt(attempt_id)
    if attempt.status not in ("details_submitted",):
        if attempt.status in ("submitted", "auto_submitted"):
            return redirect(url_for("public.result_page", attempt_id=attempt.id))
        return redirect(url_for("public.take_exam", attempt_id=attempt.id))
    return render_template("exam/instructions.html", attempt=attempt, exam=attempt.exam)


@public_bp.route("/exam/attempt/<int:attempt_id>/start", methods=["POST"])
def start_exam(attempt_id):
    attempt = _require_attempt(attempt_id)
    exam = attempt.exam

    # Phase 2 - re-check access at start time too (defense in depth): a
    # plan could have expired, or a scheduled window could have closed,
    # in the seconds between the details form and clicking Start.
    account = _current_student_account()
    allowed, reason = can_access_exam(exam, account)
    if not allowed:
        flash(ACCESS_DENIAL_MESSAGES.get(reason, "This test is no longer available to you."), "danger")
        return redirect(url_for("student.mock_tests") if account else url_for("public.landing"))

    if attempt.status == "details_submitted":
        attempt.started_at = datetime.utcnow()
        attempt.due_at = attempt.started_at + timedelta(minutes=exam.duration_minutes)
        attempt.status = "in_progress"

        question_ids = [q.id for q in exam.questions.order_by("order_index")]
        if exam.shuffle_questions:
            random.shuffle(question_ids)
        attempt.question_order = json.dumps(question_ids)

        for qid in question_ids:
            db.session.add(Answer(attempt_id=attempt.id, question_id=qid, status="not_visited"))

        db.session.commit()

    link = ExamLink.query.get(attempt.exam_link_id)
    token = create_attempt_token(attempt.id, link.token if link else "")
    session[f"attempt_{attempt.id}_token"] = token
    return redirect(url_for("public.take_exam", attempt_id=attempt.id))


@public_bp.route("/exam/attempt/<int:attempt_id>/take")
def take_exam(attempt_id):
    attempt = _require_attempt(attempt_id)
    if attempt.status not in ("in_progress",):
        if attempt.status in ("submitted", "auto_submitted"):
            return redirect(url_for("public.result_page", attempt_id=attempt.id))
        return redirect(url_for("public.instructions", attempt_id=attempt.id))

    exam = attempt.exam
    question_ids = json.loads(attempt.question_order or "[]")
    questions_by_id = {q.id: q for q in exam.questions}
    ordered_questions = [questions_by_id[qid] for qid in question_ids if qid in questions_by_id]

    answers_by_qid = {a.question_id: a for a in attempt.answers}

    # Build a JSON-serializable payload for the JS exam engine
    payload_questions = []
    for q in ordered_questions:
        options = [{"id": o.id, "text": o.option_text} for o in q.options]
        if exam.shuffle_options and q.question_type in ("single_choice", "multiple_choice", "image_based"):
            random.shuffle(options)
        payload_questions.append({
            "id": q.id,
            "type": q.question_type,
            "text": q.question_text,
            "image_url": q.image_url,
            "passage": q.passage_text,
            "marks": q.marks,
            "options": options,
            "answer": _serialize_answer(answers_by_qid.get(q.id)),
        })

    token = session.get(f"attempt_{attempt.id}_token") or create_attempt_token(attempt.id, "")
    session[f"attempt_{attempt.id}_token"] = token

    remaining_seconds = max(0, int((attempt.due_at - datetime.utcnow()).total_seconds())) if attempt.due_at else 0

    return render_template(
        "exam/take_exam.html", attempt=attempt, exam=exam,
        questions_json=json.dumps(payload_questions),
        remaining_seconds=remaining_seconds, attempt_token=token,
    )


def _serialize_answer(answer):
    if not answer:
        return {"status": "not_visited", "selected": [], "integer": None, "text": None}
    return {
        "status": answer.status,
        "selected": answer.selected_ids_list(),
        "integer": answer.integer_answer,
        "text": answer.text_answer,
    }


@public_bp.route("/exam/attempt/<int:attempt_id>/submit", methods=["POST"])
def submit_exam(attempt_id):
    attempt = _require_attempt(attempt_id)
    if attempt.status not in ("in_progress",):
        return redirect(url_for("public.result_page", attempt_id=attempt.id))

    if request.is_json:
        auto = bool((request.get_json(silent=True) or {}).get("auto"))
    else:
        auto = request.form.get("auto") == "1"
    _finalize_and_dispatch(attempt, auto_submitted=auto)
    return redirect(url_for("public.result_page", attempt_id=attempt.id))


def _finalize_and_dispatch(attempt, auto_submitted=False):
    attempt.auto_submitted = bool(auto_submitted)
    result = finalize_attempt(attempt, auto_submitted=auto_submitted)
    log_activity(f"student:{attempt.student_id}", "exam_submitted",
                  meta=f"attempt={attempt.id} auto={auto_submitted}")
    db.session.commit()

    try:
        pdf_path = generate_result_pdf(attempt, current_app.config["GENERATED_PDF_FOLDER"])
        result.pdf_path = pdf_path
        db.session.commit()
        send_result_email(attempt, pdf_path=pdf_path)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Email/PDF dispatch failed: %s", exc)

    # Module 14: notify the student's registered account (if they have one)
    # that their result is ready - purely additive, never blocks submission.
    try:
        account = StudentAccount.query.filter_by(email=attempt.student.email).first()
        if account:
            # Phase 2 (spec #20): never surface score/percentage to the
            # student in-app - just confirm the exam was recorded.
            notify_student(
                account.id, "Exam Submitted",
                f"Your response for '{attempt.exam.title}' has been recorded successfully.",
                type="result_published",
            )
            db.session.commit()
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("Result notification failed: %s", exc)

    # Phase 4: AI Performance Analysis - subject-wise breakdown, weak/strong
    # topics, expected rank and a personalised study plan. Additive and
    # never blocks submission if it fails for any reason.
    try:
        from app.services.ai_analysis_service import build_performance_analysis
        build_performance_analysis(attempt)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("AI performance analysis failed: %s", exc)

    return result


# Legacy per-type counters (kept for backward compatibility / display) mapped
# to which unified-warning event name each one now reports as.
_UNIFIED_VIOLATION_TYPES = {
    "tab_switch", "fullscreen_exit", "blur", "devtools",
    "face_missing", "multiple_faces", "looking_away", "voice_detected",
}


@public_bp.route("/exam/attempt/<int:attempt_id>/track", methods=["POST"])
@csrf.exempt
def track_event(attempt_id):
    """Phase 2 - unified 3-warning system (spec #15-17). ALL violation
    types (tab switch, fullscreen exit, face missing, multiple faces,
    looking away, voice detected, ...) increment ONE server-side counter
    per attempt. The counter, and the auto-submit decision, are computed
    here - never trusted from the client - so refresh, localStorage
    tampering, or direct API calls can't reset or bypass it."""
    attempt = _require_attempt(attempt_id)
    if attempt.status != "in_progress":
        return jsonify({"ok": False, "error": "not_in_progress"}), 409

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")

    # Keep the legacy per-type counters populated too (admin UI / exports
    # already read these), purely additive - they no longer drive behavior.
    if event == "tab_switch":
        attempt.tab_switch_count += 1
    elif event == "fullscreen_exit":
        attempt.fullscreen_exit_count += 1
    elif event == "devtools":
        attempt.devtools_warning_count += 1
    elif event == "blur":
        attempt.tab_switch_count += 1

    exam = attempt.exam
    strict = bool(payload.get("strict"))

    if strict and event:
        # Module 6 strict anti-cheat: unchanged behavior - immediate
        # auto-submit flag, exam-engine.js submits right after this call.
        attempt.security_violation_reason = event
        attempt.security_violation_at = datetime.utcnow()
        log_activity(
            f"student:{attempt.student_id}", "security_auto_submit",
            meta=f"attempt={attempt.id} reason={event}", ip_address=get_client_ip(request),
        )
        db.session.commit()
        return jsonify({"ok": True, "strict_auto_submit": True})

    if event not in _UNIFIED_VIOLATION_TYPES:
        db.session.commit()
        return jsonify({"ok": True, "warning_count": attempt.warning_count, "max_warnings": exam.max_warnings})

    warning_count, should_auto_submit = record_violation(attempt, event, max_warnings=exam.max_warnings)

    if should_auto_submit and attempt.status == "in_progress":
        attempt.security_violation_reason = attempt.security_violation_reason or event
        attempt.security_violation_at = datetime.utcnow()
        log_activity(
            f"student:{attempt.student_id}", "security_auto_submit",
            meta=f"attempt={attempt.id} reason={event} warnings={warning_count}",
            ip_address=get_client_ip(request),
        )

    db.session.commit()
    return jsonify({
        "ok": True,
        "warning_count": warning_count,
        "max_warnings": exam.max_warnings,
        "auto_submit": should_auto_submit,
    })


@public_bp.route("/result/<int:attempt_id>")
def result_page(attempt_id):
    """Phase 2 - hide results from students (spec #20). Students only ever
    see a plain submission confirmation here; no score, percentage,
    correct/wrong counts, rank, or answer review is ever sent to this
    student-facing page/template. Full results remain available to
    Admin via /admin/results and the admin PDF export, unchanged."""
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    if attempt.status not in ("submitted", "auto_submitted"):
        abort(404)
    return render_template(
        "exam/result.html", attempt=attempt, exam=attempt.exam, student=attempt.student,
    )


@public_bp.route("/review/<int:attempt_id>")
@login_required
def review_page(attempt_id):
    """Admin-only (Requirement #20/#21: students must never see the
    answer review). Protected by the existing admin Flask-Login session -
    a student hitting this URL directly is redirected to the admin login,
    never shown any answers."""
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    if attempt.status not in ("submitted", "auto_submitted"):
        abort(404)

    question_ids = json.loads(attempt.question_order or "[]")
    questions_by_id = {q.id: q for q in attempt.exam.questions}
    ordered_questions = [questions_by_id[qid] for qid in question_ids if qid in questions_by_id]
    answers_by_qid = {a.question_id: a for a in attempt.answers}

    review_rows = []
    for q in ordered_questions:
        ans = answers_by_qid.get(q.id)
        review_rows.append({"question": q, "answer": ans})

    return render_template("exam/review.html", attempt=attempt, exam=attempt.exam,
                            student=attempt.student, rows=review_rows)
