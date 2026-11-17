"""Add, list, update, and delete marketplaces of agent packages.

A marketplace is a Git repository with a catalog file, fleet-marketplace.toml.
A package bundles any mix of skills, rule sets, MCP servers, connectors, plugins, and hooks.
Pulled-in packages live in an untracked library under the manager store, never in tracked content.
The record of each installation lives in the manager store.

Usage: import this module from the command dispatcher.
Help: python3 scripts/marketplaces.py --help.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import agent_table
from common import atomic

RECORD = ".local/share/fleet/marketplaces.json"
LIBRARY = ".local/share/fleet/library"
CATALOG = "fleet-marketplace.toml"
CACHE = ".local/share/fleet/marketplaces-check.json"
SCHEMA = 1
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FEATURE = re.compile(r"([a-z0-9-]+)/((?:skills|rules|mcp|connectors|plugins|hooks)\.[a-z0-9-]+)")
PACKAGE_KEYS = {"description", "version", "split", *agent_table.KINDS}
SKILL_KEYS = {"description": str, "path": str, "domains": list}
FETCH_TIMEOUT = 10

# A command that adds, updates, or deletes a marketplace sets this before the shared apply.
# Planning then renders the pending state; the apply saves it after the backup.
PENDING = {}


def git(path, *arguments, timeout=None):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    environment.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=5")
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=environment,
        timeout=timeout,
    )
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()[-1:]
        raise ValueError(f"git {arguments[0]} failed: " + " ".join(detail))
    return result.stdout.strip()


def load_records(home):
    path = home / RECORD
    if path.is_symlink():
        raise ValueError("Refusing a symlinked marketplace record")
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("schema_version", SCHEMA)
    data.setdefault("marketplaces", {})
    if data["schema_version"] != SCHEMA:
        raise ValueError("Unknown marketplace record version. Update Fleet.")
    return data


def records(home):
    """Return the records that planning should use: saved ones with any pending change."""
    data = load_records(home)
    marketplaces = dict(data["marketplaces"])
    for name, record in PENDING.items():
        if record is None:
            marketplaces.pop(name, None)
        else:
            marketplaces[name] = record
    return marketplaces


def library(home, name):
    pending = PENDING.get(name)
    if pending and pending.get("staged"):
        return Path(pending["staged"])
    return home / LIBRARY / name


def default_name(source):
    base = source.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return name or "marketplace"


def read_catalog(root):
    path = Path(root) / CATALOG
    if not path.is_file():
        raise ValueError(f"The marketplace has no {CATALOG} at its root.")
    try:
        catalog = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{CATALOG} is not valid TOML: {error}") from error
    if catalog.get("schema") != SCHEMA:
        raise ValueError(f"{CATALOG} needs schema = {SCHEMA}.")
    unknown = sorted(set(catalog) - {"schema", "description", "packages"})
    if unknown:
        raise ValueError(f"{CATALOG} has unknown key " + ", ".join(unknown))
    packages = catalog.get("packages", {})
    if not isinstance(packages, dict) or not packages:
        raise ValueError(f"{CATALOG} declares no packages.")
    return catalog


def package_items(root, name, package, agents):
    """Validate one package. Return its items, keyed by KIND.NAME."""
    where = f"package {name}"
    if not NAME.fullmatch(name):
        raise ValueError(f"Invalid {where}: names use lowercase letters, digits, and hyphens.")
    unknown = sorted(set(package) - PACKAGE_KEYS)
    if unknown:
        raise ValueError(f"Invalid {where}: unknown key " + ", ".join(unknown))
    if not isinstance(package.get("description"), str):
        raise ValueError(f"Invalid {where}: description needs one plain sentence.")
    entries = agent_table.tracked(root)
    items = {}
    for kind in agent_table.KINDS:
        for item_name, spec in package.get(kind, {}).items():
            if kind == "skills":
                check_skill(where, item_name, spec, entries)
            else:
                # Package items name no machine domain list; the user's content defines domains.
                agent_table.check_item(kind, item_name, spec, entries, [], agents, [])
                if "domains" in spec:
                    raise ValueError(f"Invalid {where}: {kind}.{item_name} cannot limit domains.")
            items[f"{kind}.{item_name}"] = agent_table.Item(kind, item_name, spec, root, name)
    if not items:
        raise ValueError(f"Invalid {where}: it bundles nothing.")
    return items


def check_skill(where, name, spec, entries):
    if not NAME.fullmatch(name) or not isinstance(spec, dict):
        raise ValueError(f"Invalid {where}: skills.{name} needs a table and a valid name.")
    unknown = sorted(set(spec) - set(SKILL_KEYS))
    if unknown or "path" not in spec:
        raise ValueError(f"Invalid {where}: skills.{name} accepts description and path.")
    if spec.get("domains"):
        raise ValueError(f"Invalid {where}: skills.{name} cannot limit domains.")
    path = Path(spec["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid {where}: skills.{name} path must stay in the marketplace.")
    if f"{path.as_posix()}/SKILL.md" not in entries:
        raise ValueError(f"Invalid {where}: {path.as_posix()}/SKILL.md is not tracked.")


def selection(catalog, take):
    """Return the selected packages and features. Refuse a split the package forbids."""
    packages = catalog["packages"]
    if take.get("all"):
        return {name: None for name in packages}
    # None takes a whole package; a set takes only the named features.
    chosen = {name: None for name in take.get("packages", [])}
    for feature in take.get("features", []):
        match = FEATURE.fullmatch(feature)
        if not match:
            raise ValueError(f"Invalid feature {feature}. Use PACKAGE/KIND.NAME.")
        package, key = match.groups()
        if package not in packages:
            raise ValueError(f"Unknown package {package}.")
        if not packages[package].get("split", False):
            raise ValueError(f"Package {package} does not allow taking single features.")
        if package in chosen and chosen[package] is None:
            continue  # The whole package is already selected.
        chosen.setdefault(package, set()).add(key)
    for name in chosen:
        if name not in packages:
            raise ValueError(f"Unknown package {name}.")
    return chosen


def items(home, agents):
    """Return the selected items of every marketplace, with their library roots."""
    result = {}
    for name, record in sorted(records(home).items()):
        root = library(home, name)
        catalog = read_catalog(root)
        for package, keys in selection(catalog, record["take"]).items():
            found = package_items(root, package, catalog["packages"][package], agents)
            if keys is not None:
                missing = sorted(keys - set(found))
                if missing:
                    raise ValueError(f"Package {package} has no feature " + ", ".join(missing))
                found = {key: item for key, item in found.items() if key in keys}
            for key, item in found.items():
                if key in result:
                    raise ValueError(f"Two marketplace packages declare {key}.")
                result[key] = agent_table.Item(
                    item.kind, item.name, item.spec, root, f"{name}/{package}"
                )
    return result


def extend(home, table, content_skills, agents):
    """Add pulled-in items to the content's table. A name collision stops the run."""
    pulled = items(home, agents)
    known = {item.key: item for item in table.items}
    known.update({"skills." + skill: None for skill in content_skills})
    for key, item in pulled.items():
        if key in known:
            raise ValueError(
                f"{key} from marketplace {item.origin} has the same name as a content item."
            )
    return agent_table.Table(table.items + list(pulled.values()), table.warnings)


