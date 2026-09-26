# WhatsApp Support Bot + Management Handoff — Implementation Report

## A. Files Inspected
`app/__init__.py`, `app/config.py`, `app/extensions.py`, `app/models.py`, `app/admin/routes.py`,
`app/student/routes.py`, `app/api/routes.py`, `app/services/razorpay_service.py`,
`app/services/payment_service.py`, `app/templates/base.html`, `app/templates/student/_dashboard_base.html`,
`app/templates/student/login.html`, `app/templates/admin/settings.html`, `app/templates/admin/payments_list.html`,
`requirements.txt`, `.env.example` — to understand the existing blueprint structure, auth (Flask-Login for admins,
session-based for students), the self-healing column-migration pattern, the existing Razorpay/Stripe webhook
conventions (which this feature deliberately mirrors), and to confirm no admin notification system or WhatsApp
Cloud API integration already existed.

## B. Files Created
- `app/services/whatsapp_service.py` — raw Cloud API calls (send text/template), webhook verification
- `app/services/whatsapp_bot_service.py` — menu, keyword auto-replies, handoff, secure student linking
- `app/templates/admin/whatsapp_inbox.html` — support inbox list
- `app/templates/admin/whatsapp_conversation.html` — conversation detail/reply page
- `WHATSAPP_SUPPORT_REPORT.md` — this document

## C. Files Modified
- `app/config.py`, `.env.example`, `requirements.txt` (added `requests`)
- `app/models.py` (two new models + `generate_support_reference()`)
- `app/__init__.py` (admin sidebar badge count in the global context processor)
- `app/api/routes.py` (webhook routes)
- `app/admin/routes.py` (inbox, conversation detail, reply, note, update routes)
- `app/student/routes.py` (`whatsapp_support` route)
- `app/templates/base.html` (admin sidebar link + badge)
- `app/templates/student/_dashboard_base.html` (sidebar link + floating button)
- `app/templates/student/login.html` (unauthenticated help link)
- `app/templates/admin/settings.html` (message-template name settings)
- `app/static/css/style.css` (floating-button styling, reused from the existing public-site button)

## D. Database Changes
Two new tables, auto-created by the existing `db.create_all()` call on next startup — **no manual migration
command needed**, consistent with how every other table in this project has been added:
- `whatsapp_conversations` — id, support_reference (unique), student_account_id (nullable FK), whatsapp_number,
  status, category, priority, assigned_to (FK to admin `users`), bot_state, created_at, updated_at, last_message_at
- `whatsapp_messages` — id, conversation_id, direction, message_type, message_text, whatsapp_message_id (unique,
  used for webhook dedup), delivery_status, is_internal_note, sent_by, created_at

## E. Environment Variables Required
```
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_API_VERSION=v20.0
```
Also set the existing `whatsapp_number` value under **Admin → Settings → WhatsApp Chat Button** — this is the
official business number used for both the pre-existing public "click to chat" button and the new student
dashboard support button (one number, one setting, not duplicated).

## F. WhatsApp Cloud API Setup Steps
1. Create/use a Meta Business Account and a WhatsApp Business Platform app at developers.facebook.com.
2. Under the app → WhatsApp → API Setup, get a permanent access token (or a System User token for production),
   the Phone Number ID, and the WhatsApp Business Account ID.
3. Put those three values in `.env` as `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_BUSINESS_ACCOUNT_ID`.
4. Under App Settings → Basic, copy the App Secret into `WHATSAPP_APP_SECRET`.
5. Pick any random string yourself for `WHATSAPP_VERIFY_TOKEN` (e.g. generate one with
   `python -c "import secrets;print(secrets.token_hex(16))"`).

## G. Meta Developer Setup Steps (Webhook)
1. In the app dashboard, go to WhatsApp → Configuration.
2. Callback URL: `https://yourdomain.com/api/webhooks/whatsapp`
3. Verify Token: the exact same string you put in `WHATSAPP_VERIFY_TOKEN`.
4. Click Verify and Save — Meta calls the GET endpoint once; it will only succeed if your app is deployed and
   reachable with that env var already set.
5. Subscribe to the `messages` webhook field (and `message status` if you want delivery-status tracking, already
   handled by this implementation).

