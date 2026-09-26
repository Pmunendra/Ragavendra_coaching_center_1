# Kalyani Exam Hub — Phase 2 Deliverable
Subscription Plans + Secure Exam Features, built on top of the existing (Phase‑1‑cleaned) project.

Everything below was actually implemented in the project at
`exam_portal_enhanced/` — not just specified. Every claim in this doc was
verified by running the real Flask app (`db.create_all()` against both a
fresh and a simulated pre‑Phase‑2 database, plus live requests through
`app.test_client()`) during this session — see "Testing completed".

---

## 1. Files Modified

| File | Change |
|---|---|
| `app/models.py` | Added `SubscriptionPlan`, `SecurityViolation`, `record_violation()`. Extended `Exam` (test_type, availability, attempts, proctoring toggles, max_warnings), `StudentAttempt` (warning_count, auto_submitted, student_account_id), `CoursePurchase` (plan_id + plan‑aware grants_*). |
| `app/__init__.py` | Extended `_auto_migrate_new_columns` with every Phase 2 column so existing deployments upgrade automatically on next startup. |
| `app/public/routes.py` | Server-side access gate at `exam_landing` + `start_exam`; unified warning system in `track_event`; `result_page` now confirmation‑only; `review_page` now admin‑only; auto_submitted flag set in `_finalize_and_dispatch`; no-score notification text. |
| `app/student/routes.py` | New `tests()` route (demo/mock/main grouping); `home()` returns `my_active_plans`; `course_detail()` returns `subscription_plans`/`plan_status`; new `plan_buy()` route. |
| `app/admin/routes.py` | New Plan CRUD routes, Subscription management routes (extend/renew/activate/deactivate); `exam_new`/`exam_edit` parse all new Exam fields; `results_list` gets course/date filters + warning data. |
| `app/services/payment_service.py` | `activate_paid_purchase` uses `plan.duration_days()` when a purchase has a plan. |
| `app/static/js/exam-engine.js` | Replaced the four separate ad‑hoc violation counters with one unified `reportSecurityViolation()` that reads the server’s warning count/auto‑submit decision. |
| `app/templates/exam/result.html` | Rewritten — confirmation only, no score/percentage/rank/answers. |
| `app/templates/exam/instructions.html` | Rewritten — adds camera/mic permission gate before Start. |
| `app/templates/exam/take_exam.html` | Adds proctoring self-view box + `EXAM_DATA.security`/`maxWarnings` config, loads `exam-security.js`. |
| `app/templates/exam/link_unavailable.html` | Shows the specific access-denial reason (expired plan, not scheduled, etc). |
| `app/templates/admin/exam_form.html` | New fields: test type, free-without-plan, max attempts, availability window, all 6 proctoring toggles, max warnings. |
| `app/templates/admin/results_list.html` | New columns: plan, warnings (with per-violation breakdown), auto-submit + reason; new course/date filters. |
| `app/templates/student/home.html` | New "My Active Plan" widget. |
| `app/templates/student/course_detail.html` | New "Subscription Plans" section below the existing tier cards. |
| `app/templates/base.html` | Sidebar links for Subscription Plans / Student Subscriptions. |
| `app/templates/student/_dashboard_base.html` | Sidebar link for the new Tests page. |
| `MIGRATION_NOTES.md` | Documents every new column/table and the manual SQL fallback. |

## 2. Files Added

- `app/services/access_service.py` — the single server-side `can_access_exam()` gate (published / schedule / plan / attempt-limit).
- `app/static/js/exam-security.js` — camera-based face detection (present / missing / multiple), approximate looking-away heuristic, and optional voice detection, all debounced and feeding the unified warning system.
- `app/templates/admin/plans_list.html`, `plan_form.html`, `subscriptions_list.html`
- `app/templates/student/tests.html`

## 3. Database Changes

Two new tables (`subscription_plans`, `security_violations`) and new
columns on `exams`, `student_attempts`, `course_purchases` — all
additive/nullable-or-defaulted, nothing renamed or dropped. Full column
list and rationale in `MIGRATION_NOTES.md`.

## 4. Migration Commands

Nothing to run by hand on a normal deploy — `db.create_all()` +
`_auto_migrate_new_columns` (already existing project mechanism) handle
it automatically on startup, verified against a simulated pre‑Phase‑2
schema in this session. Manual `ALTER TABLE` fallback SQL is in
`MIGRATION_NOTES.md` for restricted DB users.

## 5. Admin Features Added