def stage(home, source, workspace, pin=""):
    """Clone a marketplace into scratch and check out the pin or the newest commit."""
    Path(workspace).mkdir(parents=True, exist_ok=True)
    destination = Path(workspace) / "marketplace"
    if destination.exists():
        shutil.rmtree(destination)
    git(workspace, "clone", "--quiet", "--no-hardlinks", source, str(destination), timeout=60)
    if pin:
        git(destination, "checkout", "--quiet", "--detach", pin)
    commit = git(destination, "rev-parse", "HEAD")
    return destination, commit


def version(root):
    return git(root, "describe", "--tags", "--always")


def save(home, pending, backup_path):
    """Move staged clones into the library and save the records. Run after the backup."""
    data = load_records(home)
    for name, record in pending.items():
        destination = home / LIBRARY / name
        if destination.exists():
            # The backup keeps the earlier library copy, including any edited file.
            destination.rename(Path(backup_path) / f"previous-library-{name}")
        if record is None:
            data["marketplaces"].pop(name, None)
            continue
        staged = Path(record.pop("staged"))
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.move(str(staged), destination)
        data["marketplaces"][name] = record
    atomic(home / RECORD, json.dumps(data, indent=2, sort_keys=True) + "\n")


def take_from(packages, features):
    if not packages and not features:
        return {"all": True}
    take = {}
    if packages:
        take["packages"] = sorted(set(packages))
    if features:
        take["features"] = sorted(set(features))
    return take


