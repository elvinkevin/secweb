# """
# database.py — SecWeb data layer
# ================================
# All DB interaction lives here. app.py never touches psycopg2 directly.

# Key design decisions
# ---------------------
# * RealDictCursor  — every fetchone/fetchall returns a dict, so callers use
#                     row['username'] instead of fragile positional indices.
# * One connection per call — simple and safe for a training app. Each public
#   function opens, uses, and closes its own connection so there is no shared
#   state to worry about.
# * Single get_user_by_username — previous code had it defined twice; the
#   second definition silently overwrote the first and dropped mfa_enabled,
#   causing the redirect loop on /dashboard.
# * Parameterized queries everywhere — even the intentionally-vulnerable
#   variants are commented out next to their safe counterparts so students
#   can swap and observe.
# """

# import os
# import secrets
# from datetime import datetime, timedelta

# import psycopg2
# import psycopg2.extras  # RealDictCursor
# import os
# from dotenv import load_dotenv
# load_dotenv()


# # ---------------------------------------------------------------------------
# # Connection
# # ---------------------------------------------------------------------------

# def _connect():
#     """
#     Return a fresh psycopg2 connection using environment variables.
#     Set DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD in your shell or
#     a .env file (loaded by python-dotenv if you add it).

#     Fallback defaults are provided only so the app starts without
#     configuration — change them before any real deployment.
#     """
#     return psycopg2.connect(
#         host=os.environ.get("DB_HOST", "localhost"),
#         port=os.environ.get("DB_PORT", "5432"),
#         dbname=os.environ.get("DB_NAME", "secweb"),
#         user=os.environ.get("DB_USER", "postgres"),
#         password=os.environ.get("DB_PASSWORD", ""),  # set via env var
#         cursor_factory=psycopg2.extras.RealDictCursor,
#     )


# # ---------------------------------------------------------------------------
# # Schema bootstrap
# # Run once on startup (CREATE TABLE IF NOT EXISTS is idempotent).
# # ---------------------------------------------------------------------------

# def init_db():
#     """Create tables if they do not already exist."""
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute("""
#             CREATE TABLE IF NOT EXISTS users (
#                 id           SERIAL PRIMARY KEY,
#                 username     TEXT UNIQUE NOT NULL,
#                 email        TEXT UNIQUE NOT NULL,
#                 password     TEXT NOT NULL,
#                 mfa_secret   TEXT,
#                 mfa_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
#                 reset_token  TEXT,
#                 token_expiry TIMESTAMPTZ,
#                 created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
#             );
#         """)
#         conn.commit()
#     finally:
#         cur.close()
#         conn.close()


# # ---------------------------------------------------------------------------
# # User helpers
# # ---------------------------------------------------------------------------

# def add_user(username: str, password: str, email: str) -> int | None:
#     """
#     Insert a new user. Returns the new user's id, or None on failure
#     (e.g. duplicate username / email).
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             """
#             INSERT INTO users (username, email, password)
#             VALUES (%s, %s, %s)
#             RETURNING id;
#             """,
#             (username, email, password),
#         )
#         row = cur.fetchone()
#         conn.commit()
#         return row["id"] if row else None
#     except psycopg2.errors.UniqueViolation:
#         conn.rollback()
#         return None
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] add_user error: {exc}")
#         return None
#     finally:
#         cur.close()
#         conn.close()


# def username_exists(username: str) -> bool:
#     """Return True if the username is already taken."""
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         # SECURE (parameterized) — default
#         cur.execute(
#             "SELECT EXISTS(SELECT 1 FROM users WHERE username = %s);",
#             (username,),
#         )
#         # INSECURE (string interpolation) — swap in for SQLi exercise:
#         # cur.execute(f"SELECT EXISTS(SELECT 1 FROM users WHERE username = '{username}');")
#         row = cur.fetchone()
#         return row["exists"] if row else False
#     except Exception as exc:
#         print(f"[DB] username_exists error: {exc}")
#         return False
#     finally:
#         cur.close()
#         conn.close()


# def get_user_by_username(username: str) -> dict | None:
#     """
#     Fetch a full user row by username.
#     Returns a dict with keys: id, username, email, password,
#     mfa_secret, mfa_enabled, reset_token, token_expiry, created_at.
#     Returns None if not found.

#     NOTE: this is the ONLY definition of this function — having two
#     definitions caused the MFA redirect loop (the second overwrote the
#     first and omitted mfa_enabled from the SELECT).
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             """
#             SELECT id, username, email, password,
#                    mfa_secret, mfa_enabled, reset_token,
#                    token_expiry, created_at
#             FROM users
#             WHERE username = %s;
#             """,
#             (username,),
#         )
#         return cur.fetchone()  # dict or None
#     except Exception as exc:
#         print(f"[DB] get_user_by_username error: {exc}")
#         return None
#     finally:
#         cur.close()
#         conn.close()


