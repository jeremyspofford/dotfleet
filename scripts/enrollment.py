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


def stage_repo(home, repo, workspace):
    """Fetch into scratch, enforcing the same clean/fast-forward rules as an update."""
    canonical = home / "dotfiles"
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


def adopt(home, workspace, found, excluded):
    existing = home / "dotfiles"
    if existing.exists() or existing.is_symlink():
        if existing.is_symlink() or not (existing / ".git").is_dir():
            raise ValueError(
                "~/dotfiles is not a Git checkout. Move it aside yourself before adoption."
            )
        if run("git", "-C", str(existing), "status", "--porcelain").strip():
            raise ValueError("~/dotfiles has local edits. Preserve them before adoption.")
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
    choices = ["defaults", "repo"] + (["adopt"] if set(found) - set(excluded) else [])
    enrolled = any(
        (home / name).exists()
        for name in (
            "dotfiles",
            ".config/fleet/profile.toml",
            ".config/dotfiles/profile.toml",
            ".config/chezmoi/chezmoi.toml",
        )
    )
    recommended = "repo" if enrolled else "adopt" if "adopt" in choices else "defaults"
    detail = ""
    if enrolled:
        detail += "Current source: " + profile.get("content_repo", manager.DEFAULT_REPO) + "\n"
    detail += "defaults: use our example shell and Git files.\n"
    detail += (
        "repo: keep the current source, or enter another repository URL.\n"
        if enrolled
        else "repo: enter your own Git repository URL.\n"
    )
    if "adopt" in choices:
        detail += "adopt: keep your existing files unchanged in a new local repository. Secret matches are excluded.\n"
    detail += f"Recommended: {recommended}. Press Enter to keep this choice, or type a listed door."
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
    for field, label, key in [
        ("git_name", "Git author name", "user.name"),
        ("git_email", "Git author email", "user.email"),
    ]:
        if getattr(args, field) is not None:
            continue
        current = subprocess.run(
            ["git", "-C", str(home), "config", "--get", key], text=True, capture_output=True
        ).stdout.strip()
        recorded = profile.get(field) or current
        while True:
            value = manager.ask(
                label,
                recorded,
                heading="5. Git commit identity",
                detail="Git records this value on commits.\nPress Enter to keep the value. Type skip to leave it empty explicitly.",
            )
            if value == "skip":
                profile[field] = ""
                break
            if value:
                profile[field] = value
                break
            print("Enter a value, or type skip. An empty answer cannot silently omit identity.")


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
        detail="accept: keep saved versions and use installed versions for matching defaults.\nedit: enter TOOL=VERSION pairs, separated by commas. Type none to clear selections.\nPress Enter to submit.",
    )
    if action == "edit":
        while True:
            value = manager.ask(
                "Tool versions",
                "none",
                detail=(
                    "Enter pairs such as node=24.14.1,python=3.13.0, or none. Press Enter "
                    "to submit."
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
                "Content door",
                recommended,
                choices,
                heading="3. Where your content comes from",
                detail=detail,
            )
        if door == "adopt":
            found, excluded = scan(home)
            for line in scan_view.exclusions(excluded):
                print(line)
            checkout = adopt(home, workspace, found, excluded)
            repo = str(home / "dotfiles")
            profile["adopted"] = True
            profile["source_door"] = "adopt"
            profile["adoption_excluded"] = sorted(excluded)
            print("Your repository starts local at ~/dotfiles. Nothing is uploaded.")
            print(
                (
                    "Later: create an empty remote repository, then run git remote add "
                    "origin URL and git push -u origin main in ~/dotfiles."
                )
            )
        else:
            if door == "defaults":
                repo = manager.DEFAULT_REPO
            elif not args.repo:
                repo = manager.ask(
                    "Content repository",
                    repo,
                    detail=(
                        "Enter a trusted Git URL or local repository path. Press Enter to "
                        "keep the shown value."
                    ),
                )
            checkout = stage_repo(home, repo, workspace)
        try:
            kind, manifest = detect(checkout, args.format or profile.get("format"))
        except ValueError as error:
            if "Unknown content format" not in str(error):
                raise
            kind = manager.ask(
                "Format",
                "plain",
                ["plain", "chezmoi"],
                detail="plain: real home file names.\nchezmoi: native source names and templates.\nType a format and press Enter.",
            )
            kind, manifest = detect(checkout, kind)
        profile.update(content_repo=repo, format=kind, adopted=bool(manifest.get("adopted", False)))
    if start <= 1 and args.domain is None:
        domain = manager.ask(
            "Domain",
            profile.get("domain", "core"),
            [*content_domains(manifest), "skip"],
            heading="4. Choose your domain",
            detail=manager.domain_details(checkout),
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
                    detail=f"{spec['description']}\non: include these files or commands.\noff: exclude them.\nPress Enter to keep the shown choice.",
                )
                == "on"
            )
    if start <= 5:
        if args.agents is not None:
            agent_flags(profile, args.agents)
        else:
            choose_agents(home, profile)
    warnings += gap_warnings(profile)
    return profile, checkout, manifest, list(dict.fromkeys(warnings))


def choose_agents(home, profile):
    detected = detected_agents(home)
    print("\n8. Agents\nAn agent is a coding assistant. Only detected agents can be configured.")
    if not detected:
        print("No agents detected. No configuration selected.")
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
                detail="on: render the content's agent configuration for this agent.\n"
                "off: write nothing new for it; earlier outputs stay.\n"
                "A value that Fleet did not write stops setup before writes.\n"
                "Press Enter to keep the shown choice.",
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
    details += [
        "confirm: back up files and apply these choices. Recommended.",
        "quit: cancel the planned changes and move any renamed stores back.",
        "source, domain, identity, tools, groups, agents: return to that step, then review again.",
        "Press Enter alone to confirm. Type quit to cancel.",
    ]
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
