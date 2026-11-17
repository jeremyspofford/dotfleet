"""Operate an enrolled machine through the shared manager and run renderer.

Usage: dotfleet COMMAND [ARGS]. Run dotfleet --help for commands and exit codes.
Parameters and examples are defined by each subcommand's --help.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
"""

import argparse
from convert import content_manifest
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from common import LoggerStream, atomic, read_toml, relative, run, toml
import manager
import enrollment
import registry
import owned_views
import style
import updates
import store_migration
import ui_channel
from command_display import command_name, current_command
from installation import COMMAND, MANAGER
from render_run import render, render_view
from run_account import RunAccount, commit, profile_changes

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ".config/fleet/profile.toml"
CONTROLS = {
    registry.REGISTRY,
    updates.CACHE,
    PROFILE,
    ".config/chezmoi/chezmoi.toml",
    ".local/state/dotfleet/outputs.json",
    ".local/state/dotfleet/agents.json",
    ".local/state/dotfleet/last-run.json",
    COMMAND,
}
DESCRIPTIONS = {
    "tui": (
        "Open the terminal interface, a full-screen view of local Fleet commands. "
        "Arrows and Tab move; Enter selects; Esc goes back; q quits. "
        "Configure returns to the existing wizard. If its environment is missing, "
        "print plain command alternatives and exit 0."
    ),
    "check": (
        "Fetch the manager and content upstreams quietly, with a 10-second limit per fetch. "
        "An upstream is the remote branch tracked by a checkout. Cache both results only "
        "when both checks succeed. Exit codes: 0 current; 1 updates available; 2 could not check."
    ),
    "profile": (
        "Show saved domain, identity, pins, groups, and agents from profile.toml. An agent "
        "is a coding assistant. Change choices through their commands or configure."
    ),
    "backups": (
        "List backup folders with their dates, recorded results, and sizes. A backup is a "
        "saved copy of files. Change nothing."
    ),
    "secrets": (
        "Show secret variable names declared by the content manifest, the repository’s "
        "settings file. Set means a nonempty value exists. Never print values."
    ),
    "content": (
        "Show the content checkout, a local Git copy. Report its repository, commit, behind"
        " state, and short Git status. Change nothing."
    ),
    "machines": (
        "Show recorded machine facts from registry.toml. The registry is the manager’s file of"
        " saved machine records. This command makes no live network queries. Export shows "
        "this machine; remove deletes a stale record."
    ),
    "addons": (
        "List installed add-ons, optional manager extensions. A manifest declares an "
        "extension’s actions. A receipt records its owned outputs. No add-ons ship with "
        "Fleet."
    ),
    "update": (
        "Pull the manager and content repositories, then apply your saved choices. A "
        "repository stores versioned files."
    ),
    "status": (
        "Show this machine’s profile, saved choices, installed pins, groups, checkout "
        "state, drift, recent backups, and the last update check. "
        "Drift means managed files differ. No fetch runs unless --check is given."
    ),
    "diff": (
        "Show pending file paths and unified patches from chezmoi. A patch shows removed "
        "(-) and added (+) lines. Change nothing. Exit codes: 0 no differences; 1 "
        "differences; 2 preview failed."
    ),
    "rollback": (
        "List backups or restore one after confirmation. A backup contains original files "
        "and a manifest listing them. The profile is restored too."
    ),
    "domain": (
        "Show domains, or apply a named domain. A domain selects content additions. A "
        "missing domain_chosen field means UNCHOSEN; a named choice confirms it."
    ),
    "groups": (
        "List optional content groups. Turn a group on to include it, or off to remove its "
        "managed files or alias blocks."
    ),
    "pins": (
        "List exact tool versions and installed state. Set a pin to install that exact "
        "version. Remove a pin without uninstalling software."
    ),
    "configure": (
        "Reopen the enrollment wizard. The wizard reviews source, domain, identity, pins, "
        "groups, and agents before applying choices."
    ),
    "doctor": (
        "Check profile completeness, drift, clean checkouts, and backups. A clean checkout "
        "has no local edits. Each failed check names its fix."
    ),
    "backup": (
        "Copy managed files and the profile into a new private backup. Change no managed "
        "files. Print the backup location and run record."
    ),
    "version": (
        "Show the manager version and content commit. A commit identifies a saved Git version."
    ),
    "uninstall": (
        "Remove the manager and its managed files after typed confirmation. Keep content "
        "and backups. Offer to restore the oldest backup."
    ),
}


class HelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "Usage: ")

    def _fill_text(self, text, width, indent):
        return "\n".join(
            argparse.HelpFormatter._fill_text(self, line, width, indent)
            for line in text.splitlines()
        )


TERMS = (
    "Profile: saved machine choices. Pins: exact tool versions. Groups: optional content.\n"
    "Domain: content additions. Drift: differences in managed files.\n"
    "Commit: saved Git version. Checkout: a local Git copy."
)


def parser():
    display = current_command()
    argument_parser = style.ArgumentParser(
        prog=display,
        description="Manage this machine after enrollment. A profile saves your choices.",
        epilog="Exit codes: 0 success, help, or cancellation; 1 failed operation or check; 2 invalid arguments or check failure; 130 interrupted. Check uses 1 for available updates.\n"
        f"Examples: {display} update; {display} status; {display} domain personal.\n"
        "First install or manager recovery: use the README one-line installer.\n" + TERMS,
        formatter_class=HelpFormatter,
    )
    argument_parser.add_argument(
        "--version", action="version", version=(ROOT / "VERSION").read_text().strip()
    )
    subcommands = argument_parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name, detail in DESCRIPTIONS.items():
        command_parser = subcommands.add_parser(
            name,
            prog=display + " " + name,
            help=detail.split(". ")[0] + ".",
            description=detail,
            formatter_class=HelpFormatter,
            epilog=f"Example: {display} "
            + name
            + "\n"
            + TERMS
            + f"\nRun {display} --help for exit codes. All prompts require Enter and use the terminal.",
        )
        if name == "machines":
            command_parser.add_argument(
                "action",
                nargs="?",
                choices=["export", "remove"],
                help="export shows this machine; remove deletes a stale hostname",
            )
            command_parser.add_argument(
                "hostname", nargs="?", help="the exact stale hostname to remove"
            )
            command_parser.add_argument(
                "--output",
                metavar="FILE",
                help=(
                    "with export, save one private TOML record for transfer; the terminal "
                    "stays human-readable"
                ),
            )
        elif name == "status":
            command_parser.add_argument(
                "--check",
                action="store_true",
                help="fetch and cache update results first; exit 0 current, 1 updates, or 2 could not check",
            )
        elif name == "diff":
            command_parser.add_argument(
                "--stat",
                action="store_true",
                help="print changed paths only, without patch bodies; use the same exit codes",
            )
        elif name == "rollback":
            command_parser.add_argument(
                "backup",
                nargs="?",
                help="backup folder name or path; omit to list and choose in a terminal",
            )
        elif name == "domain":
            command_parser.add_argument(
                "name", nargs="?", help="domain name from the listed content choices"
            )
        elif name == "groups":
            command_parser.add_argument(
                "name",
                nargs="?",
                help="group name from the content manifest, the repository’s settings file",
            )
            command_parser.add_argument(
                "state",
                nargs="?",
                choices=["on", "off"],
                help="on includes content; off removes selected content",
            )
        elif name == "pins":
            command_parser.add_argument(
                "action",
                nargs="?",
                choices=["set", "remove"],
                help="set installs and records; remove unpins and reapplies",
            )
            command_parser.add_argument(
                "pin",
                nargs="?",
                help="TOOL@VERSION for set; TOOL for remove. Example: node@24.14.1",
            )
    return argument_parser


def profile(home):
    result = read_toml(home / PROFILE)
    if not result:
        raise ValueError("No profile found. Run the README one-line installer first.")
    return result


def records(home):
    return sorted(
        (path for path in (home / "fleet-backups").glob("*") if (path / "MANIFEST.json").is_file()),
        key=lambda path: (path.name[:15], int(path.name[16:] or "0")),
    )


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def status(home):
    output = run("chezmoi", "status")
    return output.strip()


def info(title, pairs, home):
    print(render_view(title, pairs, str(home)), end="")


def checkout_state(path):
    sha = commit(path)
    if not sha:
        return f"missing; run {current_command()} update"
    dirty = bool(run("git", "-C", str(path), "status", "--porcelain").strip())
    ref = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "@{upstream}"],
        text=True,
        capture_output=True,
    )
    behind = "upstream unknown"
    if ref.returncode == 0:
        counts = run(
            "git",
            "-C",
            str(path),
            "rev-list",
            "--left-right",
            "--count",
            "HEAD..." + ref.stdout.strip(),
        ).split()
        behind = f"{counts[1]} behind last fetched upstream"
    return f"{sha[:8]} · {behind} · " + ("local edits" if dirty else "clean")


