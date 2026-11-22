"""Export the previous bot version with a compatible affirmation selector."""
import argparse
import io
import subprocess
import tarfile
from pathlib import Path

PREVIOUS_COMMIT = "d1a4860814cb1492adfda0174bfffef6a856b141"
OLD_CHECK = '        if not isinstance(entry, dict):\n            continue\n'
COMPATIBLE_CHECK = (
    '        if not isinstance(entry, dict) or entry.get("status", "active") != "active":\n'
    '            continue\n'
)



def compatible_app(source):
    replacements = [
        ('from scheduler_auth import is_scheduler',
         'from scheduler_auth import is_scheduler\nfrom state_storage import ensure_state, load_state, save_state'),
        ('        db.execute("INSERT INTO planner_state VALUES (1, %s) ON CONFLICT DO NOTHING",\n'
         '                   (Jsonb(new_state()),))',
         '        ensure_state(db, new_state())'),
        ('            state, profile, history = db.execute("""\n'
         "                SELECT data, to_jsonb(p)->'bazi_profile', to_jsonb(p)->'affirmations_history'\n"
         '                FROM planner_state AS p WHERE id = 1 FOR UPDATE\n'
         '            """).fetchone()',
         '            state, profile, history = load_state(db)'),
        ('            state = db.execute("SELECT data FROM planner_state WHERE id = 1 FOR UPDATE").fetchone()[0]',
         '            state = load_state(db)[0]'),
        ('        db.execute("UPDATE planner_state SET data = %s WHERE id = 1", (Jsonb(state),))',
         '        save_state(db, state)'),
    ]
    for original, replacement in replacements:
        if source.count(original) != 1:
            raise ValueError("Unexpected previous app. No rollback files were created.")
        source = source.replace(original, replacement)
    compile(source, "app.py", "exec")
    return source


def prepare(destination, repository):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Destination must be a new directory.")
    # Read only. git archive does not switch branches or modify the worktree.
    archive = subprocess.run(
        ["git", "archive", "--format=tar", PREVIOUS_COMMIT],
        cwd=repository, check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        original = files.extractfile("planner.py").read().decode()
        start = original.index("def morning_affirmation(")
        end = original.index("\ndef morning_plan(", start)
        function = original[start:end]
        if function.count(OLD_CHECK) != 1:
            raise ValueError("Unexpected previous selector. No rollback files were created.")
        compatible = original[:start] + function.replace(OLD_CHECK, COMPATIBLE_CHECK) + original[end:]
        compile(compatible, "planner.py", "exec")
        application = compatible_app(files.extractfile("app.py").read().decode())
        storage = (Path(__file__).resolve().parents[1] / "state_storage.py").read_text()
        compile(storage, "state_storage.py", "exec")
        destination.mkdir(parents=True)
        files.extractall(destination, filter="data")
    (destination / "planner.py").write_text(compatible)
    (destination / "app.py").write_text(application)
    (destination / "state_storage.py").write_text(storage)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository", default=".")
    args = parser.parse_args()
    prepare(args.output, args.repository)
    print("Previous version exported with archive-aware affirmation selection.")
