"""Install the optional terminal interface in a private Python environment.

Usage: import software, install, or launch from this module.
Help: python3 scripts/tui_environment.py --help.
"""

from pathlib import Path
import shutil
import subprocess
import hashlib
import os
import sys
import tempfile
import urllib.request
import venv

from command_display import current_command
from common import atomic, run
from render_run import render_view

ENVIRONMENT = ".fleet/tui"
NAME = "Fleet TUI (Textual)"


def required_version(root):
    lines = (root / "requirements-tui.txt").read_text().splitlines()
    return next(line.removeprefix("textual==") for line in lines if line.startswith("textual=="))


def installed_version(home):
    python = home / ENVIRONMENT / "bin/python"
    if not python.is_file():
        return None
    try:
        result = subprocess.run(
            [
                str(python),
                "-B",
                "-I",
                "-c",
                "import textual.app; from importlib.metadata import version; print(version('textual'))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def software(home, root):
    old, version = installed_version(home), required_version(root)
    requirements = (root / "requirements-tui.txt").read_bytes()
    receipt = home / ENVIRONMENT / "fleet-requirements.txt"
    same = old == version and receipt.is_file() and receipt.read_bytes() == requirements
    return {
        "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
        "name": NAME,
        "version": version,
        "path": str(home / ENVIRONMENT),
        "old": old,
        "action": "present" if same else "upgraded" if old else "installed",
    }


def install(home, root, backup, account):
    item = next(item for item in account.software if item["name"] == NAME)
    if item["action"] == "present":
        return
    destination = home / ENVIRONMENT
    previous = backup / "previous-tui-venv"
    if destination.is_symlink():
        raise ValueError("The Fleet TUI environment must be a manager-owned folder, not a link.")
    if destination.exists():
        destination.rename(previous)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=False).create(destination)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PIP_", "PYTHON"))
        }
        environment.update(
            PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV="1", PIP_NO_CACHE_DIR="1"
        )
        # Some system Python builds omit ensurepip. Bootstrap pip only inside this environment.
        with tempfile.TemporaryDirectory(prefix="fleet-pip-") as temporary:
            installer = Path(temporary) / "get-pip.py"
            with urllib.request.urlopen(
                "https://bootstrap.pypa.io/get-pip.py", timeout=30
            ) as response:
                installer.write_bytes(response.read())
            run(
                str(destination / "bin/python"),
                str(installer),
                "--disable-pip-version-check",
                env=environment,
            )
        run(
            str(destination / "bin/python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--requirement",
            str(root / "requirements-tui.txt"),
            env=environment,
        )
        if installed_version(home) != item["version"]:
            raise ValueError("The Fleet TUI environment did not pass its version check.")
        atomic(destination / "fleet-requirements.txt", (root / "requirements-tui.txt").read_bytes())
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        if previous.exists():
            previous.rename(destination)
        raise
    account.applied_software.add(NAME)
    account.save()


def launch(home, root):
    if installed_version(home) is None:
        name = current_command(home)
        print(
            render_view(
                "Fleet terminal interface",
                [
                    ("Environment", "missing; the plain commands still work"),
                    ("Inspect", f"{name} status · {name} diff · {name} doctor"),
                    ("Updates", f"{name} check · {name} update"),
                    ("Backups", f"{name} backups list · {name} backups restore"),
                    ("Machines", f"{name} machines list"),
                    ("Setup", f"{name} configure"),
                    ("Restore interface", f"Run {name} update or the README one-line installer."),
                ],
                str(home),
            ),
            end="",
        )
        return 0
    return subprocess.call(
        [str(home / ENVIRONMENT / "bin/python"), "-B", str(root / "scripts/tui.py")]
    )


def usage():
    import argparse
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
