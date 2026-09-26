"""Stripe payment gateway helpers (Module 10c) - for students paying from
outside India who can't use UPI/Razorpay (India-only rails).

Mirrors razorpay_service.py's shape deliberately: two functions the rest of
the app actually calls (create a Checkout Session, verify a webhook), same
"credentials from environment, never hard-coded" rule, same "if not
configured, just hide the button" fallback behavior.

This is also what makes currency selection real rather than decorative:
Razorpay/UPI can only ever settle in INR, so a currency picker next to them
would be theater. Stripe genuinely settles in whatever currency the
Checkout Session is created in - here, STRIPE_CURRENCY (defaults to USD),
converted from the INR list price via STRIPE_INR_TO_USD_RATE.
"""
import stripe
from flask import current_app


def is_configured():
    return bool(current_app.config.get("STRIPE_SECRET_KEY"))


def webhook_configured():
    return bool(current_app.config.get("STRIPE_WEBHOOK_SECRET"))


def _client_configure():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]


def convert_inr_to_stripe_currency(amount_inr):
    """Returns (converted_amount, currency_code). Rounded to 2 decimals -
    Stripe's own API additionally requires the smallest currency unit
    (cents) when actually creating a session; that conversion happens in
    create_checkout_session, not here, so this function stays testable
    without needing a live Stripe call."""
    rate = current_app.config.get("STRIPE_INR_TO_USD_RATE", 83.0)
    currency = current_app.config.get("STRIPE_CURRENCY", "usd")
    converted = round(amount_inr / rate, 2)
    return converted, currency


def create_checkout_session(purchase, success_url, cancel_url):
    """Creates a Stripe Checkout Session for `purchase.amount` (INR),
    converted to the configured settlement currency. Returns the Session
    object (has `.id` and `.url` - redirect the student to `.url`).

    purchase.id is embedded in `client_reference_id` so the webhook handler
    can look the purchase back up without trusting anything else the
    client sends.
    """
    _client_configure()
    converted_amount, currency = convert_inr_to_stripe_currency(purchase.amount)
    unit_amount = int(round(converted_amount * 100))  # smallest currency unit (cents)

    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": currency,
                "unit_amount": unit_amount,
                "product_data": {"name": purchase.course.title if purchase.course else "Course Purchase"},
            },
            "quantity": 1,
        }],
        client_reference_id=str(purchase.id),
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"purchase_id": str(purchase.id)},
    )
    return session


def verify_webhook_signature(raw_body, signature):
    """Verifies the Stripe-Signature header and returns the parsed Event
    object if valid, or None if the signature doesn't check out. Uses
    STRIPE_WEBHOOK_SECRET - a separate secret from the API key, configured
    in the Stripe Dashboard under the webhook endpoint itself - so a leaked
    API key alone can't be used to forge webhook calls."""
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not signature:
        return None
    try:
        return stripe.Webhook.construct_event(raw_body, signature, secret)
    except (stripe.error.SignatureVerificationError, ValueError):
        return None
