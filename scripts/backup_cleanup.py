"""Review and delete selected backup folders. Never follow links outside a backup.

Usage: dotfleet backups delete --name BACKUP | --keep N | --all.
The terminal interface calls delete_selected with the same planner and confirmation.
Deletion records stay outside the backup store, so --all leaves that store empty.
Help: python3 scripts/backup_cleanup.py --help.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import run_index

from common import atomic, read_toml
from command_display import current_command
from render_run import render

STORE = ".fleet/cleanup-runs"
WARNING = "Deleted backups cannot be rolled back to."


def inventory(folder):
    """Record links themselves; directory links never add their target contents."""
    entries = []
    for root, directories, files in os.walk(folder, followlinks=False):
        for name in [*directories, *files]:
            path = Path(root) / name
            details = path.lstat()
            entries.append(
                (
                    str(path.relative_to(folder)),
                    details.st_mode,
                    details.st_size,
                    details.st_mtime_ns,
                    os.readlink(path) if path.is_symlink() else "",
                )
            )
    return sorted(entries)


def details(folder):
    import stat

    entries = inventory(folder)
    size = sum(size for _, mode, size, _, _ in entries if not stat.S_ISDIR(mode))
    try:
        date = datetime.datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S").isoformat(" ")
    except ValueError:
        date = "date not recorded"
    return {"path": str(folder), "date": date, "size": size, "inventory": entries}


def candidates(home):
    import command

    root = home / ".fleet/backups"
    if root.is_symlink() or not root.resolve().is_relative_to(home.resolve()):
        raise ValueError("Keep the backup store inside HOME, without a directory link.")
    folders = command.records(home)
    if any(folder.is_symlink() for folder in folders):
        raise ValueError("A backup folder is a link. No backups were deleted.")
    return folders


def select(home, name=None, keep=None, all_backups=False):
    if sum((name is not None, keep is not None, all_backups)) != 1:
        raise ValueError("Choose exactly one selector: --name BACKUP, --keep N, or --all.")
    folders = candidates(home)
    if name is not None:
        if name not in {folder.name for folder in folders}:
            raise ValueError(
                "No backup named " + name + ". Run " + current_command(home) + " backups list."
            )
        return [name]
    if keep is not None:
        if keep < 0:
            raise ValueError("--keep needs zero or a positive count.")
        return [folder.name for folder in folders[: max(0, len(folders) - keep)]]
    return [folder.name for folder in folders]


def planned_record(home, names):
    folders = {folder.name: folder for folder in candidates(home)}
    if len(names) != len(set(names)) or any(name not in folders for name in names):
        raise ValueError("Select existing backup names, with no duplicates. Nothing deleted.")
    items = [details(folders[name]) for name in names]
    run = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-")
        + uuid.uuid4().hex[:12]
    )
    path = home / STORE / run / "RUN.json"
    if not path.resolve().is_relative_to(home.resolve()) or (home / STORE).is_symlink():
        raise ValueError("Keep cleanup records inside the manager store.")
    return {
        "home": str(home),
        "record_path": str(path),
        "backup_cleanup": items,
        "files": [{"path": item["path"], "action": "removed"} for item in items],
        "completed_files": [],
        "result": "PLANNED",
        "warnings": [WARNING],
        "locations": {
            "Content checkout": str(home / "dotfiles"),
            "Profile file": str(home / ".fleet/profile.toml"),
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get("sourceDir"),
        },
        "instruction_commands": dict.fromkeys(("before", "after"), current_command(home)),
        "chezmoi_status_after": "unchanged",
    }


def save(record):
    path = Path(record["record_path"])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic(path, json.dumps(record, indent=2) + "\n")
    run_index.attach(record)
    run_index.prune_diagnostics(Path(record["home"]), STORE)


def delete_selected(home, names):
    return run_index.run(home, "backups delete", lambda: delete_confirmed(home, names))


def delete_confirmed(home, names):
    import command
    import ui_channel

    if not names:
        run_index.outcome("NO_CHANGE")
        print("No backups to delete.")
        return 0
    run_index.stage("review")
    record = planned_record(home, names)
    if not ui_channel.active():
        print(render(record, future=True), end="")
    if not command.confirm("delete", "Delete the listed backups permanently. " + WARNING, record):
        run_index.outcome("CANCELLED")
        print("Cancelled. No backups deleted.")
        return 0
    # A backup must remain exactly as reviewed while the owner considers deletion.
    for item in record["backup_cleanup"]:
        folder = Path(item["path"])
        if folder.is_symlink() or not folder.is_dir() or inventory(folder) != item["inventory"]:
            raise ValueError(
                "A selected backup changed after review. Nothing deleted; review again."
            )
    save(record)
    run_index.stage("delete")
    try:
        for item in record["backup_cleanup"]:
            shutil.rmtree(item["path"])
            record["completed_files"].append(item["path"])
            save(record)
        record["result"] = "SUCCESS"
    except OSError as error:
        record["result"] = "FAILED"
        record["warnings"].append("Could not finish deleting backups: " + str(error))
    try:
        import registry

        remaining = candidates(home)
        registry.refresh(
            home,
            (command.ROOT / "VERSION").read_text().strip(),
            remaining[-1] if remaining else None,
            record["result"],
            run_record=Path(record["record_path"]),
        )
    except (OSError, ValueError, RuntimeError) as error:
        record["result"] = "FAILED"
        record["warnings"].append("Could not refresh the machine record: " + str(error))
    save(record)
    run_index.outcome(
        record["result"], record["warnings"][-1] if record["result"] == "FAILED" else ""
    )
    print(render(record), end="")
    return int(record["result"] != "SUCCESS")


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