- Subscription Plans: create/edit/deactivate/delete, per-course, configurable name/duration/price/description/included access.
- Student Subscriptions: search, extend (+N days), renew (full duration), activate, deactivate — each notifies the student in-app.
- Exam form: test type (demo/mock/main), free-without-plan override, max attempts, flexible/scheduled availability window, all 6 proctoring toggles, max warnings.
- Results list: filter by student, exam, **course, plan, and date range**; shows warning count, per-violation log, auto-submit status + reason, which plan was active, and a true nth-attempt number per student/exam.

## 6. Student Features Added

- "My Active Plan" dashboard widget (plan, course, start/expiry, days remaining, active status).
- New Tests page grouped into Demo / Mock / Main, each showing a Locked badge + reason when inaccessible.
- **Three free Demo Mock Tests are auto-seeded on first startup** (spec #5) — no admin setup required, always free, always visible, fully editable/publishable/deletable by admin like any other exam afterward.
- Subscription Plan purchase cards on the course page (alongside the existing tier cards), reusing the exact same checkout/Razorpay flow.

## 7. Subscription / Expiry Behavior

- Expiry is enforced server-side on every request to the exam-taking flow (`exam_landing`, `start_exam`) — not just at listing time — so a direct/stale link after expiry is denied with "Your plan has expired. Please renew your plan to continue."
- Admin extend/renew/activate/deactivate take effect immediately (deactivate sets `expires_at` to now).

## 8. Exam Security Behavior

- One server-side `warning_count` per attempt, incremented by `record_violation()` for every violation type (tab switch, fullscreen exit, face missing, multiple faces, looking away, voice detected). Verified end-to-end: 3× `tab_switch` → server returns `auto_submit: true` → attempt becomes `auto_submitted`.
- Camera/mic requested only on the instructions page and live exam page for a secure exam; face/voice detection uses debounce (3s persistence) + cooldown (15s) to avoid false-positive spam, and fails open (skips, never blocks) when the browser doesn't support `FaceDetector`.

## 9. Result-Hiding Behavior

- Verified: after an auto-submit, `/result/<id>` renders only "Exam Submitted Successfully" + the auto-submit banner — no percentage/score/answers anywhere in the HTML.
- `/review/<id>` now requires the admin Flask-Login session; an anonymous request is redirected to `/auth/login`.

## 10. Testing Completed (this session, against the real app)

- `db.create_all()` on a fresh SQLite DB — succeeds.
- Auto-migration against a hand-built pre-Phase-2 schema (old `exams`/`student_attempts`/`course_purchases` only) — all new columns and both new tables appear correctly.
- **Full regression sweep**: every non-parametrized `/admin/*` and `/student/*` GET route (56 admin + 33 student routes) hit with `test_client()` on a fully-seeded DB — zero 500 errors.
- Admin: login, `/admin/results` (incl. plan/course/date filters), `/admin/plans`, `/admin/subscriptions`, `/admin/exams/new`, `/admin/exams/<id>/edit`, `/admin/exams/<id>` all render 200 with seeded data (plan, purchase, exam, attempt, violations, result).
- Student: login, `/student/home`, `/student/tests`, `/student/mock-tests`, `/student/courses/<id>` all render 200.
- **3 free demo tests confirmed auto-seeded** on startup and visible/unlocked on the student Tests page with no plan.
- Paid mock test: correctly **locked** ("plan required") for a student with no active plan; correctly **open** for a student with an active plan whose plan includes mock tests.
- Anonymous (not logged in) visitor hitting a demo-test link is redirected to student login.
- Full exam flow (re-verified twice, including after the final round of changes): details → instructions → start → 3× `tab_switch` violation → server signals auto-submit on the 3rd → submit(auto=1) → attempt marked `auto_submitted` → result page shows confirmation only, no percentage anywhere → admin results list correctly shows the `3/3` warning badge.
- `/review/<id>` blocked for anonymous/student access, redirects to admin login.
- Every `.py` file in the project and every `.html` template parses cleanly (final full-project sweep).

## 11. Remaining Limitations

- Face/looking-away detection uses the browser's experimental `FaceDetector` API (Chromium-only in practice today); it is explicitly not a verified-identity or perfect-cheating-detection system, and fails open where unsupported — this is disclosed in-code and was a requirement of the spec ("do not claim perfect cheating detection").
- The existing student dashboard leaderboard/analytics/performance-history features (pre-dating Phase 2) still show a student their own past scores; Phase 2's "hide results" was interpreted as governing the immediate post-submission flow (result page, review page, submission APIs) per the spec's own wording ("After exam submission, the student must NOT see..."), not as a mandate to remove the pre-existing, separate analytics/leaderboard system. Flagging this explicitly as a scope interpretation in case it needs to go further.
- No automated browser-level test of the camera/face-detection JS (`exam-security.js`) — verified by static review and the fact that it degrades gracefully, but not exercised in a real browser with a real camera in this session.
