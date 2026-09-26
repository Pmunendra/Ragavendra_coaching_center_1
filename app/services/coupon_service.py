"""Coupon validation and pricing.

Every rule (validity window, min purchase, per-user/global usage caps,
which courses it applies to) is re-checked here, server-side, both when a
student applies a code at checkout AND again immediately before a payment
is verified/activated - never trust a discount amount that was merely
stored on a purchase row earlier and echoed back by the client.
"""
from app.extensions import db
from app.models import Coupon, CouponRedemption


def validate_and_price(code, student_account, course, base_amount):
    """Returns (coupon_or_None, discount, final_amount, error_or_None).

    `base_amount` must always be the server-computed tier/plan price, never
    a client-supplied number.
    """
    code = (code or "").strip().upper()
    if not code:
        return None, 0.0, base_amount, "Enter a coupon code."

    coupon = Coupon.query.filter(db.func.upper(Coupon.code) == code).first()
    if not coupon:
        return None, 0.0, base_amount, "Invalid coupon code."
    if not coupon.is_currently_valid():
        return None, 0.0, base_amount, "This coupon has expired or is not currently active."
    if not coupon.applies_to_course(course.id):
        return None, 0.0, base_amount, "This coupon isn't valid for this course."
    if coupon.min_purchase_amount and base_amount < coupon.min_purchase_amount:
        return None, 0.0, base_amount, f"This coupon needs a minimum purchase of Rs.{coupon.min_purchase_amount:.0f}."
    if coupon.max_usage_total is not None and coupon.usage_count() >= coupon.max_usage_total:
        return None, 0.0, base_amount, "This coupon has reached its usage limit."

    user_uses = CouponRedemption.query.filter_by(
        coupon_id=coupon.id, student_account_id=student_account.id
    ).count()
    if user_uses >= coupon.max_usage_per_user:
        return None, 0.0, base_amount, "You've already used this coupon the maximum number of times."

    discount = coupon.compute_discount(base_amount)
    final_amount = round(base_amount - discount, 2)
    return coupon, discount, final_amount, None


def record_redemption(purchase):
    """Called from activate_paid_purchase() once a purchase is actually
    paid. Idempotent - safe to call more than once for the same purchase."""
    if not purchase.coupon_id:
        return
    existing = CouponRedemption.query.filter_by(purchase_id=purchase.id).first()
    if existing:
        return
    db.session.add(CouponRedemption(
        coupon_id=purchase.coupon_id,
        student_account_id=purchase.student_account_id,
        purchase_id=purchase.id,
        discount_amount=purchase.discount_amount or 0.0,
    ))
