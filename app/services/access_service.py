"""Phase 2 - server-side subscription / exam-access checks.

Every check here is deliberately server-side and re-evaluated on every
request (never cached in the session, never trusted from the client) so
that expired plans, direct-URL access, and manipulated client state can
never grant access. This module does not replace any existing
purchase/payment code - it only reads CoursePurchase / Exam / plan data
that already exists (see app/models.py) to make one decision:
"can this student attempt this exam right now?"
"""
from datetime import datetime

from app.models import CoursePurchase, StudentAttempt, Student


def active_purchase_for_course(student_account, course_id):
    """Most-recently-created active (paid + not expired) purchase for a
    course, regardless of tier/plan. Returns None if there isn't one."""
    if not student_account or not course_id:
        return None
    return (
        CoursePurchase.query.filter_by(
            student_account_id=student_account.id, course_id=course_id, status="paid",
        )
        .filter(CoursePurchase.expires_at.isnot(None), CoursePurchase.expires_at > datetime.utcnow())
        .order_by(CoursePurchase.created_at.desc())
        .all()
    )


def _grants_for(exam, purchases):
    """True if ANY of the student's active purchases for this exam's
    course grants the right kind of access for this exam's test_type."""
    for p in purchases:
        if exam.test_type == "main":
            if p.grants_main_tests():
                return True
        else:  # "mock" (and any future non-demo type defaults to mock-style gating)
            if p.grants_mock_tests() or p.grants_exam():
                return True
    return False


def attempts_used(exam, student_account=None, email=None):
    """Count of submitted/auto-submitted attempts this student already
    has on this exam - used to enforce Exam.max_attempts server-side."""
    query = StudentAttempt.query.filter(
        StudentAttempt.exam_id == exam.id,
        StudentAttempt.status.in_(["submitted", "auto_submitted"]),
    )
    if student_account is not None:
        query = query.filter(StudentAttempt.student_account_id == student_account.id)
    elif email:
        query = query.join(Student).filter(Student.email == email)
    else:
        return 0
    return query.count()


def can_access_exam(exam, student_account):
    """Returns (allowed: bool, reason: str|None).

    `reason` is a short machine-readable code so callers (routes) can
    show the right message / redirect:
      - "login_required"   - must be logged in to a StudentAccount
      - "not_scheduled"    - outside the scheduled availability window
      - "plan_required"    - no active eligible plan for this course
      - "plan_expired"     - specifically: had one, it lapsed
      - "attempt_limit"    - Exam.max_attempts already reached
      - "not_published"    - exam isn't active
    """
    if not exam.is_active:
        return False, "not_published"

    if not exam.is_within_schedule():
        return False, "not_scheduled"

    # Demo tests: free for everyone, but still require a logged-in
    # StudentAccount so "three free demo tests" / attempt limits are
    # meaningful and so they show correctly on the student's dashboard.
    if exam.test_type == "demo":
        if not student_account:
            return False, "login_required"
    elif exam.requires_plan():
        if not student_account:
            return False, "login_required"
        if not exam.course_id:
            # A mock/main test with no course scope and no free_without_plan
            # flag can't be gated - treat as accessible (admin misconfig
            # guard: don't lock students out of a test with nothing to buy).
            pass
        else:
            purchases = active_purchase_for_course(student_account, exam.course_id)
            if not _grants_for(exam, purchases):
                had_any = (
                    CoursePurchase.query.filter_by(
                        student_account_id=student_account.id, course_id=exam.course_id, status="paid",
                    ).first()
                )
                return False, "plan_expired" if had_any else "plan_required"

    if exam.max_attempts:
        used = attempts_used(exam, student_account=student_account)
        if used >= exam.max_attempts and not exam.allow_retake:
            return False, "attempt_limit"

    return True, None


ACCESS_DENIAL_MESSAGES = {
    "login_required": "Please log in to your student account to take this test.",
    "not_scheduled": "This test is only available during its scheduled window.",
    "plan_required": "You need an active plan to access this test. Please purchase a plan to continue.",
    "plan_expired": "Your plan has expired. Please renew your plan to continue.",
    "attempt_limit": "You have already used the maximum number of attempts for this test.",
    "not_published": "This test is not currently available.",
}
