"""WhatsApp support conversation + bot menu logic. This is where an
inbound message actually gets turned into a reply and a conversation state
change - whatsapp_service.py only knows how to call the Graph API and
verify webhook signatures, nothing about menus or students.

--------------------------------------------------------------------------
Student identification - read this before changing anything here
--------------------------------------------------------------------------
A WhatsApp conversation is linked to a real StudentAccount in exactly two
ways, and both are deliberate:

  1. The student tapped "WhatsApp Support" on their own (logged-in)
     dashboard. That route (student.whatsapp_support) creates a
     WhatsAppConversation row up front with student_account_id already set
     and whatsapp_number left blank, and hands the student a short
     support_reference (e.g. "KHE-SUP-48291") embedded in the prefilled
     WhatsApp message. When that first message actually arrives here,
     find_or_create_conversation() below recognizes the reference and
     "claims" that exact pre-created row by filling in the sender's real
     WhatsApp number - it does NOT create a new row and does NOT touch any
     other student's data, because only the logged-in student themselves
     could ever have received that reference.
  2. An admin manually links a conversation from the inbox after actually
     verifying who they're talking to.

We deliberately never auto-link a conversation to a StudentAccount just
because the inbound phone number happens to match one on file. Phone
numbers are self-reported at registration, get reassigned/recycled, and
trusting a bare WhatsApp sender ID as proof of identity would let anyone
holding a former student's number read that student's support history.
`_phone_hint_student()` below is used ONLY to show the admin inbox a
non-binding "possibly this student" label - never to grant access to
anything, never treated as a confirmed link.
"""
import re
from datetime import datetime

from app.extensions import db
from app.models import (
    WhatsAppConversation, WhatsAppMessage, StudentAccount, CoursePurchase, generate_support_reference,
)
from app.services import whatsapp_service

_SUPPORT_REF_RE = re.compile(r"KHE-SUP-\d{5}")

# Phrases that mean "I want to talk to management" as an explicit ask -
# deliberately NOT a bare substring match on "management", since the
# dashboard button's own prefilled message ("Hi Kalyani Exam Hub
# Management, I am a student and I need support...") contains that word
# incidentally and would otherwise trigger an immediate, menu-skipping
# handoff for every single dashboard-originated conversation. Caught by
# testing this service in isolation before shipping it.
_MANAGEMENT_PHRASES = (
    "talk to management", "connect to management", "connect with management",
    "speak to management", "contact management", "talk to a human",
    "talk to admin", "human support", "real person",
)

_MENU_TEXT = (
    "\U0001F44B Welcome to Kalyani Exam Hub Support.\n\n"
    "How can we help you?\n\n"
    "1\uFE0F\u20E3 Exam Related Doubt\n"
    "2\uFE0F\u20E3 Payment / Subscription Issue\n"
    "3\uFE0F\u20E3 Login / Account Problem\n"
    "4\uFE0F\u20E3 Exam Attempt Problem\n"
    "5\uFE0F\u20E3 Result Related Query\n"
    "6\uFE0F\u20E3 Exam Schedule\n"
    "7\uFE0F\u20E3 Talk to Management\n"
    "8\uFE0F\u20E3 Other Support\n\n"
    "Reply with a number (1-8)."
)

_MANAGEMENT_HANDOFF_TEXT = (
    "Sure \U0001F44D\n"
    "Your support request has been forwarded to our management team.\n\n"
    "Please type your question/problem below.\n\n"
    "A management representative will respond as soon as possible."
)


def _account_status_summary(student_account, kind):
    """Real, student-specific data for the bot to show - requirement 4/12/13
    ("If the system can securely retrieve the student's actual exam
    schedule / payment status, show the correct information") explicitly
    asks for this, as long as it's SAFE and SECURELY tied to a real
    student. Both conditions are why this function is only ever called
    with a `student_account` that came from `convo.student` - i.e. a
    conversation that was linked via the dashboard's own support_reference
    flow (see module docstring), never a bare phone-number guess.

    Only ever returns: course title, package tier, plain-English status,
    and expiry date - never a purchase id, payment/order id, gateway
    response, amount, or any other student's data (requirement: "Do not
    expose database information" / "Never expose ... sensitive payment
    data"). `kind` is "payment" or "schedule" purely to tweak the wording;
    the underlying data shown is the same either way.
    """
    purchases = (
        CoursePurchase.query.filter_by(student_account_id=student_account.id)
        .order_by(CoursePurchase.created_at.desc()).limit(5).all()
    )
    if not purchases:
        return None

    lines = []
    for p in purchases:
        label = f"{p.course.title} ({p.tier_label()})"
        if p.status == "paid" and p.is_active_subscription():
            exp = p.expires_at.strftime("%d %b %Y") if p.expires_at else "no expiry set"
            lines.append(f"- {label}: Active, valid till {exp}")
        elif p.status == "paid":
            lines.append(f"- {label}: Access has expired")
        elif p.status == "pending":
            lines.append(f"- {label}: Payment pending verification")
        elif p.status == "refunded":
            lines.append(f"- {label}: Refunded")
        else:
            lines.append(f"- {label}: Payment not completed")
    return "\n".join(lines)


