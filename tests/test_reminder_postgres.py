"""Confirm and deliver through the real state transaction using a disposable DB."""
import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb

from planner import MOSCOW, handle, new_state


@unittest.skipUnless(os.getenv("PLANNER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class ReminderPostgresTests(unittest.TestCase):
    def test_double_confirmation_and_concurrent_delivery(self):
        url = os.environ["PLANNER_TEST_DATABASE_URL"]
        self.assertEqual(conninfo.conninfo_to_dict(url).get("host"), "127.0.0.1")
        env = {
            "BOT_TOKEN": "unused-in-tests", "OWNER_ID": "123",
            "WEBHOOK_SECRET": "unused-in-tests", "CRON_SECRET": "test-schedule-secret",
            "DATABASE_URL": url, "RENDER_EXTERNAL_URL": "https://unused.example",
        }
        with patch.dict(os.environ, env):
            import app

        schema = "reminder_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.drop_schema, url, schema)

        def connect():
            return psycopg.connect(url, options=f"-c search_path={schema}")

        now = datetime(2026, 11, 22, 23, 59, tzinfo=MOSCOW)
        state = new_state()
        state["tasks"] = {"1": "Сохранённая задача"}
        state["next_id"] = 2
        state["journal"] = {"2026-11-22": {"score": 4, "notes": "Сохранённый дневник"}}
        state["pending"] = ["thanks", "2026-11-22"]
        state["sent"] = {"morning": "2026-11-22", "evening": "2026-11-22"}
        handle(state, now, "Напомни через 1 минуту проверить доставку")
        token = state["reminder_draft"]["token"]
        original = copy.deepcopy(state)
        profile, history = {"建": {"text": "Описание"}}, [{"effective_from": "2026-11-16", "text": "Аффирмация"}]
        with connect() as db:
            db.execute(app.SCHEMA)
            db.execute("ALTER TABLE planner_state ADD bazi_profile jsonb, ADD affirmations_history jsonb")
            db.execute("INSERT INTO planner_state VALUES (1, %s, %s, %s)",
                       (Jsonb(state), Jsonb(profile), Jsonb(history)))

        def confirm(number):
            return {
                "update_id": number,
                "callback_query": {
                    "id": str(number), "data": f"reminder_confirm:{token}", "from": {"id": 123},
                    "message": {"chat": {"id": 123, "type": "private"}},
                },
            }

        calls = []
        with patch.dict(os.environ, env), patch.object(app, "connect", side_effect=connect), \
                patch.object(app, "telegram", side_effect=lambda method, payload: calls.append((method, payload))), \
                patch.object(app, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            start = Barrier(2)

            def confirming(number):
                start.wait(timeout=5)
                app.update_state(confirm(number))

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(confirming, number) for number in (10, 11)]
                for future in futures:
                    future.result(timeout=10)
            # A retried Telegram update must be ignored as well.
            app.update_state(confirm(10))
            with connect() as db:
                saved = db.execute("SELECT data FROM planner_state").fetchone()[0]
                self.assertEqual(len(saved["reminders"]), 1)
                self.assertEqual(saved["journal"], original["journal"])
                self.assertEqual(saved["paused_diary"], original["paused_diary"])
                self.assertEqual(saved["tasks"], original["tasks"])
            app.deliver()
            calls.clear()
            clock.now.return_value = datetime(2026, 11, 23, 0, 10, tzinfo=MOSCOW)
            ticking = Barrier(2)

            def tick():
                ticking.wait(timeout=5)
                status = []
                app.app({"PATH_INFO": "/tick", "REQUEST_METHOD": "POST", "wsgi.input": BytesIO(),
                         "HTTP_AUTHORIZATION": "Bearer test-schedule-secret"},
                        lambda code, headers: status.append(code))
                return status

            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = [pool.submit(tick) for _ in range(2)]
                self.assertEqual([run.result(timeout=10) for run in runs], [["200 OK"], ["200 OK"]])
            notifications = [p for method, p in calls if method == "sendMessage"
                             and p["text"] == "Напоминание · проверить доставку"]
            self.assertEqual(len(notifications), 1)
            with connect() as db:
                saved, actual_profile, actual_history = db.execute(
                    "SELECT data, bazi_profile, affirmations_history FROM planner_state").fetchone()
                self.assertEqual(saved["reminders"], {})
                self.assertEqual(saved["journal"], original["journal"])
                self.assertEqual(saved["tasks"], original["tasks"])
                self.assertEqual((actual_profile, actual_history), (profile, history))
                self.assertEqual(db.execute("SELECT count(*) FROM planner_outbox").fetchone()[0], 0)

    @staticmethod
    def drop_schema(url, schema):
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
