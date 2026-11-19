"""Move Fleet's own state into ~/.fleet before enrollment or command work.

Usage: python3 scripts/store_migration.py --record FILE
FILE receives private move records for the run review. Nothing is copied.
A name already present in ~/.fleet stops migration before anything moves.
A failed move puts back what already moved and reports one line.
A cancelled run moves everything back, using the same FILE.
"""

# Exit codes: 0 success/help; 1 migration failure; 2 invalid arguments.

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from common import atomic, read_toml, toml
import style

ROOT = ".fleet"
# Folders whose entries move into ~/.fleet, newest name first. Each folder is removed once empty.
MERGED = (
    ".local/share/fleet",
    ".local/share/dotfleet",
    ".config/fleet",
    ".config/dotfleet",
    ".local/state/fleet",
    ".local/state/dotfleet",
)
# Entries renamed on the way in, so every name inside ~/.fleet is plain.
RENAMED = {"fleet.toml": "registry.toml", "tui-venv": "tui"}
# Folders that move whole to a named place inside ~/.fleet.
WHOLE = (
    ("fleet-backups", ROOT + "/backups"),
    ("dotfleet-backups", ROOT + "/backups"),
    (".cache/fleet-deploy", ROOT + "/deploy"),
)
# Inventories keyed by home-relative paths. Their keys follow the moved files.
INVENTORIES = (ROOT + "/agents.json", ROOT + "/outputs.json")
# State records describe writes; reviews and previews never list them as rendered settings.
STATE_RECORDS = (*INVENTORIES, ROOT + "/last-run.json")
OLD_STORES = tuple(MERGED) + tuple(old for old, _ in WHOLE)


def relative_path(name):
    """Translate a home-relative name from any older layout into the ~/.fleet layout."""
    for old in MERGED:
        if name == old:
            return ROOT
        if name.startswith(old + "/"):
            first, _, rest = name[len(old) + 1 :].partition("/")
            first = RENAMED.get(first, first)
            return ROOT + "/" + first + ("/" + rest if rest else "")
    for old, new in WHOLE:
        if name == old or name.startswith(old + "/"):
            return new + name[len(old) :]
    return name


def relocated(path, home):
    path, home = Path(path), Path(home)
    if path.is_relative_to(home):
        return home / relative_path(path.relative_to(home).as_posix())
    return path


def config_changes(home):
    path = home / ".config/chezmoi/chezmoi.toml"
    if not path.exists():
        return None
    if path.is_symlink() or not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError(
            "The chezmoi config is a symlink. Keep the manager-owned config inside HOME."
        )
    config = read_toml(path)
    changed = False
    # Only chezmoi's path settings move. Saved template data and profile values stay intact.
    for key in ("sourceDir", "cacheDir", "persistentState", "workingTree", "scriptTempDir"):
        value = config.get(key)
        if isinstance(value, str) and Path(value).is_absolute():
            target = str(relocated(value, home))
            if target != value:
                config[key] = target
                changed = True
    return toml(config) if changed else None


def repair_config(home):
    if content := config_changes(home):
        atomic(home / ".config/chezmoi/chezmoi.toml", content)


def require_real(home, name):
    path = home / name
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"Store migration needs real folders: ~/{name} is not one.")
    return path


def plan(home):
    """Return the moves, the folders they empty, and stale deploy staging to set aside."""
    root = require_real(home, ROOT)
    moves, emptied, stale = [], [], []
    for name in MERGED:
        folder = require_real(home, name)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            moves.append((entry, root / RENAMED.get(entry.name, entry.name)))
        emptied.append(folder)
    for old, new in WHOLE:
        source = require_real(home, old)
        if not source.is_dir():
            continue
        if old == ".cache/fleet-deploy" and (home / new).exists():
            # Deploy staging is scratch from an earlier deploy; a new deploy has staged again.
            stale.append(source)
            continue
        moves.append((source, home / new))
    targets = [destination for _, destination in moves]
    for source, destination in moves:
        if destination.exists() or destination.is_symlink() or targets.count(destination) > 1:
            raise ValueError(
                f"Store migration stopped: ~/{source.relative_to(home)} and"
                f" ~/{destination.relative_to(home)} conflict. Nothing was moved."
            )
    return moves, emptied, stale


def links(home):
    """Yield links inside ~/.fleet whose targets name an older store, with their new targets."""
    root = home / ROOT
    skipped = {root / "backups", root / "manager", root / "mise"}
    for parent, folders, files in os.walk(root, followlinks=False):
        folders[:] = [name for name in folders if Path(parent, name) not in skipped]
        for name in folders + files:
            path = Path(parent, name)
            if path.is_symlink():
                target = os.readlink(path)
                moved = str(relocated(target, home)) if os.path.isabs(target) else target
                if moved != target:
                    yield path, target, moved