def _payment_reply(convo):
    if convo.student:
        summary = _account_status_summary(convo.student, "payment")
        if summary:
            return (
                f"Hi {convo.student.full_name.split()[0]}, here's your current status:\n\n{summary}\n\n"
                "If something here looks wrong, reply 7 to talk to management."
            )
    return (
        "For payment/subscription issues, please check Payment History on your Student Dashboard - it shows the "
        "verified status of every payment. If your payment is completed but access hasn't unlocked, reply 7 to "
        "talk to management and share your order/reference ID."
    )


def _schedule_reply(convo):
    if convo.student:
        summary = _account_status_summary(convo.student, "schedule")
        if summary:
            return (
                f"Hi {convo.student.full_name.split()[0]}, here's what's on your account:\n\n{summary}\n\n"
                "Open your Student Dashboard for exact exam dates/timings. For a specific question, reply 7 to talk to management."
            )
    return (
        "Your exam schedule can be checked from your Student Dashboard under Tests/Mock Tests. "
        "For a specific scheduling question, reply 7 to talk to management."
    )


# Numbered-menu replies for options that get an immediate canned answer
# rather than a full human handoff. Value is either a plain string, or a
# callable(convo) for the two options (payment/schedule) that show the
# student's real, securely-linked account data when available - see
# _payment_reply/_schedule_reply above.
_MENU_REPLIES = {
    "1": ("exam_doubt",
          "For exam-related doubts, please check the exam instructions and syllabus on your Student Dashboard first. "
          "If you still need help, reply 7 to talk to management."),
    "2": ("payment_subscription", _payment_reply),
    "3": ("login_account",
          "For login/account problems: if you've forgotten your password, use 'Forgot Password' on the login page "
          "to reset it by OTP. For anything else, reply 7 to talk to management."),
    "4": ("exam_attempt",
          "For a problem during an exam attempt, please note down the exam name and roughly when it happened, "
          "then reply 7 to talk to management so we can check it from our side."),
    "5": ("result_query",
          "Your results are available under Results on your Student Dashboard once published. "
          "For a specific result query, reply 7 to talk to management."),
    "6": ("exam_schedule", _schedule_reply),
    "8": ("other", "Please briefly describe your issue, or reply 7 to talk to management directly."),
}

# Free-text keyword auto-answers (requirement 4) - checked before falling
# back to the numbered menu, since a student may type naturally instead of
# picking a number. Kept short and deliberately non-committal on anything
# account-specific.
_KEYWORD_REPLIES = [
    (("exam date", "when is my exam", "exam schedule", "next exam"), "exam_schedule", _schedule_reply),
    (("payment", "paid", "amount deducted", "money deducted", "locked", "subscription"), "payment_subscription", _payment_reply),
    (("forgot password", "reset password", "can't login", "cant login", "login problem"), "login_account",
     "You can reset your password using 'Forgot Password' on the login page (OTP-based). "
     "If that doesn't work, reply 7 to talk to management."),
]


def _phone_hint_student(whatsapp_number):
    """Best-effort, NON-authoritative label for the admin inbox only - see
    module docstring. Matches on the last 10 digits so it tolerates a
    leading country code either being present or not."""
    if not whatsapp_number or len(whatsapp_number) < 10:
        return None
    last10 = whatsapp_number[-10:]
    return StudentAccount.query.filter(StudentAccount.phone.like(f"%{last10}")).first()


def start_support_conversation(student_account):
    """Called by the Student Dashboard "WhatsApp Support" button. Reuses
    an existing not-yet-claimed conversation for this student if they
    clicked the button before without ever actually messaging (avoids
    spawning a new placeholder row every click), otherwise creates one.
    Returns the WhatsAppConversation (whatsapp_number is blank until the
    student's first real message arrives and claims it)."""
    existing = WhatsAppConversation.query.filter_by(
        student_account_id=student_account.id, whatsapp_number=""
    ).order_by(WhatsAppConversation.created_at.desc()).first()
    if existing:
        return existing

    convo = WhatsAppConversation(
        support_reference=generate_support_reference(),
        student_account_id=student_account.id,
        whatsapp_number="",
        status="NEW", category="other", bot_state="menu",
    )
    db.session.add(convo)
    db.session.flush()  # get convo.id/support_reference without a full commit
    return convo


