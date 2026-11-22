"""Atomic history writes with concurrent callbacks on an isolated PostgreSQL."""
import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb

from planner import MOSCOW, morning_affirmation, new_state


@unittest.skipUnless(os.getenv("PLANNER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class AffirmationPostgresTests(unittest.TestCase):
    def test_history_confirmation_concurrency_and_preservation(self):
        url = os.environ["PLANNER_TEST_DATABASE_URL"]
        self.assertEqual(conninfo.conninfo_to_dict(url).get("host"), "127.0.0.1")
        env = {
            "BOT_TOKEN": "unused-in-tests", "OWNER_ID": "123",
            "WEBHOOK_SECRET": "unused-in-tests", "CRON_SECRET": "test-schedule-secret",
            "DATABASE_URL": url, "RENDER_EXTERNAL_URL": "https://unused.example",
        }
        with patch.dict(os.environ, env):
            import app
        schema = "affirmation_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        self.addCleanup(self.drop_schema, url, schema)

        def connect():
            return psycopg.connect(url, options=f"-c search_path={schema}")

        now = datetime(2026, 11, 22, 12, tzinfo=MOSCOW)
        state = new_state()
        state["tasks"] = {"1": "Сохранённая задача"}
        state["reminders"] = {"2": {"due": "2026-12-03T15:00:00+03:00", "text": "Напоминание"}}
        state["journal"] = {"2026-11-21": {"notes": "Дневник", "thanks": "Благодарности"}}
        profile = {"建": {"text": "Сохранённая характеристика"}}
        history = [
            {"effective_from": "2026-11-16", "text": "Текущая"},
            {"effective_from": "2026-12-01", "text": "Декабрьская"},
        ]
        with connect() as db:
            db.execute(app.SCHEMA)
            db.execute("ALTER TABLE planner_state ADD bazi_profile jsonb, ADD affirmations_history jsonb")
            db.execute("INSERT INTO planner_state VALUES (1, %s, %s, %s)",
                       (Jsonb(state), Jsonb(profile), Jsonb(history)))

        def incoming(number, text=None, action=None):
            source = {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}}
            if action:
                return {"update_id": number, "callback_query": {
                    "id": str(number), "data": action, "from": {"id": 123}, "message": source}}
            return {"update_id": number, "message": {**source, "text": text}}

        with patch.dict(os.environ, env), patch.object(app, "connect", side_effect=connect), \
                patch.object(app, "datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            app.update_state(incoming(1, "/affirmations"))
            with connect() as db:
                actual = db.execute("SELECT affirmations_history FROM planner_state").fetchone()[0]
                self.assertEqual(actual, history)
            app.update_state(incoming(2, "/affirm С 25 ноября"))
            app.update_state(incoming(3, "25.11.2026"))
            with connect() as db:
                saved, actual = db.execute("SELECT data, affirmations_history FROM planner_state").fetchone()
                token = saved["affirmation_draft"]["token"]
                self.assertEqual(actual, history)
            start = Barrier(2)

            def confirm(number):
                start.wait(timeout=5)
                app.update_state(incoming(number, action="af:confirm:" + token))

            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = [pool.submit(confirm, number) for number in (4, 5)]
                for run in runs:
                    run.result(timeout=10)
            app.update_state(incoming(4, action="af:confirm:" + token))
            with connect() as db:
                saved, actual_profile, actual_history = db.execute(
                    "SELECT data, bazi_profile, affirmations_history FROM planner_state").fetchone()
            self.assertEqual(len(actual_history), 3)
            self.assertEqual(actual_history[:2], history)
            self.assertEqual(actual_profile, profile)
            for field in ("tasks", "reminders", "journal", "sent", "affirmation"):
                self.assertEqual(saved[field], state[field])
            self.assertNotIn("affirmation_draft", saved)
            self.assertEqual(morning_affirmation(actual_history, now.date(), ""), "Текущая")
            self.assertEqual(morning_affirmation(actual_history, now.date().replace(day=25), ""), "С 25 ноября")
            self.assertEqual(morning_affirmation(actual_history, now.date().replace(month=12, day=1), ""), "Декабрьская")
            before = copy.deepcopy(actual_history)
            app.update_state(incoming(6, "/add Ещё задача"))
            with connect() as db:
                after = db.execute("SELECT affirmations_history FROM planner_state").fetchone()[0]
            self.assertEqual(after, before)

    @staticmethod
    def drop_schema(url, schema):
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
