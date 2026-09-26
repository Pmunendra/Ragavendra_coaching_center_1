"""
Core exam-engine grading logic.

Handles scoring for every supported question type:
single_choice, multiple_choice, true_false, integer, fill_blank,
image_based, paragraph, case_study (the last three are graded using
whatever underlying answer format they use — options or text/integer).
"""
from datetime import datetime
from app.extensions import db
from app.models import Answer, Result, StudentAttempt


def _grade_choice_question(question, answer):
    """single_choice / true_false / image_based(as single choice)"""
    correct_ids = set(question.correct_option_ids())
    selected_ids = set(answer.selected_ids_list())

    if not selected_ids:
        return None, 0  # skipped

    is_correct = selected_ids == correct_ids
    marks = question.marks if is_correct else -(question.negative_marks or 0)
    return is_correct, marks


def _grade_multi_choice(question, answer):
    correct_ids = set(question.correct_option_ids())
    selected_ids = set(answer.selected_ids_list())

    if not selected_ids:
        return None, 0

    is_correct = selected_ids == correct_ids
    marks = question.marks if is_correct else -(question.negative_marks or 0)
    return is_correct, marks


def _grade_integer(question, answer):
    if answer.integer_answer is None:
        return None, 0
    is_correct = answer.integer_answer == question.correct_integer_answer
    marks = question.marks if is_correct else -(question.negative_marks or 0)
    return is_correct, marks


def _grade_text(question, answer):
    if not answer.text_answer:
        return None, 0
    given = (answer.text_answer or "").strip().lower()
    expected = (question.correct_text_answer or "").strip().lower()
    is_correct = given == expected and expected != ""
    marks = question.marks if is_correct else -(question.negative_marks or 0)
    return is_correct, marks


GRADERS = {
    "single_choice": _grade_choice_question,
    "true_false": _grade_choice_question,
    "image_based": _grade_choice_question,
    "multiple_choice": _grade_multi_choice,
    "integer": _grade_integer,
    "fill_blank": _grade_text,
    "paragraph": _grade_choice_question,
    "case_study": _grade_choice_question,
}


def grade_answer(question, answer):
    grader = GRADERS.get(question.question_type, _grade_choice_question)
    is_correct, marks = grader(question, answer)
    answer.is_correct = is_correct
    answer.marks_awarded = round(marks, 2)
    return is_correct, marks


def finalize_attempt(attempt: StudentAttempt, auto_submitted=False):
    """Grade every answer in the attempt, build & persist the Result row."""
    exam = attempt.exam
    questions = list(exam.questions)
    answers_by_qid = {a.question_id: a for a in attempt.answers}

    total_questions = len(questions)
    attempted = correct = wrong = skipped = 0
    score = 0.0

    for q in questions:
        answer = answers_by_qid.get(q.id)
        if answer is None:
            skipped += 1
            continue

        has_response = (
            answer.selected_option_ids or answer.integer_answer is not None or answer.text_answer
        )
        if not has_response:
            skipped += 1
            answer.status = "not_answered"
            continue

        attempted += 1
        is_correct, marks = grade_answer(q, answer)
        score += marks
        if is_correct:
            correct += 1
            answer.status = "answered"
        else:
            wrong += 1
            answer.status = "answered"

    total_marks = exam.total_marks or sum(q.marks for q in questions) or 1
    percentage = max(0.0, round((score / total_marks) * 100, 2)) if total_marks else 0.0
    status = "pass" if percentage >= (exam.passing_percentage or 40) else "fail"

    attempt.submitted_at = datetime.utcnow()
    attempt.status = "auto_submitted" if auto_submitted else "submitted"

    result = attempt.result
    if result is None:
        result = Result(attempt_id=attempt.id)
        db.session.add(result)

    result.total_questions = total_questions
    result.attempted = attempted
    result.correct = correct
    result.wrong = wrong
    result.skipped = skipped
    result.score = round(score, 2)
    result.total_marks = total_marks
    result.percentage = percentage
    result.status = status
    result.time_taken_seconds = attempt.elapsed_seconds()

    db.session.commit()
    return result