def check(home):
    """Fetch each marketplace with a time limit and save how far each one is behind.

    Fetch only into the library's Git data. A pin blocks updates, not this information.
    """
    import datetime

    results = {}
    for name, record in sorted(load_records(home)["marketplaces"].items()):
        root = home / LIBRARY / name
        try:
            git(root, "fetch", "--quiet", "--no-tags", "origin", "HEAD", timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                f"marketplace {name} fetch timed out after {FETCH_TIMEOUT} seconds"
            ) from error
        latest = git(root, "rev-parse", "FETCH_HEAD")
        behind = int(git(root, "rev-list", "--count", f"{record['commit']}..{latest}"))
        results[name] = {
            "current": record["commit"],
            "latest": latest,
            "behind": behind,
            "pin": record.get("pin", ""),
        }
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    atomic(
        home / CACHE, json.dumps({"checked_at": stamp, "marketplaces": results}, indent=2) + "\n"
    )
    return results


def summary(home):
    """Describe saved marketplace results for status. Read the cache only."""
    names = load_records(home)["marketplaces"]
    if not names:
        return None
    try:
        cache = json.loads((home / CACHE).read_text())["marketplaces"]
    except (OSError, ValueError, KeyError):
        return "not checked yet"
    parts = []
    for name in sorted(names):
        result = cache.get(name)
        if result is None:
            parts.append(f"{name} not checked yet")
        elif result["behind"] and names[name].get("pin"):
            # Status says held, not pinned: tool selections own no pin wording there.
            parts.append(f"{name} +{result['behind']}, held at {names[name]['pin']}")
        elif result["behind"]:
            parts.append(f"{name} +{result['behind']}")
    return ", ".join(parts) or "none"


def entities(home):
    """Describe each marketplace for marketplaces list. Read local records and the cache only."""
    cache = {}
    if (home / CACHE).is_file():
        cache = json.loads((home / CACHE).read_text()).get("marketplaces", {})
    result = []
    for name, record in sorted(load_records(home)["marketplaces"].items()):
        take = record["take"]
        packages = (
            "all packages"
            if take.get("all")
            else ", ".join(take.get("packages", []) + take.get("features", []))
        )
        checked = cache.get(name)
        if not checked:
            updates = "not checked yet"
        elif checked["behind"] and record.get("pin"):
            updates = f"{checked['behind']} newer commits; the pin blocks them"
        elif checked["behind"]:
            updates = f"{checked['behind']} newer commits"
        else:
            updates = "none"
        result.append(
            {
                "name": name,
                "fields": [
                    ("Source", record["source"]),
                    ("Version", f"{record['version']} @ {record['commit'][:8]}"),
                    ("Pin", record.get("pin") or "none"),
                    ("Installed", packages),
                    ("Updates", updates),
                ],
            }
        )
    return result


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