def rewritten_files(home):
    """Return (path, new text) for small files that name an older store."""
    root = home / ROOT
    result = []
    config = config_changes(home)
    if config is not None:
        result.append((home / ".config/chezmoi/chezmoi.toml", config))
    settings = root / "tui/pyvenv.cfg"
    if settings.is_file():
        text = settings.read_text()
        moved = text
        for old in OLD_STORES:
            moved = moved.replace(str(home / old) + "/", str(relocated(home / old, home)) + "/")
        if moved != text:
            result.append((settings, moved))
    for name in INVENTORIES:
        path = home / name
        if not path.is_file() or path.is_symlink():
            continue
        data = json.loads(path.read_text())
        table = data.get("paths") if isinstance(data.get("paths"), dict) else data
        if isinstance(table, dict) and any(relative_path(key) != key for key in table):
            moved = {relative_path(key): value for key, value in table.items()}
            if table is data:
                data = moved
            else:
                data["paths"] = moved
            result.append((path, json.dumps(data, indent=2, sort_keys=True) + "\n"))
    return result


def undo(journal):
    """Reverse journal steps, newest first. Return the lines that could not be undone."""
    problems = []
    for step in reversed(journal):
        kind = step["kind"]
        try:
            if kind == "rename":
                source, destination = Path(step["old"]), Path(step["new"])
                source.parent.mkdir(parents=True, exist_ok=True)
                # Set-aside staging may sit on another file system; move copies when it must.
                shutil.move(str(destination), str(source))
            elif kind == "rmdir":
                Path(step["path"]).mkdir(mode=0o700, exist_ok=True)
            elif kind == "mkdir":
                folder = Path(step["path"])
                # A cancelled run may have created lock files here; they hold no data.
                for lock in (".fleet.lock", ".contacts.lock"):
                    lock_path = folder / lock
                    if lock_path.is_file() and not lock_path.stat().st_size:
                        lock_path.unlink()
                folder.rmdir()
            elif kind == "relink":
                path = Path(step["path"])
                path.unlink()
                path.symlink_to(step["old_target"])
            elif kind == "write":
                atomic(step["path"], step["old_text"])
        except OSError as error:
            problems.append(f"{step.get('path') or step.get('new')}: {error.strerror or error}")
    return problems


def migrate(home, stash):
    """Move every older store into ~/.fleet. Return display records and the undo journal."""
    moves, emptied, stale = plan(home)
    if not moves and not stale:
        return [], []
    root = home / ROOT
    journal = []
    try:
        if not root.exists():
            root.mkdir(mode=0o700)
            journal.append({"kind": "mkdir", "path": str(root)})
        for folder in stale:
            kept = Path(stash) / folder.name
            shutil.move(str(folder), str(kept))
            journal.append({"kind": "rename", "old": str(folder), "new": str(kept)})
        for source, destination in moves:
            source.rename(destination)
            journal.append({"kind": "rename", "old": str(source), "new": str(destination)})
        for folder in emptied:
            folder.rmdir()
            journal.append({"kind": "rmdir", "path": str(folder)})
        for path, old_target, new_target in list(links(home)):
            path.unlink()
            path.symlink_to(new_target)
            journal.append(
                {"kind": "relink", "path": str(path), "old_target": old_target, "new": new_target}
            )
        for path, text in rewritten_files(home):
            journal.append({"kind": "write", "path": str(path), "old_text": path.read_text()})
            atomic(path, text)
    except (OSError, ValueError) as error:
        undo(journal)
        raise ValueError(
            f"Store migration stopped and put everything back: {error}. Old stores stay in place."
        ) from error
    records = [{"old": str(folder), "new": str(root)} for folder in emptied]
    records += [
        {"old": str(source), "new": str(destination)}
        for source, destination in moves
        if source.parent != destination.parent and source.parent not in emptied
    ]
    return records, journal


def recorded_state():
    """Return this run's moves and journal. Older record files hold only display moves."""
    path = os.environ.get("DOTFLEET_STORE_MOVES_FILE")
    value = json.loads(Path(path).read_text()) if path and Path(path).is_file() else []
    if isinstance(value, list):
        return {"moves": value, "journal": []}
    return {"moves": value.get("moves", []), "journal": value.get("journal", [])}


def recorded_moves():
    return recorded_state()["moves"]


def restore(home):
    """Undo this run's store moves. A cancelled run leaves the home as it found it."""
    state = recorded_state()
    problems = undo(state["journal"])
    if problems:
        raise ValueError("Store move back stopped: " + "; ".join(problems))
    # A second cancel in the same run must find nothing left to undo.
    if path := os.environ.get("DOTFLEET_STORE_MOVES_FILE"):
        atomic(path, json.dumps({"moves": [], "journal": []}) + "\n")
    return state["moves"] if state["journal"] else []


def main():
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--record", required=True, type=Path, help="private file for this run's move records"
    )
    args = parser.parse_args()
    state = recorded_state()
    stash = args.record.parent / "stale-stores"
    stash.mkdir(mode=0o700, exist_ok=True)
    moved, journal = migrate(Path.home(), stash)
    state["moves"].extend(item for item in moved if item not in state["moves"])
    state["journal"].extend(journal)
    atomic(args.record, json.dumps(state) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(style.run_cli(main, Path(__file__).resolve().parents[1]))
