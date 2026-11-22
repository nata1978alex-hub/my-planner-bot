"""Create test-only schema variants matching the supported state layouts."""
import os


def configure_state_table(db, app):
    column = os.environ.get("PLANNER_TEST_STATE_COLUMN", "data")
    if column not in {"data", "payload"}:
        raise ValueError("Unexpected test state column")
    db.execute(app.SCHEMA)
    if column == "payload":
        db.execute("ALTER TABLE planner_state RENAME COLUMN data TO payload")
    db.execute("ALTER TABLE planner_state ADD bazi_profile jsonb, ADD affirmations_history jsonb")
    db.execute("ALTER TABLE planner_state ADD version integer NOT NULL DEFAULT 7")
    return column
