import re
import jwt
from datetime import datetime, timedelta
from flask import current_app


def create_attempt_token(attempt_id, exam_link_token):
    """Short-lived JWT proving the bearer legitimately started this attempt.
    Stored in an HttpOnly-less client cookie is avoided; instead we keep it in
    sessionStorage on the client and send it as a Bearer header for the
    autosave / submit API calls (paired with the server-side Flask session
    cookie, which IS HttpOnly+Secure, for defense in depth).
    """
    payload = {
        "attempt_id": attempt_id,
        "link": exam_link_token,
        "exp": datetime.utcnow() + timedelta(hours=current_app.config["JWT_EXP_HOURS"]),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")


def decode_attempt_token(token):
    try:
        return jwt.decode(token, current_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def create_material_token(material_id, student_account_id, minutes=20):
    """Short-lived token so a copied/shared streaming URL stops working
    quickly, even if the underlying subscription is still active."""
    payload = {
        "material_id": material_id,
        "student_id": student_account_id,
        "exp": datetime.utcnow() + timedelta(minutes=minutes),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")


def decode_material_token(token):
    try:
        return jwt.decode(token, current_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# --------------------------------------------------------------------------
# Video embed helper (Course demo / paid-class videos)
# --------------------------------------------------------------------------
_YOUTUBE_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})"),
)
_VIMEO_PATTERN = re.compile(r"vimeo\.com/(?:video/)?(\d+)")


def to_embed_url(url):
    """Converts a normal YouTube/Vimeo watch URL into its embeddable form so
    the raw share/watch URL is never placed in the rendered HTML - only an
    `/embed/...` iframe source is. Falls back to the original URL untouched
    for any other host (e.g. a self-hosted MP4 or a provider not recognised
    here) so nothing breaks for existing data.
    """
    if not url:
        return None
    url = url.strip()

    for pattern in _YOUTUBE_PATTERNS:
        match = pattern.search(url)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0&modestbranding=1"

    match = _VIMEO_PATTERN.search(url)
    if match:
        return f"https://player.vimeo.com/video/{match.group(1)}"

    return url
