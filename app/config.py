import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _bool(val, default=False):
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-jwt-secret-change-me")
    JWT_EXP_HOURS = int(os.environ.get("JWT_EXP_HOURS", 12))

    # ---- Database -----------------------------------------------------
    # SQLite by default - the .db file lives under instance/, right next to
    # UPLOAD_FOLDER/GENERATED_PDF_FOLDER below, so one persistent-disk mount
    # in production covers the database *and* uploaded files together (see
    # instance/README.md and the Render section of MIGRATION_NOTES.md).
    # `basedir` is computed from this file's own location, not a hard-coded
    # path, so the same config works unmodified on Windows, Linux, and
    # Render's container filesystem.
    #
    # Set DATABASE_URL to point at any other SQLAlchemy-supported database
    # (e.g. a managed Postgres/MySQL instance) instead, if ever needed -
    # nothing else in the app assumes SQLite specifically.
    SQLITE_DB_PATH = os.environ.get(
        "SQLITE_DB_PATH", os.path.join(basedir, "instance", "exam_portal.db")
    )
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + SQLITE_DB_PATH.replace(os.sep, "/")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    _is_sqlite = SQLALCHEMY_DATABASE_URI.startswith("sqlite:")
    if _is_sqlite:
        # pool_pre_ping/pool_recycle are for network-connected databases
        # (MySQL/Postgres) whose connections can go stale or get dropped by
        # the server; SQLite is a local file with no such connection to
        # recycle, so those options are simply skipped here. `timeout` is
        # SQLite's own busy-wait (seconds) before raising "database is
        # locked" on a concurrent write - see the WAL-mode setup in
        # app/__init__.py for the other half of the concurrency story.
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USE_TLS = _bool(os.environ.get("SMTP_USE_TLS"), True)
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Exam Portal")
    MAIL_FROM_ADDRESS = os.environ.get("MAIL_FROM_ADDRESS", SMTP_USERNAME)

    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool(os.environ.get("SESSION_COOKIE_SECURE"), False)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    WTF_CSRF_TIME_LIMIT = None
    UPLOAD_FOLDER = os.path.join(basedir, "instance", "uploads")
    GENERATED_PDF_FOLDER = os.path.join(basedir, "instance", "reports")
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB uploads

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

    # UPI payment (Module 10) - set these to the institute's real UPI ID.
    UPI_PAYEE_VPA = os.environ.get("UPI_PAYEE_VPA", "exam-portal@upi")
    UPI_PAYEE_NAME = os.environ.get("UPI_PAYEE_NAME", "Kalyani Exam Hub")

    # Razorpay payment gateway (Module 10b) - get these from the Razorpay
    # Dashboard > Settings > API Keys. Leave blank to disable the "Pay with
    # Razorpay" button and keep only the UPI-QR + manual-verification flow.
    RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
    # Dashboard > Settings > Webhooks - a separate secret from the API keys
    # above, used to verify the X-Razorpay-Signature header on incoming
    # webhook calls (payment.captured / payment.failed). Optional but
    # strongly recommended in production: without it, activation relies
    # solely on the student's browser calling back after Checkout closes,
    # which is skipped if they close the tab right after paying.
    RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

    # Stripe payment gateway (Module 10c) - for international students who
    # can't pay via UPI/Razorpay (India-only). Get these from the Stripe
    # Dashboard > Developers > API Keys. Leave blank to disable the "Pay
    # with Card (International)" option and keep only Razorpay/UPI.
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    # Dashboard > Developers > Webhooks - verifies the Stripe-Signature
    # header on incoming webhook calls (checkout.session.completed /
    # checkout.session.async_payment_failed). Same reasoning as
    # RAZORPAY_WEBHOOK_SECRET above: without it, activation relies solely
    # on the student's browser returning to the success URL.
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    # Stripe settles in whatever currency the Checkout Session is created
    # in - unlike Razorpay/UPI, which are INR-only. INR prices are
    # converted to this currency using STRIPE_INR_TO_USD_RATE below.
    STRIPE_CURRENCY = os.environ.get("STRIPE_CURRENCY", "usd")
    STRIPE_INR_TO_USD_RATE = float(os.environ.get("STRIPE_INR_TO_USD_RATE", "83"))

    # Admin bootstrap account (auto-created on first startup if no admin
    # exists yet - see app/__init__.py). Change these in .env before going
    # live; they are never displayed anywhere in the app.
    ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # WhatsApp Business Cloud API (Meta) - student support bot + management
    # handoff. Get these from Meta for Developers > your app > WhatsApp >
    # API Setup. Leave WHATSAPP_ACCESS_TOKEN blank to disable outbound
    # sending (the webhook still logs inbound messages, but replies/bot
    # auto-replies are skipped rather than raising - see whatsapp_service.py
    # is_configured()).
    WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_BUSINESS_ACCOUNT_ID = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    # Set by you (any random string) and entered again in the Meta App
    # Dashboard's webhook config - Meta echoes it back on the GET
    # verification handshake so only requests naming this exact token are
    # accepted as the real webhook setup call.
    WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    # Meta App Dashboard > Settings > Basic > App Secret - used to verify
    # the X-Hub-Signature-256 header on incoming webhook POSTs, same
    # reasoning as RAZORPAY_WEBHOOK_SECRET/STRIPE_WEBHOOK_SECRET above.
    # Optional but strongly recommended: without it, anyone who discovers
    # the webhook URL could POST forged "incoming messages".
    WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
    WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
