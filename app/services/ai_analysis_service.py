"""
AI Performance Analysis Engine (Phase 4).

Runs once, right after grading, alongside the existing Result row. This is
a deterministic, explainable analytics engine (subject-wise breakdown,
weak/strong-topic detection, percentile-based rank estimate, and a
rule-based study plan) - not a trained ML model. That's a conscious,
honest scoping choice: it's fast, has zero training/infra cost, and every
number it produces is directly traceable back to the student's own
answers, which matters for a platform students are trusting with their
exam prep.

Call build_performance_analysis(attempt) once an attempt has been graded
(attempt.result must already exist). Safe to call multiple times for the
same attempt - it updates the existing row instead of duplicating it.
"""
import json
from datetime import datetime

from app.extensions import db
from app.models import PerformanceAnalysis, Result, StudentAccount, StudentAttempt


def _subject_breakdown(attempt):
    """Per-subject stats across every question in the exam (not just the
    ones the student answered), so skipped questions count against
    attempt-rate the same way finalize_attempt() already treats them."""
    answers_by_qid = {a.question_id: a for a in attempt.answers}
    stats = {}
    for q in attempt.exam.questions:
        subject_name = q.subject.name if q.subject else "General"
        row = stats.setdefault(subject_name, {"subject": subject_name, "total": 0, "correct": 0, "wrong": 0, "skipped": 0})
        row["total"] += 1
        answer = answers_by_qid.get(q.id)
        if answer is None or answer.status == "not_answered":
            row["skipped"] += 1
        elif answer.is_correct:
            row["correct"] += 1
        else:
            row["wrong"] += 1

    breakdown = []
    for row in stats.values():
        attempted = row["correct"] + row["wrong"]
        row["accuracy"] = round((row["correct"] / attempted) * 100, 1) if attempted else 0.0
        breakdown.append(row)
    return sorted(breakdown, key=lambda r: r["total"], reverse=True)


def _percentile_and_rank(attempt, percentage):
    """Where this attempt's % lands among every graded result for the same
    exam - a lightweight, always-available stand-in for a true all-India
    rank, computed purely from data already on this platform."""
    peer_percentages = [
        p for (p,) in
        db.session.query(Result.percentage)
        .join(StudentAttempt, StudentAttempt.id == Result.attempt_id)
        .filter(StudentAttempt.exam_id == attempt.exam_id)
        .all()
    ]
    if not peer_percentages:
        return 50.0, "Not enough data yet"

    below_or_equal = sum(1 for p in peer_percentages if p <= percentage)
    percentile = round((below_or_equal / len(peer_percentages)) * 100, 1)
    top_pct = max(1, round(100 - percentile))
    label = f"Top {top_pct}%" if len(peer_percentages) >= 3 else "Not enough data yet"
    return percentile, label


def _previous_analysis(student_account_id, exclude_attempt_id):
    if not student_account_id:
        return None
    return (
        PerformanceAnalysis.query.filter_by(student_account_id=student_account_id)
        .filter(PerformanceAnalysis.attempt_id != exclude_attempt_id)
        .order_by(PerformanceAnalysis.created_at.desc())
        .first()
    )


def _build_plan(weak_subjects, strong_subjects):
    if not weak_subjects:
        daily = ["30 min - Timed revision of your last mock test's mistakes",
                 "1 topic-wise test in your strongest subject, to keep sharp"]
    else:
        top_weak = weak_subjects[0]
        daily = [f"30 min - Focused revision of {top_weak}",
                 f"1 topic-wise test - {top_weak}"]
        if len(weak_subjects) > 1:
            daily.append(f"20 min - Quick notes review - {weak_subjects[1]}")

    weekly = [f"Full-length test covering {', '.join(weak_subjects[:3])}" if weak_subjects
              else "Full-length mixed-subject mock test"]
    weekly.append("Review every wrong answer from this week's tests with explanations")

    monthly = ["1 full-syllabus Grand Test under exam-day conditions"]
    if weak_subjects:
        monthly.append(f"Re-attempt a previous mock focused on {weak_subjects[0]} to measure improvement")
    if strong_subjects:
        monthly.append(f"Spend 1 session teaching/explaining {strong_subjects[0]} to reinforce mastery")

    return daily, weekly, monthly


def build_performance_analysis(attempt):
    result = attempt.result
    if result is None:
        return None

    breakdown = _subject_breakdown(attempt)
    weak_subjects = [r["subject"] for r in breakdown if r["correct"] + r["wrong"] >= 2 and r["accuracy"] < 50]
    strong_subjects = [r["subject"] for r in breakdown if r["correct"] + r["wrong"] >= 2 and r["accuracy"] >= 80]

    accuracy = round((result.correct / result.attempted) * 100, 1) if result.attempted else 0.0
    attempt_rate = round((result.attempted / result.total_questions) * 100, 1) if result.total_questions else 0.0
    negative_lost = round(sum(-a.marks_awarded for a in attempt.answers if a.marks_awarded and a.marks_awarded < 0), 2)
    avg_speed = round(result.time_taken_seconds / result.attempted, 1) if result.attempted else 0.0

    percentile, rank_label = _percentile_and_rank(attempt, result.percentage)
    negative_ratio = min(1.0, negative_lost / result.total_marks) if result.total_marks else 0
    readiness = round(max(0.0, min(100.0,
        0.55 * accuracy + 0.30 * attempt_rate + 0.15 * (100 - negative_ratio * 100)
    )), 1)

    student_account = StudentAccount.query.filter_by(email=attempt.student.email).first()

    prev = _previous_analysis(student_account.id if student_account else None, attempt.id)
    trend = round(accuracy - prev.accuracy_percent, 1) if prev else None

    daily_plan, weekly_plan, monthly_plan = _build_plan(weak_subjects, strong_subjects)

    feedback_bits = [f"You scored {result.percentage}% with {accuracy}% accuracy on attempted questions."]
    if weak_subjects:
        feedback_bits.append(f"Your weakest area right now is {weak_subjects[0]} - prioritise it this week.")
    if strong_subjects:
        feedback_bits.append(f"You're strong in {strong_subjects[0]}, so keep it in light-revision mode.")
    if trend is not None:
        direction = "up" if trend > 0 else ("down" if trend < 0 else "flat")
        feedback_bits.append(f"Accuracy is {direction} {abs(trend)} pts vs your last attempt.")
    ai_feedback = " ".join(feedback_bits)

    analysis = attempt.performance_analysis or PerformanceAnalysis(attempt_id=attempt.id)
    analysis.student_account_id = student_account.id if student_account else None
    analysis.accuracy_percent = accuracy
    analysis.attempt_rate_percent = attempt_rate
    analysis.negative_marks_lost = negative_lost
    analysis.avg_seconds_per_question = avg_speed
    analysis.exam_readiness_percent = readiness
    analysis.expected_percentile = percentile
    analysis.expected_rank_label = rank_label
    analysis.trend_vs_previous = trend
    analysis.subject_breakdown_json = json.dumps(breakdown)
    analysis.weak_topics_json = json.dumps(weak_subjects)
    analysis.strong_topics_json = json.dumps(strong_subjects)
    analysis.daily_plan_json = json.dumps(daily_plan)
    analysis.weekly_plan_json = json.dumps(weekly_plan)
    analysis.monthly_plan_json = json.dumps(monthly_plan)
    analysis.ai_feedback = ai_feedback
    analysis.created_at = analysis.created_at or datetime.utcnow()

    db.session.add(analysis)
    db.session.commit()
    return analysis
