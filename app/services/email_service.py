import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import current_app, render_template

from app.extensions import db
from app.models import EmailLog


def _build_message(to_address, subject, html_body, pdf_path=None):
    msg = MIMEMultipart("mixed")
    from_name = current_app.config["MAIL_FROM_NAME"]
    from_addr = current_app.config["MAIL_FROM_ADDRESS"]
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_address
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)

    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header(
                "Content-Disposition", "attachment",
                filename=os.path.basename(pdf_path),
            )
            msg.attach(part)
    return msg


def send_raw_email(to_address, subject, html_body, pdf_path=None, attempt_id=None):
    cfg = current_app.config
    status, error = "sent", None
    try:
        msg = _build_message(to_address, subject, html_body, pdf_path)
        if cfg["SMTP_USE_TLS"]:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20)
        if cfg["SMTP_USERNAME"]:
            server.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
        server.sendmail(cfg["MAIL_FROM_ADDRESS"], [to_address], msg.as_string())
        server.quit()
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)

    log = EmailLog(
        attempt_id=attempt_id, to_address=to_address, subject=subject,
        status=status, error_message=error,
    )
    db.session.add(log)
    db.session.commit()
    return status == "sent", error


def send_result_email(attempt, pdf_path=None):
    """Sends the 'Your Exam Result' email with the result PDF attached."""
    student = attempt.student
    exam = attempt.exam
    result = attempt.result

    view_result_url = f"{current_app.config['APP_BASE_URL']}/result/{attempt.id}"

    html_body = render_template(
        "email/result_email.html",
        student=student, exam=exam, result=result,
        view_result_url=view_result_url,
    )

    subject = "Your Exam Result"
    return send_raw_email(
        to_address=student.email, subject=subject, html_body=html_body,
        pdf_path=pdf_path, attempt_id=attempt.id,
    )


def send_otp_email(account, otp_code, purpose="verify"):
    """Sends a one-time-password email for student email verification or
    password reset. `account` is a StudentAccount instance."""
    subject = (
        "Verify Your Email - OTP"
        if purpose == "verify"
        else "Password Reset OTP"
    )
    html_body = render_template(
        "email/otp_email.html",
        account=account, otp_code=otp_code, purpose=purpose,
    )
    return send_raw_email(to_address=account.email, subject=subject, html_body=html_body)


def send_exam_reminder_email(enrollment, hours_before):
    """Module 7 - 24h / 1h exam reminder email."""
    exam = enrollment.exam
    account = enrollment.student
    link_url = None
    if enrollment.exam_link:
        link_url = enrollment.exam_link.public_url(current_app.config["APP_BASE_URL"])

    subject = f"Reminder: {exam.title} starts in {hours_before} hour(s)"
    html_body = render_template(
        "email/exam_reminder_email.html",
        exam=exam, account=account, hours_before=hours_before, link_url=link_url,
    )
    return send_raw_email(to_address=account.email, subject=subject, html_body=html_body)
