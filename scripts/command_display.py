"""Choose the command shown in instructions. Keep executable names unchanged.

Usage: import command_name or current_command from this module.
Help: python3 scripts/command_display.py --help.
"""

from pathlib import Path
import shutil

from common import read_toml


def command_name(profile):
    """Match the helpers alias guard without mistaking a shell alias for a binary."""
    helpers = profile.get("groups", {}).get("helpers", False)
    return "fleet" if helpers and shutil.which("fleet") is None else "dotfleet"


def current_command(home=None):
    home = Path.home() if home is None else home
    try:
        profile = read_toml(home / ".config/fleet/profile.toml")
    except (OSError, ValueError):
        # Help must remain available when the profile cannot be read.
        profile = {}
    return command_name(profile)


def usage():
    import argparse
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