def pin_rows(saved_profile, home):
    installed = enrollment.installed(home)
    return [
        (name, f"{format_pin(request)} · installed: {enrollment.versions(installed.get(name, []))}")
        for name, request in saved_profile.get("pins", {}).items()
    ]


def format_pin(value):
    from render_run import human

    return human(value)


def domain_choices(home):
    descriptions = content_manifest(home / "dotfiles").get("domains", {})
    return {
        name: descriptions.get(
            name,
            "Base files only."
            if name == "core"
            else "Content additions; inspect the overlay file.",
        )
        for name in ["core", "personal", "work", "client"]
    }


def command_profile_gaps(saved_profile):
    """Keep the manager's validation, with fixes users can run through this command."""
    display = command_name(saved_profile)
    gaps = []
    for gap in manager.profile_gaps(saved_profile):
        if gap.startswith("Domain "):
            gap = gap.replace(
                "Supply --domain.",
                f"Run {display} domain to list choices, then {display} domain NAME.",
            )
        elif gap.startswith(("Git name ", "Git email ")):
            gap = gap.split(". Supply ", 1)[0] + f". Run {display} configure."
        elif " pin is floating " in gap:
            tool = gap.split(" pin is floating ", 1)[0]
            gap = (
                gap.split(". Supply ", 1)[0]
                + f". Run {display} pins set {tool}@VERSION with a full version."
            )
        gaps.append(gap)
    return gaps


def read_view(args, home, saved_profile):
    display = command_name(saved_profile)
    name = args.command
    manifest = content_manifest(home / "dotfiles")
    if name == "profile":
        pairs = owned_views.profile_rows(home, saved_profile)
    elif name == "backups":
        pairs = owned_views.backup_rows(home, records(home))
    elif name == "secrets":
        pairs = owned_views.secret_rows(home, manifest)
    elif name == "content":
        pairs = owned_views.content_rows(home, saved_profile, checkout_state)
    elif name == "machines":
        pairs = registry.rows(home, (ROOT / "VERSION").read_text().strip())
    elif name == "addons":
        from addons import installed_addons

        items = installed_addons(home, (ROOT / "VERSION").read_text().strip())
        pairs = [(item["id"], item["version"] + " · " + item["description"]) for item in items]
        if not pairs:
            pairs = [("Installed add-ons", "none; no add-ons ship with Fleet.")]
    elif name == "version":
        pairs = [
            ("Manager", (ROOT / "VERSION").read_text().strip()),
            ("Content commit", commit(home / "dotfiles")),
        ]
    elif name == "domain":
        pairs = [
            ("Current", saved_profile.get("domain", "core")),
            ("Choice", "chosen" if saved_profile.get("domain_chosen", False) else "UNCHOSEN"),
        ]
        pairs += list(domain_choices(home).items())
        pairs.append(("Choose", f"{display} domain NAME · replace NAME with a listed choice"))
    elif name == "groups":
        pairs = [
            (
                group,
                ("on" if saved_profile.get("groups", {}).get(group, True) else "off")
                + " · "
                + spec["description"],
            )
            for group, spec in manifest.get("groups", {}).items()
        ]
    elif name == "pins":
        pairs = pin_rows(saved_profile, home)
    elif name == "doctor":
        gaps = command_profile_gaps(saved_profile)
        drift = status(home)
        pairs = [("Profile", "PASS" if not gaps else "FAIL · resolve the gaps below")]
        pairs.append(
            (
                "Domain choice",
                "PASS · chosen"
                if saved_profile.get("domain_chosen", False)
                else f"FAIL · UNCHOSEN; run {display} domain",
            )
        )
        pairs += [("Gap", gap) for gap in gaps]
        pairs += [
            (
                "Managed files",
                f"FAIL · run {display} diff to inspect drift" if drift else "PASS · no drift",
            )
        ]
        for label, path in [
            ("Manager checkout", home / MANAGER),
            ("Content checkout", home / "dotfiles"),
        ]:
            clean = (path / ".git").exists() and not run(
                "git", "-C", str(path), "status", "--porcelain"
            ).strip()
            pairs.append(
                (
                    label,
                    "PASS · clean"
                    if clean
                    else f"FAIL · save local edits, then run {display} update",
                )
            )
        pairs.append(("Backup", "PASS" if records(home) else f"FAIL · run {display} backup"))
        config_path = home / ".config/chezmoi/chezmoi.toml"
        source = read_toml(config_path).get("sourceDir", "")
        pairs.append(("Manager config", str(config_path)))
        pairs.append(
            (
                "Converted source",
                "PASS · " + source
                if source and Path(source).is_dir()
                else f"FAIL · run {display} update",
            )
        )
        info("Doctor", pairs, home)
        return int(any("FAIL" in value for _, value in pairs))
    else:
        pairs = status_rows(home, saved_profile)
    info(name.capitalize(), pairs or [("Selections", "none")], home)
    if name == "status":
        print(updates.summary(home, saved_profile))
    return 0


