# Exam Portal — Online Examination System

A complete, production-ready online examination system built with Flask,
SQLAlchemy, SQLite, Bootstrap 5, and vanilla JavaScript.

Admins create exams and generate public exam links
(`https://domain.com/exam/ABC123XYZ`). Students open the link, enter their
details (no registration required), and take a timed, auto-graded exam with
a full question palette, autosave, anti-cheating telemetry, instant results,
a detailed answer review, a PDF report, and an emailed result summary.

## Features implemented

- Public exam links: expiry date, max attempts, optional password, QR code, WhatsApp share, copy link
- Student details capture (name, phone, email, college, city, declaration) with validation
- Question types: single choice, multiple choice, true/false, integer, fill-in-the-blank,
  image-based, paragraph, and case-study
- Question palette (answered / not answered / not visited / marked for review), Previous/Next/Save & Next/Mark for Review/Clear/Submit
- Autosave every 10s, on question change, and on reconnect; JWT-scoped API + session-cookie defense in depth
- Server-authoritative countdown timer with auto-submit
- Anti-cheating: right-click/copy/paste/selection disabled, tab-switch/fullscreen-exit/devtools warnings + counters, IP/browser/OS/device logging
- Instant grading engine (incl. negative marking) → Result page → full color-coded Answer Review page
- HTML result email with PDF report attached (ReportLab: student details, exam details, score summary, full question-wise answer sheet)
- Admin panel: dashboard (totals, averages, pass rate), exam & question CRUD, subjects, public link management, student search, results search + CSV/Excel/PDF export, activity log
- Security: CSRF protection, secure/HttpOnly session cookies, password hashing, JWT attempt tokens, rate limiting, security headers

## Quick start (local)

```bash
cd exam_portal
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Defaults to SQLite (instance/exam_portal.db, created automatically) -
# no DATABASE_URL needed for local use.

flask --app run.py create-admin      # create your admin login
flask --app run.py seed-demo         # optional: creates a demo exam + public link

python run.py                        # http://localhost:5000
```

Then visit `/auth/login` to sign in as admin, or open the printed
`/exam/<token>` link as a student.

## Running in production

The app runs on SQLite by default - see the "SQLite in production /
Render" section of `MIGRATION_NOTES.md` for what that means in practice
(persistent disk, WAL mode, backups) and its limits under write
concurrency. To use a managed database instead, set `DATABASE_URL` (see
`.env.example`) to any SQLAlchemy-supported URL, e.g.:

```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
DATABASE_URL=mysql+pymysql://user:password@host:3306/dbname   # pip install pymysql first
```

Then:
1. `flask --app run.py create-admin`
2. `gunicorn -c gunicorn_conf.py run:app`

## Docker

```bash
cp .env.example .env   # fill in SMTP + secrets
docker compose up --build -d
docker compose exec web flask --app run.py create-admin
```

This brings up the Flask app (Gunicorn) and Nginx as a reverse proxy on
port 80. The database is SQLite, stored in `./instance` on the host via
the compose volume mount, so it (and uploaded files) survive
`docker compose down`/`up`.

## Deploying to Render

`render.yaml` in this repo is a ready-to-use Render Blueprint: Render
dashboard → New → Blueprint → point it at this repo. It builds from the
existing `Dockerfile` and attaches a 1GB persistent disk mounted at
`/app/instance`, which is where both the SQLite database file and all
uploaded files (course PDFs, student photos, generated result PDFs) live -
one disk, one mount, everything that needs to survive a redeploy is
covered. Env vars marked `sync: false` in `render.yaml` are prompted for
once in the dashboard rather than stored in the repo. See "SQLite in
production / on Render" in `MIGRATION_NOTES.md` for what does and doesn't
persist, and SQLite's practical limits under concurrent writes.

## Project layout

```
app/
  admin/          admin dashboard, exam/question CRUD, links, results, exports
  auth/            admin login/logout
  public/          student details → instructions → exam-taking → submit → result → review
  api/             JWT-authenticated autosave/heartbeat/visit endpoints
  services/        grading engine, PDF generator, email service, QR service, device parsing
  templates/       Jinja2 templates (admin/, auth/, exam/, email/)
  static/          Bootstrap-based CSS + the exam-engine.js client
  models.py        all SQLAlchemy models
  config.py        environment-driven configuration
  seed.py          `flask create-admin` / `flask seed-demo` CLI commands
run.py             app entrypoint
gunicorn_conf.py   production WSGI server config
Dockerfile / docker-compose.yml / nginx.conf   deployment
```

## Notes & next steps

- Email sending requires real SMTP credentials in `.env`; without them, the
  exam still grades and generates the PDF, and the failure is recorded in
  the `EmailLogs` table rather than raising an error to the student.
- The demo/seed data is meant for trying the system quickly — remove
  `seed-demo` usage in a real deployment.
- Consider adding Flask-Migrate/Alembic if you plan to evolve the schema in
  production instead of relying on `db.create_all()`.
