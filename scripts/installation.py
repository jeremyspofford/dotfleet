"""Install the versioned command and manager after review and backup.

Usage: import this module from Fleet.
Help: python3 scripts/installation.py --help.
Direct execution without help keeps its existing behavior.
"""

import shutil
from common import atomic, run

MANAGER = ".local/share/fleet/manager"
COMMAND = ".local/bin/dotfleet"


def launcher(root):
    return (root / "scripts/dotfleet.sh").read_bytes()


def command_software(home, root):
    destination = home / COMMAND
    old = None
    version_path = home / MANAGER / "VERSION"
    if version_path.exists():
        old = version_path.read_text().strip()
    version = (root / "VERSION").read_text().strip()
    same = destination.is_file() and destination.read_bytes() == launcher(root) and old == version
    return {
        "name": "dotfleet",
        "version": version,
        "path": str(destination),
        "old": old,
        "action": "present" if same else "upgraded" if old else "installed",
    }


def publish(home, root, backup):
    destination = home / MANAGER
    if root.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = backup / "manager-staging"
        run("git", "clone", "--no-hardlinks", str(root), str(staged))
        from updates import copy_tracking

        copy_tracking(root, staged)
        # Working-tree bytes allow local development tests without installing stale committed code.
        # Production installers run clean checkouts; update refuses local manager edits.
        for name in run("git", "-C", str(root), "ls-files", "-z").split("\0"):
            if name and (root / name).is_file():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / name, target)
        if destination.exists():
            destination.rename(backup / "previous-manager")
        staged.rename(destination)
    atomic(home / COMMAND, launcher(root), 0o700)


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    from style import ArgumentParser

    argument_parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from pathlib import Path
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