def status_rows(home, saved_profile):
    display = command_name(saved_profile)
    backups = records(home)
    last = next((path for path in reversed(backups) if (path / "RUN.json").exists()), None)
    record = load_json(last / "RUN.json", {}) if last else {}
    pairs = [
        (
            "Domain",
            saved_profile.get("domain", "core")
            + (
                " · chosen"
                if saved_profile.get("domain_chosen", False)
                else f" · UNCHOSEN; run {display} domain"
            ),
        ),
        (
            "Identity",
            (saved_profile.get("git_name", "") + " <" + saved_profile.get("git_email", "") + ">")
            if saved_profile.get("git_name", "").strip()
            and saved_profile.get("git_email", "").strip()
            else f"missing; run {display} configure",
        ),
    ]
    pairs += [("Pin " + key, value) for key, value in pin_rows(saved_profile, home)] or [
        ("Pins", "none")
    ]
    pairs += [("Group " + key, value) for key, value in saved_profile.get("groups", {}).items()]
    pairs += [
        ("Content", checkout_state(home / "dotfiles")),
        ("Drift", f"present; run {display} diff" if status(home) else "none"),
        (
            "Last run",
            str(last) + " · " + str(record.get("result", "unknown")) if last else "none",
        ),
        ("Last backup", str(backups[-1]) if backups else "none"),
        ("Manager config", str(home / ".config/chezmoi/chezmoi.toml")),
        (
            "Converted source",
            read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", f"missing; run {display} update"
            ),
        ),
    ]
    return pairs


def prepare_manager(home, workspace):
    return updates.stage(home / MANAGER, workspace / "manager")


def update(home):
    with tempfile.TemporaryDirectory(prefix="dotfleet-update-") as folder:
        staged = prepare_manager(home, Path(folder))
        environment = dict(os.environ)
        environment.pop("DOTFLEET_LOCAL_CONTENT", None)
        arguments = ["bash", str(staged / "install.sh")]
        if ui_channel.active():
            # The TUI hands profile changes to Configure, which restores the real terminal.
            arguments.append("--defaults")
        return subprocess.call(arguments, env=environment, pass_fds=ui_channel.descriptors())


def preview(home, saved_profile, stat=False):
    # Fetch only into scratch. Tool queries and chezmoi state also stay there.
    with tempfile.TemporaryDirectory(prefix="dotfleet-diff-") as folder:
        workspace = Path(folder)
        staged = prepare_manager(home, workspace)
        # Use the updated manager's code, without applying it or installing tools.
        return subprocess.call(
            [
                sys.executable,
                str(staged / "scripts/command.py"),
                "_preview",
                *(["--stat"] if stat else []),
            ],
            env=dict(os.environ, DOTFLEET_PREVIEW="1"),
        )


def preview_current(home, saved_profile, stat=False):
    with tempfile.TemporaryDirectory(prefix="dotfleet-plan-") as folder:
        workspace = Path(folder)
        original = Path(os.environ.get("MISE_DATA_DIR", home / ".local/share/mise"))
        os.environ.setdefault("MISE_INSTALLS_DIR", str(original / "installs"))
        os.environ.setdefault("MISE_PLUGINS_DIR", str(original / "plugins"))
        for key in ("DATA", "CACHE", "STATE"):
            os.environ["MISE_" + key + "_DIR"] = str(workspace / key.lower())
        checkout = enrollment.stage_repo(home, saved_profile["content_repo"], workspace)
        _, manifest = manager.detect(checkout, saved_profile.get("format"))
        # Agent planning messages are progress, not differences.
        with contextlib.redirect_stdout(io.StringIO()):
            plan = manager.prepare(home, checkout, saved_profile, manifest, workspace / "plan")
        from preview_diff import patches

        changes = patches(home, saved_profile, plan, workspace / "plan", ROOT)
        if not changes:
            print(
                f"No differences. Your files match {saved_profile['content_repo']} @ {commit(checkout)[:8]}."
            )
            return 0
        if isinstance(sys.stdout, LoggerStream):
            sys.stdout.diff = True
        for name, patch in changes:
            print("~/" + name)
            if not stat:
                print(patch, end="" if patch.endswith("\n") else "\n")
        return 1


