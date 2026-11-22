"""Read-only export of affirmation history, without credentials in the file."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def checksum(history):
    data = json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def verify(path):
    with Path(path).open(encoding="utf-8") as stream:
        backup = json.load(stream)
    if backup.get("format_version") != 1 or backup.get("row_id") != 1:
        raise ValueError("Unexpected backup format or owner row.")
    if backup.get("sha256") != checksum(backup.get("history")):
        raise ValueError("Backup checksum mismatch.")
    return backup


def export(connection, path):
    # A separate read-only transaction supplies a consistent snapshot.
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = connection.execute(
            "SELECT affirmations_history FROM public.planner_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise ValueError("Owner row id=1 was not found.")
        history = row[0]
    backup = {
        "format_version": 1,
        "source": "public.planner_state.affirmations_history",
        "row_id": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "history": history,
        "sha256": checksum(history),
    }
    # Exclusive creation preserves any older backup. Restrict access to its owner.
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(backup, stream, ensure_ascii=False, indent=2)
        verified = verify(path)
        if verified["history"] != history:
            raise ValueError("Backup verification failed.")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output")
    group.add_argument("--verify")
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify)
        else:
            # DATABASE_URL must be supplied through the environment, never a command argument.
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=8) as db:
                export(db, args.output)
    except Exception as error:
        # Connection errors may contain credential-bearing addresses.
        raise SystemExit(f"Backup operation failed ({type(error).__name__}).") from None
    print("Affirmation backup verified.")
