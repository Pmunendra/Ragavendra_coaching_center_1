from datetime import datetime
from flask import Blueprint, request, jsonify, session, current_app

from app.extensions import db, limiter
from app.models import StudentAttempt, Answer, Question, CoursePurchase, notify_student, log_payment_transaction, WhatsAppMessage
from app.utils import decode_attempt_token
from app.services import razorpay_service
from app.services import stripe_service
from app.services import whatsapp_service, whatsapp_bot_service
from app.services.payment_service import activate_paid_purchase

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _authorize(attempt_id):
    """Defense in depth: require BOTH the Flask session flag (HttpOnly+Secure
    cookie) AND a valid JWT bearer token scoped to this attempt."""
    if not session.get(f"attempt_{attempt_id}_verified"):
        return None
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    payload = decode_attempt_token(auth.split(" ", 1)[1])
    if not payload or payload.get("attempt_id") != attempt_id:
        return None
    attempt = StudentAttempt.query.get(attempt_id)
    if not attempt or attempt.status != "in_progress":
        return None
    return attempt


@api_bp.route("/attempt/<int:attempt_id>/save", methods=["POST"])
@limiter.limit("120 per minute")
def save_answer(attempt_id):
    attempt = _authorize(attempt_id)
    if attempt is None:
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    question_id = data.get("question_id")
    action = data.get("action", "save")  # save | mark_for_review | clear

    question = Question.query.get(question_id)
    if not question or question.exam_id != attempt.exam_id:
        return jsonify({"ok": False, "error": "invalid_question"}), 400

    answer = Answer.query.filter_by(attempt_id=attempt.id, question_id=question_id).first()
    if answer is None:
        answer = Answer(attempt_id=attempt.id, question_id=question_id)
        db.session.add(answer)

    if action == "clear":
        answer.selected_option_ids = None
        answer.integer_answer = None
        answer.text_answer = None
        answer.status = "not_answered"
    else:
        selected = data.get("selected_option_ids")
        if selected is not None:
            answer.selected_option_ids = ",".join(str(int(x)) for x in selected) if selected else None
        if "integer_answer" in data:
            answer.integer_answer = data.get("integer_answer")
        if "text_answer" in data:
            answer.text_answer = (data.get("text_answer") or "").strip() or None

        has_response = answer.selected_option_ids or answer.integer_answer is not None or answer.text_answer
        if action == "mark_for_review":
            answer.status = "answered_marked" if has_response else "marked_for_review"
        else:
            answer.status = "answered" if has_response else "not_answered"

    answer.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"ok": True, "status": answer.status})


@api_bp.route("/attempt/<int:attempt_id>/visit", methods=["POST"])
@limiter.limit("240 per minute")
def mark_visited(attempt_id):
    attempt = _authorize(attempt_id)
    if attempt is None:
        return jsonify({"ok": False}), 403

    data = request.get_json(silent=True) or {}
    question_id = data.get("question_id")
    answer = Answer.query.filter_by(attempt_id=attempt.id, question_id=question_id).first()
    if answer and answer.status == "not_visited":
        answer.status = "not_answered"
        db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/attempt/<int:attempt_id>/heartbeat", methods=["POST"])
@limiter.limit("30 per minute")
def heartbeat(attempt_id):
    """Used by the client to detect reconnects and confirm the attempt is
    still valid / how much time is remaining (server-authoritative timer)."""
    attempt = StudentAttempt.query.get_or_404(attempt_id)
    if not session.get(f"attempt_{attempt_id}_verified"):
        return jsonify({"ok": False}), 403

    remaining = 0
    if attempt.due_at:
        remaining = max(0, int((attempt.due_at - datetime.utcnow()).total_seconds()))

    return jsonify({"ok": True, "status": attempt.status, "remaining_seconds": remaining})


