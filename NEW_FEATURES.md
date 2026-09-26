# New Features Reference (Phases 1-5)

Everything below was **added**; nothing in the original project was
removed or renamed. See `MIGRATION_NOTES.md` first if you have an
existing database.

## New environment variables
| Variable | Default | Purpose |
|---|---|---|
| `UPI_PAYEE_VPA` | `exam-portal@upi` | Your real UPI ID - payments won't reach you until you set this |
| `UPI_PAYEE_NAME` | `Kalyani Exam Hub` | Name shown in the student's UPI app |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | *(blank)* | From dashboard.razorpay.com - enables the instant "Pay with Razorpay" button. Blank = button is hidden, UPI-QR flow still works. |
| `ADMIN_NAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | *(blank)* | Admin login, auto-created on first startup if no admin exists yet. Never overwrites an existing admin. |

Set these in your `.env` / environment before going live with real payments.

## Tiered course pricing (Module 10b)
Each course can now be sold as up to 4 independent plans, each with its
own price (admin sets these on the course create/edit form; a plan with
price 0 is simply hidden from students):
- **Exam Only** - mock tests / exams for that course
- **Exam + PDF/Notes** - exams plus notes, assignments, practice sheets, previous papers
- **Class Only** - recorded/live video classes only
- **All Access** - everything combined (this is the original `price` field)

A student can buy several plans for the same course independently (e.g.
buy "Class Only" now, add "Exam + PDF" later) and each grants only the
access it covers - see `CoursePurchase.grants_exam()` /
`grants_pdf_materials()` / `grants_class_materials()` in `app/models.py`.

## Razorpay (Module 10b)
`app/services/razorpay_service.py` creates a Razorpay order and verifies
the payment signature server-side, activating the course instantly on
success - no manual admin verification needed for Razorpay payments. The
original UPI-QR + manual-verification flow (`payment_service.py`) is
untouched and still works side-by-side; the checkout page shows both
options (Razorpay hidden automatically if keys aren't set).


## New CLI command
```bash
flask send-exam-reminders
```
Sends 24h/1h exam reminder emails (Module 7). Schedule it with system cron
every 5 minutes - see the docstring in `app/cli.py` for the exact crontab
line. Do NOT rely on an in-process scheduler under gunicorn (duplicate
emails per worker).

## New database tables
`student_accounts`, `courses`, `course_purchases`, `notifications`,
`course_materials`, `exam_enrollments` - all in `app/models.py`.

## New columns on existing tables
`exams.strict_anti_cheat`, `exams.scheduled_at`, `exams.syllabus`,
`questions.detailed_explanation` (+3 more explanation fields),
`student_attempts.security_violation_reason`, `.security_violation_at`.

## New routes (student-facing, prefix `/student`)
| Route | Module |
|---|---|
| `/register`, `/login`, `/logout`, `/verify-otp`, `/forgot-password`, `/reset-password` | 1, 2 |
| `/home`, `/profile/edit`, `/change-password` | 2 |
| `/courses`, `/courses/<id>`, `/courses/<id>/buy`, `/checkout/<id>`, `/my-courses`, `/invoice/<id>` | 9, 10 |
| `/notifications` | 14 |
| `/leaderboard` | 12 |
| `/analytics` | 13 |
| `/courses/<id>/materials`, `/materials/<id>/view`, `/materials/<id>/stream` | 11 |

## New routes (admin-facing, prefix `/admin`)
| Route | Module |
|---|---|
| `/college-students`, `/students/export` | 3, 4 |
| `/results/export.pdf` | 4 |
| `/courses`, `/courses/new`, `/courses/<id>/edit`, `/courses/<id>/delete` | 9 |
| `/courses/<id>/materials`, `/materials/<id>/delete` | 11 |
| `/payments`, `/payments/<id>/verify`, `/payments/<id>/reject` | 10 |
| `/exams/<id>/enroll`, `/exams/<id>/enroll/<id>/delete` | 7 |

## Feature notes worth remembering
- **Payments (Module 10)** use UPI intent QR codes (no gateway credentials
  needed) with **manual admin verification** - not instant. Swap
  `app/services/payment_service.py` for a real gateway SDK later if you
  want automatic confirmation.
- **Anti-cheat strict mode (Module 6)** is opt-in per exam
  (`strict_anti_cheat` checkbox on the exam form). Existing exams keep
  today's lenient 3-warning behavior unless you turn it on.
- **PDF viewer (Module 11)** deters download/print/right-click/devtools
  and live-checks subscription expiry on every request, but it is not
  unbreakable DRM - nothing rendered in a browser can be 100%
  screenshot-proof.
- **Dark mode (Module 16)** - the student portal now shares the same
  toggle/`theme.js` your admin panel already had.