def managed_paths(home):
    paths = set(load_json(home / ".local/state/dotfleet/outputs.json", {})) | CONTROLS
    paths |= set(load_json(home / ".local/state/dotfleet/agents.json", {}))
    if (home / ".config/chezmoi/chezmoi.toml").exists():
        paths |= set(
            filter(
                None,
                run(
                    "chezmoi",
                    "managed",
                    "--include",
                    "files,symlinks",
                    "--path-style",
                    "relative",
                    "-0",
                ).split("\0"),
            )
        )
    return paths


def confirm(word, explanation, record=None):
    if ui_channel.active():
        return ui_channel.review(record, word, explanation)
    if not manager.has_terminal():
        raise ValueError(
            f"This action needs a terminal. Type {word} there after reviewing the changes."
        )
    return (
        manager.ask(
            "Confirm",
            "cancel",
            [word, "cancel"],
            detail=explanation + f" Type {word} to proceed or cancel to stop. Press Enter.",
        )
        == word
    )


def validate_backup(home, selected):
    folder = Path(selected).expanduser()
    if not folder.is_absolute():
        folder = home / "fleet-backups" / folder
    folder = folder.resolve()
    if folder.parent != (home / "fleet-backups").resolve():
        raise ValueError("Select a backup directly inside ~/fleet-backups.")
    entries = load_json(folder / "MANIFEST.json", None)
    if not isinstance(entries, list):
        raise ValueError("Backup MANIFEST.json is missing or invalid.")
    operations = []
    for item in entries:
        saved_name = str(relative(item["path"]))
        name = store_migration.relative_path(saved_name)
        if name == registry.REGISTRY:
            continue  # Other machines' records must not rewind during local restore.
        destination = home / name
        if not destination.parent.resolve().is_relative_to(home.resolve()):
            raise ValueError("Restore parent leaves home: " + name)
        if item["state"] not in ("saved", "absent"):
            raise ValueError("Invalid backup state: " + name)
        source = folder / "home" / saved_name if item["state"] == "saved" else None
        if source:
            if not source.parent.resolve().is_relative_to((folder / "home").resolve()):
                raise ValueError("Backup parent leaves backup: " + name)
            if manager.fingerprint(source) != item.get("fingerprint") or not (
                source.is_file() or source.is_symlink()
            ):
                raise ValueError("Backup file failed its recorded fingerprint: " + name)
        operations.append((name, source))
        if item.get("external"):
            target = Path(item["external"])
            external = folder / "external" / str(target).lstrip("/")
            if (
                not target.is_absolute()
                or ".." in target.parts
                or not external.is_file()
                or external.is_symlink()
            ):
                raise ValueError("Invalid external backup target: " + str(target))
            if not external.resolve().is_relative_to((folder / "external").resolve()):
                raise ValueError("External backup leaves its storage folder.")
            # Restore only the original linked destination, never an arbitrary path in edited metadata.
            if (
                not source
                or not source.is_symlink()
                or (destination.parent / os.readlink(source)).resolve() != target
            ):
                raise ValueError("External target does not match the saved symlink.")
            if manager.fingerprint(target) != manager.fingerprint(external):
                # The normal backup inventory follows current symlinks. Without this
                # link, it cannot capture the external file before replacement.
                if not destination.is_symlink() or destination.resolve() != target:
                    raise ValueError(
                        (
                            "External target changed and its original link is missing. "
                            "Preserve it before restoring: "
                        )
                        + str(target)
                    )
                operations.append((str(target), external))
    return folder, operations