# --------------------------------------------------------------------------
# Razorpay webhook (Module 10b hardening) - server-to-server confirmation
# that activates a course even if the student's browser never calls back
# to /checkout/<id>/razorpay/verify (closed tab, lost connection, etc.).
# Configure this URL under Razorpay Dashboard > Settings > Webhooks as
# https://yourdomain.com/api/webhooks/razorpay, subscribed to at least
# "payment.captured" and "payment.failed". Already CSRF-exempt via
# csrf.exempt(api_bp) in app/__init__.py, since Razorpay - not a logged-in
# browser - is the caller.
# --------------------------------------------------------------------------
@api_bp.route("/webhooks/razorpay", methods=["POST"])
@limiter.limit("120 per minute")
def razorpay_webhook():
    if not razorpay_service.webhook_configured():
        # Webhook secret not set up yet - safely ignore rather than 500,
        # the browser-side verify flow still handles activation on its own.
        return jsonify({"status": "ignored"}), 200

    signature = request.headers.get("X-Razorpay-Signature", "")
    raw_body = request.get_data()  # signature is over the exact raw bytes
    if not razorpay_service.verify_webhook_signature(raw_body, signature):
        current_app.logger.warning("Razorpay webhook: signature verification failed.")
        return jsonify({"error": "invalid signature"}), 400

    event = request.get_json(silent=True) or {}
    event_type = event.get("event", "")
    payment_entity = event.get("payload", {}).get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")

    if not order_id:
        return jsonify({"status": "ignored"}), 200

    purchase = CoursePurchase.query.filter_by(razorpay_order_id=order_id).first()
    if not purchase:
        # Order wasn't created by this app (or already cleaned up) - nothing to do.
        return jsonify({"status": "ignored"}), 200

    if event_type == "payment.captured":
        activate_paid_purchase(purchase, payment_id, signature=None, order_id=order_id, method="Razorpay")
        db.session.commit()
    elif event_type == "payment.failed":
        if purchase.status == "pending":
            purchase.status = "failed"
            log_payment_transaction(
                purchase, gateway="razorpay", status="failed",
                order_id=order_id, failure_reason="payment.failed webhook event",
            )
            notify_student(
                purchase.student_account_id, "Payment Failed",
                f"Your payment for '{purchase.course.title}' failed. Please contact support or try again.",
                type="payment_failed",
            )
            db.session.commit()

    return jsonify({"status": "ok"}), 200


# --------------------------------------------------------------------------
# Stripe webhook (Module 10c) - server-to-server confirmation for
# international card payments, same reasoning as the Razorpay webhook
# above: activates the purchase even if the student's browser never makes
# it back to the success page. Configure this URL under Stripe Dashboard >
# Developers > Webhooks as https://yourdomain.com/api/webhooks/stripe,
# subscribed to checkout.session.completed and
# checkout.session.async_payment_failed. Already CSRF-exempt via
# csrf.exempt(api_bp) in app/__init__.py, since Stripe - not a logged-in
# browser - is the caller.
# --------------------------------------------------------------------------
@api_bp.route("/webhooks/stripe", methods=["POST"])
@limiter.limit("120 per minute")
def stripe_webhook():
    if not stripe_service.webhook_configured():
        return jsonify({"status": "ignored"}), 200

    signature = request.headers.get("Stripe-Signature", "")
    raw_body = request.get_data()  # signature is over the exact raw bytes
    event = stripe_service.verify_webhook_signature(raw_body, signature)
    if event is None:
        current_app.logger.warning("Stripe webhook: signature verification failed.")
        return jsonify({"error": "invalid signature"}), 400

    session = event["data"]["object"]
    purchase_ref = getattr(session, "client_reference_id", None)
    if not purchase_ref:
        return jsonify({"status": "ignored"}), 200

    try:
        purchase = CoursePurchase.query.get(int(purchase_ref))
    except (TypeError, ValueError):
        purchase = None
    if not purchase:
        # client_reference_id didn't map to a real purchase - nothing to do.
        return jsonify({"status": "ignored"}), 200

    if event["type"] == "checkout.session.completed" and getattr(session, "payment_status", None) == "paid":
        activate_paid_purchase(
            purchase, payment_id=getattr(session, "payment_intent", None), signature=None,
            order_id=getattr(session, "id", None), method="Stripe",
        )
        db.session.commit()
    elif event["type"] == "checkout.session.async_payment_failed":
        if purchase.status == "pending":
            purchase.status = "failed"
            log_payment_transaction(
                purchase, gateway="stripe", status="failed",
                order_id=getattr(session, "id", None), failure_reason="checkout.session.async_payment_failed webhook event",
            )
            notify_student(
                purchase.student_account_id, "Payment Failed",
                f"Your payment for '{purchase.course.title}' failed. Please contact support or try again.",
                type="payment_failed",
            )
            db.session.commit()

    return jsonify({"status": "ok"}), 200

