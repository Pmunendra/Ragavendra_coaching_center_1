"""GST/tax pricing.

Deliberately scoped to tax only, not multi-currency: this platform's
payment gateway (Razorpay India account + UPI) can only actually settle in
INR, so a currency selector would change nothing about what gets charged -
exactly the kind of non-functional control the project brief calls out as
forbidden. Tax, by contrast, genuinely changes the amount sent to Razorpay/
shown on the UPI QR, so it's real, wired-in configuration.

`CoursePurchase.amount` remains the single source of truth for "what the
student actually pays" everywhere it was already used (Razorpay order
amount, UPI QR, invoice). `tax_amount` is a breakdown/display field derived
from it, not a second source of truth.
"""
from app.models import SiteSetting


def gst_settings():
    return {
        "enabled": SiteSetting.get_bool("gst_enabled", False),
        "percent": float(SiteSetting.get("gst_percent", "18") or 18),
        "inclusive": SiteSetting.get_bool("gst_inclusive", True),
    }


def price_after_discount(base_amount, discount_amount=0.0):
    """Returns (tax_amount, final_amount) for a given pre-tax, post-discount
    base. tax_amount is always >= 0 and is a breakdown of what's already in
    final_amount when inclusive, or added on top when exclusive."""
    settings = gst_settings()
    taxable = max(0.0, base_amount - (discount_amount or 0.0))

    if not settings["enabled"] or settings["percent"] <= 0:
        return 0.0, round(taxable, 2)

    rate = settings["percent"] / 100.0
    if settings["inclusive"]:
        # Price already includes GST - back it out for display, total unchanged.
        tax_amount = round(taxable - (taxable / (1 + rate)), 2)
        final_amount = round(taxable, 2)
    else:
        # GST added on top - total goes up.
        tax_amount = round(taxable * rate, 2)
        final_amount = round(taxable + tax_amount, 2)

    return tax_amount, final_amount


def platform_fee_settings():
    """Admin-configurable "Platform / Payment Convenience Fee" - separate
    from Razorpay's own gateway charges (see project brief section 9): this
    is what WE charge the student on top of the plan price, not a pass-
    through of whatever Razorpay deducts from the merchant. Supports either
    a flat rupee amount or a percentage, same on/off + type pattern as GST
    above, and (per the brief) is a single global setting for now - the
    calculation is written to take a plain number either way, so per-plan
    fees can be layered in later without touching this function's shape."""
    return {
        "enabled": SiteSetting.get_bool("platform_fee_enabled", False),
        "type": SiteSetting.get("platform_fee_type", "fixed"),  # "fixed" (rupees) or "percent"
        "value": float(SiteSetting.get("platform_fee_value", "0") or 0),
    }


def compute_platform_fee(amount_before_fee, settings=None):
    """Fee is computed on the amount the student would otherwise pay
    (post-discount, post-tax) - i.e. it's charged on top of everything
    else, never baked into the taxable plan price. Always non-negative,
    rounded to paise like every other money value in this module."""
    settings = settings if settings is not None else platform_fee_settings()
    if not settings["enabled"] or settings["value"] <= 0 or amount_before_fee <= 0:
        return 0.0
    if settings["type"] == "percent":
        fee = amount_before_fee * (settings["value"] / 100.0)
    else:
        fee = settings["value"]
    return round(max(0.0, fee), 2)


def apply_pricing(purchase, base_amount=None, discount_amount=None):
    """Recomputes purchase.tax_amount, purchase.platform_fee_amount and
    purchase.amount from purchase.original_amount (or base_amount, for
    fresh purchases) and the given/current discount. Does not commit -
    caller decides transaction boundaries, same convention as
    coupon_service.

    Calculation order (matches the project brief exactly):
        original_amount - discount_amount  = discounted amount
                         (+/- GST, see price_after_discount)
                         + platform_fee_amount
                         = purchase.amount   <- single source of truth,
                           already used everywhere (Razorpay order amount,
                           UPI QR, invoice) so nothing downstream needs to
                           know the fee exists separately from the total.
    This function is only ever called server-side (course/plan price and
    coupon discount both come from the database, never from the request),
    so a student can't manipulate the final amount from the browser."""
    base = base_amount if base_amount is not None else (purchase.original_amount or purchase.amount)
    discount = discount_amount if discount_amount is not None else (purchase.discount_amount or 0.0)

    purchase.original_amount = base
    purchase.discount_amount = discount
    tax_amount, amount_after_tax = price_after_discount(base, discount)
    purchase.tax_amount = tax_amount

    platform_fee = compute_platform_fee(amount_after_tax)
    purchase.platform_fee_amount = platform_fee
    purchase.amount = round(amount_after_tax + platform_fee, 2)
    return purchase
