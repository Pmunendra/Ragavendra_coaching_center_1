from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db, limiter
from app.models import User, log_activity
from app.services.device_service import get_client_ip

auth_bp = Blueprint("auth", __name__, url_prefix="/auth", template_folder="../templates")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=remember)
            log_activity(user.email, "admin_login", ip_address=get_client_ip(request))
            db.session.commit()
            next_url = request.args.get("next")
            return redirect(next_url or url_for("admin.dashboard"))

        flash("Invalid email or password.", "danger")
        log_activity(email or "unknown", "admin_login_failed", ip_address=get_client_ip(request))
        db.session.commit()

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_activity(current_user.email, "admin_logout", ip_address=get_client_ip(request))
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
