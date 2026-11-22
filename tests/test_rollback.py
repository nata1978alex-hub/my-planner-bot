import copy
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb

from schema_fixture import configure_state_table

from affirmation_flow import entry_key
from planner import MOSCOW, morning_affirmation, new_state
from scripts.backup_affirmations import checksum, export, verify
from scripts.prepare_rollback import PREVIOUS_COMMIT, prepare


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackupFormatTests(unittest.TestCase):
    def test_integrity_check_detects_modified_history(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            path = Path(temp) / "history.json"
            history = [{"effective_from": "2026-11-16", "text": "Сохранённый текст"}]
            payload = {"format_version": 1, "row_id": 1, "history": history, "sha256": checksum(history)}
            path.write_text(json.dumps(payload))
            self.assertEqual(verify(path)["history"], history)
            payload["history"][0]["text"] = "Повреждено"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify(path)

    def test_export_is_read_only_exclusive_and_verifiable(self):
        from contextlib import nullcontext
        from unittest.mock import Mock

        connection = Mock()
        connection.transaction.side_effect = nullcontext
        history = [{"effective_from": "2026-11-16", "text": "Текст"}]
        connection.execute.return_value.fetchone.return_value = (history,)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            path = Path(temp) / "history.json"
            export(connection, path)
            self.assertEqual(verify(path)["history"], history)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                export(connection, path)
            self.assertEqual(path.read_bytes(), before)
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertTrue(any("READ ONLY" in statement for statement in statements))
        self.assertTrue(all(statement.startswith(("SET TRANSACTION", "SELECT ")) for statement in statements))


@unittest.skipUnless(os.getenv("PLANNER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL and git history")
class RollbackPostgresTests(unittest.TestCase):
    def test_add_reschedule_cancel_backup_and_compatible_rollback(self):
        url = os.environ["PLANNER_TEST_DATABASE_URL"]
        self.assertEqual(conninfo.conninfo_to_dict(url).get("host"), "127.0.0.1")
        database = "rollback_test_" + uuid4().hex
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        self.addCleanup(self.drop_database, url, database)
        test_url = conninfo.make_conninfo(url, dbname=database)
        env = {
            "BOT_TOKEN": "unused-in-tests", "OWNER_ID": "123",
            "WEBHOOK_SECRET": "unused-in-tests", "CRON_SECRET": "test-schedule-secret",
            "DATABASE_URL": test_url, "RENDER_EXTERNAL_URL": "https://unused.example",
        }
        with patch.dict(os.environ, env):
            import app
        state = new_state()
        state["tasks"] = {"1": "Сохранённая задача"}
        state["next_id"] = 3
        state["reminders"] = {"2": {"due": "2026-12-31T15:00:00+03:00", "text": "Позвонить"}}
        state["journal"] = {"2026-11-21": {"notes": "Дневник", "thanks": "Благодарности", "score": 5}}
        state["days"] = {"2026-12-01": "Личная заметка"}
        state["affirmation"] = "Прежнее поле"
        profile = {"建": {"text": "Пользовательское описание"}}
        initial = [
            {"effective_from": "2026-11-16", "text": "Текущая"},
            {"effective_from": "2026-12-01", "text": "Декабрьская"},
        ]

        def connect():
            return psycopg.connect(test_url)

        with connect() as db:
            column = configure_state_table(db, app)
            db.execute(f"INSERT INTO planner_state (id, {column}, bazi_profile, affirmations_history) VALUES (1, %s, %s, %s)",
                       (Jsonb(state), Jsonb(profile), Jsonb(initial)))

        now = datetime(2026, 11, 22, 12, tzinfo=MOSCOW)
        number = 0

        def send(text=None, action=None):
            nonlocal number
            number += 1
            source = {"chat": {"id": 123, "type": "private"}, "from": {"id": 123}}
            update = {"update_id": number, "message": {**source, "text": text}}
            if action:
                update = {"update_id": number, "callback_query": {
                    "id": str(number), "data": action, "from": {"id": 123}, "message": source}}
            app.update_state(update)

        def saved_row():
            with connect() as db:
                return db.execute(f"SELECT {column}, bazi_profile, affirmations_history, version, to_jsonb(p) FROM planner_state AS p").fetchone()

        def confirm():
            draft = saved_row()[0]["affirmation_draft"]
            send(action="af:confirm:" + draft["token"])

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            temp = Path(temp)
            before_path, after_path = temp / "before.json", temp / "after.json"
            with connect() as db:
                export(db, before_path)
            self.assertEqual(verify(before_path)["history"], initial)
            with patch.dict(os.environ, env), patch.object(app, "connect", side_effect=connect), \
                    patch.object(app, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                send("/affirm Новая")
                send("25.11.2026")
                confirm()
                added = saved_row()[2][-1]
                send(action="af:edit:" + entry_key(added))
                send("Перенесённая")
                send("27.11.2026")
                confirm()
                send(action="af:remove:" + entry_key(initial[1]))
                confirm()
            after = saved_row()
            with connect() as db:
                export(db, after_path)
            self.assertEqual(verify(after_path)["history"], after[2])
            self.assertTrue(any(e.get("status") == "cancelled" for e in after[2]))
            self.assertTrue(any(e.get("status") == "superseded" for e in after[2]))
            self.assertEqual(len(after[2]), 4)
            self.assertEqual(after[1], profile)
            self.assertEqual(after[3], 7)
            for field in ("tasks", "reminders", "journal", "days", "affirmation"):
                self.assertEqual(after[0][field], state[field])

            rollback_path = prepare(temp / "rollback", Path.cwd())
            previous_source = subprocess.run(
                ["git", "show", PREVIOUS_COMMIT + ":planner.py"], check=True, capture_output=True,
                text=True,
            ).stdout
            actual_source = (rollback_path / "planner.py").read_text()
            from scripts.prepare_rollback import OLD_CHECK, COMPATIBLE_CHECK
            self.assertEqual(actual_source, previous_source.replace(OLD_CHECK, COMPATIBLE_CHECK, 1))
            from scripts.prepare_rollback import compatible_app
            old_application = subprocess.run(
                ["git", "show", PREVIOUS_COMMIT + ":app.py"], check=True, capture_output=True, text=True,
            ).stdout
            self.assertEqual((rollback_path / "app.py").read_text(), compatible_app(old_application))
            self.assertEqual((rollback_path / "state_storage.py").read_text(), Path("state_storage.py").read_text())
            rollback_planner = load_module("rollback_planner", rollback_path / "planner.py")
            for stamp in ("2026-11-22", "2026-11-25", "2026-11-27", "2026-12-01"):
                day = date.fromisoformat(stamp)
                expected = morning_affirmation(after[2], day, "Прежнее поле")
                self.assertEqual(rollback_planner.morning_affirmation(after[2], day, "Прежнее поле"), expected)
            self.assertEqual(rollback_planner.morning_affirmation(after[2], date(2026, 12, 1), ""), "Перенесённая")

            # Start the exported old app with the compatible selector, then process a read command.
            with patch.dict(os.environ, env), patch.dict("sys.modules", {"planner": rollback_planner}):
                old_app = load_module("rollback_app", rollback_path / "app.py")
            with patch.object(old_app, "connect", side_effect=connect), \
                    patch.object(old_app, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                old_app.update_state({"update_id": 1000, "message": {"text": "/today"}})
            final = saved_row()
            self.assertEqual(final, after)
            self.assertEqual(verify(before_path)["history"], initial)
            self.assertEqual(verify(after_path)["history"], after[2])

    @staticmethod
    def drop_database(url, database):
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
