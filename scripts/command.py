"""Operate an enrolled machine through the shared manager and run renderer.

Usage: dotfleet COMMAND [ARGS]. Run dotfleet --help for commands.
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
import remote
import connections
import topology
import command_arguments
import command_views
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
    "addons": (
        "List optional manager extensions.\n"
        "A manifest declares an extension’s actions.\n"
        "A receipt records its owned outputs.\n"
        "No add-ons ship with Fleet."
    ),
    "backups": (
        "Create, list, restore, or delete saved copies.\n"
        "A backup is a saved copy of managed files and the profile.\n"
        "List shows dates, recorded results, sizes, and locations without changing files."
    ),
    "check": (
        "Fetch new update results.\n"
        "Status reads saved results; check fetches new results.\n"
        "Fetch manager and content upstreams quietly, with a 10-second limit per fetch.\n"
        "An upstream is the remote branch tracked by a checkout, a local Git copy.\n"
        "Save both results only when both checks succeed."
    ),
    "configure": (
        "Reopen the enrollment wizard.\n"
        "Review source, domain, identity, tools, groups, and agents before applying choices."
    ),
    "content": (
        "Show the content checkout and Git state.\n"
        "A checkout is a local Git copy.\n"
        "Show its repository, commit, behind state, and short Git status without changing files."
    ),
    "diff": (
        "Show pending file differences.\n"
        "Print paths and unified patches from chezmoi.\n"
        "A patch shows removed (-) and added (+) lines.\n"
        "Change nothing."
    ),
    "doctor": (
        "Check this machine and name any fixes.\n"
        "Check profile completeness, managed file differences, clean checkouts, and backups.\n"
        "A profile stores machine choices; a clean checkout has no local edits."
    ),
    "domain": (
        "Show or choose content additions.\n"
        "A domain selects content additions.\n"
        "A missing domain_chosen field means UNCHOSEN.\n"
        "Setting a named domain confirms the choice."
    ),
    "groups": (
        "Show or switch optional content.\n"
        "A group selects optional files or alias blocks.\n"
        "Turn a group on to include it, or off to remove its managed content."
    ),
    "machines": (
        "Manage saved machine connections.\n"
        "The registry, registry.toml, stores machine records.\n"
        "Listing makes no live network queries.\n"
        "Export shows this machine; remove deletes a stale record.\n"
        "SSH securely connects to another machine.\n"
        "Set-ssh saves a target and tests SSH once.\n"
        "One name supplies both fields; without flags, pick a host in a terminal."
    ),
    "tools": (
        "Show or set exact tool versions.\n"
        "A selection records an exact tool version.\n"
        "Set installs that exact version.\n"
        "Remove clears the selection without uninstalling the tool.\n"
        "Uninstall removes one extra installed version after typed confirmation."
    ),
    "secrets": (
        "List secret names as set or unset.\n"
        "The content manifest, the repository’s settings file, declares secret variable names.\n"
        "Set means a nonempty value exists.\n"
        "Never print values."
    ),
    "status": (
        "Show saved state without network requests.\n"
        "Status reads saved update results; check fetches new results.\n"
        "Show the profile, selected tools, groups, checkouts, drift, backups, and last update check.\n"
        "A profile stores machine choices; drift means managed files differ."
    ),
    "topology": (
        "Show this machine’s connection tree.\n"
        "Topology means connection layout.\n"
        "Show each recorded observation with its time.\n"
        "Check nothing live; another machine may have a different view."
    ),
    "tui": (
        "Open the full-screen terminal interface.\n"
        "Arrows and Tab move; Enter selects; Esc goes back; q quits.\n"
        "Configure returns to the existing wizard.\n"
        "If the interface environment is missing, print plain command alternatives and exit 0."
    ),
    "uninstall": (
        "Remove Fleet and its managed files.\n"
        "Type uninstall to confirm; press Enter after each answer.\n"
        "Keep content and backups.\n"
        "Offer to restore the oldest backup."
    ),
    "update": (
        "Check updates and drift before applying.\n"
        "Compare manager and content with their upstreams, the remote branches they track.\n"
        "Drift means managed files differ.\n"
        "Current sources without drift stop without a backup, run record, or review.\n"
        "A failed check stops without applying files."
    ),
    "version": ("Show manager and content versions.\nA commit identifies a saved Git version."),
}


class HelpFormatter(argparse.RawTextHelpFormatter):
    def add_argument(self, action):
        super().add_argument(action)
        # Older Python releases omit nested indentation when sizing command columns.
        if action.help is not argparse.SUPPRESS:
            for nested in self._iter_indented_subactions(action):
                width = len(self._format_action_invocation(nested)) + self._current_indent
                self._action_max_length = max(self._action_max_length, width)

    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, prefix or "Usage: ")


def parser():
    display = current_command()
    argument_parser = style.ArgumentParser(
        prog=display,
        description="Manage enrolled machines.",
        formatter_class=HelpFormatter,
    )
    argument_parser.add_argument(
        "--version",
        action="version",
        version=(ROOT / "VERSION").read_text().strip(),
        help="show manager and content versions, then exit",
    )
    subcommands = argument_parser.add_subparsers(
        dest="command", required=True, metavar="COMMAND", title="commands"
    )
    argument_parser._optionals.title = "global options"
    argument_parser._action_groups.remove(argument_parser._optionals)
    argument_parser._action_groups.append(argument_parser._optionals)
    for name, detail in DESCRIPTIONS.items():
        if name in remote.CAPABLE:
            detail += (
                "\nSSH securely connects to another machine."
                "\nAdd MACHINE to use one registered machine and its own saved choices."
                "\nRemote runs never prompt; connection failures exit nonzero."
            )
        elif name in remote.REASONS:
            detail += "\nRemote use is refused: " + remote.REASONS[name] + "."
        epilog = command_arguments.help_epilog(name, display)
        command_parser = subcommands.add_parser(
            name,
            prog=display + " " + name,
            help=detail.splitlines()[0],
            description=detail,
            formatter_class=HelpFormatter,
            epilog=epilog,
        )
        if name in command_arguments.GROUPS:
            command_arguments.add_groups(command_parser, name, HelpFormatter, display)
        else:
            command_arguments.add_parameters(command_parser, name)
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
                f"Run {display} domain list, then {display} domain set --name NAME.",
            )
        elif gap.startswith(("Git name ", "Git email ")):
            gap = gap.split(". Supply ", 1)[0] + f". Run {display} configure."
        elif " tool version is floating " in gap:
            tool = gap.split(" tool version is floating ", 1)[0]
            gap = (
                gap.split(". Supply ", 1)[0]
                + f". Run {display} tools set {tool} --version VERSION with a full version."
            )
        gaps.append(gap)
    return gaps


def read_view(args, home, saved_profile):
    display = command_name(saved_profile)
    name = args.command
    manifest = content_manifest(home / "dotfiles")
    if name == "content":
        pairs = owned_views.content_rows(home, saved_profile, checkout_state)
    elif name in ("backups", "secrets", "machines", "addons", "domain", "groups", "tools"):
        entities = command_views.collection(name, home, saved_profile, manifest)
        empty = {
            "backups": f"No backups yet. Run {display} backups create.",
            "secrets": "No secret variables declared.",
            "tools": "No tool versions recorded.",
            "groups": "No content groups declared.",
            "addons": "No installed add-ons. None ship with Fleet.",
        }.get(name, "None recorded.")
        command_views.directory(name.capitalize(), entities, home, empty)
        if name == "domain":
            print(f"\nChoose: {display} domain set --name NAME · use a name listed above")
        return 0
    elif name == "version":
        pairs = [
            ("Manager", (ROOT / "VERSION").read_text().strip()),
            ("Content commit", commit(home / "dotfiles")),
        ]
    elif name == "doctor":
        gaps = command_profile_gaps(saved_profile)
        drift = status(home)
        pairs = [("Profile", "PASS" if not gaps else "FAIL · resolve the gaps below")]
        pairs.append(
            (
                "Domain choice",
                "PASS · chosen"
                if saved_profile.get("domain_chosen", False)
                else f"FAIL · UNCHOSEN; run {display} domain list",
            )
        )
        pairs += [(f"Gap {number}", gap) for number, gap in enumerate(gaps, 1)]
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
    print(
        render_view(
            name.capitalize(),
            pairs or [("Selections", "none")],
            str(home),
            inline_keys={key for key, _ in pairs if key.startswith("Tool ")}
            if name == "status"
            else (),
        ),
        end="",
    )
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
                else f" · UNCHOSEN; run {display} domain list"
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
    pairs += [("Tool " + key, value) for key, value in pin_rows(saved_profile, home)] or [
        ("Tools", "none")
    ]
    pairs += [("Group " + key, value) for key, value in saved_profile.get("groups", {}).items()]
    pairs += [
        (
            "Agents",
            ", ".join(
                name + (" on" if enabled else " off")
                for name, enabled in sorted(saved_profile.get("agents", {}).items())
            )
            or "none",
        ),
        ("Profile file", str(home / PROFILE)),
        ("Edit choices", f"Use {display} domain set, tools set, groups set, or configure."),
        ("Content", checkout_state(home / "dotfiles")),
        ("Updates", updates.summary(home, saved_profile).removeprefix("Updates: ")),
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
    saved_profile = profile(home)
    if updates.check(home, saved_profile, display=False) == 2:
        return 2
    try:
        drift = status(home)
    except (OSError, ValueError, RuntimeError) as error:
        print("Warning: Could not check local drift: " + str(error), file=sys.stderr)
        return 2
    checked = load_json(home / updates.CACHE, {})
    triggers = [source["name"] + " update" for source in checked["sources"] if source["behind"]]
    if drift:
        triggers.append("drift repair")
    if not triggers:
        print(updates.CURRENT_MESSAGE)
        return 0
    with tempfile.TemporaryDirectory(prefix="dotfleet-update-") as folder:
        staged = prepare_manager(home, Path(folder))
        environment = dict(os.environ)
        environment.pop("DOTFLEET_LOCAL_CONTENT", None)
        environment["FLEET_UPDATE_TRIGGERS"] = json.dumps(triggers)
        arguments = ["bash", str(staged / "install.sh")]
        if ui_channel.active() or os.environ.get(remote.CONTEXT) == "1":
            # TUI and SSH runs leave profile decisions to a real terminal on the target.
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
    command_views.directory(
        "Backups", owned_views.backup_entities(home, backups), home, "No backups yet."
    )
    if not args.backup:
        if not backups:
            return 0
        if ui_channel.active():
            from tui_actions import choose

            chosen = choose(
                "Restore a backup",
                "Backup folder",
                [(path.name, str(path)) for path in reversed(backups)],
            )
        elif manager.has_terminal():
            chosen = manager.ask(
                "Backup",
                "cancel",
                detail="Enter a listed backup folder to restore, or cancel to stop. Press Enter.",
            )
        else:
            return 0
        if chosen in (None, "cancel"):
            return 0
    else:
        chosen = args.backup
    folder, operations = validate_backup(home, chosen)
    return transaction(home, operations, "Restore the listed files and profile.", folder, "restore")


def main(argv=None):
    home = Path.home()
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    if argv == ["_remote"]:
        return remote.receive()
    if argv in (["_preview"], ["_preview", "--stat"]) and os.environ.get("DOTFLEET_PREVIEW") == "1":
        try:
            return preview_current(home, profile(home), "--stat" in argv)
        except (OSError, ValueError, RuntimeError) as error:
            print("Preview failed: " + str(error), file=sys.stderr)
            return 2
    argument_parser = parser()
    original_arguments = list(sys.argv[1:] if argv is None else argv)
    successor = command_arguments.successor(original_arguments)
    if successor:
        print("Error: Use " + current_command(home) + " " + successor + ".", file=sys.stderr)
        return 2
    try:
        argv = command_arguments.help_arguments(
            sys.argv[1:] if argv is None else argv, DESCRIPTIONS
        )
    except ValueError as error:
        argument_parser.error(str(error))
    args = command_arguments.normalize(argument_parser.parse_args(argv), argument_parser)
    if args.command in command_arguments.GROUPS and args.action is None:
        args.help_parser.print_help()
        return 0
    if args.command == "version":
        return read_view(args, home, {})
    if getattr(args, "machine", None):
        return remote.execute(
            home,
            args.machine,
            args.command,
            check=getattr(args, "check", False),
            stat=getattr(args, "stat", False),
        )
    if args.command == "tui":
        from tui_environment import launch

        return launch(home, ROOT)
    if args.command == "topology":
        view = topology.view(home)
        if args.json:
            stream = getattr(sys.stdout, "stream", sys.stdout)
            stream.write(json.dumps(view, indent=2) + "\n")
            stream.flush()
        else:
            print(topology.render(view), end="")
        return 0
    try:
        saved_profile = profile(home)
        display = command_name(saved_profile)
        if args.command == "check":
            return updates.check(home, saved_profile)
        if args.command == "backups" and args.action == "delete":
            import backup_cleanup

            if args.keep is not None and args.keep < 0:
                argument_parser.error("--keep needs zero or a positive count")
            names = backup_cleanup.select(home, args.name, args.keep, args.all_backups)
            return backup_cleanup.delete_selected(home, names)
        if args.command == "machines":
            if args.action == "set-ssh":
                name, target = args.hostname, args.ssh_target
                if name is None and target is None:
                    target = connections.pick(home)
                    if not target:
                        print("Cancelled. No connection saved.")
                        return 0
                return set_machine_ssh(home, name or target, target or name)
            if args.action == "remove":
                return remove_machine(home, args.hostname)
            if args.action == "export":
                info(
                    "Machine export",
                    owned_views.export_rows(
                        home, (ROOT / "VERSION").read_text().strip(), args.output
                    ),
                    home,
                )
                return 0
        if args.command in ("status", "doctor", "content") or getattr(args, "action", None) in (
            "list",
            "show",
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
        if args.command == "backups" and args.action == "restore":
            if args.backup in registry.load(home)["machines"]:
                return remote.refusal(args.backup, "rollback")
            return restore_command(args, home)
        if args.command == "backups" and args.action == "create":
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
            if not manager.has_terminal() and not ui_channel.active():
                raise ValueError("Uninstall needs a terminal and typed confirmation.")
            backups = records(home)
            selected, restored = None, []
            restore = "keep"
            if backups and ui_channel.active():
                from tui_actions import choose

                restore = choose(
                    "After removing Fleet",
                    "Oldest backup",
                    [
                        ("keep — remove managed files; keep the backups", "keep"),
                        ("restore — restore the oldest saved files", "restore"),
                    ],
                    "keep",
                )
                if restore is None:
                    print("Cancelled. Nothing removed.")
                    return 0
            elif backups:
                restore = manager.ask(
                    "Oldest backup",
                    "keep",
                    ["restore", "keep"],
                    detail=(
                        "restore: restore the oldest saved files after removal; keep: "
                        "remove managed files only. Content and backups stay. Press Enter."
                    ),
                )
            if restore == "restore":
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
                raise ValueError(f"Unknown domain. Run {display} domain list to see choices.")
            return manager.main(["--defaults", "--domain", args.name])
        if args.command == "groups":
            return manager.main(["--defaults", "--group", args.name + "=" + args.state])
        if args.command == "tools":
            if args.action == "uninstall":
                from tool_actions import uninstall

                return uninstall(home, saved_profile, args.tool, args.version)
            if not args.pin:
                argument_parser.error("tools set needs TOOL and --version; tools remove needs TOOL")
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
                argument_parser.error(
                    "tools set needs TOOL --version VERSION with an exact version, such as 24.14.1"
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
        if args.command == "check":
            print("Warning: Could not check updates: " + str(error), file=sys.stderr)
            return 2
        style.record_failure()
        print("Error: " + str(error), file=sys.stderr)
        return 2 if args.command == "diff" else 1
    return 0


def remove_machine(home, hostname):
    import socket

    if hostname == socket.gethostname():
        raise ValueError(
            "This machine is active. Only another machine’s stale record can be removed."
        )
    if not (home / registry.REGISTRY).exists():
        raise ValueError("No registry exists. Nothing was removed.")

    def remove(data):
        if hostname not in data["machines"]:
            raise ValueError("No saved machine named " + hostname + ". Nothing was removed.")
        del data["machines"][hostname]

    return change_registry(home, remove)


def set_machine_ssh(home, hostname, target):
    remote.validate_target(hostname)
    remote.validate_target(target)

    def connect(data):
        if hostname not in data["machines"]:
            # A connection can be saved before any facts have been imported from that machine.
            data["machines"][hostname] = {
                name: False if kind is bool else "" for name, kind in registry.FIELDS.items()
            }
            data["machines"][hostname].update(hostname=hostname, result="not recorded")
        data["machines"][hostname]["ssh_target"] = target

    result = change_registry(home, connect)
    return result or remote.test_connection(home, hostname, target)


def change_registry(home, edit):
    import difflib
    import fcntl

    path = home / registry.REGISTRY
    if not path.parent.resolve().is_relative_to(home.resolve()):
        raise ValueError("Fleet registry parent leaves HOME.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.parent / ".fleet.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = registry.load(home)
        edit(data)
        before = path.read_text() if path.exists() else ""
        after = registry.serialize(data)
        account = RunAccount(home, ROOT)
        account.checkouts = []
        account.software = []
        account.manifest = manager.backup_inventory(home, [registry.REGISTRY])
        account.files = (
            []
            if before == after
            else [
                {
                    "path": str(path),
                    "relative": registry.REGISTRY,
                    "action": "changed" if path.exists() else "added",
                    "before": manager.fingerprint(path),
                }
            ]
        )
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
                        before.splitlines(True),
                        after.splitlines(True),
                        fromfile=str(path),
                        tofile=str(path),
                    )
                ),
            )
            if ui_channel.active() and not ui_channel.review(account.record()):
                return account.finish("CANCELLED")
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
