# Database Migration Notes (read this before deploying)

> **Update:** the manual SQL below is now handled automatically. See
> `_auto_migrate_new_columns()` in `app/__init__.py` - it runs on every
> app startup, inspects the live database, and adds any of these columns
> that are missing, using plain `ALTER TABLE ... ADD COLUMN` (works
> identically on SQLite and MySQL). You no longer need to run
> `MIGRATION_SETUP.sql` by hand for this. This file is kept as reference
> for what changed and why; skip straight to "If you're running this on a
> fresh/empty database" below - that's now true for upgrades too.

This project uses `db.create_all()` on startup (see `app/__init__.py`), not
Flask-Migrate/Alembic. That has one important consequence for everything
added across Phases 1-5:

> `db.create_all()` only creates tables that **don't exist yet**. It will
> happily create the 6 brand-new tables below on first run. It will
> **NOT** add new columns to tables that already exist in your live
> database (`exams`, `questions`, `student_attempts`).

## If you're running this on a fresh/empty database
Nothing to do. `db.create_all()` creates everything, new columns included.

## If you already have a populated database from before these phases
You need to add the new columns manually before starting the app, or the
app will throw SQL errors ("Unknown column ...") the first time it reads
one of these tables. Run this against your MySQL database:

```sql
-- Exam (Module 6: strict anti-cheat, Module 7: scheduled reminders)
ALTER TABLE exams
  ADD COLUMN strict_anti_cheat BOOLEAN DEFAULT FALSE,
  ADD COLUMN scheduled_at DATETIME NULL,
  ADD COLUMN syllabus TEXT NULL;

-- Question (Module 5: richer explanations)
ALTER TABLE questions
  ADD COLUMN detailed_explanation TEXT NULL,
  ADD COLUMN explanation_reference VARCHAR(300) NULL,
  ADD COLUMN explanation_youtube_url VARCHAR(400) NULL,
  ADD COLUMN explanation_voice_url VARCHAR(400) NULL;

-- StudentAttempt (Module 6: security-violation logging)
ALTER TABLE student_attempts
  ADD COLUMN security_violation_reason VARCHAR(100) NULL,
  ADD COLUMN security_violation_at DATETIME NULL;
```

New tables (`student_accounts`, `courses`, `course_purchases`,
`notifications`, `course_materials`, `exam_enrollments`) don't need
anything - `db.create_all()` creates them fine either way.

## Tiered pricing + Razorpay (Payments Module 10b)
As of this update, the app **auto-adds** the columns below to an existing
database on every startup (see `_auto_migrate_new_columns` in
`app/__init__.py`) - you normally don't need to run anything by hand.
If that ever fails (e.g. a DB user without ALTER privileges), run this
manually against MySQL:

```sql
ALTER TABLE courses
  ADD COLUMN price_exam_only FLOAT DEFAULT 0,
  ADD COLUMN price_exam_pdf FLOAT DEFAULT 0,
  ADD COLUMN price_class_only FLOAT DEFAULT 0;

ALTER TABLE course_purchases
  ADD COLUMN tier VARCHAR(20) DEFAULT 'combined',
  ADD COLUMN razorpay_order_id VARCHAR(80) NULL,
  ADD COLUMN razorpay_payment_id VARCHAR(80) NULL,
  ADD COLUMN razorpay_signature VARCHAR(200) NULL;
```

Existing `course_purchases` rows default to `tier='combined'`, so anyone
who already bought a course before this update keeps full access to
exams, PDFs/notes, and video classes - nothing is taken away.

## Recommended for future changes
If you plan to keep extending this project, it's worth adding
**Flask-Migrate** now rather than hand-writing `ALTER TABLE` statements
each time:

```bash
pip install Flask-Migrate
```

```python
# app/extensions.py
from flask_migrate import Migrate
migrate = Migrate()

# app/__init__.py
from app.extensions import migrate
migrate.init_app(app, db)
```

Then `flask db init`, `flask db migrate`, `flask db upgrade` going
forward, instead of manual SQL. Not required for anything already
built - just a suggestion for what comes next.

## Phase 2 - Subscription Plans + Secure Exam Features

Two brand-new tables, `subscription_plans` and `security_violations`,
are created automatically by `db.create_all()` on any deployment (fresh
or existing) - nothing to do there.

New **columns** on existing tables are added automatically by
`_auto_migrate_new_columns` on every startup, same mechanism as the
tiered-pricing migration above. If your DB user lacks `ALTER TABLE`
privileges and the auto-migration is skipped (check the logs), run this
manually:

