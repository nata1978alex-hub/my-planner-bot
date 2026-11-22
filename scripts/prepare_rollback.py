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
        destination.mkdir(parents=True)
        files.extractall(destination, filter="data")
    (destination / "planner.py").write_text(compatible)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository", default=".")
    args = parser.parse_args()
    prepare(args.output, args.repository)
    print("Previous version exported with archive-aware affirmation selection.")
