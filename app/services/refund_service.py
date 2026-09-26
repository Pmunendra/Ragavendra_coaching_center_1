"""Refunds / cancellations.

Deliberately a thin, explicit module rather than folded into
payment_service.py - refunding is a distinct operation (admin-initiated,
reverses an already-completed payment) from activating a payment, and
keeping it separate matches how pricing_service/coupon_service are already
split out by concern.

Business rule (config-driven, not hard-coded - see SiteSetting keys
below, set from Admin > Settings):
  refund_platform_fee_policy = "exclude" (default) - the Platform/Payment
    Convenience Fee is treated as non-refundable, same as how most Indian
    payment/booking platforms handle their own convenience fee. Only the
    plan + GST portion is refunded by default.
  refund_platform_fee_policy = "include" - the fee is refunded too, i.e.
    the full `purchase.amount` is refunded by default.

Either way this is only the *default* pre-filled in the admin refund
form - the admin can still type a different amount for a genuine partial
refund, bounded to (0, purchase.amount].
"""
from datetime import datetime

from app.extensions import db
from app.models import CoursePurchase, SiteSetting, log_payment_transaction, notify_student


def refund_policy():
    return SiteSetting.get("refund_platform_fee_policy", "exclude")


def default_refund_amount(purchase):
    """The amount pre-filled in the admin refund form for this purchase,
    per the configured policy. Always rounded to paise, always <=
    purchase.amount."""
    if refund_policy() == "include":
        return round(purchase.amount, 2)
    fee = purchase.platform_fee_amount or 0.0
    return round(max(0.0, purchase.amount - fee), 2)


def process_refund(purchase, amount, reason, admin_user):
    """Refunds `purchase` for `amount` rupees, processed by `admin_user`.

    Only ever called for a `status == "paid"` purchase - the admin route
    enforces this before calling in, and this function re-checks it too
    (defense in depth, same reasoning as activate_paid_purchase's own
    re-checks).

    Gateway behavior:
      - If the purchase was paid via Razorpay (`razorpay_payment_id` set
        AND payment_method mentions Razorpay/UPI-via-gateway - i.e. NOT
        the manual UPI-QR flow, which has no payment_id to refund via
        API), calls Razorpay's Refunds API for the real money movement.
        A failed Razorpay call raises back to the caller - nothing in our
        database changes, so we never claim a refund that didn't happen.
      - Otherwise (manual UPI-QR payment, or Stripe - Stripe refunds
        aren't wired up here since the settlement currency/rate at
        refund time may differ from purchase time and needs its own
        reviewed conversion logic, not a guess) this is recorded as a
        MANUAL refund: the admin has already moved the money back
        outside this system (bank transfer, UPI, etc.) and is just
        logging it here so entitlement is revoked and the record is
        accurate.

    Returns (ok: bool, error: str|None). On success, `purchase` has
    already been updated in place; caller still owns the db.session.commit().
    """
    if purchase.status != "paid":
        return False, "This purchase isn't in a paid state, so it can't be refunded."

    if amount is None or amount <= 0:
        return False, "Refund amount must be greater than zero."
    if amount > purchase.amount + 0.01:  # small float-rounding tolerance
        return False, f"Refund amount can't exceed the amount actually paid (Rs. {purchase.amount:.2f})."

    amount = round(amount, 2)
    fee = purchase.platform_fee_amount or 0.0
    # How much of the refund counts as "fee refunded" - only relevant for
    # the accounting breakdown, never changes what's actually sent to the
    # gateway or charged back to the student.
    refunded_fee_portion = round(min(fee, amount), 2) if refund_policy() == "include" else 0.0

    is_gateway_refund = bool(
        purchase.razorpay_payment_id and purchase.payment_method and "razorpay" in purchase.payment_method.lower()
    )

    razorpay_refund_id = None
    if is_gateway_refund:
        from app.services import razorpay_service
        try:
            refund = razorpay_service.refund_payment(
                purchase.razorpay_payment_id, amount,
                notes={"purchase_id": purchase.id, "reason": reason or ""},
            )
        except Exception as exc:
            return False, f"Razorpay refund failed: {exc}. No changes were made - nothing has been marked refunded."
        razorpay_refund_id = refund.get("id")

    purchase.status = "refunded"
    purchase.refund_amount = amount
    purchase.refund_platform_fee_amount = refunded_fee_portion
    purchase.refund_reason = (reason or "").strip() or None
    purchase.refunded_at = datetime.utcnow()
    purchase.refunded_by = admin_user.id
    purchase.razorpay_refund_id = razorpay_refund_id

    log_payment_transaction(
        purchase, gateway="razorpay" if is_gateway_refund else "manual", status="refunded",
        payment_id=razorpay_refund_id, amount=amount,
        failure_reason=None,
    )
    notify_student(
        purchase.student_account_id, "Payment Refunded",
        f"Rs. {amount:.2f} for '{purchase.course.title}' ({purchase.tier_label()}) has been refunded. "
        f"Access to this plan has been removed.",
        type="payment_refunded",
    )
    return True, None
