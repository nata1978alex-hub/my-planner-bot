"""Create test-only schema variants matching the supported state layouts."""
import os


def configure_state_table(db, app):
    layout = os.environ.get("PLANNER_TEST_STATE_COLUMN", "data")
    if layout not in {"data", "payload", "data-with-null-payload"}:
        raise ValueError("Unexpected test state column")
    column = "payload" if layout == "payload" else "data"
    db.execute(app.SCHEMA)
    if layout == "data-with-null-payload":
        db.execute("ALTER TABLE planner_state ADD payload jsonb")
    if column == "payload":
        db.execute("ALTER TABLE planner_state RENAME COLUMN data TO payload")
    db.execute("ALTER TABLE planner_state ADD bazi_profile jsonb, ADD affirmations_history jsonb")
    db.execute("ALTER TABLE planner_state ADD version integer NOT NULL DEFAULT 7")
    return column
