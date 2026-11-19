"""Plan terminal enrollment without changing the home directory.

Internal wizard helpers use manager.py's logger-backed terminal prompts.
The installer is the command-line entry point; run bash install.sh --help.

Usage: import this module from Fleet.
Help: python3 scripts/enrollment.py --help.
Direct execution without help keeps its existing behavior.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from common import atomic, read_toml, run, toml
from render_run import human
from convert import FURNITURE, content_domains, detect
import manager
import agent_detection
import dotfiles_library
import scan_view
import updates

STEPS = ["source", "domain", "identity", "tools", "groups", "agents"]
PRIVATE_NAMES = re.compile(
    r"(^|/)(?:\.secrets(?:\..*)?|\.env(?:\..*)?|\.netrc|\.npmrc|\.pypirc|credentials[^/]*|.*_history|.*\.(?:key|pem|secret)|id_(?:rsa|ed25519).*)$",
    re.I,
)
SECRET = re.compile(
    rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bgh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|\b[A-Za-z0-9_]*(?:api[_-]?key|token|password|passwd|secret|access[_-]?key)[A-Za-z0-9_]*\b[\w\s"\x27.-]*[:=]\s*["\x27]?[^\s"\x27#]{4,}',
    re.I,
)
EXCLUDE_CONFIG = {"fleet", "dotfleet", "chezmoi", "dotfiles/profile.toml"}


def scan(home):
    """Inspect root dotfiles and .config files; never traverse linked directories."""
    found, excluded = [], {}
    for path in sorted(home.iterdir()):
        if (
            path.name.startswith(".")
            and path.name not in FURNITURE
            and not scan_view.transient(path.name)
            and (path.is_file() or path.is_symlink())
        ):
            found.append(path.name)
    config = home / ".config"
    if config.is_dir() and not config.is_symlink():
        for parent, directories, files in os.walk(config, followlinks=False):
            directories[:] = sorted(
                d
                for d in directories
                if not (Path(parent) / d).is_symlink()
                and d != ".git"
                and not scan_view.transient(d)
            )
            for name in sorted(files):
                path = Path(parent) / name
                relative_name = path.relative_to(config).as_posix()
                if scan_view.transient(relative_name):
                    continue
                if any(
                    relative_name == item or relative_name.startswith(item + "/")
                    for item in EXCLUDE_CONFIG
                ):
                    continue
                found.append(path.relative_to(home).as_posix())
    for name in found:
        path = home / name
        if PRIVATE_NAMES.search(name) or scan_view.sensitive(name):
            excluded[name] = "credential filename"
        elif not path.is_file():
            excluded[name] = "not a regular file or readable file link"
        elif path.stat().st_size > 2 * 1024 * 1024:
            excluded[name] = "larger than the 2 MiB adoption limit"
        else:
            data = path.read_bytes()
            if SECRET.search(data):
                excluded[name] = "secret pattern found"
            elif b"\0" in data:
                excluded[name] = "binary data; review separately"
    return sorted(found), excluded


def installed(home):
    return json.loads(run("mise", "ls", "--installed", "--json", "--cd", str(home)))


def versions(items):
    return ", ".join(str(item.get("version", "?")) for item in items) or "not installed"


def agent_manifest():
    return read_toml(manager.ROOT / "agents/manifest.toml")["agents"]


def detected_agents(home=None, tools=None):
    return agent_detection.detected(home or Path.home(), agent_manifest(), tools)


def stage_repo(home, repo, workspace, name=None):
    """Fetch into scratch, enforcing the same clean/fast-forward rules as an update.

    Name selects the download in ~/.fleet/dotfiles; without one, the active download.
    """
    canonical = dotfiles_library.path(home, name) if name else dotfiles_library.active(home)
    destination = workspace / f"checkout-{len(list(workspace.glob('checkout-*')))}"
    if canonical.exists() or canonical.is_symlink():
        updates.validate_content(canonical, repo)
        if not os.environ.get("DOTFLEET_LOCAL_CONTENT"):
            local_only = str(canonical) == repo
            return updates.stage(canonical, destination, local_only=local_only, content=True)
        updates.require_clean(canonical, content=True)
    source = (
        str(canonical) if os.environ.get("DOTFLEET_LOCAL_CONTENT") and canonical.exists() else repo
    )
    run("git", "clone", "--no-hardlinks", source, str(destination))
    if source != repo:
        run("git", "-C", str(destination), "remote", "set-url", "origin", repo)
    if canonical.exists():
        old = run("git", "-C", str(canonical), "rev-parse", "HEAD").strip()
        # Fetch only into scratch. Divergence must fail before review or home writes.
        run("git", "-C", str(destination), "fetch", str(canonical), old)
        run("git", "-C", str(destination), "merge-base", "--is-ancestor", old, "HEAD")
    return destination


def adopt(home, workspace, found, excluded, name=dotfiles_library.ADOPTED):
    existing = dotfiles_library.path(home, name)
    shown = "~/" + dotfiles_library.LIBRARY + "/" + name
    if existing.exists() or existing.is_symlink():
        if existing.is_symlink() or not (existing / ".git").is_dir():
            raise ValueError(f"{shown} is not a Git checkout. Move it aside before adoption.")
        if run("git", "-C", str(existing), "status", "--porcelain").strip():
            raise ValueError(f"{shown} has local edits. Preserve them before adoption.")
    selected = [name for name in found if name not in excluded]
    if not selected:
        raise ValueError("No files remain after the secrets scan. Nothing was committed.")
    destination = workspace / f"checkout-{len(list(workspace.glob('checkout-*')))}"
    destination.mkdir()
    for name in selected:
        source, target = home / name, destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
    atomic(
        destination / "fleet.toml", toml({"format": "plain", "adopted": True, "paths": selected})
    )
    run("git", "init", "-b", "main", str(destination))
    run("git", "-C", str(destination), "add", "--force", "--", *selected, "fleet.toml")
    run(
        "git",
        "-C",
        str(destination),
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "user.name=Fleet",
        "-c",
        "user.email=dotfleet@localhost",
        "commit",
        "-m",
        "feat: adopt existing home configuration",
    )
    return destination


def welcome(home, sources, profile=None, record=None):
    print(
        "\n1. Welcome\nWe will scan this machine, choose content, and review every choice before apply."
    )
    print(
        (
            "A backup comes before replacement. Existing files are kept in backups; nothing"
            " is discarded."
        )
    )
    print("Type quit at review to cancel. Fleet then moves any renamed stores back.")
    found, excluded = scan(home)
    tools = installed(home)
    os_name = (
        "WSL"
        if os.environ.get("WSL_DISTRO_NAME")
        or Path("/proc/version").exists()
        and "microsoft" in Path("/proc/version").read_text().lower()
        else os.uname().sysname
    )
    details = {
        "os": os_name,
        "enrollment": sources,
        "tools": tools,
        "candidates": [name for name in found if name not in excluded],
        "excluded": excluded,
    }
    if record is not None:
        record.update(details)
    print(
        "\n"
        + "\n".join(
            scan_view.lines(details, (profile or {}).get("pins", {}), detected_agents(home, tools))
        )
    )
    return found, excluded


def door_options(home, profile, found, excluded):
    """An enrollment keeps its source. New homes choose from safe candidates."""
    choices = ["example", "repo"] + (["adopt"] if set(found) - set(excluded) else [])
    enrolled = any(
        (home / name).exists()
        for name in (
            dotfiles_library.LIBRARY,
            ".fleet/profile.toml",
            ".config/dotfiles/profile.toml",
            ".config/chezmoi/chezmoi.toml",
        )
    )
    recommended = "repo" if enrolled else "adopt" if "adopt" in choices else "example"
    intro = "Content is the set of shell, Git, and tool files that Fleet manages."
    if enrolled:
        intro += "\nCurrent source: " + profile.get("content_repo", manager.DEFAULT_REPO)
    rows = [
        ("example", "Start from Fleet's example shell and Git files."),
        (
            "repo",
            "Keep the current repository, or type another one next."
            if enrolled
            else "Use your own Git repository. You type its URL next.",
        ),
    ]
    if "adopt" in choices:
        rows.append(
            (
                "adopt",
                "Copy your existing files, unchanged, into a new local repository."
                " Files that look secret stay out.",
            )
        )
    detail = manager.options(rows, recommended, intro=intro)
    return choices, recommended, detail


def group_flags(profile, values, manifest):
    for name, choice in values:
        if name not in manifest.get("groups", {}) or choice not in ("on", "off"):
            raise ValueError(
                f"Unknown group or choice: {name}={choice}. Use a listed group with on or off."
            )
        profile.setdefault("groups", {})[name] = choice == "on"


def agent_flags(profile, value):
    known = agent_manifest()
    chosen = [] if value == "none" else value.split(",")
    if any(name not in known for name in chosen):
        raise ValueError("Unknown agent. Choose " + ", ".join(known) + ", or none.")
    for name in chosen:
        if not known[name]["supported"]:
            raise ValueError(f"{name}: {known[name]['reason']}")
        if not known[name].get("render", True):
            raise ValueError(f"{name} is detected only: {known[name]['render_reason']}")
        if name not in detected_agents():
            raise ValueError(
                f"{name} is not installed. Install it separately before configuring it."
            )
    profile["agents"] = {name: name in chosen for name in known}


def identity(home, profile, args):
    fields = [
        ("git_name", "Git author name", "user.name", "name"),
        ("git_email", "Git author email", "user.email", "email address"),
    ]
    # One numbered heading per step: the first prompt carries it, and the second uses its label.
    heading = "5. Git commit identity"
    for field, label, key, noun in fields:
        if getattr(args, field) is not None:
            continue
        current = subprocess.run(
            ["git", "-C", str(home), "config", "--get", key], text=True, capture_output=True
        ).stdout.strip()
        recorded = profile.get(field) or current
        while True:
            enter = (
                f"Press Enter alone to keep {recorded}."
                if recorded
                else "Press Enter alone to ask again; this value has no saved answer."
            )
            value = manager.ask(
                label,
                recorded,
                heading=heading,
                detail=f"Git records your {noun} on each commit. Type your {noun}.\n"
                f"  skip  Leave the {noun} empty.\n{enter}",
            )
            if value == "skip":
                profile[field] = ""
                break
            if value:
                profile[field] = value
                break
            print(f"Type your {noun}, or type skip. An empty answer cannot silently omit it.")
        heading = None


def pins(home, profile, checkout):
    defaults = content_defaults(checkout).get("tools", {})
    tools = installed(home)
    print("\n6. Tools\nSelect exact tool versions. No tools are installed here.")
    print("Tool | Installed versions | Content default | Saved version")
    for tool in sorted(set(tools) | set(defaults) | set(profile.get("pins", {}))):
        print(
            f"{tool} | {versions(tools.get(tool, []))} | {human(defaults.get(tool))} | {human(profile.get('pins', {}).get(tool))}"
        )
    action = manager.ask(
        "Tools",
        "accept",
        ["accept", "edit"],
        detail=manager.options(
            [
                ("accept", "Keep saved versions. Use installed versions for the content defaults."),
                ("edit", "Type exact versions next, as TOOL=VERSION pairs."),
            ],
            "accept",
        ),
    )
    if action == "edit":
        while True:
            value = manager.ask(
                "Tool versions",
                "none",
                detail=(
                    "Type pairs separated by commas, such as node=24.14.1,python=3.13.0.\n"
                    "  none  Clear every tool selection.\n"
                    "Press Enter alone to choose none, which clears every selection."
                ),
            )
            try:
                profile["pins"] = (
                    {}
                    if value == "none"
                    else dict(manager.pin_argument(item.strip()) for item in value.split(","))
                )
                break
            except (ValueError, argparse.ArgumentTypeError) as error:
                print(str(error))
        profile["unpinned_tools"] = sorted(set(defaults) - set(profile["pins"]))
    # Resolve floating entries with the existing explicit prompt and skip policy.
    shadow = dict(profile, domain_chosen=True, git_name="validated", git_email="validated")
    warnings = manager.validate_profile(home, shadow)
    profile["pins"] = shadow["pins"]
    if "unpinned_tools" in shadow:
        profile["unpinned_tools"] = shadow["unpinned_tools"]
    allowed = {
        name: value
        for name, value in defaults.items()
        if name not in profile.get("unpinned_tools", [])
    }
    effective, _ = manager.installed_defaults(allowed, profile["pins"], home)
    profile["pins"] = effective
    return warnings


def content_defaults(checkout):
    path = checkout / ".config/mise/config.toml"
    return read_toml(path if path.exists() else checkout / "packages/mise/.config/mise/config.toml")


def steps(home, args, workspace, profile, found, excluded, start=0, checkout=None, manifest=None):
    warnings = []
    if start <= 0:
        repo = args.repo or profile.get("content_repo", manager.DEFAULT_REPO)
        if args.adopt:
            door = "adopt"
        elif args.repo:
            door = "repo"
        else:
            choices, recommended, detail = door_options(home, profile, found, excluded)
            door = manager.ask(
                "Content source",
                recommended,
                choices,
                heading="3. Where your content comes from",
                detail=detail,
            )
        if door == "adopt":
            found, excluded = scan(home)
            for line in scan_view.exclusions(excluded):
                print(line)
            name = dotfiles_library.free_name(home, dotfiles_library.ADOPTED, True, reuse="local")
            checkout = adopt(home, workspace, found, excluded, name)
            repo = str(dotfiles_library.path(home, name))
            shown = "~/" + dotfiles_library.LIBRARY + "/" + name
            profile["adopted"] = True
            profile["source_door"] = "adopt"
            profile["adoption_excluded"] = sorted(excluded)
            profile["dotfiles"] = name
            print(f"Your repository starts local at {shown}. Nothing is uploaded.")
            print(
                (
                    "Later: create an empty remote repository, then run git remote add "
                    f"origin URL and git push -u origin main in {shown}."
                )
            )
        else:
            if door == "example":
                repo = manager.DEFAULT_REPO
            elif not args.repo:
                repo = manager.ask(
                    "Content repository",
                    repo,
                    detail=(
                        "Type a Git URL or a local repository path. Use only content you trust.\n"
                        f"Press Enter alone to keep {repo}."
                    ),
                )
            name = dotfiles_library.name_for_source(home, repo, profile, True)
            profile["dotfiles"] = name
            checkout = stage_repo(home, repo, workspace, name)
        try:
            kind, manifest = detect(checkout, args.format or profile.get("format"))
        except ValueError as error:
            if "Unknown content format" not in str(error):
                raise
            kind = manager.ask(
                "Format",
                "plain",
                ["plain", "chezmoi"],
                detail=manager.options(
                    [
                        ("plain", "Files use their real home names, such as .zshrc."),
                        ("chezmoi", "Files use chezmoi source names, such as dot_zshrc."),
                    ],
                    "plain",
                    intro="Fleet could not tell how this repository names its files.",
                ),
            )
            kind, manifest = detect(checkout, kind)
        profile.update(content_repo=repo, format=kind, adopted=bool(manifest.get("adopted", False)))
    if start <= 1 and args.domain is None:
        domain = manager.ask(
            "Domain",
            profile.get("domain", "core"),
            [*content_domains(manifest), "skip"],
            heading="4. Choose your domain",
            detail=manager.domain_details(
                checkout,
                profile.get("domain", "core"),
                "current" if profile.get("domain_chosen") else "recommended",
            ),
        )
        if domain != "skip":
            profile.update(domain=domain, domain_chosen=True)
    if start <= 2:
        identity(home, profile, args)
    if start <= 3:
        warnings += pins(home, profile, checkout)
    if start <= 4:
        group_flags(profile, args.group, manifest)
        supplied = dict(args.group)
        selections = profile.setdefault("groups", {})
        print("\n7. Content groups")
        for group, spec in manifest.get("groups", {}).items():
            if group in supplied:
                continue
            new = " NEW" if group not in selections else ""
            selections[group] = (
                manager.ask(
                    f"{group}",
                    "on" if selections.get(group, True) else "off",
                    ["on", "off"],
                    heading=f"Group: {group}{new}",
                    detail=manager.options(
                        [
                            ("on", "Include these files or commands."),
                            ("off", "Leave them out."),
                        ],
                        "on" if selections.get(group, True) else "off",
                        "recommended" if new else "current",
                        intro=spec["description"],
                    ),
                )
                == "on"
            )
    if start <= 5:
        if args.agents is not None:
            agent_flags(profile, args.agents)
        else:
            choose_agents(home, profile, checkout, manifest)
    warnings += gap_warnings(profile)
    return profile, checkout, manifest, list(dict.fromkeys(warnings))


AGENT_KINDS = [
    ("skills", "skill", "skills"),
    ("mcp", "MCP entry", "MCP entries"),
    ("rules", "rule", "rules"),
    ("connectors", "connector", "connectors"),
    ("plugins", "plugin", "plugins"),
    ("hooks", "hook", "hooks"),
]
NO_AGENT_CONFIGURATION = (
    "This content declares no agent configuration yet. On or off changes nothing today.\n"
    "On means this machine receives agent configuration at a later update, when you add some."
)


def counted(number, one, many):
    return f"{number} {one if number == 1 else many}"


def listed(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]


def marketplace_packages(home):
    """Count the marketplace packages this machine takes. An unreadable record counts none."""
    import marketplaces

    try:
        return sum(
            len(
                marketplaces.selection(
                    marketplaces.read_catalog(marketplaces.library(home, name)), record["take"]
                )
            )
            for name, record in marketplaces.records(home).items()
        )
    except (OSError, ValueError, KeyError):
        return 0


def agent_configuration(home, checkout, manifest):
    """Return one line that sizes the declared agent configuration, or the nothing-declared text.

    Skills are skill folders in the checkout. Packages come from this machine's marketplaces.
    """
    import agent_table

    table = (manifest or {}).get("agents")
    table = table if isinstance(table, dict) else {}
    counts = {
        kind: len(table.get(kind)) if isinstance(table.get(kind), dict) else 0
        for kind, _, _ in AGENT_KINDS
    }
    if checkout is not None:
        # Every skill folder deploys; a skills entry only limits its domains.
        counts["skills"] = len(agent_table.skills(checkout))
    parts = [counted(counts[kind], one, many) for kind, one, many in AGENT_KINDS if counts[kind]]
    packages = marketplace_packages(home)
    if not parts and not packages:
        return NO_AGENT_CONFIGURATION
    package_text = counted(packages, "marketplace package", "marketplace packages")
    if not parts:
        return f"This content declares no agent configuration. This machine takes {package_text}."
    line = "This content declares " + listed(parts) + "."
    return line + (f" This machine also takes {package_text}." if packages else "")


def choose_agents(home, profile, checkout=None, manifest=None):
    detected = detected_agents(home)
    print("\n8. Agents\nAn agent is a coding assistant. Only detected agents can be configured.")
    if not detected:
        print("No agents detected. No configuration selected.")
    else:
        print(agent_configuration(home, checkout, manifest))
    selections = profile.setdefault("agents", {})
    for name, spec in detected.items():
        if not spec["supported"]:
            print(f"{name}: {spec['reason']}")
            continue
        if not spec.get("render", True):
            print(f"{name}: detected. {spec['render_reason']} Fleet writes nothing for it.")
            continue
        selections[name] = (
            manager.ask(
                name,
                "on" if selections.get(name, False) else "off",
                ["on", "off"],
                heading="Agent: " + name,
                detail=manager.options(
                    [
                        ("on", "Fleet manages its skills, MCP entries, rules, and packages."),
                        ("off", "Fleet leaves its configuration alone. The agent still works."),
                    ],
                    "on" if selections.get(name, False) else "off",
                    "current",
                    intro=(
                        "When on, Fleet writes only entries it owns and never touches the agent's "
                        "login.\nWhen off, outputs Fleet wrote earlier stay in place.\n"
                        "Setup stops before writing if it finds a value Fleet did not write."
                    ),
                ),
            )
            == "on"
        )


def gap_warnings(profile):
    result = []
    if not profile.get("domain_chosen", False):
        result.append("No domain choice confirmed. The recorded domain remains in use.")
    if not profile.get("git_name") or not profile.get("git_email"):
        result.append("No complete git identity recorded. Git commits will fail until one is set.")
    for tool in profile.get("unpinned_tools", []):
        result.append(f"{tool} has no selected version. Content defaults will not select it.")
    return result


def review(profile, account, warnings):
    account.warnings = warnings
    details = account.lines(future=True)
    if profile.get("adopted"):
        details += [
            (
                "Adopted files stay byte-identical. Recorded choices do not rewrite their "
                "existing content."
            )
        ]
    rows = [
        ("confirm", "Back up files, then apply these choices."),
        ("quit", "Cancel. Nothing is applied, and any renamed stores move back."),
    ]
    rows += [
        (step, f"Return to step {number}, {title}, then review again.")
        for number, (step, title) in enumerate(
            [
                ("source", "content source"),
                ("domain", "domain"),
                ("identity", "Git commit identity"),
                ("tools", "tools"),
                ("groups", "content groups"),
                ("agents", "agents"),
            ],
            3,
        )
    ]
    details += ["", *manager.options(rows, "confirm").splitlines()]
    return manager.ask(
        "Review",
        "confirm",
        ["confirm", "quit", *STEPS],
        heading="9. Review before apply",
        detail="\n".join(details),
        formatted=True,
    )


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
