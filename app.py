"""
app.py — SecWeb Flask application
===================================
Full auth cycle:
  / ..................... home
  /register ............. create account
  /login ................ password login → branches to MFA
  /logout ............... clear session
  /forgot-password ...... request reset link
  /reset-password/<tok>.. set new password via token
  /setup-mfa ............ scan QR / copy secret (first-time)
  /verify-mfa-setup ..... confirm TOTP code to activate MFA
  /verify-mfa-login ..... enter TOTP code on each login (returning users)
  /dashboard ............ protected — requires login + MFA enabled

Session keys
------------
  username          str   set after full auth (login + MFA passed)
  user_id           int   set alongside username; used by base.html nav check
  _mfa_pending      str   temporary — username awaiting MFA verification on login
                          cleared once MFA passes or user logs out

MFA flow (new user)
-------------------
  register → dashboard redirect → dashboard sees mfa_enabled=False
           → redirect to /setup-mfa → scan QR → POST /verify-mfa-setup
           → MFA enabled in DB → redirect /dashboard (now passes the guard)

MFA flow (returning user)
--------------------------
  login → password OK, mfa_enabled=True → set session['_mfa_pending']
        → redirect /verify-mfa-login → TOTP OK → set session['username']
        → redirect /dashboard
"""

import os
import re

import pyotp
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_bcrypt import Bcrypt

import database as db
import os
from dotenv import load_dotenv

# Load the keys out of your hidden .env file into the system environment memory space
load_dotenv()

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

# SECRET_KEY must come from the environment in any real deployment.
# The fallback here is intentionally obvious so students notice it.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    import warnings
    warnings.warn(
        "SECRET_KEY env var not set — using insecure default. "
        "Set it before running in any shared environment.",
        stacklevel=1,
    )
    _secret = "INSECURE_DEFAULT_CHANGE_ME"
app.config["SECRET_KEY"] = _secret

app.config.update(
    SECRET_KEY=_secret,
    SESSION_COOKIE_HTTPONLY=True,   # JS cannot read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",  # basic CSRF mitigation
    # SESSION_COOKIE_SECURE=True,   # uncomment when serving over HTTPS
)

bcrypt = Bcrypt(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,30}$")
EMAIL_RE    = re.compile(r"[^@]+@[^@]+\.[^@]+")


def _set_session(user: dict) -> None:
    """Populate the session after successful full authentication."""
    session.clear()
    
    #  SAFETY CHECK: Bail out early if the user object is missing or empty
    if not user:
        import warnings
        warnings.warn("Attempted to set a session for an empty or Non-existent user object.", stacklevel=2)
        return
        
    try:
        session["username"] = user["username"]
        session["user_id"]  = user["id"]   # base.html uses session.user_id for nav
    except KeyError as e:
        # Catch errors if the dictionary layout is missing an expected key
        print(f"Session Allocation Error: Missing expected database column key {e}")


def _current_user() -> dict | None:
    """Return the DB row for the logged-in user, or None."""
    username = session.get("username")
    if not username:
        return None
    return db.get_user_by_username(username)