def transaction(home, operations, label, selected=None, require_word=None, tree_removals=()):
    """Record the pre-change inventory, confirm, back up, and execute exact file operations."""
    operations = [(name, source) for name, source in operations if name != registry.REGISTRY]
    backup_paths = sorted(
        {name for name, _ in operations if not Path(name).is_absolute()} | {registry.REGISTRY}
    )
    account = RunAccount(home, ROOT)
    account.checkouts = []
    account.software = []
    folder = manager.backup_location(home)
    account.locations.update(
        {
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", "not configured"
            ),
            "Backup folder": str(folder),
        }
    )
    account.manifest = manager.backup_inventory(home, backup_paths)
    account.files = []
    for name, source in operations:
        path = home / name
        before = manager.fingerprint(path)
        after = manager.fingerprint(source) if source else None
        if before != after:
            account.files.append(
                {
                    "path": str(path),
                    "relative": name,
                    "before": before,
                    "action": "removed"
                    if after is None
                    else "added"
                    if before is None
                    else "changed",
                }
            )
    for path in tree_removals:
        if path.exists():
            account.files.append(
                {
                    "path": str(path),
                    "relative": str(path.relative_to(home)),
                    "before": None,
                    "action": "removed",
                }
            )
    new_profile = next(
        (read_toml(source) if source else {} for name, source in operations if name == PROFILE),
        account.old_profile,
    )
    account.profile = profile_changes(account.old_profile, new_profile)
    account.selected_profile = new_profile
    account.instruction_commands["after"] = command_name(new_profile)
    with tempfile.TemporaryDirectory(prefix="dotfleet-restore-") as temporary:
        account.diff_path = Path(temporary) / "diff"
        diff = (
            run("chezmoi", "--no-pager", "--use-builtin-diff", "--color=false", "diff")
            if (home / ".config/chezmoi/chezmoi.toml").exists()
            else ""
        )
        atomic(account.diff_path, diff)
        account.status = status(home) if (home / ".config/chezmoi/chezmoi.toml").exists() else ""
        for name, _ in operations:
            target = home / name
            if target.is_dir() and not target.is_symlink():
                raise ValueError("Refusing to replace a directory: " + str(target))
        if require_word:
            print(render(account.record(), future=True), end="")
            if not confirm(require_word, label, account.record()):
                account.finish("CANCELLED")
                return 0
        if manager.backup_inventory(home, backup_paths) != account.manifest:
            raise ValueError("Files changed during review. Run the command again.")
        manager.backup(home, backup_paths, folder)
        account.bind_backup(folder)
        source = home / ".local/share/fleet/chezmoi-source"
        if source.is_dir():
            shutil.copytree(source, folder / "previous-chezmoi-source", symlinks=True)
        try:
            # External target backups follow the original symlink inventory above.
            for name, saved in operations:
                target = home / name
                if target.exists() and target.is_dir() and not target.is_symlink():
                    raise ValueError("Refusing to replace a directory with a file: " + str(target))
                if saved is None:
                    target.unlink(missing_ok=True)
                elif saved.is_symlink():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.unlink(missing_ok=True)
                    target.symlink_to(os.readlink(saved))
                else:
                    atomic(target, saved.read_bytes(), saved.stat().st_mode & 0o777)
            if selected and (selected / "previous-chezmoi-source").is_dir():
                if source.exists():
                    shutil.rmtree(source)
                shutil.copytree(selected / "previous-chezmoi-source", source, symlinks=True)
            # Logging must survive removing the running manager checkout.
            if tree_removals:
                log_root = Path(temporary) / "logger"
                (log_root / "scripts").mkdir(parents=True)
                shutil.copy2(ROOT / "scripts/logger.sh", log_root / "scripts/logger.sh")
                shutil.copy2(ROOT / "VERSION", log_root / "VERSION")
                for stream in (sys.stdout, sys.stderr):
                    if isinstance(stream, LoggerStream):
                        stream.root = log_root
            for path in tree_removals:
                if path.exists():
                    path.rename(folder / ("removed-" + path.name))
                    account.applied_files.add(str(path))
            store_migration.repair_config(home)
            account.profile_written = True
            account.confirm_files(manager.fingerprint)
            drift = status(home) if (home / ".config/chezmoi/chezmoi.toml").exists() else ""
            return account.finish(
                "SUCCESS",
                [],
                f"differences remain; run {current_command(home)} diff" if drift else "clean",
            )
        except (OSError, ValueError, RuntimeError) as error:
            account.confirm_files(manager.fingerprint)
            account.finish("FAILED", [str(error)])
            return 1


def restore_command(args, home):
    backups = records(home)
    info(
        "Backups",
        [
            (path.name, load_json(path / "RUN.json", {}).get("result", "snapshot"))
            for path in backups
        ],
        home,
    )
    if not args.backup:
        if not backups or not manager.has_terminal():
            return 0
        chosen = manager.ask(
            "Backup",
            "cancel",
            detail="Enter a listed backup folder to restore, or cancel to stop. Press Enter.",
        )
        if chosen == "cancel":
            return 0
    else:
        chosen = args.backup
    folder, operations = validate_backup(home, chosen)
    return transaction(home, operations, "Restore the listed files and profile.", folder, "restore")


