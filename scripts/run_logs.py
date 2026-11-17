"""Read bounded run entries and their saved reviews without changing state.

Usage: dotfleet logs list [--limit N]; dotfleet logs show [--name ENTRY] [--json].
Help: python3 scripts/run_logs.py --help.
"""

import argparse
import json
from pathlib import Path
import sys

import run_index
from render_run import render, render_view
from style import section


def listed(home, limit=100):
    if limit < 1:
        raise ValueError("--limit needs a positive count.")
    return run_index.entries(home)[:limit]


def label(entry):
    time = entry["time"].replace("+00:00", "Z")
    remote = " · " + entry["machine"] if entry["remote"] else ""
    text = f"{time} · {entry['command']} · {entry['result']}{remote}"
    return text if len(text) <= 98 else text[:97] + "…"


def render_list(entries):
    return (
        section("Logs")
        + "\n"
        + (
            "\n".join("  " + label(entry) for entry in entries)
            if entries
            else "  No attempts recorded."
        )
        + "\n"
    )


def selected(home, name=None):
    entries = run_index.entries(home)
    if not name:
        return entries[0] if entries else None
    matches = [
        entry
        for entry in entries
        if name
        in (
            entry["id"],
            entry["time"],
            entry["time"].replace("+00:00", "Z"),
            entry.get("backup"),
            Path(entry["backup"]).name if entry.get("backup") else None,
        )
    ]
    if len(matches) != 1:
        raise ValueError("Choose one recorded timestamp or backup name from logs list.")
    return matches[0]


def saved_review(home, entry):
    name = entry.get("record_path")
    if not name:
        return None
    path = Path(name)
    permitted = [home / "fleet-backups", home / ".local/share/fleet/cleanup-runs"]
    if (
        path.name != "RUN.json"
        or path.is_symlink()
        or not any(path.resolve().is_relative_to(root.resolve()) for root in permitted)
    ):
        raise ValueError("This run record is outside Fleet's saved record locations.")
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError("The saved run review is not an object.")
    return record


def records(home, name=None):
    entry = selected(home, name)
    return {"entry": entry, "run": saved_review(home, entry) if entry else None}


def render_detail(payload, home):
    entry = payload["entry"]
    if entry is None:
        return render_list([])
    rows = [("Time", entry["time"]), ("Command", entry["command"])]
    if entry["remote"]:
        rows.append(("Machine", entry["machine"] + " · remote target"))
    rows += [("Result", entry["result"]), ("Stage reached", entry["stage"])]
    if entry["error"]:
        rows.append(("Error", entry["error"]))
    if entry.get("backup"):
        rows.append(("Backup", entry["backup"]))
    if entry["result"] == "RUNNING":
        rows.append(("Completion", "Not recorded. The process may still run or have stopped."))
    if payload["run"]:
        return render_view("Run attempt", rows, str(home)) + "\n" + render(payload["run"])
    rows.append(
        (
            "Review",
            "Saved review was removed with its backup or pruned."
            if entry.get("record_path")
            else "No apply review was saved.",
        )
    )
    return render_view("Run attempt", rows, str(home))


def show_command(home, args):
    try:
        if args.action == "list":
            print(render_list(listed(home, args.limit)), end="")
        else:
            payload = records(home, args.name)
            if args.json:
                stream = getattr(sys.stdout, "stream", sys.stdout)
                stream.write(json.dumps(payload, indent=2) + "\n")
                stream.flush()
            else:
                print(render_detail(payload, home), end="")
        return 0
    except (OSError, ValueError, KeyError) as error:
        print("Error: Could not read run records: " + str(error), file=sys.stderr)
        return 1


def usage():
    from style import ArgumentParser

    ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
