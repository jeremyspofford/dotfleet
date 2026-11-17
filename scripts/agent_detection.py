"""Find coding agents from commands, installed Mise packages, or configuration folders.

Usage: import this module from Fleet.
Help: python3 scripts/agent_detection.py --help.
Direct execution without help keeps its existing behavior.
"""

import json
from pathlib import Path
import shutil
import subprocess


def inventory(home):
    if not shutil.which("mise"):
        return {}
    result = subprocess.run(
        ["mise", "ls", "--installed", "--json", "--cd", str(home)], text=True, capture_output=True
    )
    return json.loads(result.stdout) if result.returncode == 0 else {}


def detected(home, manifest, tools=None):
    tools = inventory(home) if tools is None else tools
    installed = {name.removeprefix("npm:") for name, versions in tools.items() if versions}
    result = {}
    for name, spec in manifest.items():
        packages = set(spec.get("mise_packages", [spec["command"]]))
        folders = {str(Path(path).parent) for path in spec.get("config_paths", {}).values() if path}
        folders.update(spec.get("config_folders", []))
        if (
            shutil.which(spec["command"])
            or packages & installed
            or any((home / path).is_dir() for path in folders)
        ):
            result[name] = spec
    return result


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