def main(argv=None):
    home = Path.home()
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    if argv in (["_preview"], ["_preview", "--stat"]) and os.environ.get("DOTFLEET_PREVIEW") == "1":
        try:
            return preview_current(home, profile(home), "--stat" in argv)
        except (OSError, ValueError, RuntimeError) as error:
            print("Preview failed: " + str(error), file=sys.stderr)
            return 2
    args = parser().parse_args(argv)
    if args.command == "tui":
        from tui_environment import launch

        return launch(home, ROOT)
    try:
        saved_profile = profile(home)
        display = command_name(saved_profile)
        if args.command == "check":
            return updates.check(home, saved_profile)
        if args.command == "status" and args.check:
            result = updates.check(home, saved_profile, display=False)
            read_view(args, home, saved_profile)
            return result
        if args.command == "machines":
            if args.action == "remove":
                if not args.hostname or args.output:
                    parser().error("machines remove needs a hostname and does not accept --output")
                return remove_machine(home, args.hostname)
            if args.hostname or args.output and args.action != "export":
                parser().error("use machines export --output FILE or machines remove HOSTNAME")
            if args.action == "export":
                info(
                    "Machine export",
                    owned_views.export_rows(
                        home, (ROOT / "VERSION").read_text().strip(), args.output
                    ),
                    home,
                )
                return 0
        if (
            args.command
            in (
                "profile",
                "backups",
                "secrets",
                "content",
                "status",
                "doctor",
                "version",
                "machines",
                "addons",
            )
            or args.command in ("domain", "groups")
            and not args.name
            or args.command == "pins"
            and not args.action
        ):
            return read_view(args, home, saved_profile)
        if args.command == "update":
            return update(home)
        if args.command == "diff":
            return preview(home, saved_profile, args.stat)
        if args.command == "configure":
            if not manager.has_terminal():
                raise ValueError(
                    f"Configure needs a terminal. Run {display} configure in an interactive shell."
                )
            return manager.main(["--configure"])
        if args.command == "rollback":
            return restore_command(args, home)
        if args.command == "backup":
            operations = [
                (
                    name,
                    home / name if (home / name).exists() or (home / name).is_symlink() else None,
                )
                for name in sorted(managed_paths(home))
            ]
            # Snapshot only: transaction has no replacement operations or profile changes.
            return snapshot(home, operations)
        if args.command == "uninstall":
            if not manager.has_terminal():
                raise ValueError("Uninstall needs a terminal and typed confirmation.")
            backups = records(home)
            selected, restored = None, []
            if (
                backups
                and manager.ask(
                    "Oldest backup",
                    "keep",
                    ["restore", "keep"],
                    detail=(
                        "restore: restore the oldest saved files after removal; keep: "
                        "remove managed files only. Content and backups stay. Press Enter."
                    ),
                )
                == "restore"
            ):
                selected, restored = validate_backup(home, str(backups[0]))
            operations = dict.fromkeys(managed_paths(home))
            operations.update(dict(restored))
            return transaction(
                home,
                sorted(operations.items()),
                "Remove the manager and listed managed files. Content and backups stay.",
                selected,
                "uninstall",
                [
                    home / MANAGER,
                    home / ".local/share/fleet/chezmoi-source",
                    home / ".local/share/fleet/tui-venv",
                ],
            )
        os.environ["DOTFLEET_LOCAL_CONTENT"] = "1"
        if args.command == "domain":
            if args.name not in domain_choices(home):
                raise ValueError(f"Unknown domain. Run {display} domain to list choices.")
            return manager.main(["--defaults", "--domain", args.name])
        if args.command == "groups":
            if not args.state:
                parser().error("groups NAME needs on or off")
            return manager.main(["--defaults", "--group", args.name + "=" + args.state])
        if args.command == "pins":
            if not args.pin:
                parser().error("pins needs TOOL@VERSION for set, or TOOL for remove")
            if args.action == "remove":

                def unpin(value):
                    value.setdefault("pins", {}).pop(args.pin, None)
                    value["unpinned_tools"] = sorted(
                        set(value.get("unpinned_tools", [])) | {args.pin}
                    )

                return manager.main(["--defaults"], edit=unpin)
            tool, separator, version = args.pin.rpartition("@")
            if (
                not separator
                or not tool
                or not manager.exact_version(version)
                or tool.startswith("-")
            ):
                parser().error(
                    "pins set needs TOOL@VERSION with an exact version, such as node@24.14.1"
                )
            installed = enrollment.installed(home).get(tool, [])
            present = next((item for item in installed if item.get("version") == version), None)
            location = (
                str(present.get("install_path", "Mise installation"))
                if present
                else str(home / ".local/share/mise/installs" / tool / version)
            )
            software = [
                {
                    "name": tool,
                    "version": version,
                    "old": None,
                    "path": location,
                    "action": "present" if present else "installed",
                }
            ]

            def install(account):
                if not present:
                    run("mise", "install", tool + "@" + version)
                    actual = next(
                        item
                        for item in enrollment.installed(home).get(tool, [])
                        if item.get("version") == version
                    )
                    software[0]["path"] = actual.get("install_path", location)
                    account.applied_software.add(tool)
                    account.save()

            return manager.main(
                ["--defaults", "--pin", tool + "=" + version],
                software=software,
                before_apply=install,
            )
    except (OSError, ValueError, RuntimeError) as error:
        if args.command == "check" or args.command == "status" and args.check:
            print("Warning: Could not check updates: " + str(error), file=sys.stderr)
            return 2
        style.record_failure()
        print("Error: " + str(error), file=sys.stderr)
        return 2 if args.command == "diff" else 1
    return 0


