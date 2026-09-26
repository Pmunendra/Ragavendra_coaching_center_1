"""Razorpay payment gateway helpers (Module 10b).

Wraps the official `razorpay` Python SDK so the rest of the app only ever
talks to two small functions: create an order, and verify a completed
payment's signature. Credentials (`RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`)
are read from the environment (see `.env`) and never hard-coded.

If Razorpay isn't configured (keys missing) `is_configured()` returns
False and the checkout page simply hides the "Pay with Razorpay" button,
falling back to the existing UPI-QR + manual-verification flow.
"""
import razorpay
from flask import current_app


def is_configured():
    key_id = current_app.config.get("RAZORPAY_KEY_ID")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET")
    return bool(key_id and key_secret)


def _client():
    key_id = current_app.config["RAZORPAY_KEY_ID"]
    key_secret = current_app.config["RAZORPAY_KEY_SECRET"]
    return razorpay.Client(auth=(key_id, key_secret))


def create_order(amount_rupees, receipt, notes=None):
    """Creates a Razorpay order for `amount_rupees` (converted to paise).
    Returns the order dict from Razorpay (contains `id`, `amount`, etc.)."""
    client = _client()
    order = client.order.create({
        "amount": int(round(amount_rupees * 100)),
        "currency": "INR",
        "receipt": receipt,
        "notes": notes or {},
        "payment_capture": 1,
    })
    return order


def verify_payment_signature(order_id, payment_id, signature):
    """Returns True if the payment signature Razorpay sent back is valid,
    i.e. the payment genuinely happened and wasn't forged client-side."""
    client = _client()
    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def refund_payment(payment_id, amount_rupees, notes=None):
    """Issues a refund for a captured payment via Razorpay's Refunds API.
    `amount_rupees` may be less than the original payment (a partial
    refund, e.g. minus the non-refundable platform fee - see
    refund_service.py for that policy) - Razorpay supports partial
    refunds natively, it does not have to be the full amount.

    Raises on failure (network error, already-fully-refunded payment,
    invalid amount, etc.) - the caller is responsible for catching this
    and NOT marking the purchase as refunded in our own database unless
    this call actually succeeded, so our records never claim a refund
    that Razorpay didn't actually process.

    Returns the refund dict from Razorpay (contains `id`, `amount`,
    `status`, etc.).
    """
    client = _client()
    return client.payment.refund(payment_id, {
        "amount": int(round(amount_rupees * 100)),
        "notes": notes or {},
    })


def webhook_configured():
    return bool(current_app.config.get("RAZORPAY_WEBHOOK_SECRET"))


# Common UPI handle suffixes mapped to the likely app - heuristic, not
# authoritative. Banks issue these handles and more than one app can use
# the same one in principle, but in practice these mappings are reliable
# for the big three. Never claim more certainty than "likely" in the UI.
_UPI_HANDLE_APP_MAP = {
    "ybl": "PhonePe", "ibl": "PhonePe", "axl": "PhonePe",
    "okhdfcbank": "Google Pay", "okaxis": "Google Pay", "okicici": "Google Pay", "oksbi": "Google Pay",
    "paytm": "Paytm",
    "apl": "Amazon Pay",
}


def fetch_payment_method_detail(payment_id):
    """Calls Razorpay's Payments.fetch API to find out HOW a payment was
    actually made - card, UPI (and likely app), netbanking, or wallet -
    since the browser-side checkout response only gives an opaque
    payment_id, not the method used. Returns a short human-readable string
    for admin display (e.g. "UPI - likely PhonePe (user@ybl)", "Card -
    Visa ****4242", "Netbanking - HDFC Bank"), or None if the lookup fails
    for any reason (network issue, payment_id not found) - a failed
    lookup should never block payment verification, which is why this is
    called as a best-effort enrichment step after activation, not as part
    of the verification itself."""
    client = _client()
    try:
        payment = client.payment.fetch(payment_id)
    except Exception:
        current_app.logger.warning("Razorpay payment.fetch failed for %s - method detail unavailable.", payment_id)
        return None

    method = payment.get("method")
    if method == "upi":
        vpa = payment.get("vpa", "") or ""
        handle = vpa.split("@")[-1].lower() if "@" in vpa else ""
        app_name = _UPI_HANDLE_APP_MAP.get(handle)
        return f"UPI - likely {app_name} ({vpa})" if app_name else f"UPI ({vpa or 'unknown handle'})"
    elif method == "card":
        card = payment.get("card") or {}
        network = card.get("network", "Card")
        last4 = card.get("last4", "")
        card_type = card.get("type", "")  # "credit" or "debit"
        label = f"{card_type.capitalize()} Card" if card_type else "Card"
        return f"{label} - {network} ****{last4}" if last4 else f"{label} - {network}"
    elif method == "netbanking":
        return f"Netbanking - {payment.get('bank', 'Unknown Bank')}"
    elif method == "wallet":
        return f"Wallet - {payment.get('wallet', 'Unknown Wallet')}"
    return method or None


def verify_webhook_signature(raw_body, signature):
    """Verifies the `X-Razorpay-Signature` header on an incoming webhook
    call. This uses RAZORPAY_WEBHOOK_SECRET - a *different* secret from the
    API key/secret pair, configured separately under Dashboard > Settings >
    Webhooks - so a leaked API key alone can't be used to forge webhook
    calls. `raw_body` must be the exact, unparsed request bytes (signature
    is computed over the raw JSON text, not the re-serialized dict).
    """
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    client = _client()
    body_str = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    try:
        client.utility.verify_webhook_signature(body_str, signature, secret)
        return True
    except razorpay.errors.SignatureVerificationError:
        return False
