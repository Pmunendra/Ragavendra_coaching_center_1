FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# build-essential/pkg-config: needed to build a couple of requirements.txt
# packages from source on some platforms (e.g. bcrypt). The MySQL client
# dev headers this image used to install here are gone along with PyMySQL -
# the database is SQLite now, which needs no system client library at all.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p instance/uploads instance/reports

EXPOSE 8000

CMD ["gunicorn", "-c", "gunicorn_conf.py", "run:app"]