def remove_machine(home, hostname):
    import difflib
    import fcntl
    import socket

    if hostname == socket.gethostname():
        raise ValueError(
            "This machine is active. Only another machine’s stale record can be removed."
        )
    path = home / registry.REGISTRY
    if not path.exists():
        raise ValueError("No registry exists. Nothing was removed.")
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = registry.load(home)
        if hostname not in data["machines"]:
            raise ValueError("No saved machine named " + hostname + ". Nothing was removed.")
        del data["machines"][hostname]
        after = registry.serialize(data)
        account = RunAccount(home, ROOT)
        account.checkouts = []
        account.software = []
        account.manifest = manager.backup_inventory(home, [registry.REGISTRY])
        account.files = [
            {
                "path": str(path),
                "relative": registry.REGISTRY,
                "action": "changed",
                "before": manager.fingerprint(path),
            }
        ]
        folder = manager.backup_location(home)
        account.locations["Backup folder"] = str(folder)
        account.locations["Converted source"] = read_toml(
            home / ".config/chezmoi/chezmoi.toml"
        ).get("sourceDir", "not configured")
        with tempfile.TemporaryDirectory() as temporary:
            account.diff_path = Path(temporary) / "registry.patch"
            atomic(
                account.diff_path,
                "".join(
                    difflib.unified_diff(
                        path.read_text().splitlines(True),
                        after.splitlines(True),
                        fromfile=str(path),
                        tofile=str(path),
                    )
                ),
            )
            manager.backup(home, [registry.REGISTRY], folder)
            account.bind_backup(folder)
            atomic(path, after)
            account.confirm_files(manager.fingerprint)
    return account.finish(
        "SUCCESS",
        [],
        f"differences remain; run {current_command(home)} diff" if status(home) else "clean",
    )


def snapshot(home, operations):
    account = RunAccount(home, ROOT)
    account.checkouts = []
    account.software = []
    account.manifest = manager.backup_inventory(home, [name for name, _ in operations])
    folder = manager.backup_location(home)
    account.locations.update(
        {
            "Backup folder": str(folder),
            "Converted source": read_toml(home / ".config/chezmoi/chezmoi.toml").get(
                "sourceDir", "not configured"
            ),
        }
    )
    with tempfile.TemporaryDirectory() as temporary:
        account.diff_path = Path(temporary) / "diff"
        atomic(
            account.diff_path,
            run("chezmoi", "--no-pager", "--use-builtin-diff", "--color=false", "diff"),
        )
        account.status = status(home)
        manager.backup(home, [name for name, _ in operations], folder)
        account.bind_backup(folder)
        source = home / ".local/share/fleet/chezmoi-source"
        if source.exists():
            shutil.copytree(source, folder / "previous-chezmoi-source", symlinks=True)
        return account.finish(
            "SUCCESS",
            [],
            f"differences remain; run {current_command(home)} diff" if account.status else "clean",
        )


if __name__ == "__main__":
    sys.exit(style.run_cli(lambda: main(sys.argv[1:]), ROOT))
