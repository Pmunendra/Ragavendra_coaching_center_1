"""Module 7 - Email Reminders.

Sends 24-hour and 1-hour reminder emails to students enrolled (via the
admin panel) in a scheduled exam. This is implemented as a Flask CLI
command rather than an in-process background thread, because the app is
deployed under gunicorn (app/../Dockerfile shows a multi-worker style
setup) where an in-process scheduler would fire once per worker and send
duplicate emails.

Run it on a schedule with system cron (recommended for production):

    */5 * * * *  cd /path/to/exam_portal && /path/to/venv/bin/flask send-exam-reminders >> /var/log/exam_reminders.log 2>&1

Or, for quick local testing, just run it manually:

    flask send-exam-reminders
"""
import click
from datetime import datetime, timedelta

from app.extensions import db
from app.models import ExamEnrollment, Exam
from app.services.email_service import send_exam_reminder_email


def register_reminder_cli(app):
    @app.cli.command("send-exam-reminders")
    def send_exam_reminders():
        """Send 24h and 1h exam reminder emails to enrolled students."""
        now = datetime.utcnow()
        sent_24h = _send_window(now, hours_before=24, window_minutes=10)
        sent_1h = _send_window(now, hours_before=1, window_minutes=10)
        click.echo(f"Sent {sent_24h} 24-hour reminder(s) and {sent_1h} 1-hour reminder(s).")

    @app.cli.command("send-expiry-reminders")
    def send_expiry_reminders():
        """In-app "your course expires soon" notification (spec #37), sent
        once per purchase within 3 days of expiry. Same reasoning as
        send-exam-reminders above: a CLI command run via system cron, not an
        in-process scheduler, so it fires exactly once regardless of how
        many gunicorn workers are running.

            0 9 * * *  cd /path/to/exam_portal && /path/to/venv/bin/flask send-expiry-reminders >> /var/log/expiry_reminders.log 2>&1
        """
        from app.models import CoursePurchase, notify_student

        now = datetime.utcnow()
        window_end = now + timedelta(days=3)
        purchases = (
            CoursePurchase.query.filter(
                CoursePurchase.status == "paid",
                CoursePurchase.expires_at.isnot(None),
                CoursePurchase.expires_at >= now,
                CoursePurchase.expires_at <= window_end,
                CoursePurchase.expiry_reminder_sent.is_(False),
            ).all()
        )
        sent = 0
        for purchase in purchases:
            try:
                days_left = max(0, (purchase.expires_at - now).days)
                notify_student(
                    purchase.student_account_id, "Your course access expires soon",
                    f"'{purchase.course.title}' expires in {days_left} day(s), on "
                    f"{purchase.expires_at.strftime('%d %b %Y')}. Renew to keep access.",
                    type="course_expiry",
                )
                purchase.expiry_reminder_sent = True
                sent += 1
            except Exception as exc:  # noqa: BLE001
                click.echo(f"Failed to notify purchase {purchase.id}: {exc}")
        db.session.commit()
        click.echo(f"Sent {sent} expiry reminder(s).")


def _send_window(now, hours_before, window_minutes=10):
    target_time = now + timedelta(hours=hours_before)
    window_start = target_time - timedelta(minutes=window_minutes)
    window_end = target_time + timedelta(minutes=window_minutes)
    flag_field = ExamEnrollment.reminder_24h_sent if hours_before == 24 else ExamEnrollment.reminder_1h_sent

    enrollments = (
        ExamEnrollment.query.join(Exam)
        .filter(
            Exam.scheduled_at.isnot(None),
            Exam.scheduled_at >= window_start,
            Exam.scheduled_at <= window_end,
            flag_field.is_(False),
        )
        .all()
    )

    sent = 0
    for enrollment in enrollments:
        try:
            send_exam_reminder_email(enrollment, hours_before=hours_before)
            if hours_before == 24:
                enrollment.reminder_24h_sent = True
            else:
                enrollment.reminder_1h_sent = True
            sent += 1
        except Exception as exc:  # noqa: BLE001
            click.echo(f"Failed to send reminder to enrollment {enrollment.id}: {exc}")
    db.session.commit()
    return sent
