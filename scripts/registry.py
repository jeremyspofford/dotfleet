"""Keep recorded machine facts in the manager store. Never query other machines.

Internal module. Use dotfleet machines --help for the command interface.

The installer refreshes this machine after a backed-up run. Read-only commands
read the file without refreshing timestamps. Profile identity and secrets stay out.

Usage: import this module from Fleet.
Help: python3 scripts/registry.py --help.
Direct execution without help keeps its existing behavior.
"""

import datetime
import fcntl
import os
from pathlib import Path
import platform
import socket

from common import atomic, read_toml, run, value
from store_migration import relocated

REGISTRY = ".local/share/fleet/registry.toml"
FIELDS = {
    "hostname": str,
    "home": str,
    "os": str,
    "domain": str,
    "domain_chosen": bool,
    "content_repo": str,
    "content_commit": str,
    "manager_version": str,
    "last_run": str,
    "last_backup": str,
    "last_run_record": str,
    "result": str,
    "enrolled": bool,
}


def load(home):
    path = home / REGISTRY
    if path.is_symlink():
        raise ValueError(
            "Fleet registry is a symlink. Keep registry.toml inside the manager store."
        )
    data = read_toml(path) if path.exists() else {"schema_version": 1, "machines": {}}
    if (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("machines"), dict)
    ):
        raise ValueError("Fleet registry needs schema_version = 1 and a machines table.")
    for key, record in data["machines"].items():
        if not isinstance(key, str) or not key or not isinstance(record, dict):
            raise ValueError("Each fleet record needs a nonempty machine key and a table.")
        if record.get("hostname") != key:
            raise ValueError("Fleet record keys must match their hostname fields.")
        for name, kind in FIELDS.items():
            if name not in record or type(record[name]) is not kind:
                raise ValueError(f"Fleet record {key}: {name} must be {kind.__name__}.")
        for name in ("home", "last_backup", "last_run_record"):
            if record[name] and not Path(record[name]).is_absolute():
                raise ValueError(f"Fleet record {key}: {name} must be an absolute path.")
        if key == socket.gethostname() and record["home"] == str(home):
            for name in ("last_backup", "last_run_record"):
                if record[name]:
                    record[name] = str(relocated(record[name], home))
    return data


def os_name():
    system = platform.system()
    if system == "Linux" and (
        os.environ.get("WSL_DISTRO_NAME")
        or Path("/proc/version").exists()
        and "microsoft" in Path("/proc/version").read_text().lower()
    ):
        return "WSL"
    return "macOS" if system == "Darwin" else system


def machine(home, manager_version, fallback=None):
    profile_path = home / ".config/fleet/profile.toml"
    profile = read_toml(profile_path) if profile_path.exists() else fallback or {}
    checkout = home / "dotfiles"
    commit = (
        run("git", "-C", str(checkout), "rev-parse", "HEAD").strip()
        if (checkout / ".git").exists()
        else ""
    )
    return {
        "hostname": socket.gethostname(),
        "home": str(home),
        "os": os_name(),
        "domain": profile.get("domain", ""),
        "domain_chosen": profile.get("domain_chosen", False),
        "content_repo": profile.get("content_repo", ""),
        "content_commit": commit,
        "manager_version": manager_version,
        "last_run": "",
        "last_backup": "",
        "last_run_record": "",
        "result": "not recorded",
        "enrolled": profile_path.exists(),
    }


def serialize(data):
    lines = [
        "# Recorded machine facts. This file is not a live network view.",
        "schema_version = 1",
        "",
    ]
    for key, record in sorted(data["machines"].items()):
        lines.append("[machines." + value(key) + "]")
        lines += [value(name) + " = " + value(item) for name, item in record.items()]
        lines.append("")
    return "\n".join(lines)


def refresh(home, manager_version, backup, result, fallback=None):
    """Replace this hostname's record under a lock; preserve all other records."""
    path = home / REGISTRY
    if not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError("Fleet registry parent leaves HOME. Keep the manager store inside HOME.")
    current = machine(home, manager_version, fallback)
    current.update(
        last_run=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        last_backup=str(backup),
        last_run_record=str(backup / "RUN.json"),
        result=result,
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # No saved credentials or persistent network service are involved.
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(home)
        data["machines"][current["hostname"]] = current
        atomic(path, serialize(data))
    return current


def rows(home, manager_version):
    """Render stored records; add a local unrecorded view for older enrollments."""
    from render_run import human
    from command_display import current_command

    data = load(home)
    local = socket.gethostname()
    if local not in data["machines"]:
        data["machines"][local] = machine(home, manager_version)
    output = [
        ("Scope", "Recorded data only; no live queries to other machines."),
        ("Registry", str(home / REGISTRY)),
    ]
    for key, record in sorted(
        data["machines"].items(), key=lambda item: (item[0] != local, item[0])
    ):
        output += [
            ("Machine", key + (" · this machine" if key == local else " · recorded only")),
            ("Hostname", record["hostname"]),
            ("OS", record["os"]),
            (
                "Domain",
                (record["domain"] or "not set")
                + (" · chosen" if record["domain_chosen"] else " · UNCHOSEN"),
            ),
            ("Content repo", record["content_repo"] or "not set"),
            ("Content commit", record["content_commit"] or "not recorded"),
            ("Manager version", record["manager_version"]),
            ("Last run", record["last_run"] or f"not recorded; run {current_command(home)} update"),
            ("Last result", record["result"]),
            (
                "Last backup",
                human(record["last_backup"], record["home"]) if record["last_backup"] else "none",
            ),
            (
                "Run record",
                human(record["last_run_record"], record["home"])
                if record["last_run_record"]
                else "none",
            ),
            ("Enrolled", record["enrolled"]),
        ]
    return output


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
