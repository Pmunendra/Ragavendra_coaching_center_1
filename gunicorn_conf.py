import multiprocessing
import os

# Render assigns the port to listen on via the $PORT env var at runtime (it
# is NOT always 8000/10000 - Render can change it) and routes external
# traffic to whatever that is; a hard-coded port here would make the
# service unreachable on Render even though the container itself boots
# fine. Falls back to 8000 for local `docker-compose up` / plain
# `gunicorn -c gunicorn_conf.py run:app`, where nothing sets $PORT.
bind = "0.0.0.0:" + os.environ.get("PORT", "8000")

# SQLite allows one writer at a time for the whole database file (see the
# WAL-mode notes in app/__init__.py) no matter how many worker PROCESSES
# are running, so more workers doesn't buy write throughput the way it
# would with MySQL/Postgres - it mostly just means more processes queuing
# on the same busy_timeout. `cpu_count() * 2 + 1` (gunicorn's usual
# recommendation) can also way overshoot on small/shared-CPU Render
# instances, which report the host's core count rather than what's
# actually allocated to the container. WEB_CONCURRENCY lets this be tuned
# per-environment (e.g. set higher after moving to Postgres/MySQL) without
# editing code; 3 is a reasonable default for a single small SQLite-backed
# web service.
workers = int(os.environ.get("WEB_CONCURRENCY", min(multiprocessing.cpu_count() * 2 + 1, 3)))
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
