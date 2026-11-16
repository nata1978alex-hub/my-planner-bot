"""Integration test. Use a disposable PostgreSQL, never the bot's database."""
import os
import time
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

from planner import MOSCOW, new_state


@unittest.skipUnless(os.getenv("PLANNER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class PostgresMorningTests(unittest.TestCase):
    def test_concurrent_ticks_read_profiles_and_queue_one_morning_plan(self):
        url = os.environ["PLANNER_TEST_DATABASE_URL"]
        self.assertEqual(conninfo.conninfo_to_dict(url).get("host"), "127.0.0.1")
        variables = {
            "BOT_TOKEN": "unused-in-tests", "OWNER_ID": "123",
            "WEBHOOK_SECRET": "unused-in-tests", "CRON_SECRET": "test-schedule-secret",
            "DATABASE_URL": url, "RENDER_EXTERNAL_URL": "https://unused.example",
        }
        with patch.dict(os.environ, variables):
            import app

        schema = "morning_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            self.addCleanup(self.drop_schema, url, schema)

        def connect():
            return psycopg.connect(url, options=f"-c search_path={schema}", application_name=schema)

        state = new_state()
        state["tasks"] = {"1": "Сохранённая задача"}
        state["days"] = {"2026-12-07": "Сохранённая заметка"}
        state["journal"] = {"2026-12-06": {"score": 4, "notes": "Дневник", "complete": True}}
        state["reminders"] = {"2": {"due": "2026-12-07T14:00:00+03:00", "text": "Напоминание"}}
        profile = {"定": {"name": "Определение", "text": "Описание из PostgreSQL"}}
        history = [
            {"effective_from": "2026-11-16", "text": "Аффирмация из PostgreSQL"},
            {"effective_from": "2027-01-01", "text": "Будущая аффирмация"},
        ]
        with connect() as db:
            db.execute(app.SCHEMA)
            db.execute("ALTER TABLE planner_state ADD bazi_profile jsonb, ADD affirmations_history jsonb")
            db.execute("INSERT INTO planner_state VALUES (1, %s, %s, %s)",
                       (Jsonb(state), Jsonb(profile), Jsonb(history)))

        start = Barrier(2)

        def tick():
            start.wait(timeout=5)
            statuses = []
            environ = {"PATH_INFO": "/tick", "REQUEST_METHOD": "POST", "wsgi.input": BytesIO(),
                       "HTTP_AUTHORIZATION": "Bearer test-schedule-secret"}
            app.app(environ, lambda status, headers: statuses.append(status))
            return statuses

        with patch.dict(os.environ, variables), patch.object(app, "connect", side_effect=connect), \
                patch.object(app, "deliver"), \
                patch.object(app, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 12, 7, 5, tzinfo=MOSCOW)
            with ThreadPoolExecutor(max_workers=2) as pool:
                with connect() as held_lock:
                    held_lock.execute("SELECT id FROM planner_state WHERE id = 1 FOR UPDATE")
                    runs = [pool.submit(tick) for _ in range(2)]
                    deadline = time.monotonic() + 8
                    with psycopg.connect(url, autocommit=True) as monitor:
                        while time.monotonic() < deadline:
                            waiting = monitor.execute("""
                                SELECT count(*) FROM pg_stat_activity
                                WHERE application_name = %s AND wait_event_type = 'Lock'
                            """, (schema,)).fetchone()[0]
                            if waiting >= 2:
                                break
                            time.sleep(0.05)
                    self.assertGreaterEqual(waiting, 2, "Both /tick requests must overlap at the row lock")
                self.assertEqual([run.result(timeout=10) for run in runs], [["200 OK"], ["200 OK"]])

        with connect() as db:
            messages = db.execute("SELECT payload FROM planner_outbox ORDER BY id").fetchall()
            saved, saved_profile, saved_history = db.execute(
                "SELECT data, bazi_profile, affirmations_history FROM planner_state").fetchone()
        self.assertEqual(len(messages), 1)
        self.assertIn("Описание из PostgreSQL", messages[0][0]["text"])
        self.assertIn("Аффирмация из PostgreSQL", messages[0][0]["text"])
        self.assertNotIn("Будущая аффирмация", messages[0][0]["text"])
        self.assertEqual(saved_profile, profile)
        self.assertEqual(saved_history, history)
        self.assertEqual(saved, {**state, "sent": {"morning": "2026-12-07"}})

    @staticmethod
    def drop_schema(url, schema):
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