# def get_user_by_email(email: str) -> dict | None:
#     """Fetch a user row by email address."""
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             """
#             SELECT id, username, email, password,
#                    mfa_secret, mfa_enabled, reset_token,
#                    token_expiry, created_at
#             FROM users
#             WHERE email = %s;
#             """,
#             (email,),
#         )
#         return cur.fetchone()
#     except Exception as exc:
#         print(f"[DB] get_user_by_email error: {exc}")
#         return None
#     finally:
#         cur.close()
#         conn.close()


# def update_user_password(user_id: int, new_hashed_password: str) -> bool:
#     """
#     Update a user's password by user id and clear the reset token.

#     SECURE version (parameterized) is active.
#     INSECURE version is commented below for SQLi training — swap to observe.
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         # SECURE — active
#         cur.execute(
#             """
#             UPDATE users
#             SET password = %s, reset_token = NULL, token_expiry = NULL
#             WHERE id = %s;
#             """,
#             (new_hashed_password, user_id),
#         )
#         # INSECURE — uncomment for SQLi exercise, comment out the block above:
#         # cur.execute(
#         #     f"UPDATE users SET password = '{new_hashed_password}' WHERE id = {user_id};"
#         # )
#         conn.commit()
#         return True
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] update_user_password error: {exc}")
#         return False
#     finally:
#         cur.close()
#         conn.close()


# def delete_user(username: str) -> bool:
#     """
#     Permanently delete a user by username.

#     SECURE version (parameterized) is active.
#     INSECURE version is commented below for SQLi training.
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         # SECURE — active
#         cur.execute("DELETE FROM users WHERE username = %s;", (username,))
#         # INSECURE — uncomment for SQLi exercise:
#         # cur.execute(f"DELETE FROM users WHERE username = '{username}';")
#         conn.commit()
#         return True
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] delete_user error: {exc}")
#         return False
#     finally:
#         cur.close()
#         conn.close()


# # ---------------------------------------------------------------------------
# # Password reset
# # ---------------------------------------------------------------------------

# def generate_reset_token(email: str) -> str | None:
#     """
#     Create a 32-byte URL-safe token, store it against the user, and
#     return it so the caller (app.py) can build the reset URL.

#     Returns None if the email is not registered. The caller should NOT
#     expose this distinction to the end user (prevents email enumeration).
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute("SELECT id FROM users WHERE email = %s;", (email,))
#         user = cur.fetchone()
#         if not user:
#             return None

#         token = secrets.token_urlsafe(32)
#         expiry = datetime.utcnow() + timedelta(hours=1)

#         cur.execute(
#             """
#             UPDATE users
#             SET reset_token = %s, token_expiry = %s
#             WHERE id = %s;
#             """,
#             (token, expiry, user["id"]),
#         )
#         conn.commit()
#         return token
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] generate_reset_token error: {exc}")
#         return None
#     finally:
#         cur.close()
#         conn.close()


# def get_user_by_reset_token(token: str) -> dict | None:
#     """
#     Return the user row if the token exists and has not expired.
#     Returns None if invalid or expired.
#     """
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             """
#             SELECT id, username, token_expiry
#             FROM users
#             WHERE reset_token = %s;
#             """,
#             (token,),
#         )
#         user = cur.fetchone()
#         if not user:
#             return None
#         if not user["token_expiry"]:
#             return None
#         # Compare as naive UTC datetimes
#         expiry = user["token_expiry"].replace(tzinfo=None)
#         if datetime.utcnow() > expiry:
#             return None
#         return user
#     except Exception as exc:
#         print(f"[DB] get_user_by_reset_token error: {exc}")
#         return None
#     finally:
#         cur.close()
#         conn.close()


# # ---------------------------------------------------------------------------
# # MFA
# # ---------------------------------------------------------------------------

# def set_mfa_secret(username: str, secret: str) -> bool:
#     """Store a TOTP secret for a user (does not enable MFA yet)."""
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             "UPDATE users SET mfa_secret = %s WHERE username = %s;",
#             (secret, username),
#         )
#         conn.commit()
#         return True
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] set_mfa_secret error: {exc}")
#         return False
#     finally:
#         cur.close()
#         conn.close()


# def enable_mfa(username: str) -> bool:
#     """Mark MFA as fully enabled after the user verifies their first TOTP code."""
#     conn = _connect()
#     cur = conn.cursor()
#     try:
#         cur.execute(
#             "UPDATE users SET mfa_enabled = TRUE WHERE username = %s;",
#             (username,),
#         )
#         conn.commit()
#         return True
#     except Exception as exc:
#         conn.rollback()
#         print(f"[DB] enable_mfa error: {exc}")
#         return False
#     finally:
#         cur.close()
#         conn.close()
