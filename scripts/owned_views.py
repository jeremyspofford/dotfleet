"""Read manager-owned stores as human rows. Never execute a secrets file.

Usage: use dotfleet profile, backups, secrets, content, or machines export.
"""

import os
from pathlib import Path
import re
import shlex
import socket

from common import atomic, read_toml, run
import registry
from command_display import command_name, current_command


def profile_rows(home, profile):
    display = command_name(profile)
    rows = [
        ("Profile file", str(home / ".config/fleet/profile.toml")),
        ("Domain", profile.get("domain", "core")),
        (
            "Domain choice",
            "chosen" if profile.get("domain_chosen", False) else f"UNCHOSEN; run {display} domain",
        ),
        ("Git name", profile.get("git_name") or f"missing; run {display} configure"),
        ("Git email", profile.get("git_email") or f"missing; run {display} configure"),
    ]
    for field, label in [("pins", "Pin"), ("groups", "Group"), ("agents", "Agent")]:
        rows += [
            (label + " " + name, value) for name, value in sorted(profile.get(field, {}).items())
        ] or [(field.capitalize(), "none")]
    rows.append(
        (
            "Edit choices",
            f"Use {display} domain, pins, groups, or configure. Do not edit profile.toml.",
        )
    )
    return rows


def backup_rows(home, folders):
    rows = [("Backups", str(home / "fleet-backups"))]
    for folder in reversed(folders):
        import json

        record_path = folder / "RUN.json"
        record = json.loads(record_path.read_text()) if record_path.is_file() else {}
        size = sum(
            path.lstat().st_size
            for path in folder.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        date = folder.name[:15]
        if re.fullmatch(r"\d{8}-\d{6}", date):
            date = f"{date[:4]}-{date[4:6]}-{date[6:8]} {date[9:11]}:{date[11:13]}:{date[13:15]}"
        rows.append(
            (folder.name, f"{date} · {record.get('result') or 'not recorded'} · {size:,} bytes")
        )
    if not folders:
        rows.append(("Saved backups", f"none; run {current_command(home)} backup"))
    return rows


def secret_rows(home, manifest):
    names = sorted(manifest.get("secrets", {}))
    present = {name: bool(os.environ.get(name)) for name in names}
    path = home / ".secrets"
    if path.is_file():
        # shlex reads literal shell quoting. No shell expansion or command execution occurs.
        try:
            words = shlex.split(path.read_text(), comments=True)
        except ValueError:
            raise ValueError(
                "Cannot read secret assignments. No secret values were printed."
            ) from None
        for word in words:
            name, separator, value = word.partition("=")
            if separator and name in present:
                present[name] = bool(value)
    return [(name, "set" if present[name] else "unset") for name in names] or [
        ("Declared variables", "none")
    ]


def content_rows(home, profile, checkout_state):
    checkout = home / "dotfiles"
    return [
        ("Checkout", str(checkout)),
        ("Repository", profile["content_repo"]),
        ("Commit and behind", checkout_state(checkout)),
        ("Git status", run("git", "-C", str(checkout), "status", "--short").strip() or "clean"),
    ]


def export_rows(home, version, output=None):
    name = socket.gethostname()
    data = registry.load(home)
    record = data["machines"].get(name) or registry.machine(home, version)
    rows = [("Machine", name), ("Scope", "This machine’s saved record only.")]
    labels = {
        "os": "OS",
        "content_repo": "Content repo",
        "content_commit": "Content commit",
        "manager_version": "Manager version",
        "last_run_record": "Run record",
    }
    rows += [
        (labels.get(key, key.replace("_", " ").capitalize()), value)
        for key, value in record.items()
        if key != "hostname"
    ]
    if output:
        destination = Path(output).expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError("Export destination already exists. Choose a new file.")
        atomic(destination, registry.serialize({"schema_version": 1, "machines": {name: record}}))
        rows.append(("Export file", str(destination)))
    else:
        rows.append(
            (
                "Carry this record",
                f"Use {current_command(home)} machines export --output FILE to save a private TOML copy.",
            )
        )
    return rows


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