```sql
-- CoursePurchase: link a purchase to a configurable SubscriptionPlan
ALTER TABLE course_purchases ADD COLUMN plan_id INTEGER NULL;

-- Exam: test type / availability / attempts / secure-exam & proctoring toggles
ALTER TABLE exams
  ADD COLUMN test_type VARCHAR(10) DEFAULT 'mock',
  ADD COLUMN free_without_plan BOOLEAN DEFAULT FALSE,
  ADD COLUMN availability_mode VARCHAR(12) DEFAULT 'flexible',
  ADD COLUMN available_from DATETIME NULL,
  ADD COLUMN available_until DATETIME NULL,
  ADD COLUMN max_attempts INTEGER NULL,
  ADD COLUMN camera_required BOOLEAN DEFAULT FALSE,
  ADD COLUMN mic_required BOOLEAN DEFAULT FALSE,
  ADD COLUMN face_detection_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN multi_face_detection_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN looking_away_detection_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN voice_detection_enabled BOOLEAN DEFAULT FALSE,
  ADD COLUMN max_warnings INTEGER DEFAULT 3;

-- StudentAttempt: unified 3-warning counter + auto-submit flag + link to a
-- logged-in StudentAccount (so plan-gated tests can be tied to an account)
ALTER TABLE student_attempts
  ADD COLUMN warning_count INTEGER DEFAULT 0,
  ADD COLUMN auto_submitted BOOLEAN DEFAULT FALSE,
  ADD COLUMN student_account_id INTEGER NULL;
```

Every column above defaults to the value that preserves old behavior
exactly - existing exams default to `test_type='mock'` (still gated the
same way exams always were), `availability_mode='flexible'` (no
schedule restriction, same as before), all proctoring flags default to
`FALSE` (no camera/mic/face/voice checks unless an admin opts an exam
in), and `max_warnings` defaults to `3` (the required unified limit).
Existing `course_purchases` rows keep `plan_id = NULL` and continue
being driven entirely by their `tier`, exactly as before.

Verified: this migration was tested end-to-end in this session against
both a brand-new empty SQLite database and a hand-built "old schema"
database missing all Phase 2 columns/tables - both start up cleanly and
end up with the full Phase 2 schema.


## Coupons (added after Phase 5)

Two brand-new tables (`coupons`, `coupon_redemptions`) - `db.create_all()`
creates these automatically on a fresh database, same as every other new
table above.

The three new columns on `course_purchases` (`coupon_id`, `original_amount`,
`discount_amount`) are handled by the existing `_auto_migrate_new_columns()`
mechanism in `app/__init__.py` (the same one that added `tier`, `plan_id`
etc. above) - added automatically on the next app restart, no manual SQL
needed.

Every column defaults to the value that preserves old behavior exactly:
existing purchase rows keep `coupon_id = NULL` / `discount_amount = 0`, and
`original_amount` is left NULL (the checkout/apply-coupon code always
falls back to `purchase.amount` when `original_amount` isn't set yet, so
nothing breaks for purchases created before this change).