## H. Webhook Configuration Notes
- GET requests are the one-time verification handshake; POST requests are every subsequent event.
- POST requests are signature-verified via `X-Hub-Signature-256` whenever `WHATSAPP_APP_SECRET` is set — this is
  enforced (rejects on mismatch). If left unset, the webhook still processes messages (so real support requests
  aren't silently dropped) but logs a warning on every request; **set the secret before going live**.
- Duplicate webhook deliveries (Meta retries aggressively on anything but a fast 200) are detected by
  `whatsapp_message_id` and skipped.
- The route is rate-limited to 120/minute, mirroring the existing Razorpay webhook.

## I. Local Testing Procedure
1. `pip install -r requirements.txt` (adds `requests`).
2. Set the six `WHATSAPP_*` env vars in `.env`. For local testing without a public URL, use a tunnel
   (e.g. ngrok) so Meta can reach your webhook.
3. Run the app, log in as a student, click "WhatsApp Support" in the sidebar or the floating button — confirm it
   opens WhatsApp with a prefilled message containing your name and a `KHE-SUP-#####` reference.
4. Send that message from your own WhatsApp to the configured test number — confirm:
   - The webhook receives it, creates/claims a `WhatsAppConversation` linked to your student account
   - The bot replies with the numbered menu
   - Replying "2" shows your real purchase/subscription status (or the generic fallback if you have none)
   - Replying "7" (or "talk to management") sends the handoff message and stops further auto-replies
5. In Admin → WhatsApp Support, confirm the conversation appears, open it, reply — confirm the reply arrives on
   WhatsApp, and the status flips to "Waiting for Student".
6. Reply again from WhatsApp — confirm status flips back to "In Progress".
7. Click "Mark Resolved" — confirm the status updates (and, if a `support_resolved` template is configured, a
   template send is attempted).

## J. Production Deployment Procedure
1. Deploy the updated code (no destructive migration — the new tables are additive).
2. Set all six `WHATSAPP_*` env vars for real (never commit them — `.env` is already gitignored).
3. Point the Meta webhook at your real production domain.
4. Set the official WhatsApp number in Admin → Settings.
5. Verify the webhook handshake succeeds in the Meta dashboard before relying on it.

## K. Security Changes / Considerations
- Webhook signature verification (HMAC-SHA256 via `WHATSAPP_APP_SECRET`), mirroring the existing
  Razorpay/Stripe webhook secret pattern in this codebase.
- Every admin route is `@login_required`, same guard as the rest of the admin panel — no new auth system.
- **No student-facing route ever reads conversation contents back** — the only student-facing action is a
  one-way redirect to WhatsApp. This is what actually satisfies "a student must never access another student's
  conversation": there is no route to misuse in the first place.
- Student identification is reference-code-based, never a bare phone-number match (see design note below) —
  this was the single most important security decision in this feature.
- Rate limiting on both the webhook (120/min) and the student's own support-link route (20/hour, to prevent
  someone hammering conversation creation).
- Internal notes are stored with `is_internal_note=True` and are never passed to `whatsapp_service.send_*`.

## L. Student Flow
Dashboard (sidebar link or floating button) → generates a `KHE-SUP-#####` reference tied to the logged-in
student → opens WhatsApp with a prefilled message → student sends it → bot shows the numbered menu → student
picks an option or types naturally → bot answers directly (with real account data for payment/schedule
questions, when the conversation is confirmed linked to them) or hands off to management on request → student
gets a real, human reply from the admin inbox.

## M. Management Flow
Admin → WhatsApp Support → sees New/In Progress/Waiting/Resolved counts and a searchable/filterable list →
opens a conversation → sees full message history, the bot's own replies, and (if linked) the student's real
purchase/subscription status inline → replies (sent for real via the Cloud API) or adds an internal note (never
sent) → sets priority/category/assignment → marks resolved (optionally triggering a template close-out message)
or reopens later.

## N. Known Limitations
1. **No live testing was possible in the environment this was built in** — no network access to install
   Flask/SQLAlchemy or reach `graph.facebook.com`. Everything was verified via static analysis, full-project
   compilation, a complete `url_for()`/CSRF audit, and isolated unit tests of the bot's decision logic (menu,
   keywords, handoff, and the student-linking flow) using a hand-built fake ORM layer — not a real database or a
   real Meta API call.
2. Only `send_template_message` for "Support Resolved" is actually wired into a flow; the other four template
   settings (payment_success, exam_reminder, subscription_expiry, support_received) are configurable and ready
   to call, but nothing triggers them yet — those are proactive/marketing-adjacent sends that need their own
   opt-in flow per WhatsApp policy, which is genuinely separate scope from a support-conversation feature.
3. Stripe-paid purchases aren't included in refund automation (a pre-existing limitation from the earlier
   payment work, unrelated to WhatsApp, noted here for completeness since it affects what the bot can truthfully
   tell a student about a Stripe refund).
4. Media messages (images/documents — e.g. a payment screenshot) are logged with a placeholder and immediately
   routed to a human; the bot cannot read or verify their contents.
5. The `_phone_hint_student()` helper (unused so far, kept for a possible future "possibly this student" admin
   hint) is explicitly non-authoritative and must never be upgraded to auto-link a conversation — phone numbers
   are reassignable and self-reported.

## O. Future Improvements
- Interactive list/button messages instead of a numbered-text menu, once template approval is set up.
- A dedicated admin-side notification system (this feature currently uses a sidebar count badge because no
  general notification system existed to integrate with).
- Wiring the remaining four message templates into their natural trigger points (e.g. `payment_success` from
  `activate_paid_purchase`, `subscription_expiry` from the existing expiry-reminder logic).
- Rich media replies from management (the API supports sending images/documents; only text is wired in).
- An audit/report view of resolved conversations by category, for spotting recurring issues.

---
**Design decision worth re-reading if this code is modified later**: student identification is deliberately
reference-code-based (`KHE-SUP-#####`, generated only for a logged-in student and echoed back in their own
WhatsApp message), never inferred from a bare phone-number match. See the module docstring at the top of
`app/services/whatsapp_bot_service.py` before changing this.
