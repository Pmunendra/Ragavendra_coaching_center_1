"""UPI payment helpers.

Generates a standard UPI deep-link (upi://pay?...) which any UPI app
(PhonePe, Google Pay, Paytm, BHIM, etc.) can open directly, encoded as a
QR code. This does NOT require gateway/merchant API credentials - it's
the same mechanism static UPI QR codes use. Because there is no gateway
webhook without a registered payment aggregator, confirmation here is
semi-automated: the student enters the UPI reference/UTR number after
paying, and an admin verifies it before the course is activated (see
Module 10 in the admin panel).

To go fully automatic later (instant, webhook-based confirmation), swap
this module for a real gateway SDK (Razorpay/PhonePe Business/Cashfree)
once merchant credentials are available - the rest of the purchase flow
(CoursePurchase model, invoice, activation) stays the same.
"""
import random
import string
from datetime import datetime

from app.services.qr_service import generate_qr_base64


def build_upi_qr(payee_vpa, payee_name, amount, note):
    """Returns (upi_uri, qr_data_uri) for a dynamic-amount UPI payment request."""
    from urllib.parse import quote

    upi_uri = (
        f"upi://pay?pa={quote(payee_vpa)}&pn={quote(payee_name)}"
        f"&am={amount:.2f}&cu=INR&tn={quote(note)}"
    )
    qr_data_uri = generate_qr_base64(upi_uri)
    return upi_uri, qr_data_uri


def generate_invoice_number():
    stamp = datetime.utcnow().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.digits, k=5))
    return f"INV-{stamp}-{suffix}"


def activate_paid_purchase(purchase, payment_id, signature=None, order_id=None, method="Razorpay"):
    """Marks a pending CoursePurchase as paid and activates course access.
    This is the single place both the browser-side Razorpay verify route
    and the server-side webhook call into, so a payment is always recorded
    and activated the exact same way regardless of which one gets there
    first - and never twice.

    Idempotent / duplicate-safe:
      - Re-fetches this purchase with a row lock (`SELECT ... FOR UPDATE`)
        before doing anything else. Without this, the webhook and the
        browser's own verify call landing within milliseconds of each
        other could both read `status == "pending"` before either had
        committed - each request runs in its own DB transaction, so a
        plain re-check of `purchase.status` here is not enough to stop
        that. The lock makes the second caller wait for the first
        transaction to commit, then see `status == "paid"` and safely
        no-op below - closing the exact "duplicate payment callback /
        race conditions in payment activation" case called out in the
        project brief. (No-op on SQLite, which doesn't support row locks -
        harmless there since SQLite already serializes writes at the file
        level; this matters for the MySQL/Postgres deployment target.)
      - If `purchase` is already paid, does nothing (handles the normal
        race where both the webhook and the browser's own verify call
        arrive for the same successful payment).
      - If `payment_id` is already attached to a *different* purchase,
        refuses to activate this one too - a captured Razorpay payment_id
        can only ever activate a single CoursePurchase.

    Returns True if this call actually activated the purchase, False if it
    was a no-op (already handled / duplicate). Caller is responsible for
    the db.session.commit() (which also releases the row lock taken here).
    """
    from app.models import CoursePurchase, notify_student
    from app.services.coupon_service import record_redemption
    from datetime import timedelta

    purchase = CoursePurchase.query.with_for_update().get(purchase.id)

    if purchase.status == "paid":
        return False

    if payment_id:
        duplicate = (
            CoursePurchase.query.filter(
                CoursePurchase.razorpay_payment_id == payment_id,
                CoursePurchase.id != purchase.id,
            ).first()
        )
        if duplicate:
            return False

    purchase.status = "paid"
    purchase.payment_method = method
    if order_id:
        purchase.razorpay_order_id = order_id
    purchase.razorpay_payment_id = payment_id
    purchase.razorpay_signature = signature
    purchase.verified_at = datetime.utcnow()  # doubles as "payment time"
    purchase.invoice_number = purchase.invoice_number or generate_invoice_number()
    days = purchase.plan.duration_days() if purchase.plan_id and purchase.plan else purchase.course.duration_days
    purchase.expires_at = datetime.utcnow() + timedelta(days=days)
    purchase.expiry_reminder_sent = False
    record_redemption(purchase)

    from app.models import log_payment_transaction
    method_detail = None
    if method == "Razorpay" and payment_id:
        from app.services import razorpay_service
        # Best-effort enrichment - never let a lookup failure affect
        # payment activation itself, which has already happened above.
        method_detail = razorpay_service.fetch_payment_method_detail(payment_id)
        if method_detail:
            purchase.payment_method = method_detail  # e.g. "UPI - likely PhonePe (x@ybl)" instead of just "Razorpay"

    log_payment_transaction(
        purchase, gateway=method.lower(), status="success",
        order_id=order_id, payment_id=payment_id, method_detail=method_detail,
    )

    notify_student(
        purchase.student_account_id, "Payment Confirmed",
        f"Your {method} payment for '{purchase.course.title}' ({purchase.tier_label()}) "
        f"was successful. Course activated!",
        type="payment_success",
    )
    return True
