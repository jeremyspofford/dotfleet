"""Move old manager stores before enrollment or command work.

Usage: python3 scripts/store_migration.py --record FILE
FILE receives private move records for the run review. Nothing is copied.
Existing destination stores stop migration before any folder moves.
Exit codes: 0 success/help; 1 migration failure; 2 invalid arguments.
"""

import argparse
import json
import os
from pathlib import Path
import sys

from common import atomic, read_toml, toml
import style

MOVES = (
    (".config/dotfleet", ".config/fleet"),
    ("dotfleet-backups", "fleet-backups"),
    (".local/share/dotfleet", ".local/share/fleet"),
)
OLD_REGISTRY = ".local/share/fleet/fleet.toml"
REGISTRY = ".local/share/fleet/registry.toml"


def relative_path(name):
    for old, new in MOVES:
        if name == old or name.startswith(old + "/"):
            name = new + name[len(old) :]
            break
    return REGISTRY if name == OLD_REGISTRY else name


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


def migrate(home):
    moves = []
    for old, new in MOVES:
        source, destination = home / old, home / new
        if (
            source.is_symlink()
            or destination.is_symlink()
            or not destination.parent.resolve().is_relative_to(home.resolve())
        ):
            raise ValueError(f"Store migration needs real directories: ~/{old} and ~/{new}.")
        if source.exists():
            if not source.is_dir() or destination.exists():
                raise ValueError(
                    f"Store migration stopped: ~/{old} and ~/{new} conflict. Nothing was moved."
                )
            moves.append((source, destination))
    registry_source = home / OLD_REGISTRY
    if (home / ".local/share/dotfleet").exists():
        registry_source = home / ".local/share/dotfleet/fleet.toml"
    registry_target = home / REGISTRY
    prospective_target = registry_source.with_name("registry.toml")
    if registry_source.is_symlink() or registry_target.is_symlink():
        raise ValueError("Store migration needs a registry file, not a symlink.")
    if registry_source.exists() and not registry_source.is_file():
        raise ValueError("Store migration needs a regular registry file.")
    if registry_source.exists() and (prospective_target.exists() or registry_target.exists()):
        raise ValueError(
            "Both fleet.toml and registry.toml exist in the manager store. Nothing was moved."
        )
    config = config_changes(home)
    records = []
    for source, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        records.append({"old": str(source), "new": str(destination)})
    old_registry = home / OLD_REGISTRY
    if old_registry.exists():
        old_registry.rename(registry_target)
    if config is not None:
        atomic(home / ".config/chezmoi/chezmoi.toml", config)
    return records


def recorded_moves():
    path = os.environ.get("DOTFLEET_STORE_MOVES_FILE")
    return json.loads(Path(path).read_text()) if path and Path(path).is_file() else []


def main():
    parser = style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--record", required=True, type=Path, help="private file for this run's move records"
    )
    args = parser.parse_args()
    records = recorded_moves()
    records.extend(item for item in migrate(Path.home()) if item not in records)
    atomic(args.record, json.dumps(records) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(style.run_cli(main, Path(__file__).resolve().parents[1]))
