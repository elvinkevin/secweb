# SecWeb — Minimal Flask App for Security Training

A small, deliberately simple Flask web application for practicing
web application security concepts.

## Pages
- **Home** (`/`)
- **Create Account** (`/register`)
- **Login** (`/login`)
- **Forgot Password** (`/forgot-password`) and **Reset Password** (`/reset-password/<token>`)
- **Dashboard** (`/dashboard`) — login required

## Setup

```bash
cd secweb
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app runs at http://127.0.0.1:5000 and creates `secweb.db` (SQLite) automatically.

## Architecture notes for trainers

- `templates/base.html` is the shared layout (nav, flash messages, footer).
  All other templates `{% extends "base.html" %}` and override the
  `title` and `content` blocks — extend this pattern for any new pages.
- Passwords are hashed with Werkzeug's `generate_password_hash` /
  `check_password_hash` (PBKDF2 by default).
- SQL queries use parameterized placeholders (`?`) — a good place to
  demonstrate SQL injection by swapping to string concatenation/f-strings,
  then fixing it back.
- Sessions use Flask's signed cookie session; `SECRET_KEY` is randomly
  generated per run (set the `SECRET_KEY` env var for a stable key across
  restarts).
- The forgot-password flow generates a reset token (`secrets.token_urlsafe`)
  with a 1-hour expiry and single-use flag, and displays the reset link
  directly in the UI (no email server needed for the lab).

## Suggested exercises

1. **SQL Injection** — modify the login query to use string formatting and
   try to bypass authentication with `' OR '1'='1`.
2. **Broken Access Control / IDOR** — add a route like
   `/dashboard/<user_id>` that doesn't check ownership.
3. **Session Management** — inspect cookies, test session fixation, try
   accessing `/dashboard` after logout.
4. **Password Reset Weaknesses** — shorten/weaken the token, remove the
   expiry check, or remove the single-use flag, and discuss the impact.
5. **XSS** — render user input (e.g. username) with `| safe` or
   `{% autoescape false %}` and test stored/reflected XSS, then fix it.
6. **CSRF** — the forms currently have no CSRF protection; add
   Flask-WTF / `flask-seasurf` and demonstrate a CSRF attack before and after.
7. **Rate limiting / brute force** — add `Flask-Limiter` to `/login` and
   `/forgot-password`.
8. **User enumeration** — compare responses for valid vs invalid emails on
   `/forgot-password` (currently identical by design — try breaking it).

## Security disclaimer

This app is intended for **local, isolated training environments only**.
Do not deploy it to the public internet as-is.
