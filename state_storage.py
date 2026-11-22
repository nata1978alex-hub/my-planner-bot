"""Read and write the existing state column without changing database schema."""
from psycopg import sql
from psycopg.types.json import Jsonb


class StateStorageError(RuntimeError):
    pass


def state_column(db):
    columns = dict(db.execute("""
        SELECT attname, atttypid::regtype::text
        FROM pg_attribute
        WHERE attrelid = to_regclass('planner_state')
          AND attnum > 0 AND NOT attisdropped
          AND attname IN ('payload', 'data')
    """).fetchall())
    # Preserve the current bot's data column. payload is only used in payload-only schemas.
    name = 'data' if 'data' in columns else 'payload'
    if columns.get(name) not in {'json', 'jsonb'}:
        raise StateStorageError('Expected a JSON/JSONB state column named payload or data.')
    return name


def validate_state(state):
    required = {'tasks', 'reminders', 'days', 'journal', 'sent'}
    if not isinstance(state, dict) or not all(isinstance(state.get(key), dict) for key in required):
        raise StateStorageError('Stored state has an unsupported shape. No state was written.')
    if not isinstance(state.get('next_id'), int) or not isinstance(state.get('affirmation'), str):
        raise StateStorageError('Stored state is incomplete. No state was written.')


def load_state(db):
    column = state_column(db)
    row = db.execute(sql.SQL("""
        SELECT {}, to_jsonb(p)->'bazi_profile', to_jsonb(p)->'affirmations_history'
        FROM planner_state AS p WHERE id = 1 FOR UPDATE
    """).format(sql.Identifier(column))).fetchone()
    if row is None:
        raise StateStorageError('Owner row id=1 was not found. No state was written.')
    validate_state(row[0])
    return row


def save_state(db, state):
    validate_state(state)
    column = state_column(db)
    db.execute(sql.SQL('UPDATE planner_state SET {} = %s WHERE id = 1').format(
        sql.Identifier(column)), (Jsonb(state),))


def ensure_state(db, initial):
    column = state_column(db)
    if db.execute('SELECT id FROM planner_state WHERE id = 1').fetchone() is not None:
        load_state(db)
        return
    validate_state(initial)
    # Explicit columns avoid placing JSON in version or another auxiliary column.
    db.execute(sql.SQL('INSERT INTO planner_state (id, {}) VALUES (1, %s)').format(
        sql.Identifier(column)), (Jsonb(initial),))