# --------------------------------------------------------------------------
# WhatsApp Business Cloud API webhook (Module 15) - GET is Meta's one-time
# verification handshake when you save the webhook URL in the App
# Dashboard; POST is every subsequent event (inbound messages + delivery
# status updates for our own outbound messages). Configure this URL under
# Meta for Developers > your app > WhatsApp > Configuration as
# https://yourdomain.com/api/webhooks/whatsapp. Already CSRF-exempt via
# csrf.exempt(api_bp) in app/__init__.py, since Meta - not a logged-in
# browser - is the caller.
# --------------------------------------------------------------------------
@api_bp.route("/webhooks/whatsapp", methods=["GET"])
def whatsapp_webhook_verify():
    challenge = whatsapp_service.verify_webhook_challenge(
        request.args.get("hub.mode", ""),
        request.args.get("hub.verify_token", ""),
        request.args.get("hub.challenge", ""),
    )
    if challenge is None:
        return jsonify({"error": "verification failed"}), 403
    # Meta expects the raw challenge string back, not JSON.
    return challenge, 200


@api_bp.route("/webhooks/whatsapp", methods=["POST"])
@limiter.limit("120 per minute")
def whatsapp_webhook():
    # Signature verification: enforced whenever WHATSAPP_APP_SECRET is
    # configured (reject on mismatch), same "optional but strongly
    # recommended" posture this codebase already takes for
    # RAZORPAY_WEBHOOK_SECRET/STRIPE_WEBHOOK_SECRET - see config.py. If
    # it's genuinely not set up yet, we still process rather than silently
    # dropping real student support messages (unlike a payment webhook,
    # where "ignore until configured" is safe because the browser-side
    # flow still activates the purchase on its own - there's no equivalent
    # fallback path for an inbound WhatsApp message, so ignoring it here
    # would just lose the student's message).
    raw_body = request.get_data()
    if whatsapp_service.webhook_signature_configured():
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not whatsapp_service.verify_webhook_signature(raw_body, signature):
            current_app.logger.warning("WhatsApp webhook: signature verification failed.")
            return jsonify({"error": "invalid signature"}), 400
    else:
        current_app.logger.warning("WhatsApp webhook: WHATSAPP_APP_SECRET not configured - processing unverified.")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "malformed payload"}), 400

    try:
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value", {}) or {}

                for msg in value.get("messages", []) or []:
                    whatsapp_message_id = msg.get("id")
                    from_number = whatsapp_service.normalize_number(msg.get("from", ""))
                    if not from_number or not whatsapp_message_id:
                        continue
                    # Duplicate delivery of an already-processed message
                    # (Meta retries on anything but a fast 200) - skip
                    # rather than replying/logging twice.
                    if WhatsAppMessage.query.filter_by(whatsapp_message_id=whatsapp_message_id).first():
                        continue

                    msg_type = msg.get("type", "text")
                    if msg_type == "text":
                        body_text = msg.get("text", {}).get("body", "")
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {}) or {}
                        body_text = (
                            interactive.get("button_reply", {}).get("title")
                            or interactive.get("list_reply", {}).get("title")
                            or ""
                        )
                    else:
                        # Image/document/audio/location/etc. - the bot can't
                        # meaningfully auto-answer these, so log a
                        # placeholder and route straight to a human rather
                        # than guessing at content.
                        body_text = f"[{msg_type} message received - view in WhatsApp Business app]"

                    convo = whatsapp_bot_service.handle_incoming_message(from_number, body_text, whatsapp_message_id)
                    if msg_type != "text" and convo.bot_state is not None:
                        convo.bot_state = None  # needs a human to actually look at the attachment
                    db.session.commit()

                for status in value.get("statuses", []) or []:
                    wamid = status.get("id")
                    delivery_status = status.get("status")  # sent / delivered / read / failed
                    if not wamid or not delivery_status:
                        continue
                    outbound = WhatsAppMessage.query.filter_by(whatsapp_message_id=wamid, direction="out").first()
                    if outbound:
                        outbound.delivery_status = delivery_status
                db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("WhatsApp webhook: failed to process payload.")
        # Still 200 - Meta retries aggressively on non-2xx, and a payload
        # we can't parse won't parse better on retry.
        return jsonify({"status": "error_logged"}), 200

    return jsonify({"status": "ok"}), 200
