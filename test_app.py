"""
test_app.py — unit tests for app.py
===================================
Run with:
    pytest test_app.py -v

The tests mock out the `database` module entirely so no Postgres server
is needed — everything runs against Flask's test client with stubbed DB
functions. TOTP codes are generated with pyotp so the real MFA logic in
app.py is exercised.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pyotp
import pytest  # pyright: ignore[reportMissingImports]

import app as app_module


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def make_user(
    username="alice",
    user_id=1,
    email="alice@example.com",
    password_hash="hashed-password",
    mfa_enabled=False,
    mfa_secret=None,
):
    """Build a fake user row shaped like a RealDictCursor result."""
    return {
        "id": user_id,
        "username": username,
        "email": email,
        "password": password_hash,
        "mfa_secret": mfa_secret,
        "mfa_enabled": mfa_enabled,
        "reset_token": None,
        "token_expiry": None,
        "created_at": datetime.now(timezone.utc),
    }


@pytest.fixture()
def client():
    """Flask test client with the DB layer mocked out."""
    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "db") as mock_db:
        with app_module.app.test_client() as client:
            client.mock_db = mock_db
            yield client


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

class TestHome:
    def test_home_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200


class TestLogout:
    def test_logout_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["username"] = "alice"
            sess["user_id"] = 1
        resp = client.get("/logout")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "username" not in sess
            assert "user_id" not in sess

    def test_logout_redirects_home(self, client):
        resp = client.get("/logout", follow_redirects=True)
        assert resp.status_code == 200
        # ends up on the home page
        assert resp.request.path == "/"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_get_shows_form(self, client):
        assert client.get("/register").status_code == 200

    def test_register_success_sets_session_and_redirects_to_mfa_setup(self, client):
        new_user = make_user(username="bob", user_id=2, mfa_secret=None)
        client.mock_db.username_exists.return_value = False
        client.mock_db.add_user.return_value = 2
        client.mock_db.get_user_by_username.return_value = new_user

        with patch.object(app_module, "bcrypt") as mock_bcrypt:
            mock_bcrypt.generate_password_hash.return_value = b"hashed"

            resp = client.post(
                "/register",
                data={
                    "username": "bob",
                    "email": "bob@example.com",
                    "password": "longenough1",
                    "confirm_password": "longenough1",
                },
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert resp.location.endswith("/setup-mfa")
        with client.session_transaction() as sess:
            assert sess["username"] == "bob"
            assert sess["user_id"] == 2

    def test_register_invalid_username_rejected(self, client):
        resp = client.post(
            "/register",
            data={
                "username": "x",  # too short
                "email": "x@example.com",
                "password": "longenough1",
                "confirm_password": "longenough1",
            },
        )
        assert "3–30 characters" in resp.get_data(as_text=True)
        client.mock_db.add_user.assert_not_called()

    def test_register_invalid_email_rejected(self, client):
        resp = client.post(
            "/register",
            data={
                "username": "valid_user",
                "email": "not-an-email",
                "password": "longenough1",
                "confirm_password": "longenough1",
            },
        )
        assert b"valid email" in resp.data
        client.mock_db.add_user.assert_not_called()

    def test_register_short_password_rejected(self, client):
        resp = client.post(
            "/register",
            data={
                "username": "valid_user",
                "email": "v@example.com",
                "password": "short",
                "confirm_password": "short",
            },
        )
        assert b"at least 8 characters" in resp.data
        client.mock_db.add_user.assert_not_called()

    def test_register_password_mismatch_rejected(self, client):
        resp = client.post(
            "/register",
            data={
                "username": "valid_user",
                "email": "v@example.com",
                "password": "longenough1",
                "confirm_password": "different12",
            },
        )
        assert b"do not match" in resp.data
        client.mock_db.add_user.assert_not_called()

    def test_register_duplicate_username_rejected(self, client):
        client.mock_db.username_exists.return_value = True
        resp = client.post(
            "/register",
            data={
                "username": "taken",
                "email": "t@example.com",
                "password": "longenough1",
                "confirm_password": "longenough1",
            },
        )
        assert b"already taken" in resp.data
        client.mock_db.add_user.assert_not_called()

    def test_register_duplicate_email_returns_error(self, client):
        """add_user returns None on UniqueViolation → email flash."""
        client.mock_db.username_exists.return_value = False
        client.mock_db.add_user.return_value = None
        resp = client.post(
            "/register",
            data={
                "username": "valid_user",
                "email": "used@example.com",
                "password": "longenough1",
                "confirm_password": "longenough1",
            },
        )
        assert b"email already exists" in resp.data


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_get_shows_form(self, client):
        assert client.get("/login").status_code == 200

    def test_login_missing_fields(self, client):
        resp = client.post("/login", data={"username": "", "password": ""})
        assert b"enter your username and password" in resp.data

    def test_login_unknown_user_same_message_as_wrong_password(self, client):
        client.mock_db.get_user_by_username.return_value = None
        resp = client.post(
            "/login", data={"username": "ghost", "password": "whatever"}
        )
        assert b"Invalid username or password." in resp.data

    def test_login_wrong_password(self, client):
        user = make_user(mfa_enabled=True, mfa_secret="SECRET")
        client.mock_db.get_user_by_username.return_value = user
        with patch.object(app_module, "bcrypt") as mock_bcrypt:
            mock_bcrypt.check_password_hash.return_value = False
            resp = client.post(
                "/login", data={"username": "alice", "password": "bad"}
            )
        assert b"Invalid username or password." in resp.data

    def test_login_mfa_user_sets_pending_and_redirects(self, client):
        user = make_user(mfa_enabled=True, mfa_secret="SECRET")
        client.mock_db.get_user_by_username.return_value = user
        with patch.object(app_module, "bcrypt") as mock_bcrypt:
            mock_bcrypt.check_password_hash.return_value = True
            resp = client.post(
                "/login", data={"username": "alice", "password": "good"}
            )
        assert resp.status_code == 302
        assert resp.location.endswith("/verify-mfa-login")
        with client.session_transaction() as sess:
            assert sess["_mfa_pending"] == "alice"
            assert "username" not in sess  # full access NOT granted yet

    def test_login_non_mfa_user_redirects_to_setup(self, client):
        user = make_user(mfa_enabled=False)
        client.mock_db.get_user_by_username.return_value = user
        with patch.object(app_module, "bcrypt") as mock_bcrypt:
            mock_bcrypt.check_password_hash.return_value = True
            resp = client.post(
                "/login", data={"username": "alice", "password": "good"}
            )
        assert resp.status_code == 302
        assert resp.location.endswith("/setup-mfa")
        with client.session_transaction() as sess:
            assert sess["username"] == "alice"


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class TestForgotPassword:
    def test_get_shows_form(self, client):
        assert client.get("/forgot-password").status_code == 200

    def test_unknown_email_does_not_show_link(self, client):
        client.mock_db.generate_reset_token.return_value = None
        resp = client.post("/forgot-password", data={"email": "nobody@x.com"})
        assert b"reset link has been generated" in resp.data
        # no actual link rendered
        assert b"reset-password/" not in resp.data

    def test_known_email_shows_reset_link(self, client):
        client.mock_db.generate_reset_token.return_value = "tok123"
        resp = client.post("/forgot-password", data={"email": "a@x.com"})
        assert b"/reset-password/tok123" in resp.data


class TestResetPassword:
    def test_invalid_token_redirects_to_forgot(self, client):
        client.mock_db.get_user_by_reset_token.return_value = None
        resp = client.get("/reset-password/badtok", follow_redirects=True)
        assert resp.request.path == "/forgot-password"

    def test_valid_token_shows_form(self, client):
        client.mock_db.get_user_by_reset_token.return_value = make_user()
        resp = client.get("/reset-password/tok")
        assert resp.status_code == 200

    def test_short_password_rejected(self, client):
        client.mock_db.get_user_by_reset_token.return_value = make_user()
        resp = client.post(
            "/reset-password/tok",
            data={"password": "short", "confirm_password": "short"},
        )
        assert b"at least 8 characters" in resp.data
        client.mock_db.update_user_password.assert_not_called()

    def test_mismatch_rejected(self, client):
        client.mock_db.get_user_by_reset_token.return_value = make_user()
        resp = client.post(
            "/reset-password/tok",
            data={"password": "longenough1", "confirm_password": "different12"},
        )
        assert b"do not match" in resp.data
        client.mock_db.update_user_password.assert_not_called()

    def test_successful_reset_updates_and_redirects_to_login(self, client):
        client.mock_db.get_user_by_reset_token.return_value = make_user()
        client.mock_db.update_user_password.return_value = True
        with patch.object(app_module, "bcrypt") as mock_bcrypt:
            mock_bcrypt.generate_password_hash.return_value = b"newhash"
            resp = client.post(
                "/reset-password/tok",
                data={"password": "longenough1", "confirm_password": "longenough1"},
            )
        assert resp.status_code == 302
        assert resp.location.endswith("/login")
        client.mock_db.update_user_password.assert_called_once_with(1, "newhash")

    def test_failed_update_shows_error(self, client):
        client.mock_db.get_user_by_reset_token.return_value = make_user()
        client.mock_db.update_user_password.return_value = False
        resp = client.post(
            "/reset-password/tok",
            data={"password": "longenough1", "confirm_password": "longenough1"},
        )
        assert b"Something went wrong" in resp.data


# ---------------------------------------------------------------------------
# MFA setup (first time)
# ---------------------------------------------------------------------------

class TestSetupMfa:
    def test_requires_login(self, client):
        resp = client.get("/setup-mfa", follow_redirects=True)
        assert resp.request.path == "/login"

    def test_generates_secret_when_missing(self, client):
        user = make_user(mfa_enabled=False, mfa_secret=None)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        with patch.object(app_module.pyotp, "random_base32", return_value="NEWSECRET"):
            resp = client.get("/setup-mfa")
        client.mock_db.set_mfa_secret.assert_called_once_with("alice", "NEWSECRET")
        assert b"NEWSECRET" in resp.data

    def test_reuses_existing_secret(self, client):
        user = make_user(mfa_enabled=False, mfa_secret="EXISTING")
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        resp = client.get("/setup-mfa")
        client.mock_db.set_mfa_secret.assert_not_called()
        assert b"EXISTING" in resp.data

    def test_already_enabled_redirects_to_dashboard(self, client):
        user = make_user(mfa_enabled=True, mfa_secret="S")
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        resp = client.get("/setup-mfa", follow_redirects=True)
        assert resp.request.path == "/dashboard"


class TestVerifyMfaSetup:
    def test_requires_login(self, client):
        resp = client.post("/verify-mfa-setup", data={"mfa_token": "123456"},
                           follow_redirects=True)
        assert resp.request.path == "/login"

    def test_correct_code_enables_mfa(self, client):
        secret = pyotp.random_base32()
        user = make_user(mfa_enabled=False, mfa_secret=secret)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        code = pyotp.TOTP(secret).now()

        resp = client.post("/verify-mfa-setup", data={"mfa_token": code})
        assert resp.status_code == 302
        assert resp.location.endswith("/dashboard")
        client.mock_db.enable_mfa.assert_called_once_with("alice")

    def test_wrong_code_redirects_back_to_setup(self, client):
        secret = pyotp.random_base32()
        user = make_user(mfa_enabled=False, mfa_secret=secret)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        resp = client.post("/verify-mfa-setup", data={"mfa_token": "000000"})
        assert resp.status_code == 302
        assert resp.location.endswith("/setup-mfa")
        client.mock_db.enable_mfa.assert_not_called()


# ---------------------------------------------------------------------------
# MFA login (returning users)
# ---------------------------------------------------------------------------

class TestVerifyMfaLogin:
    def test_no_pending_redirects_to_login(self, client):
        resp = client.get("/verify-mfa-login", follow_redirects=True)
        assert resp.request.path == "/login"

    def test_correct_code_grants_session(self, client):
        secret = pyotp.random_base32()
        user = make_user(mfa_enabled=True, mfa_secret=secret)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["_mfa_pending"] = "alice"
        code = pyotp.TOTP(secret).now()

        resp = client.post("/verify-mfa-login", data={"mfa_token": code})
        assert resp.status_code == 302
        assert resp.location.endswith("/dashboard")
        with client.session_transaction() as sess:
            assert sess["username"] == "alice"
            assert sess["user_id"] == 1
            assert "_mfa_pending" not in sess  # cleared by _set_session

    def test_wrong_code_stays_on_page(self, client):
        secret = pyotp.random_base32()
        user = make_user(mfa_enabled=True, mfa_secret=secret)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["_mfa_pending"] = "alice"

        resp = client.post("/verify-mfa-login", data={"mfa_token": "000000"})
        assert resp.status_code == 200
        assert b"Incorrect code" in resp.data
        with client.session_transaction() as sess:
            assert "_mfa_pending" in sess  # still pending
            assert "username" not in sess

    def test_missing_user_clears_session(self, client):
        client.mock_db.get_user_by_username.return_value = None
        with client.session_transaction() as sess:
            sess["_mfa_pending"] = "ghost"
        resp = client.post(
            "/verify-mfa-login", data={"mfa_token": "123456"}, follow_redirects=True
        )
        assert resp.request.path == "/login"
        with client.session_transaction() as sess:
            assert "_mfa_pending" not in sess


# ---------------------------------------------------------------------------
# Dashboard & guards
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_requires_login(self, client):
        resp = client.get("/dashboard", follow_redirects=True)
        assert resp.request.path == "/login"

    def test_requires_mfa(self, client):
        user = make_user(mfa_enabled=False)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        resp = client.get("/dashboard", follow_redirects=True)
        assert resp.request.path == "/setup-mfa"

    def test_mfa_enabled_user_can_access(self, client):
        user = make_user(mfa_enabled=True, mfa_secret="S")
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
            sess["user_id"] = 1
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert b"alice" in resp.data


# ---------------------------------------------------------------------------
# Unit tests for helpers (no HTTP)
# ---------------------------------------------------------------------------

class TestSetSession:
    def test_sets_username_and_id(self, client):
        with client.session_transaction() as sess:
            pass
        with app_module.app.test_request_context():
            app_module.app.config.update(
                SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax"
            )
            from flask import session as flask_session

            app_module._set_session({"username": "bob", "id": 7})
            assert flask_session["username"] == "bob"
            assert flask_session["user_id"] == 7

    def test_clears_previous_keys(self, client):
        with app_module.app.test_request_context():
            from flask import session as flask_session

            flask_session["_mfa_pending"] = "old"
            app_module._set_session({"username": "bob", "id": 7})
            assert "_mfa_pending" not in flask_session

    def test_empty_user_noop(self, client):
        with app_module.app.test_request_context():
            from flask import session as flask_session

            flask_session["username"] = "keep"
            app_module._set_session(None)
            # session.clear() runs first, then the early return — so keys
            # are gone, but no KeyError was raised.
            assert "username" not in flask_session

    def test_missing_keys_warns(self, client):
        with app_module.app.test_request_context():
            from flask import session as flask_session

            with pytest.warns(UserWarning, match="empty or Non-existent"):
                app_module._set_session(None)
            # missing key path prints an error but does not raise
            app_module._set_session({"id": 5})


class TestLoginRequiredDecorator:
    def test_redirects_without_session(self):
        with app_module.app.test_request_context("/dashboard"):
            from flask import session as flask_session

            flask_session.clear()
            result = app_module.login_required(lambda: "secret")()
            # returns a redirect response
            assert result.status_code == 302

    def test_calls_view_with_session(self):
        with app_module.app.test_request_context("/dashboard"):
            from flask import session as flask_session

            flask_session["username"] = "alice"
            assert app_module.login_required(lambda: "secret")() == "secret"


class TestMfaRequiredDecorator:
    def test_redirects_when_mfa_disabled(self, client):
        user = make_user(mfa_enabled=False)
        client.mock_db.get_user_by_username.return_value = user
        with app_module.app.test_request_context("/dashboard"):
            from flask import session as flask_session

            flask_session["username"] = "alice"
            result = app_module.mfa_required(lambda: "secret")()
            assert result.status_code == 302

    def test_allows_when_mfa_enabled(self, client):
        user = make_user(mfa_enabled=True)
        client.mock_db.get_user_by_username.return_value = user
        with client.session_transaction() as sess:
            sess["username"] = "alice"
        with app_module.app.test_request_context("/dashboard"):
            assert app_module.mfa_required(lambda: "secret")() == "secret"