def find_or_create_conversation(from_number, message_text):
    """Called from the inbound webhook. See module docstring for the full
    identification reasoning - short version: prefer an existing
    conversation for this exact phone number; otherwise, if the message
    contains a support reference generated by the dashboard button, claim
    that specific pre-created row; otherwise start a brand-new unlinked
    conversation."""
    convo = (
        WhatsAppConversation.query.filter_by(whatsapp_number=from_number)
        .filter(WhatsAppConversation.status != "RESOLVED")
        .order_by(WhatsAppConversation.created_at.desc()).first()
    )
    if convo:
        return convo

    ref_match = _SUPPORT_REF_RE.search(message_text or "")
    if ref_match:
        placeholder = WhatsAppConversation.query.filter_by(
            support_reference=ref_match.group(0), whatsapp_number=""
        ).first()
        if placeholder:
            placeholder.whatsapp_number = from_number
            return placeholder

    # No pending reference matched (organic WhatsApp message, not started
    # from the dashboard) - still create a conversation so management can
    # see and respond to it; just unlinked to any StudentAccount.
    convo = WhatsAppConversation(
        support_reference=generate_support_reference(),
        student_account_id=None,
        whatsapp_number=from_number,
        status="NEW", category="other", bot_state="menu",
    )
    db.session.add(convo)
    db.session.flush()
    return convo


def _log_inbound(convo, text, whatsapp_message_id):
    db.session.add(WhatsAppMessage(
        conversation_id=convo.id, direction="in", message_type="text",
        message_text=text, whatsapp_message_id=whatsapp_message_id,
    ))
    convo.last_message_at = datetime.utcnow()
    if convo.status == "WAITING_FOR_STUDENT":
        # Ball was in the student's court; they've responded, so this
        # needs management's attention again.
        convo.status = "IN_PROGRESS"


def _send_and_log(convo, text, message_type="bot_auto"):
    """Sends `text` to the conversation's number and logs it as an
    outbound message - a no-op (still logged, just with no
    whatsapp_message_id) if WhatsApp isn't configured or the send fails,
    per requirement 21 (never crash, degrade gracefully)."""
    msg_id = whatsapp_service.send_text_message(convo.whatsapp_number, text)
    db.session.add(WhatsAppMessage(
        conversation_id=convo.id, direction="out", message_type=message_type,
        message_text=text, whatsapp_message_id=msg_id,
    ))


def handle_incoming_message(from_number, message_text, whatsapp_message_id):
    """Top-level entry point called by the webhook route for every genuine
    (non-duplicate) inbound text message. Persists the message, updates
    conversation state, and sends an automated reply if the bot is still
    handling this conversation (bot_state == "menu"). Returns the
    WhatsAppConversation, mainly so the caller can commit and optionally
    use it for a notification/log line.
    """
    convo = find_or_create_conversation(from_number, message_text)
    _log_inbound(convo, message_text, whatsapp_message_id)

    if convo.bot_state is None:
        # Already handed off to management (requirement 17: "the bot stops
        # sending irrelevant automated responses"). Nothing more to do -
        # an admin will reply from the inbox.
        return convo

    text = (message_text or "").strip()
    text_lower = text.lower()
    # Only treat the message as a menu pick if it's JUST the number
    # (optionally "1.", "1)", " 1 ") - not merely containing a digit
    # somewhere in a longer sentence.
    choice_match = re.fullmatch(r"0*([1-8])[.).\s]*", text)
    choice = choice_match.group(1) if choice_match else None

    if choice == "7" or any(p in text_lower for p in _MANAGEMENT_PHRASES) or text_lower.strip() == "management":
        convo.category = "management"
        convo.bot_state = None  # stop automated replies from here on
        _send_and_log(convo, _MANAGEMENT_HANDOFF_TEXT)
        return convo

    if choice in _MENU_REPLIES:
        category, reply = _MENU_REPLIES[choice]
        convo.category = category
        _send_and_log(convo, reply(convo) if callable(reply) else reply)
        return convo

    for keywords, category, reply in _KEYWORD_REPLIES:
        if any(k in text_lower for k in keywords):
            convo.category = category
            _send_and_log(convo, reply(convo) if callable(reply) else reply)
            return convo

    # Greeting, first message, or anything unrecognized - (re)send the menu.
    _send_and_log(convo, _MENU_TEXT)
    return convo
