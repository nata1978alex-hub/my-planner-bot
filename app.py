"""Telegram webhook and scheduled jobs, served by Gunicorn."""
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
from psycopg.types.json import Jsonb

from scheduler_auth import is_scheduler
from state_storage import ensure_state, load_state, save_state
from planner import MOSCOW, handle, message, new_state, scheduled

TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
WEBHOOK_SEED = os.environ["WEBHOOK_SECRET"]
WEBHOOK_SECRET = hashlib.sha256(WEBHOOK_SEED.encode()).hexdigest()
CRON_SECRET = os.environ["CRON_SECRET"]
DATABASE_URL = os.environ["DATABASE_URL"]
LOCK_ID = 87346109

SCHEMA = """
CREATE TABLE IF NOT EXISTS planner_state (
    id INTEGER PRIMARY KEY,
    data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS planner_updates (
    id BIGINT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS planner_outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method TEXT NOT NULL,
    payload JSONB NOT NULL
);
"""


def connect():
    return psycopg.connect(DATABASE_URL, connect_timeout=8)


def telegram(method, payload):
    content_type = "application/json"
    if method == "sendDocument":
        boundary = secrets.token_hex(16)
        content_type = f"multipart/form-data; boundary={boundary}"
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n'
            f'{payload["chat_id"]}\r\n--{boundary}\r\nContent-Disposition: form-data; '
            f'name="document"; filename="{payload["filename"]}"\r\n'
            f'Content-Type: text/markdown; charset=utf-8\r\n\r\n'
        )
        data = (prefix + payload["content"] + f"\r\n--{boundary}--\r\n").encode()
    else:
        data = json.dumps(payload, ensure_ascii=False).encode()
    request = Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data,
                      headers={"Content-Type": content_type})
    with urlopen(request, timeout=6) as response:
        result = json.load(response)
        if not result["ok"]:
            raise RuntimeError("Telegram request failed")
        return result["result"]


def initialize():
    with connect() as db:
        db.execute(SCHEMA)
        ensure_state(db, new_state())
    telegram("setWebhook", {
        "url": os.environ["RENDER_EXTERNAL_URL"].rstrip("/") + "/telegram",
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query"],
        "max_connections": 1,
    })


def update_state(update=None):
    now = datetime.now(MOSCOW)
    with connect() as db:
        if update is None:
            state, profile, history = load_state(db)
            responses = scheduled(state, now, bazi_profile=profile, affirmations_history=history)
        else:
            state = load_state(db)[0]
            processed = db.execute(
                "INSERT INTO planner_updates VALUES (%s) ON CONFLICT DO NOTHING RETURNING id",
                (update["update_id"],),
            ).fetchone()
            if not processed:
                return
            callback = update.get("callback_query")
            incoming = callback["message"] if callback else update["message"]
            responses = handle(state, now, incoming.get("text", "") if not callback else "",
                               callback.get("data", "") if callback else "")
            if callback:
                responses.insert(0, ("answerCallbackQuery", {"callback_query_id": callback["id"]}))
        save_state(db, state)
        for method, payload in responses:
            if method != "answerCallbackQuery":
                payload["chat_id"] = OWNER_ID
            db.execute("INSERT INTO planner_outbox (method, payload) VALUES (%s, %s)",
                       (method, Jsonb(payload)))


def deliver():
    with connect() as db:
        for _ in range(10):
            with db.transaction():
                locked = db.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_ID,)).fetchone()[0]
                if not locked:
                    return
                row = db.execute("SELECT id, method, payload FROM planner_outbox ORDER BY id LIMIT 1").fetchone()
                if not row:
                    return
                row_id, method, payload = row
                try:
                    telegram(method, payload)
                except HTTPError as error:
                    if error.code != 400 or method != "answerCallbackQuery":
                        raise
                db.execute("DELETE FROM planner_outbox WHERE id = %s", (row_id,))
            if method == "sendMessage" and payload.get("text", "").startswith("Напоминание · "):
                logging.warning("Telegram accepted a due reminder")


def private_update(update):
    callback = update.get("callback_query")
    incoming = callback.get("message", {}) if callback else update.get("message", {})
    sender = callback.get("from", {}) if callback else incoming.get("from", {})
    chat = incoming.get("chat", {})
    if chat.get("type") != "private" or sender.get("id") != chat.get("id"):
        return
    if OWNER_ID == 0 and not callback:
        if incoming.get("text", "").strip() == "/id":
            _, payload = message(f"Твой Telegram ID — {sender['id']}")
            telegram("sendMessage", {"chat_id": sender["id"], **payload})
        return
    if sender.get("id") == OWNER_ID:
        update_state(update)
        deliver()


def app(environ, start_response):
    path, method = environ["PATH_INFO"], environ["REQUEST_METHOD"]
    status = "200 OK"
    try:
        if (path, method) == ("/health", "GET"):
            pass
        elif (path, method) == ("/telegram", "POST"):
            if not hmac.compare_digest(environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", ""), WEBHOOK_SECRET):
                status = "403 Forbidden"
            elif not environ.get("CONTENT_LENGTH", "").isdigit() or int(environ["CONTENT_LENGTH"]) > 262144:
                status = "400 Bad Request"
            else:
                raw = environ["wsgi.input"].read(int(environ["CONTENT_LENGTH"]))
                private_update(json.loads(raw))
        elif (path, method) == ("/tick", "POST"):
            authorization = environ.get("HTTP_AUTHORIZATION", "")
            audience = os.environ["RENDER_EXTERNAL_URL"].rstrip("/") + "/tick"
            shared_secret = hmac.compare_digest(authorization, f"Bearer {CRON_SECRET}")
            if not shared_secret and not is_scheduler(authorization, audience):
                status = "403 Forbidden"
            elif not OWNER_ID:
                status = "503 Service Unavailable"
            else:
                update_state()
                deliver()
        else:
            status = "404 Not Found"
    except Exception as error:
        logging.error("Request failed on %s (%s)", path, type(error).__name__)
        status = "503 Service Unavailable"
    start_response(status, [("Content-Type", "text/plain; charset=utf-8")])
    return [status.encode()]


if __name__ == "__main__":
    if min(len(WEBHOOK_SEED), len(CRON_SECRET)) < 32:
        raise SystemExit("Set separate webhook and cron secrets of at least 32 characters.")
    try:
        initialize()
    except Exception as error:
        raise SystemExit(f"Initialization failed ({type(error).__name__}). Check Render environment variables.")
