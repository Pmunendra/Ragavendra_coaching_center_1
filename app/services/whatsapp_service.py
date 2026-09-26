"""WhatsApp Business Cloud API (Meta) - low-level HTTP calls + webhook
verification only. Mirrors razorpay_service.py's shape deliberately: a
handful of functions the rest of the app calls, credentials read from
config/environment only, an is_configured() check everything else guards
on, and no conversation/bot logic in here at all - that lives in
whatsapp_bot_service.py. Keeping the two separate is what requirement 8
("do not scatter WhatsApp API calls throughout random Flask routes... keep
WhatsApp logic isolated") actually asks for: routes call
whatsapp_bot_service, which calls this module for the raw send/verify
primitives, and nothing else touches the Graph API directly.
"""
import hashlib
import hmac
import re

import requests
from flask import current_app


def is_configured():
    return bool(current_app.config.get("WHATSAPP_ACCESS_TOKEN") and current_app.config.get("WHATSAPP_PHONE_NUMBER_ID"))


def webhook_verify_token_configured():
    return bool(current_app.config.get("WHATSAPP_VERIFY_TOKEN"))


def webhook_signature_configured():
    return bool(current_app.config.get("WHATSAPP_APP_SECRET"))


def _api_url():
    version = current_app.config.get("WHATSAPP_API_VERSION", "v20.0")
    phone_number_id = current_app.config["WHATSAPP_PHONE_NUMBER_ID"]
    return f"https://graph.facebook.com/{version}/{phone_number_id}/messages"


def normalize_number(raw):
    """Digits only, no leading '+' - the format Meta sends inbound sender
    IDs in and expects outbound `to` values in. Never raises on odd input;
    worst case returns an empty string, which send_text_message() below
    then safely no-ops on rather than making a doomed API call."""
    return re.sub(r"\D", "", raw or "")


def send_text_message(to_number, body):
    """Sends a plain-text WhatsApp message. Returns the Meta message id
    (str) on success, or None on failure - never raises, since a WhatsApp
    delivery failure must not break the student-facing or admin-facing
    request that triggered it (see requirement 21: don't crash the app if
    WhatsApp is unavailable). Failures are logged server-side only -
    never with the access token in the log line.
    """
    to_number = normalize_number(to_number)
    if not is_configured() or not to_number:
        current_app.logger.info("WhatsApp send skipped (not configured or no recipient number).")
        return None

    try:
        resp = requests.post(
            _api_url(),
            headers={"Authorization": f"Bearer {current_app.config['WHATSAPP_ACCESS_TOKEN']}"},
            json={
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "text",
                "text": {"body": body[:4096]},  # WhatsApp's own text-message length ceiling
            },
            timeout=10,
        )
        data = resp.json()
        if resp.status_code >= 400:
            current_app.logger.warning("WhatsApp send failed (%s): %s", resp.status_code, data.get("error", data))
            return None
        return data.get("messages", [{}])[0].get("id")
    except requests.RequestException as exc:
        current_app.logger.warning("WhatsApp send failed (network error): %s", exc)
        return None


def send_template_message(to_number, template_name, language_code="en", components=None):
    """Sends a pre-approved template message - required by the Cloud API
    for the *first* outbound message in a new 24-hour window (a plain
    send_text_message() only works once the student has messaged first,
    or within 24h of their last message). Not wired into the bot flow
    yet since every reply in this feature is triggered by an inbound
    student message, but kept here, configured and ready, for anything
    that needs to message a student first later (e.g. a "your support
    ticket was resolved, reply if you still need help" nudge) - see
    admin/settings.html "Message Templates" for where template names are
    configured. Same never-raises contract as send_text_message()."""
    to_number = normalize_number(to_number)
    if not is_configured() or not to_number:
        return None
    try:
        resp = requests.post(
            _api_url(),
            headers={"Authorization": f"Bearer {current_app.config['WHATSAPP_ACCESS_TOKEN']}"},
            json={
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                    "components": components or [],
                },
            },
            timeout=10,
        )
        data = resp.json()
        if resp.status_code >= 400:
            current_app.logger.warning("WhatsApp template send failed (%s): %s", resp.status_code, data.get("error", data))
            return None
        return data.get("messages", [{}])[0].get("id")
    except requests.RequestException as exc:
        current_app.logger.warning("WhatsApp template send failed (network error): %s", exc)
        return None


def verify_webhook_challenge(mode, token, challenge):
    """The GET handshake Meta calls once when you save the webhook URL in
    the App Dashboard. Returns the challenge string to echo back if
    `token` matches WHATSAPP_VERIFY_TOKEN, else None (caller returns 403)."""
    if mode == "subscribe" and webhook_verify_token_configured() and token == current_app.config["WHATSAPP_VERIFY_TOKEN"]:
        return challenge
    return None


def verify_webhook_signature(raw_body, signature_header):
    """Verifies the `X-Hub-Signature-256` header Meta sends on every
    webhook POST, using WHATSAPP_APP_SECRET (App Dashboard > Settings >
    Basic > App Secret - a different value from the access token). Same
    reasoning as razorpay_service.verify_webhook_signature: without this,
    anyone who discovers the webhook URL could POST forged incoming
    messages. `raw_body` must be the exact, unparsed request bytes."""
    secret = current_app.config.get("WHATSAPP_APP_SECRET")
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)
