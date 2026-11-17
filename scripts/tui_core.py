"""Expose existing command cores to the terminal interface. No Textual dependency.

Usage: python3 scripts/tui_core.py ACTION [BACKUP] [--machine NAME].
Actions: dashboard, status, check, update, diff, backups, rollback, machines, doctor.
The private channel carries screen data and confirmations. stdout carries human output.
"""

import argparse
import datetime
from pathlib import Path
import socket
import sys

import command
import registry
import remote
import style
import ui_channel
import updates
from run_account import commit

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = (
    "dashboard",
    "status",
    "check",
    "update",
    "diff",
    "backups",
    "rollback",
    "machines",
    "doctor",
)


def dashboard(home):
    profile = command.profile(home)
    rows = dict(command.status_rows(home, profile))
    backup = rows["Last backup"]
    if backup != "none":
        try:
            backup = datetime.datetime.strptime(Path(backup).name[:15], "%Y%m%d-%H%M%S").isoformat(
                " "
            )
        except ValueError:
            pass
    return [
        ("Machine", socket.gethostname()),
        ("Domain", rows["Domain"]),
        ("Content", profile["content_repo"]),
        ("Commit", commit(home / "dotfiles") or "not recorded"),
        ("Fleet", (ROOT / "VERSION").read_text().strip()),
        ("Updates", updates.summary(home, profile).removeprefix("Updates: ")),
        ("Last backup", backup),
        ("Drift", rows["Drift"]),
    ]


def dispatch(action, backup=None, machine=None):
    home = Path.home()
    if machine:
        return command.main([action, machine])
    if action == "dashboard":
        ui_channel.send({"kind": "dashboard", "rows": dashboard(home)})
        return 0
    if action == "backups":
        folders = command.records(home)
        ui_channel.send({"kind": "backups", "items": [str(path) for path in reversed(folders)]})
    if action == "machines":
        ui_channel.send(
            {
                "kind": "connections",
                "items": [
                    name
                    for name, record in sorted(registry.load(home)["machines"].items())
                    if record.get("ssh_target")
                ],
            }
        )
    if action == "rollback" and not backup:
        raise ValueError("Select a backup before restoring.")
    return command.main([action, *([backup] if backup else [])])


def main(argv=None):
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=ACTIONS, help="existing core action to run")
    parser.add_argument(
        "backup", nargs="?", help="saved folder for rollback; no other action accepts it"
    )
    parser.add_argument(
        "--machine", help="saved remote machine; only status, check, diff, doctor, update"
    )
    args = parser.parse_args(argv)
    if args.backup and args.action != "rollback":
        parser.error("only rollback accepts a backup")
    if args.machine and (args.action not in remote.CAPABLE or args.backup):
        parser.error("remote actions are status, check, diff, doctor, and update")
    if not ui_channel.active():
        parser.error("this internal bridge requires the terminal interface channel")
    return dispatch(args.action, args.backup, args.machine)


if __name__ == "__main__":
    sys.exit(style.run_cli(main, ROOT))
