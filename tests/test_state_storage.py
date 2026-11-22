import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb

from planner import new_state
from state_storage import StateStorageError, ensure_state, load_state, save_state, state_column


@unittest.skipUnless(os.getenv("PLANNER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL")
class StateStorageTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["PLANNER_TEST_DATABASE_URL"]
        self.assertEqual(conninfo.conninfo_to_dict(self.url).get("host"), "127.0.0.1")
        self.schema = "storage_test_" + uuid4().hex
        with psycopg.connect(self.url, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)

    def connect(self):
        return psycopg.connect(self.url, options=f"-c search_path={self.schema}")

    def drop_schema(self):
        with psycopg.connect(self.url, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_payload_with_version_and_other_columns(self):
        original = new_state()
        original["tasks"] = {"1": "Сохранить"}
        original["custom_setting"] = {"keep": True}
        history = [{"effective_from": "2026-11-16", "text": "Текст"}]
        profile = {"建": {"text": "Характеристика"}}
        with self.connect() as db:
            db.execute("""CREATE TABLE planner_state (
                id integer PRIMARY KEY, version integer NOT NULL,
                payload jsonb NOT NULL, affirmations_history jsonb, bazi_profile jsonb,
                last_message_date date)""")
            db.execute("INSERT INTO planner_state VALUES (1, 42, %s, %s, %s, '2026-11-22')",
                       (Jsonb(original), Jsonb(history), Jsonb(profile)))
            columns_before = db.execute("SELECT attname FROM pg_attribute WHERE attrelid = 'planner_state'::regclass AND attnum>0").fetchall()
            ensure_state(db, new_state())
            actual, actual_profile, actual_history = load_state(db)
            self.assertEqual((actual, actual_profile, actual_history), (original, profile, history))
            actual["pending"] = ["task", "2026-11-22"]
            save_state(db, actual)
            version, saved, saved_history, saved_profile, stamp = db.execute(
                "SELECT version, payload, affirmations_history, bazi_profile, last_message_date FROM planner_state").fetchone()
            self.assertEqual(version, 42)
            self.assertEqual(saved["custom_setting"], {"keep": True})
            self.assertEqual(saved["tasks"], original["tasks"])
            self.assertEqual((saved_history, saved_profile), (history, profile))
            self.assertEqual(stamp.isoformat(), "2026-11-22")
            self.assertEqual(columns_before, db.execute("SELECT attname FROM pg_attribute WHERE attrelid = 'planner_state'::regclass AND attnum>0").fetchall())

    def test_existing_data_remains_authoritative_when_both_have_values(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, data jsonb, payload jsonb)")
            state = new_state()
            state["tasks"] = {"1": "Рабочая задача"}
            untouched = {"other": "untouched"}
            db.execute("INSERT INTO planner_state VALUES (1, %s, %s)", (Jsonb(state), Jsonb(untouched)))
            self.assertEqual(state_column(db), "data")
            actual, _, _ = load_state(db)
            actual["pending"] = ["task", "2026-11-22"]
            save_state(db, actual)
            saved, payload = db.execute("SELECT data, payload FROM planner_state").fetchone()
            self.assertEqual(saved["tasks"], state["tasks"])
            self.assertEqual(payload, untouched)

    def test_null_payload_never_replaces_working_data(self):
        for payload in (None, Jsonb(None)):
            with self.subTest(payload=payload):
                with self.connect() as db:
                    db.execute("DROP TABLE IF EXISTS planner_state")
                    db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, data jsonb, payload jsonb)")
                    original = new_state()
                    original["tasks"] = {"1": "Задача"}
                    db.execute("INSERT INTO planner_state VALUES (1, %s, %s)", (Jsonb(original), payload))
                    ensure_state(db, new_state())
                    actual, _, _ = load_state(db)
                    self.assertEqual(actual, original)
                    actual["affirmation"] = "Текст"
                    save_state(db, actual)
                    self.assertIsNone(db.execute("SELECT payload FROM planner_state").fetchone()[0])
                    self.assertEqual(load_state(db)[0]["tasks"], original["tasks"])

    def test_empty_data_is_rejected_instead_of_switching_to_payload(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, data jsonb, payload jsonb)")
            db.execute("INSERT INTO planner_state VALUES (1, NULL, %s)", (Jsonb(new_state()),))
        with self.assertRaises(StateStorageError):
            with self.connect() as db:
                ensure_state(db, new_state())
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT data FROM planner_state").fetchone()[0])

    def test_null_payload_only_row_is_not_reinitialized(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, payload jsonb)")
            db.execute("INSERT INTO planner_state VALUES (1, NULL)")
        with self.assertRaises(StateStorageError):
            with self.connect() as db:
                ensure_state(db, new_state())
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT payload FROM planner_state").fetchone()[0])

    def test_incomplete_payload_is_rejected_without_repair_or_overwrite(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, payload jsonb)")
            db.execute("INSERT INTO planner_state VALUES (1, %s)", (Jsonb({"unexpected_wrapper": {"tasks": []}}),))
        with self.assertRaises(StateStorageError):
            with self.connect() as db:
                ensure_state(db, new_state())
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT payload FROM planner_state").fetchone()[0],
                             {"unexpected_wrapper": {"tasks": []}})

    def test_json_column_and_empty_installation_with_explicit_columns(self):
        with self.connect() as db:
            db.execute("""CREATE TABLE planner_state (
                id integer PRIMARY KEY, version integer NOT NULL DEFAULT 8,
                payload json NOT NULL, affirmations_history jsonb)""")
            ensure_state(db, new_state())
            state, _, history = load_state(db)
            self.assertEqual(state, new_state())
            self.assertIsNone(history)
            state["tasks"] = {"1": "Задача"}
            save_state(db, state)
            self.assertEqual(db.execute("SELECT version FROM planner_state").fetchone()[0], 8)
            self.assertEqual(load_state(db)[0]["tasks"], {"1": "Задача"})

    def test_invalid_data_type_does_not_switch_to_payload(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, data text, payload jsonb)")
            db.execute("INSERT INTO planner_state VALUES (1, 'unexpected', %s)", (Jsonb(new_state()),))
        with self.assertRaises(StateStorageError):
            with self.connect() as db:
                load_state(db)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT data FROM planner_state").fetchone()[0], "unexpected")

    def test_extra_non_json_payload_does_not_block_existing_data(self):
        with self.connect() as db:
            db.execute("CREATE TABLE planner_state (id integer PRIMARY KEY, data jsonb, payload text)")
            db.execute("INSERT INTO planner_state VALUES (1, %s, 'untouched')", (Jsonb(new_state()),))
            actual, _, _ = load_state(db)
            self.assertEqual(actual, new_state())
            save_state(db, actual)
            self.assertEqual(db.execute("SELECT payload FROM planner_state").fetchone()[0], "untouched")