def login_required(view):
    """Decorator: redirect to /login if no active session."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def mfa_required(view):
    """
    Decorator: redirect to /setup-mfa if the user hasn't enabled MFA yet.
    Apply after @login_required so session['username'] is guaranteed.
    """
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _current_user()
        if user and not user["mfa_enabled"]:
            flash(
                "Please complete MFA setup before accessing the dashboard.",
                "warning",
            )
            return redirect(url_for("setup_mfa"))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Routes — public
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        # --- Validation ---
        if not USERNAME_RE.match(username):
            flash("Username must be 3–30 characters: letters, numbers, underscores only.", "danger")
        elif not EMAIL_RE.match(email):
            flash("Please enter a valid email address.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        elif db.username_exists(username):
            flash("That username is already taken.", "danger")
        else:
            hashed = bcrypt.generate_password_hash(password).decode("utf-8")
            user_id = db.add_user(username=username, password=hashed, email=email)

            if user_id is None:
                # add_user returns None on UniqueViolation (e.g. duplicate email)
                flash("An account with that email already exists.", "danger")
            else:
                # Log the user in immediately and send them to set up MFA
                user = db.get_user_by_username(username)
                _set_session(user)
                flash("Account created! Please set up MFA to secure your account.", "success")
                return redirect(url_for("setup_mfa"))

        return render_template("register.html", username=username, email=email)

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password   = request.form.get("password", "")

        # Reject inputs that can't possibly be a valid username
        if not identifier or not password:
            flash("Please enter your username and password.", "danger")
            return render_template("login.html")

        user = db.get_user_by_username(identifier)

        # Single error message for both "user not found" and "wrong password"
        # to prevent username enumeration.
        if not user or not bcrypt.check_password_hash(user["password"], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        # --- Password correct ---
        if user["mfa_enabled"]:
            # MFA is set up: hold username in a temporary session key,
            # do NOT grant full access yet.
            session.clear()
            session["_mfa_pending"] = user["username"]
            return redirect(url_for("verify_mfa_login"))
        else:
            # MFA not yet configured: grant session and redirect to setup.
            _set_session(user)
            flash("Welcome! Please configure MFA to protect your account.", "info")
            return redirect(url_for("setup_mfa"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Routes — password reset
# ---------------------------------------------------------------------------

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    reset_link = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        token = db.generate_reset_token(email)

        if token:
            reset_link = url_for("reset_password", token=token, _external=True)
            # In production you would email this link, not display it.
            # It is shown here so the training lab works without an SMTP server.

        # Same flash message whether the email exists or not — prevents
        # user enumeration (an attacker cannot tell if an email is registered).
        flash(
            "If that email is registered, a reset link has been generated below.",
            "info",
        )

    return render_template("forgot_password.html", reset_link=reset_link)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = db.get_user_by_reset_token(token)

    if not user:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        else:
            hashed = bcrypt.generate_password_hash(password).decode("utf-8")
            ok = db.update_user_password(user["id"], hashed)
            if ok:
                flash("Password updated. Please log in.", "success")
                return redirect(url_for("login"))
            else:
                flash("Something went wrong. Please try again.", "danger")

    return render_template("reset_password.html", token=token)


# ---------------------------------------------------------------------------
# Routes — MFA setup (first time)
# ---------------------------------------------------------------------------

@app.route("/setup-mfa")
@login_required
def setup_mfa():
    """
    Show the QR code / manual secret for Google Authenticator / Authy.
    The user must not already have MFA enabled (guard below).
    """
    user = _current_user()
    if not user:
        return redirect(url_for("login"))

    if user["mfa_enabled"]:
        flash("MFA is already active on your account.", "info")
        return redirect(url_for("dashboard"))

    # Generate a secret if the user doesn't have one yet
    mfa_secret = user["mfa_secret"]
    if not mfa_secret:
        mfa_secret = pyotp.random_base32()
        db.set_mfa_secret(user["username"], mfa_secret)

    totp = pyotp.TOTP(mfa_secret)
    provision_url = totp.provisioning_uri(
        name=user["username"], issuer_name="SecWeb"
    )

    return render_template(
        "setup_mfa.html",
        secret=mfa_secret,
        provision_url=provision_url,
    )


@app.route("/verify-mfa-setup", methods=["POST"])
@login_required
def verify_mfa_setup():
    """
    Validate the first TOTP code entered during setup.
    On success: mark mfa_enabled=True in the DB and redirect to dashboard.
    """
    user = _current_user()
    if not user:
        return redirect(url_for("login"))

    token = request.form.get("mfa_token", "").strip()
    totp  = pyotp.TOTP(user["mfa_secret"])

    if totp.verify(token):
        db.enable_mfa(user["username"])
        flash("MFA enabled successfully. Your account is now fully secured.", "success")
        return redirect(url_for("dashboard"))
    else:
        flash("Incorrect code — please try again.", "danger")
        return redirect(url_for("setup_mfa"))


# ---------------------------------------------------------------------------
# Routes — MFA login (returning users)
# ---------------------------------------------------------------------------

@app.route("/verify-mfa-login", methods=["GET", "POST"])
def verify_mfa_login():
    """
    Second factor for users who already have MFA enabled.
    Requires session['_mfa_pending'] set by /login.
    """
    pending_username = session.get("_mfa_pending")
    if not pending_username:
        # Someone landed here without going through /login first
        return redirect(url_for("login"))

    if request.method == "POST":
        token = request.form.get("mfa_token", "").strip()
        user  = db.get_user_by_username(pending_username)

        if not user:
            session.clear()
            flash("Session error. Please log in again.", "danger")
            return redirect(url_for("login"))

        totp = pyotp.TOTP(user["mfa_secret"])
        if totp.verify(token):
            _set_session(user)          # clears _mfa_pending, sets username + user_id
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Incorrect code. Please try again.", "danger")

    return render_template("verify_mfa_login.html")


# ---------------------------------------------------------------------------
# Routes — protected
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
@mfa_required
def dashboard():
    """
    Only reachable when:
      1. session['username'] is set  (@login_required)
      2. user.mfa_enabled is True    (@mfa_required)
    """
    user = _current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))

    return render_template("dashboard.html", user=user)


# ---------------------------------------------------------------------------
# Startup — initialize the database schema
# ---------------------------------------------------------------------------
# This MUST run at import time, not just under `if __name__ == "__main__"`.
# Containers typically boot the app with gunicorn/flask (which import the
# module), so a __main__-guarded init would never execute and the first
# request would hit a missing table.


def _init_db_with_retry(max_attempts: int = 30, delay: float = 1.0) -> None:
    """
    Run db.init_db(), retrying while the database is unreachable.

    In a container stack (e.g. docker compose) the Postgres container often
    starts after the app container, so the first connection attempts fail.
    Retrying here lets the app wait for the DB instead of crash-looping.
    """
    import time

    for attempt in range(1, max_attempts + 1):
        try:
            db.init_db()
            print(f"[startup] database schema ready (attempt {attempt})")
            return
        except Exception as exc:
            print(f"[startup] DB not ready (attempt {attempt}/{max_attempts}): {exc}")
            if attempt == max_attempts:
                raise
            time.sleep(delay)


_init_db_with_retry()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    # PORT is set by the container/compose; default 5000 for local runs.
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=debug, host="0.0.0.0", port=port)