Coupon usage is only ever recorded in `coupon_redemptions` at the moment a
purchase is actually marked "paid" (Razorpay verify, Razorpay webhook, or
an admin's manual UPI verification/activation) - never when a coupon is
merely applied at checkout, so an abandoned or failed payment never
consumes a student's coupon usage.

Verified: tested end-to-end in this session on a fresh SQLite database -
coupon creation, checkout apply/remove, and redemption recording (via the
admin manual-verify path) all confirmed working through real HTTP routes.

## Categories (added after Coupons)

One brand-new table (`categories`) - auto-created by `db.create_all()`.
`courses.category_id` (nullable FK) is added automatically by
`_auto_migrate_new_columns()`, same mechanism as above. Existing courses
keep `category_id = NULL` and simply show as "Uncategorized" in the admin
course form / ungrouped on the public marketplace - nothing breaks.

12 default categories (SSC, Banking & Insurance, Railways, UPSC, APPSC,
TSPSC, Teaching, Police, Defence, Technical & Engineering, Software Jobs,
Other) are seeded once on first run, the same pattern as the homepage
slides - admin can rename/reorder/deactivate/add more from
Admin > Categories at any time.

## Leaderboard Privacy Controls (added after Bulk Import)

One brand-new table (`site_settings`) - a small reusable key-value store,
auto-created by `db.create_all()`. No columns were added to any existing
table, so there's nothing to migrate on an existing database - the app
just starts reading two keys (`leaderboard_enabled`, `leaderboard_anonymous`)
that default to "leaderboard on, not anonymized" (today's behavior) until
an admin changes them from Admin > Settings.

Verified live: admin toggle-off correctly hides all ranking data from the
student-facing page (not just a cosmetic banner); toggle-on-anonymous
correctly masks other students' name/college/photo with a stable,
non-reversible per-student code while always showing a student their own
real name (marked "You") and everyone's rank/score/accuracy unchanged.

Scope note: this covers the *global* leaderboard, which is the only
leaderboard that exists in this codebase today. Per-exam / per-test-series
leaderboards (also mentioned in the spec) would need their own separate
view built first - adding admin toggles for a per-exam leaderboard that
doesn't exist yet would just be a dead control, so that wasn't done here.

## Notification Triggers Completed (Section 37)

One new column: `course_purchases.expiry_reminder_sent` (Boolean, default
False) - handled automatically by `_auto_migrate_new_columns()`, same as
every column above. Existing purchases default to `False`, so the very
next scheduled run of `flask send-expiry-reminders` correctly evaluates
them - nothing needs backfilling.

Two gaps closed:
1. **Payment failed** notifications were entirely missing across all four
   places a payment can fail (admin manual-reject, Razorpay browser-signature
   mismatch, Razorpay widget failure callback, Razorpay webhook) - all four
   now notify the student.
2. **Course expiry approaching** had no trigger at all. Added
   `flask send-expiry-reminders`, mirroring the existing
   `send-exam-reminders` command's pattern exactly (a CLI command run via
   system cron, not an in-process scheduler, so it's safe under multiple
   gunicorn workers). Notifies once per purchase within 3 days of expiry;
   the flag resets automatically on renewal/extension so the next cycle's
   reminder isn't silently skipped - verified this specifically, since it's
   the easy way this kind of feature quietly breaks after go-live.

Add to cron alongside the existing exam-reminders line:

    0 9 * * *  cd /path/to/exam_portal && /path/to/venv/bin/flask send-expiry-reminders >> /var/log/expiry_reminders.log 2>&1

## GST/Tax Settings (Section 52 — Commerce)

One new column: `course_purchases.tax_amount` (Float, default 0) - handled
automatically by `_auto_migrate_new_columns()`. Existing purchases default
to 0, which is correct since GST defaults to *disabled* platform-wide until
an admin turns it on from Admin > Settings.

Scope note: currency is deliberately NOT configurable. The payment gateway
here (Razorpay India account + UPI) only settles in INR - a currency
selector wouldn't change what's actually charged, so it would be exactly
the kind of non-functional control the project brief explicitly forbids
("do not create fake buttons"). GST, by contrast, genuinely changes the
amount sent to Razorpay and shown on the UPI QR.

`pricing_service.py` is the single source of truth for the discount->tax
ordering (GST is computed on the post-coupon-discount amount, not the
listed price), used identically at purchase creation, coupon apply, and
coupon removal so the three code paths can't drift out of sync with each
other.

Verified with exact-value assertions, not just "no error": GST off, GST
inclusive (backs tax out of an unchanged total), GST exclusive (adds tax on
top), and GST exclusive combined with a coupon (confirmed tax is computed
on the discounted base, not the original price) - all matched hand-computed
expected values exactly. Also confirmed live through real HTTP requests:
purchasing a ₹1000 course with 18% exclusive GST correctly shows/charges
₹1180, and applying a ₹100 coupon afterward correctly recalculates to
₹1062 (18% of the discounted ₹900 base), not a stale ₹1080.

## Super Admin Tier (Section 41 — Roles)

One new column: `users.is_super_admin` (Boolean, default False) - handled
automatically by `_auto_migrate_new_columns()`.

**Migration safety net, not just a column default.** Adding this column
with `default=False` to an *existing* database with admins already in it
would leave the platform with zero Super Admins - locking every admin out
of managing any other admin, permanently, the moment this update is
deployed. `_ensure_super_admin_exists()` runs on every app boot (after
migration, before anything else) and auto-promotes the oldest active admin
if no Super Admin exists yet - verified this exact scenario directly by
resetting the seeded admin's flag to False and confirming the safety net
correctly re-promotes it.

Why this exists: testing the admin-user-management feature earlier this
session showed that *any* admin could freely demote, deactivate, or delete
*any other* admin, with no protected tier - a real privilege/sabotage gap,
not just a spec checkbox. Now: a regular admin can freely create/edit/
delete Staff accounts, but touching another Admin-role account (edit,
delete, or promoting someone TO admin) requires Super Admin - enforced
both in the UI (the action isn't rendered) and independently server-side
(so bypassing the UI doesn't bypass the check). Verified both paths
directly: the users list correctly shows a lock icon instead of edit/
delete controls for a regular admin viewing another admin's row, AND a
direct POST to the delete route with a validly-obtained CSRF token (as if
someone scripted around the UI) is independently rejected server-side.

Roles are still just admin/staff + the is_super_admin flag - a distinct
"Teacher" role was NOT built. Your spec marks it "if required," and this
platform doesn't have an institute/teacher business model elsewhere in the
codebase; adding a Teacher role with a fake/unused permission set would
just be an unused control, which the project brief explicitly warns
against. Worth building for real if there's an actual teacher-facing
workflow needed.

## Per-Exam / Test-Series Leaderboards (Section 28, completing the gap)

One new column: `exams.show_leaderboard` (Boolean, nullable, no default -
NULL means "inherit the global Admin > Settings toggle"). Handled
automatically by `_auto_migrate_new_columns()`. Existing exams get NULL,
i.e. exactly today's global-only behavior, until an admin explicitly
overrides a specific exam.

Before building this, checked whether it would conflict with an existing,
intentional design decision: `public.result_page` deliberately withholds
score/rank/answers from students immediately after submission ("hide
results from students... published by the administrator"). Per-exam
leaderboards are NOT reachable from that anonymous/link-based flow - they
only exist inside the authenticated student-account area, reusing the same
underlying Result data the pre-existing *global* leaderboard already
exposes there. So this doesn't cross a line the app hadn't already crossed;
it just scopes the existing exposure down to one test.

New route `/student/leaderboard/exam/<exam_id>`, plus an exam picker added
to the main leaderboard page listing only exams the viewing student has
actually attempted AND that are currently visible (so the picker never
links to a page that immediately says "unavailable"). Admin controls the
per-exam override from the exam edit form: Inherit global / Always show /
Always hide.

Verified with real data across three exams and three students: an exam
with 2 attempts and another with 1 attempt correctly produced leaderboards
of exactly that size (not bleeding into each other or into the 3-entry
global leaderboard); the tri-state override was tested in isolation
(inherit-with-global-on, explicit-hide, explicit-show-despite-global-off)
before being tested live - a student's exam picker correctly showed only
the exam they'd attempted, the scoped leaderboard correctly excluded a
different exam's student, and an exam explicitly hidden via the admin
override correctly rendered the "turned off" state rather than leaking
data. Admin form save confirmed through a real authenticated POST.

## Teacher Role (Section 41, completing the roles gap)

No new columns - reuses the existing `users.role` string field (now
"admin" / "staff" / "teacher"). Nothing to migrate.

Built as deny-by-default: `TEACHER_ALLOWED_ENDPOINTS` is an explicit
allowlist checked in a blueprint-wide `before_request` guard, so any admin
route added in the future is automatically blocked for teachers unless
someone deliberately adds it to the list - failing closed, not open.
Verified this property directly: added the brand-new payment-history route
below and confirmed a teacher was blocked from it with zero additional
code, exactly as designed.

Scope: teachers can manage exams, the question bank, bulk import, exam
links, results, and study-material subjects. They cannot see payments,
coupons, pricing, categories, users, settings, the dashboard, analytics,
or the audit log. Sidebar navigation hides links to sections a teacher
can't reach. Verified live: every allowed route returns 200, every
restricted route redirects with a clear message, and regular admin/staff
access is completely unaffected by the new guard.

## Payment Transaction History (Section 10, the safer alternative to a full Order/Payment table split)

One new table: `payment_transactions` - auto-created by `db.create_all()`,
no columns added to any existing table, so nothing to migrate on an
existing database.

Why this instead of splitting CoursePurchase into separate Order/Payment
entities: that split would touch checkout, both Razorpay code paths, the
webhook, three admin routes, invoice generation, and the coupon service
built earlier - a lot of surface area on a working system for a schema
purity concern, not a functional gap. This instead closes the *actual*
gap I found while reviewing that flow: `CoursePurchase` only has one
`razorpay_order_id`/`payment_id` slot, so a retry after a failed payment
silently overwrote the previous attempt's transaction record, losing that
history entirely. `PaymentTransaction` is a pure append-only log sitting
alongside the existing purchase record - every transaction point (order
creation, both failure paths, the canonical success path, the webhook,
and the two admin routes that duplicate that success logic) now logs
through one shared `log_payment_transaction()` helper.

New admin view at Payments > History shows the full attempt history per
order. Automatically respects the Teacher-role restriction above with no
extra code, since it's just another admin route outside the allowlist.

Verified with a real failed-then-retried-then-succeeded scenario, not just
a single happy path: 4 transactions logged in the correct order (created,
failed with reason, created again with a new order ID, success) with
nothing overwritten - confirmed both at the data level and by rendering
the actual admin page through a real HTTP request.

## Stripe Integration (Section 9, international/non-INR payments)

No schema changes - reuses `CoursePurchase`'s existing `razorpay_order_id`/
`razorpay_payment_id`/`payment_method` columns (legacy-named, but function
as generic string slots - `activate_paid_purchase` already parameterizes
`method`, so nothing gateway-specific was assumed). New table
`payment_transactions` (added for the audit-trail feature above) logs
Stripe attempts the same way as Razorpay/UPI ones.

New env vars (`.env.example` should be updated): `STRIPE_SECRET_KEY`,
`STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CURRENCY`
(default "usd"), `STRIPE_INR_TO_USD_RATE` (default 83). Leave
`STRIPE_SECRET_KEY` blank to disable the "Pay with Card (International)"
button entirely - same fallback pattern as Razorpay.

This is also what makes currency selection genuinely real rather than
decorative, closing the loop on something flagged earlier this session:
Razorpay/UPI can only ever settle in INR, so a currency picker next to
them would be non-functional theater. Stripe genuinely settles in
whatever currency the Checkout Session is created in - course prices
(always stored in INR) are converted via STRIPE_INR_TO_USD_RATE at
checkout time, only for the Stripe path.

New route `/student/checkout/<id>/stripe/start` creates a Checkout Session
and redirects; new webhook `/api/webhooks/stripe` (subscribe to
`checkout.session.completed` and `checkout.session.async_payment_failed`
in the Stripe Dashboard) does the actual activation - same "never trust
the browser alone" rule as Razorpay's webhook, since a student closing the
tab right after paying must not be the only path to activation.

**Testing limitation, stated plainly:** this sandbox cannot reach
`api.stripe.com` (network is restricted to package registries only - the
same restriction applied to Razorpay all session; no live call was ever
made to Razorpay's API either). What "verified" means here specifically:
currency-conversion math tested directly; Checkout Session request
construction tested by mocking `stripe.checkout.Session.create` and
inspecting exactly what would have been sent (correct amount in cents,
correct currency, correct client_reference_id); webhook signature
verification tested with a real HMAC-SHA256 signature computed by hand
(valid, tampered, and wrong-secret cases) rather than a live Stripe call,
since that's pure cryptographic verification with no network dependency;
and the full webhook-to-activation flow was run through the actual live
route with that hand-signed payload, catching and fixing a real bug in
the process - this Stripe SDK version's `Webhook.construct_event` returns
a `StripeObject`, not a plain dict, so `.get()` calls in my first draft
raised `AttributeError` in production, not just failed a test. Also
verified: invalid signature is rejected (400), and a duplicate webhook
delivery (Stripe retries these) doesn't double-activate or double-log,
via the existing idempotency guard in `activate_paid_purchase`.

**Not verified:** an actual live Checkout Session created against Stripe's
real API, and a real card payment flowing through it end-to-end. That
requires either network access this sandbox doesn't have, or manual
testing with real Stripe test-mode keys after deployment.

## Ticker Banner (new feature, per user request)

One new table: `ticker_items` - auto-created by `db.create_all()`. Reuses
the existing `site_settings` key-value store for the global on/off toggle
(`ticker_enabled`, defaults to true). Nothing to migrate.

Admin-managed scrolling announcement strip under the public navbar
("Today's Offer...", "Our Specialty..."). Pure CSS marquee (duplicated
content + `translateX(-50%)` keyframe) - no JS, pauses on hover so a
linked item is actually clickable, respects `prefers-reduced-motion`.
Each item is independently toggleable; there's also one global switch to
turn the whole strip off without touching individual items.

Deny-by-default Teacher-role guard (built earlier this session) protected
this automatically - verified a teacher account is blocked from
`/admin/ticker*` with zero additional code, same as the payment-history
route earlier.

Verified live: admin create (with and without a link) → both items render
on the public homepage AND the courses page (global context processor, so
every public page gets it for free) → linked item renders as `<a>`,
unlinked as `<span>` → global disable toggle correctly removes the whole
block → re-enabling restores it.

## Slider Black Background Fix (user-reported bug)

Root cause: a pre-existing filename mismatch in `app/__init__.py`'s
default-slide seed data - it references `slide2_ai_analysis.jpg` etc., but
the actual bundled files in `app/static/images/slides/` were named
`slide2_neet.jpg` etc. (leftover from an earlier version of the seed data,
never renamed to match when the seed dict was updated). Every default
slide's background-image 404'd, leaving only the dark readability-overlay
gradient visible - which reads as flat solid black, not obviously "an
image is missing."

Fixed both the data AND the failure mode:
1. Copied the 4 real slide images to the filenames the seed code (and any
   already-seeded database - this was NOT a fresh-install-only bug) expects,
   so existing HomeSlide rows are fixed without needing a re-seed or DB
   migration.
2. Added `background-color: #1c2440` (ink-navy) as a fallback on `.kh-slide`
   itself, so if a future slide's image is ever missing for any reason -
   admin uploads then deletes a file, a typo in a custom slide - it
   degrades to a branded dark background instead of flat black with no
   indication anything is wrong.

Verified: all 4 seeded slide image URLs now return 200 (were 404), and
slide 2's title matches the exact slide shown in the reported screenshot.

## Mobile Buttons Not Responding (user-reported bug)

Root cause found: `.kh-hero::before` (a purely decorative dot-texture
layer on the homepage hero) covers the entire hero section (`inset: 0`)
with no `pointer-events: none` set. That box sits directly over the
hero's primary CTA buttons ("Register Free" / "Explore Courses" /
"Explore Now"). CSS `mask-image` was fading it out *visually* toward the
right side of the hero, but a CSS mask only affects rendering, not hit-
testing - the element was still there for click purposes across its full
area. This is most noticeable on mobile, where the hero fills most of the
screen and stacks the CTA buttons directly under this layer, versus
desktop where the buttons sit further from the masked region.

Fixed by adding `pointer-events: none` to that layer, plus five other
purely-decorative absolutely-positioned elements I found on the same
audit that had the same gap (navbar underline, admit-card's perforated
edge + corner brackets, "Most Popular" pricing tag, course discount
badge) - all cosmetic-only elements that should never intercept a click,
fixed preventively even though they're much smaller/lower-risk than the
hero overlay.

Left alone on purpose: `.kh-slider-dots` (the carousel navigation dots -
those ARE meant to be clickable) and `.stat-card i` (a small low-risk
decorative icon, not worth the churn).

**Honest limitation:** I don't have a real mobile device or browser to
tap-test in this environment - this fix is based on a thorough CSS/hit-
testing audit that found one clear, high-confidence culprit (the hero
overlay) plus preventive hardening elsewhere, not a confirmed
reproduction-then-fix on an actual phone. If buttons are still
unresponsive after this update on specific pages, that's the next thing
to narrow down with the user directly (which exact page/button).

## Missing Icon Glyph (empty box) Bug (user-reported, screenshot evidence)

Root cause: the bundled Bootstrap Icons stylesheet declares
`font-display: block`, which hides icons for up to ~3 seconds while the
font downloads, then permanently falls back to a system font if it's not
ready in time. Bootstrap Icons uses private-use-area Unicode codepoints
that no ordinary system font maps a glyph to, so that fallback renders as
a visible empty box - exactly what showed up next to "Our Specialty" in
the reported screenshot, on what's most likely a slower mobile
connection where the font didn't finish loading in time.

Fixed by adding `<link rel="preload" ... as="font">` for the woff2 file
so the browser starts fetching it immediately, in parallel with the
stylesheet, rather than only starting once the CSS itself is parsed and
requests it - closing most of that timing gap without touching the
vendored third-party CSS file itself.

Applied to all 12 templates in the app that load the icon font (checked
exhaustively, not just the one page from the screenshot) - initially
missed 7 of them on the first pass (only fixed the 4 main shared shells)
and caught the gap via a full regression assertion across every public/
auth route before considering this done, rather than stopping at the one
page I had direct evidence for.

## Ticker Banner Not Showing (user-reported - not a code bug, a design gap)

Root cause: the ticker feature was built to show nothing until an admin
explicitly adds an item (Admin > Ticker Banner > New Item) - deliberately
not seeded with placeholder text, unlike HomeSlide which does have
default slides (an empty homepage hero looks broken; an empty ticker just
means "nothing to announce today," a valid state). This meant a fresh
install showed no ticker at all, which read as a bug rather than an
intentional empty state.

Fixed by seeding 3 sensible default items on first run, using the exact
same seeding pattern already established for HomeSlide/Category in this
codebase (`if TickerItem.query.count() == 0: seed defaults`) - runs
automatically inside `create_app()` on every app startup, so a simple
restart after updating the code populates it, no manual re-seed command
needed.

**Trade-off, stated plainly rather than left as a surprise:** this is the
same convention already used for HomeSlide/Category, and it has the same
edge case those already have - if an admin deletes every ticker item
intending to show none at all, the next server restart will re-seed the
3 defaults, since "zero rows" is the trigger condition. Not a new
inconsistency introduced here, but worth knowing before relying on "leave
it empty on purpose" as a way to hide the strip - use the global
enable/disable toggle in Admin > Ticker Banner for that instead, which
this seeding logic doesn't touch.

Verified: fresh boot seeds exactly 3 items and they render correctly on
the homepage; a second boot against the same database does NOT duplicate
them (still 3, not 6); and the delete-all-then-restart edge case above
was deliberately tested and confirmed to behave exactly as described, not
guessed at.

## WhatsApp Button + Payment Method Detail (user request)

**Already existed, clarified for the user:** UPI apps (PhonePe/GPay/Paytm
intent buttons + a UPI QR tab), Debit/Credit Card, Netbanking and Wallets
were ALL already offered via the "Pay with Razorpay" button - the
checkout widget config already had `method: {upi:true, card:true,
netbanking:true, wallet:true}` set explicitly (built earlier this
session). Nothing to build there; the ask was mostly a UI-clarity gap.

**New: payment method detail capture.** One new column,
`payment_transactions.method_detail` - handled by
`_auto_migrate_new_columns()` for anyone with an existing (this-session)
payment_transactions table, `db.create_all()` for anyone fresh. After a
Razorpay payment activates, `razorpay_service.fetch_payment_method_detail()`
calls Razorpay's Payments.fetch API (a second, server-side call - the
browser-side checkout response never includes which method was used) and
turns the result into a human-readable string: "UPI - likely PhonePe
(x@ybl)", "Debit Card - Visa ****4242", "Netbanking - HDFC Bank", "Wallet
- Paytm". Shown on both the main Payments list and the detailed Payment
History page. UPI-app identification is heuristic (VPA handle suffix ->
likely app), stated as "likely" rather than certain, since Razorpay
itself doesn't label which literal app was used.

Best-effort by design: if the Razorpay lookup fails for any reason
(network hiccup, payment_id not found), it returns None rather than
raising - this runs AFTER activation already succeeded, so a lookup
failure never blocks or reverses a real payment, it just leaves that one
transaction's method column blank.

**New: WhatsApp floating chat button**, admin-configurable from Admin >
Settings (number, pre-filled message, on/off) - stays hidden until BOTH
enabled AND a number is actually set, so it can never render a dead
button. Fixed z-index (1040) verified against this project's actual
bundled Bootstrap values (navbar sticky=1020, offcanvas mobile menu=1045)
so it sits above the navbar but is correctly covered when the mobile menu
opens, rather than floating awkwardly on top of it.

**Bug found and fixed while building this:** Admin > Settings had grown
into three separate `<form>` tags (Leaderboard, GST, now WhatsApp), and
the two older forms were missing hidden fields for settings added after
them - saving the Leaderboard section alone would have silently reset GST
and WhatsApp back to their defaults. Rather than patch this with more
hidden fields (the same mistake, deferred), merged into a single form
covering every section, which removes this entire bug class permanently
rather than just this one instance of it.

Verified: settings save without wiping unrelated sections (tested by
pre-setting GST values, saving via the single form, confirming they
survived); WhatsApp button correctly hidden with no config, correctly
visible with a real number, with a correctly URL-encoded wa.me link;
payment method detail tested through the real `activate_paid_purchase`
function with a mocked Razorpay response and confirmed showing up
correctly on both admin pages via real HTTP requests.

## Content Security Policy Blocking Fonts, PDF Viewer, and Map (screenshot evidence)

Root cause, visible directly in the browser DevTools console the user
shared: the app's Content-Security-Policy header (pre-existing, set in
`app/__init__.py`, not something added this session) allowed
`cdn.jsdelivr.net` but was never updated when Google Fonts (Fraunces/IBM
Plex) got introduced earlier this session - `style-src` didn't include
`fonts.googleapis.com`, so the browser was silently blocking the
stylesheet load entirely, on every single page, in any real browser. This
is exactly the class of bug my own testing this whole session could never
have caught: Flask's test client checks HTTP status codes and HTML
content, it does not enforce CSP the way a real browser does - a real
browser's console was the only way to surface it.

Rather than patch just the one error visible in the screenshot, audited
every external domain referenced anywhere in the templates and found two
more pre-existing gaps (not introduced this session, just never
noticed): the "our location" map iframe (`openstreetmap.org`, on both the
homepage and contact page) was missing from `frame-src`, and the
study-material PDF viewer's `pdf.js` library (`cdnjs.cloudflare.com`) was
missing from `script-src` - both silently broken for as long as this CSP
header has existed, unrelated to anything built this session.

Also fixed a subtler gap: Google Fonts serves its stylesheet from
`fonts.googleapis.com` but the actual `.woff2` font FILES it references
live on a *different* domain, `fonts.gstatic.com` - allowing the
stylesheet without also allowing the font domain would still silently
fail to render any glyphs. Both are now in the policy.

**Deliberately NOT added:** `js.stripe.com`/`checkout.stripe.com` in
frame-src or script-src, `api.stripe.com` in connect-src. Checked first -
this project's Stripe integration is a pure server-side redirect
(`redirect(session.url)`), no Stripe script or iframe is ever loaded on
our own pages, so adding those origins would be unused permissions on a
security header, not a fix for anything actually broken. Kept the policy
to exactly what's used, on purpose.

Verified: the live response header on every public/auth route now
includes all four newly-needed domains - checked directly against the
actual `Content-Security-Policy` header value via real HTTP requests, not
just visual inspection of the code.

**Investigated but not conclusively resolved:** the user also reported
text becoming invisible in dark mode "throughout the whole project."
Re-audited every `!important` color rule in the CSS for the same class of
bug fixed earlier this session (dark-navy text/links on a near-black
dark-mode background) and found nothing new beyond what was already
fixed - the remaining `!important` rules are all light-colored text
intentionally paired with permanently-dark backgrounds (exam header,
timer pill), which is correct in both themes. The CSP fix means fonts
now load and fall back correctly either way, which may or may not be
the actual cause of the reported invisible text - stated honestly rather
than assumed fixed, since no screenshot of the specific dark-mode
rendering was available to confirm which page/element is affected.

## MySQL → SQLite Conversion + Render Deployment Readiness (user request)

Converted the database layer from MySQL (PyMySQL) to SQLite as the
default, for both local development and Render deployment, per an
explicit request to inspect the whole project, find every MySQL
dependency, and convert without changing business logic or removing
features.

**What was actually MySQL-specific (turned out to be very little):** the
whole ~9,600-line app is ORM-first - the *only* raw SQL in the entire
project was one `ALTER TABLE ADD COLUMN` statement in
`_auto_migrate_new_columns` (already ANSI-compatible, untouched). No
MySQL-only SQL syntax (`AUTO_INCREMENT`, `ON DUPLICATE KEY`, backticks,
`GROUP_CONCAT`, etc.) anywhere. Cascades are ORM-level
(`cascade="all, delete-orphan"`), defaults are Python-side
(`default=datetime.utcnow`), not MySQL server-side functions. So the
conversion is contained almost entirely to `app/config.py` and
`app/__init__.py`.

**Changed:**
- `app/config.py` - SQLite default at `instance/exam_portal.db` (built
  from this file's own location via `basedir`, not a hard-coded path so
  it works unmodified on Windows/Linux/Render); `DATABASE_URL` still
  overrides it with any SQLAlchemy URL (Postgres/MySQL/etc.) if ever
  needed. Engine options are conditional now: SQLite gets `connect_args:
  {timeout: 15}`; anything else keeps the old `pool_pre_ping`/
  `pool_recycle` MySQL/Postgres pooling options.
- `app/__init__.py` - new `_configure_sqlite_pragmas()`, a SQLAlchemy
  `connect` event listener that sets `journal_mode=WAL`,
  `synchronous=NORMAL`, `busy_timeout=30000`, `foreign_keys=ON` on every
  SQLite connection (no-op for any other database). `foreign_keys=ON`
  matters more than it sounds: SQLite silently ignores FK constraints by
  default even though the schema declares them, so without this a bug
  elsewhere could insert orphaned rows (e.g. an Answer pointing at a
  deleted Attempt) that MySQL would have rejected - verified this is
  actually active with a real test (inserting a row with a bogus FK is
  correctly rejected with `FOREIGN KEY constraint failed`). Also added a
  directory-creation safety net so a fresh checkout / first Render deploy
  doesn't fail with "unable to open database file" before `instance/`
  exists - this reads the real resolved path back from `db.engine.url`
  rather than re-deriving it, because Flask-SQLAlchemy 3.x resolves a
  *relative* `sqlite:///` URI against `app.instance_path`, not the
  process's cwd (a genuinely surprising behavior, found by testing an
  override of `DATABASE_URL` with a relative path - documented in
  `.env.example` so nobody else loses time on it).
- `requirements.txt` - removed `PyMySQL` (nothing else needed it).
- `Dockerfile` - removed `default-libmysqlclient-dev` (was only ever
  needed if using a *compiled* MySQL client; PyMySQL is pure Python and
  is gone anyway).
- `gunicorn_conf.py` - now binds to `$PORT` (was hard-coded to `8000`,
  which would have made the service unreachable on Render - Render
  assigns the port dynamically). Also capped worker count sensibly
  (`WEB_CONCURRENCY`, default `min(cpu_count()*2+1, 3)`) instead of the
  bare CPU-scaling formula, since SQLite allows one writer at a time for
  the whole file regardless of worker *process* count, and the formula
  can wildly overshoot on small/shared-CPU Render instances.
- `docker-compose.yml` - removed the `db` (MySQL) service and its
  `depends_on`/`DB_HOST` override entirely; local Docker use now just
  runs the app against the same SQLite file as everywhere else,
  persisted through the existing `./instance:/app/instance` volume.
- `.env` / `.env.example` - swapped the MySQL `DB_*` block for the
  SQLite default + `DATABASE_URL` override docs; removed a leftover
  hard-coded Windows path that was sitting commented out in `.env`
  (`sqlite:///D:/complated_project_fille/...`) - exactly the kind of
  non-portable path the conversion was asked to avoid.
- `render.yaml` (new) - a Render Blueprint using the existing Dockerfile
  as-is, with a persistent disk mounted at `/app/instance` (covers the
  database file *and* uploads/reports together - one disk, one mount).
  Every secret-shaped env var is `sync: false` so nothing sensitive lands
  in the repo; Render prompts for them once in the dashboard.
- `README.md` - updated the MySQL-centric quick-start/Docker/production
  sections to reflect SQLite as the default, added a "Deploying to
  Render" section pointing at `render.yaml`.
- `MIGRATION_NOTES.md` / `MIGRATION_SETUP.sql` - added notes that the
  manual `ALTER TABLE` steps they describe are now handled automatically
  by `_auto_migrate_new_columns` on every startup (true for SQLite *and*
  MySQL) - kept both files as historical reference rather than deleting
  them.

**Also fixed a real, separate seed-data bug spotted along the way, since
it directly affects "run locally, verify everything works":** the auto-
seeded default `SiteSetting`/`HomeSlide` rows were unaffected, but while
exercising every table I confirmed `Exam.attempts` has no cascade and
`StudentAttempt.exam_id` is `NOT NULL` - deleting an Exam that already
has attempts is correctly rejected once `foreign_keys=ON` is actually
enforced. This is *not* a bug introduced by the SQLite conversion; it's
the app's existing constraint becoming properly enforced (MySQL/InnoDB
would reject the same delete for the same reason) - noted here rather
than "fixed", since changing it would mean changing cascade/business
behavior, which was explicitly out of scope for this task.

