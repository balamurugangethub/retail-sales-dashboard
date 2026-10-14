"""Sign-in, per-user data and the privacy/cookie consent gate.

* Consent gate: every page and API call needs the visitor to have accepted the
  privacy policy and the essential cookies on /welcome. Declining stores nothing
  and keeps them out.
* Accounts: email + password (salted scrypt hash via werkzeug). Each account
  gets its own SQLite file, so one shop never sees another shop's data.

Files live in <folder of the main database>/userdata/: accounts.db, one
shop_<id>.db per account and secret_key (signs the login cookie) unless the
SECRET_KEY environment variable is set.
"""
import os
import secrets
import sqlite3
import time
from datetime import timedelta

from flask import g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from cloud import StoreError

POLICY_VERSION = "2026-10-11"
CONSENT_COOKIE = "rsd_consent"
CONSENT_MAX_AGE = 365 * 24 * 3600
MIN_PASSWORD = 8
MAX_FAILS, LOCK_SECONDS = 5, 300
DEMO_HOURS = 24                     # demo accounts are deleted after this long
DEMO_PER_HOUR = 30                  # most demo accounts one server hands out per hour

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    shop TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now')),
    policy_version TEXT NOT NULL,
    policy_accepted_at TEXT NOT NULL DEFAULT (datetime('now')),
    demo INTEGER NOT NULL DEFAULT 0
);
"""

# Reachable before accepting the policy (the policy itself, the choice, assets)
CONSENT_FREE = {"/welcome", "/privacy", "/api/consent", "/api/auth/me", "/api/status", "/favicon.ico"}
# Reachable before signing in
SIGNED_OUT_OK = {"/login", "/register", "/api/auth/login", "/api/auth/register", "/api/auth/logout", "/api/auth/demo"}
PAGES = {"/welcome": "welcome.html", "/privacy": "privacy.html", "/login": "auth.html",
         "/register": "auth.html", "/account": "account.html"}


def _secret_key(folder):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(folder, "secret_key")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="ascii") as f:
        return f.read().strip()


def safe_next(target):
    """Only allow redirects back into this site."""
    target = str(target or "")
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


def register(app, prepare_db, consent=True, auth=True, sync=None, seed_demo=None):  # pylint: disable=too-many-locals,too-many-statements
    """prepare_db(path) creates an empty shop database. sync (cloud.Sync) is optional: it keeps the
    account files in an online store between visits when the host's disk is temporary. seed_demo(path)
    fills a new demo account with sample data; without it there is no demo button."""
    app.config["CONSENT_REQUIRED"], app.config["AUTH_ENABLED"] = consent, auth
    folder = os.path.join(os.path.dirname(os.path.abspath(app.config["DB_PATH"])), "userdata")
    accounts_db = os.path.join(folder, "accounts.db")
    contact = os.environ.get("PRIVACY_CONTACT", "")
    fails = {}                      # email -> (count, locked_until); in memory is enough for one shop

    if auth:
        os.makedirs(folder, exist_ok=True)
        app.secret_key = _secret_key(folder)
        with sqlite3.connect(accounts_db) as c:
            c.executescript(ACCOUNTS_SCHEMA)
            if "demo" not in {row[1] for row in c.execute("PRAGMA table_info(users)")}:
                c.execute("ALTER TABLE users ADD COLUMN demo INTEGER NOT NULL DEFAULT 0")     # accounts from older versions
        c.close()
    app.config.update(SESSION_COOKIE_NAME="rsd_session", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
                      PERMANENT_SESSION_LIFETIME=timedelta(days=14))

    def accounts():
        if "accounts" not in g:
            g.accounts = sqlite3.connect(accounts_db)
            g.accounts.row_factory = sqlite3.Row
        return g.accounts

    @app.teardown_appcontext
    def close_accounts(_exc):
        con = g.pop("accounts", None)
        if con is not None:
            con.close()

    def shop_db_path(uid):
        return os.path.join(folder, f"shop_{int(uid)}.db")

    def current_user():
        if not auth or "uid" not in session:
            return None
        if "user" not in g:
            g.user = accounts().execute("SELECT * FROM users WHERE id = ?", (session["uid"],)).fetchone()
        return g.user

    def public(user):
        return {"name": user["name"], "email": user["email"], "shop": user["shop"],
                "created": user["created"], "policy_version": user["policy_version"],
                "policy_accepted_at": user["policy_accepted_at"], "demo": bool(user["demo"])} if user else None

    def has_consent():
        return request.cookies.get(CONSENT_COOKIE) == POLICY_VERSION

    def blocked(api_message, page_target):
        if request.path.startswith("/api/"):
            return jsonify({"error": api_message}), 401
        return redirect(page_target + "?next=" + safe_next(request.full_path.rstrip("?")))

    @app.before_request
    def gate():
        path = request.path
        if path.startswith("/static/") or path in CONSENT_FREE:
            return None
        if consent and not has_consent():
            return blocked("Please accept the privacy policy and cookies first.", "/welcome")
        if sync and auth:
            try:
                sync.ensure("accounts.db", accounts_db)
            except StoreError as e:
                return unavailable(e)
        if not auth or path in SIGNED_OUT_OK:
            return None
        user = current_user()
        if user is None:
            session.clear()
            return blocked("Please sign in.", "/login")
        g.db_path = shop_db_path(user["id"])
        if sync:
            try:
                sync.ensure(os.path.basename(g.db_path), g.db_path)
            except StoreError as e:
                return unavailable(e)
        prepare_db(g.db_path)               # creates the file, and adds tables that newer versions need
        return None

    def unavailable(error):
        message = f"Online storage is not reachable right now ({error}). Try again in a moment."
        return (jsonify({"error": message}) if request.path.startswith("/api/") else message), 503

    @app.after_request
    def store_changes(resp):
        """Save changed files to the online store before the answer goes back."""
        if not (sync and auth) or request.method in ("GET", "HEAD", "OPTIONS") or resp.status_code >= 400:
            return resp
        try:
            sync.commit("accounts.db", accounts_db)
            if g.get("db_path"):
                sync.commit(os.path.basename(g.db_path), g.db_path)
        except StoreError:
            failed = jsonify({"error": "Your change could not be saved online. Please try again."})
            failed.status_code = 503
            return failed
        return resp

    def page(name):
        return send_from_directory(app.static_folder, PAGES[name])

    for route in PAGES:
        app.add_url_rule(route, "page_" + route.strip("/"), lambda route=route: page(route))

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @app.post("/api/consent")
    def set_consent():
        resp = jsonify({"consent": bool(body().get("accept"))})
        if body().get("accept") is True:
            resp.set_cookie(CONSENT_COOKIE, POLICY_VERSION, max_age=CONSENT_MAX_AGE, httponly=True,
                            samesite="Lax", secure=app.config["SESSION_COOKIE_SECURE"])
        else:                                   # declining: keep nothing about this visitor
            resp.delete_cookie(CONSENT_COOKIE)
            resp.delete_cookie(app.config["SESSION_COOKIE_NAME"])
            if auth:
                session.clear()
        return resp

    @app.get("/api/auth/me")
    def me():
        return jsonify({"consent": has_consent(), "consent_required": consent, "auth_enabled": auth,
                        "read_only": app.config["READ_ONLY"], "policy_version": POLICY_VERSION,
                        "contact": contact, "demo_available": bool(auth and seed_demo), "user": public(current_user()) if has_consent() or not consent else None})

    def sign_in(uid):
        session.clear()
        session.permanent = True
        session["uid"] = uid

    @app.post("/api/auth/register")
    def register_user():
        if not auth:
            return jsonify({"error": "Accounts are switched off on this site."}), 400
        data = body()
        email = str(data.get("email") or "").strip()
        name = str(data.get("name") or "").strip()[:80]
        shop = str(data.get("shop") or "").strip()[:80]
        password = str(data.get("password") or "")
        if "@" not in email or "." not in email.rsplit("@", maxsplit=1)[-1] or len(email) > 200:
            return jsonify({"error": "Enter a valid email address."}), 400
        if not name:
            return jsonify({"error": "Enter your name."}), 400
        if len(password) < MIN_PASSWORD:
            return jsonify({"error": f"Password must be at least {MIN_PASSWORD} characters."}), 400
        if data.get("accept_policy") is not True:
            return jsonify({"error": "You need to accept the privacy policy to create an account."}), 400
        try:
            with accounts() as c:
                cur = c.execute("INSERT INTO users(email, name, shop, password_hash, policy_version)"
                                " VALUES (?,?,?,?,?)",
                                (email, name, shop, generate_password_hash(password), POLICY_VERSION))
        except sqlite3.IntegrityError:
            return jsonify({"error": "An account with this email already exists. Sign in instead."}), 400
        g.db_path = shop_db_path(cur.lastrowid)
        prepare_db(g.db_path)
        sign_in(cur.lastrowid)
        return jsonify({"user": public(current_user())})

    def remove_files(uid):
        path = shop_db_path(uid)
        for suffix in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
        if sync:
            try:
                sync.forget(os.path.basename(path))
            except StoreError:
                pass                                    # the account row is gone; the file is orphaned

    def purge_old_demos():
        old = accounts().execute("SELECT id FROM users WHERE demo = 1 AND created < datetime('now', ?)",
                                 (f"-{DEMO_HOURS} hours",)).fetchall()
        for row in old:
            with accounts() as c:
                c.execute("DELETE FROM users WHERE id = ?", (row["id"],))
            remove_files(row["id"])

    demo_times = []

    @app.post("/api/auth/demo")
    def start_demo():
        if not (auth and seed_demo):
            return jsonify({"error": "The demo is not available on this site."}), 400
        now = time.time()
        demo_times[:] = [t for t in demo_times if t > now - 3600]
        if len(demo_times) >= DEMO_PER_HOUR:
            return jsonify({"error": "Lots of people are trying the demo right now. Try again in a few minutes."}), 429
        demo_times.append(now)
        purge_old_demos()
        email = f"demo-{secrets.token_hex(6)}@demo.invalid"
        with accounts() as c:
            cur = c.execute("INSERT INTO users(email, name, shop, password_hash, policy_version, demo)"
                            " VALUES (?,?,?,?,?,1)",
                            (email, "Demo visitor", "Demo shop", generate_password_hash(secrets.token_hex(16)), POLICY_VERSION))
        g.db_path = shop_db_path(cur.lastrowid)
        prepare_db(g.db_path)
        seed_demo(g.db_path)
        sign_in(cur.lastrowid)
        return jsonify({"user": public(current_user())})

    dummy_hash = generate_password_hash(secrets.token_hex(8))

    @app.post("/api/auth/login")
    def login():
        if not auth:
            return jsonify({"error": "Accounts are switched off on this site."}), 400
        data = body()
        email = str(data.get("email") or "").strip().lower()
        count, until = fails.get(email, (0, 0))
        if until > time.time():
            return jsonify({"error": "Too many failed attempts. Try again in a few minutes."}), 429
        user = accounts().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        # Check a hash even for unknown emails so response time doesn't reveal which emails exist
        ok = check_password_hash(user["password_hash"] if user else dummy_hash, str(data.get("password") or ""))
        if not (user and ok):
            count += 1
            fails[email] = (0, time.time() + LOCK_SECONDS) if count >= MAX_FAILS else (count, 0)
            return jsonify({"error": "Wrong email or password."}), 401
        fails.pop(email, None)
        sign_in(user["id"])
        return jsonify({"user": public(user)})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/account/delete")
    def delete_account():
        user = current_user()
        if not user["demo"] and not check_password_hash(user["password_hash"], str(body().get("password") or "")):
            return jsonify({"error": "Password is not correct."}), 401
        with accounts() as c:
            c.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        remove_files(user["id"])
        g.pop("db_path", None)
        session.clear()
        return jsonify({"ok": True})
