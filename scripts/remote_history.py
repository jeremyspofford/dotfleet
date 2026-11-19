"""Save raw SSH diagnostics in a private run record, never in terminal output.

Usage: save(home, machine, target, action, phase, result) returns the record path.
These failure records stay on the caller. Successful applies use the target's own backup store.
Help: python3 scripts/remote_history.py --help.
"""

import argparse
import datetime
import json
from pathlib import Path
import sys
import uuid

import run_index

from common import atomic

STORE = ".fleet/remote-runs"


def output_text(result, name):
    value = getattr(result, name, "") or ""
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    return text[:65536] + ("\n[diagnostic truncated]" if len(text) > 65536 else "")


def save(home, machine, target, action, phase, result):
    folder = home / STORE
    if folder.is_symlink() or not folder.resolve().is_relative_to(home.resolve()):
        raise ValueError("Remote records must stay inside the manager store.")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.datetime.now(datetime.timezone.utc)
    run = folder / (now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:12])
    run.mkdir(mode=0o700)
    path = run / "RUN.json"
    record = {
        "schema_version": 1,
        "kind": "remote diagnostics",
        "machine": machine,
        "ssh_target": target,
        "action": action,
        "phase": phase,
        "time": now.isoformat(timespec="seconds"),
        "exit_code": getattr(result, "returncode", None),
        "stdout": output_text(result, "stdout"),
        "stderr": output_text(result, "stderr"),
        "error": str(result)[:2000] if isinstance(result, Exception) else "",
    }
    atomic(path, json.dumps(record, indent=2) + "\n")
    run_index.prune_diagnostics(home, STORE)
    return path


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