### SQLite in production / on Render

**Where the database lives:** `instance/exam_portal.db` (relative to the
repo root - resolves to an absolute path via `basedir` in
`app/config.py`, so it's the same file whether you're on Windows, Linux,
or inside the Render container at `/app/instance/exam_portal.db`).

**Render Persistent Disk:** required. Without it, Render's container
filesystem is ephemeral and the database (plus every uploaded course PDF,
student photo, and generated result PDF) is wiped on every redeploy or
restart. `render.yaml` already configures this: a 1GB disk named
`exam-portal-data` mounted at `/app/instance`. That single mount covers
the database file and `UPLOAD_FOLDER`/`GENERATED_PDF_FOLDER`, since all
three already live under `instance/` (see `app/config.py`).

**Does the database survive a redeploy/restart?** Yes, *as long as the
Render disk is attached* - a persistent disk is not part of the
container image, so redeploying the app (new image, same service) does
not touch it. Deleting the *disk itself* (a separate action from
redeploying) or recreating the service from scratch would lose it, same
as it would for a managed MySQL/Postgres instance you forgot to back up.

**Concurrency - what WAL mode does and doesn't buy you:** `journal_mode=
WAL` (set in `app/__init__.py`) allows many concurrent *readers* plus
*one* concurrent *writer* at a time; a second simultaneous writer waits
(up to the 30s `busy_timeout`) rather than running in parallel, unlike
MySQL/Postgres which handle many concurrent writers natively. For a
single web service with a small number of Gunicorn workers (see
`gunicorn_conf.py`), this is a reasonable, well-understood limit - it
comfortably handles the kind of write pattern this app has (exam
autosave every ~10s per student, occasional payment webhooks,
notifications). It is **not** a drop-in replacement for MySQL under
heavy write concurrency (e.g. thousands of students autosaving in the
same second, or multiple app server instances writing to the same file
over a network filesystem - SQLite is explicitly not designed for
network filesystems at all). If that ever becomes the bottleneck, set
`DATABASE_URL` to a managed Postgres/MySQL instance instead; nothing
else in the app assumes SQLite specifically.

**Backups:** since it's one file, a backup is `cp instance/exam_portal.db
somewhere-safe.db` (SQLite's own docs recommend using the `.backup`
command or the online backup API rather than copying a live file
mid-write, to avoid grabbing it mid-transaction - for a low-write app
like this, a simple scheduled copy during low-traffic hours is
reasonable). Render's disk itself is not automatically backed up outside
of whatever snapshot features are available on your Render plan.
