"""Remove one installed extra version after review and typed confirmation.

Usage: dotfleet tools uninstall TOOL --version VERSION.
Help: python3 scripts/tool_actions.py --help.
"""

import argparse
from pathlib import Path
import sys
import tempfile

import run_index

from common import atomic, read_toml, run
from command_display import current_command
from render_run import render
from run_account import RunAccount


def recorded_versions(value):
    values = value if isinstance(value, list) else [value]
    return {str(item.get("version", "") if isinstance(item, dict) else item) for item in values}


def installed_labels(installed, recorded):
    versions = recorded_versions(recorded)
    return (
        ", ".join(
            item["version"] + (" (recorded)" if item["version"] in versions else " (extra)")
            for item in installed
        )
        or "none"
    )


def uninstall(home, saved, tool, version):
    import command
    import enrollment
    import manager
    import ui_channel

    if not manager.exact_version(version) or tool.startswith("-") or not tool:
        raise ValueError("Choose a tool and an exact version, such as node --version 26.3.1.")
    display = current_command(home)
    if version in recorded_versions(saved.get("pins", {}).get(tool)):
        raise ValueError(
            f"{tool} {version} is recorded in ~/.config/fleet/profile.toml; "
            f"run {display} tools remove {tool}, or {display} tools set {tool} --version VERSION first."
        )
    installed = enrollment.installed(home).get(tool, [])
    item = next((item for item in installed if item["version"] == version), None)
    if item is None:
        raise ValueError(f"{tool} {version} is not installed; run {display} tools list.")
    run_index.stage("review")
    account = RunAccount(home, command.ROOT)
    account.checkouts = []
    account.software = [
        {
            "name": tool,
            "version": version,
            "action": "removed",
            "path": item.get(
                "install_path", str(home / ".local/share/mise/installs" / tool / version)
            ),
        }
    ]
    paths = sorted(command.managed_paths(home))
    account.manifest = manager.backup_inventory(home, paths)
    folder = manager.backup_location(home)
    account.locations.update(
        {
            "Backup folder": str(folder),
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get("sourceDir"),
        }
    )
    note = (
        "Software is not copied into this backup. Reinstall the version with tools set if needed."
    )
    with tempfile.TemporaryDirectory(prefix="fleet-uninstall-tool-") as temporary:
        account.diff_path = Path(temporary) / "diff"
        atomic(
            account.diff_path,
            run("chezmoi", "--no-pager", "--use-builtin-diff", "--color=false", "diff"),
        )
        account.warnings = [note]
        if not ui_channel.active():
            print(render(account.record(), future=True), end="")
        if not command.confirm(
            "uninstall", f"Uninstall {tool} {version}. {note}", account.record()
        ):
            return account.finish("CANCELLED")
        if (
            command.profile(home) != saved
            or manager.backup_inventory(home, paths) != account.manifest
        ):
            raise ValueError(
                "Saved choices or files changed during review. Nothing uninstalled; run again."
            )
        run_index.stage("backup")
        manager.backup(home, paths, folder)
        account.bind_backup(folder)
        try:
            run("mise", "uninstall", tool + "@" + version, "--yes", "--cd", str(home))
            if any(item["version"] == version for item in enrollment.installed(home).get(tool, [])):
                raise ValueError(
                    "Mise still lists the selected version. Inspect tools list before retrying."
                )
            account.applied_software.add(tool)
            drift = command.status(home)
            return account.finish("SUCCESS", [note], "differences remain" if drift else "clean")
        except (OSError, ValueError, RuntimeError) as error:
            if not any(
                item["version"] == version for item in enrollment.installed(home).get(tool, [])
            ):
                account.applied_software.add(tool)
            return account.finish("FAILED", [note, str(error)])


def usage():
    from style import ArgumentParser

    ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
