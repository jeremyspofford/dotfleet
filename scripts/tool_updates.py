"""Find the newest available version of each selected tool, and record it on request.

Usage: dotfleet tools update [TOOL]; dotfleet check; dotfleet status.
A selected tool has an exact version recorded in the profile.
Mise reports the newest available version. That lookup can use the network.
Check saves tool results under the manager store. Status reads them without network requests.
Help: python3 scripts/tool_updates.py --help.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sys

import run_index
from common import atomic, run

CACHE = ".local/share/fleet/tools-check.json"
SCHEMA = 1


def recorded(value):
    """Return the one exact version a selection records, or None when it records several."""
    if isinstance(value, dict):
        value = value.get("version")
    return value if isinstance(value, str) else None


def selected(profile):
    return dict(sorted(profile.get("pins", {}).items()))


def newest(home, tool):
    """Ask Mise for the newest available version. Mise may query the network."""
    import manager

    version = run("mise", "latest", "--cd", str(home), tool).strip()
    if not manager.exact_version(version):
        raise ValueError(f"Mise reported no exact version for {tool} ({version or 'no answer'})")
    return version


def release(version):
    """Return the release numbers of a version, or nothing for a commit identifier."""
    if re.fullmatch(r"[0-9a-f]{40}", version):
        return ()
    match = re.search(r"\d+(?:\.\d+)*", version)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def newer(candidate, current):
    """Treat a different version as newer unless its release numbers are lower."""
    return candidate != current and release(candidate) >= release(current)


def lookup(home, tool, value):
    """Describe one selected tool against the newest available version."""
    version = recorded(value)
    if version is None:
        return {"tool": tool, "state": "skipped"}
    try:
        latest = newest(home, tool)
    except (OSError, ValueError, RuntimeError) as error:
        return {"tool": tool, "state": "failed", "recorded": version, "error": str(error)}
    state = "behind" if newer(latest, version) else "current"
    return {"tool": tool, "state": state, "recorded": version, "newest": latest}


def check(home, profile):
    """Look up every selected tool and save the results for status."""
    results = {}
    for tool, value in selected(profile).items():
        result = lookup(home, tool, value)
        results[tool] = {key: item for key, item in result.items() if key != "tool"}
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    record = {"schema_version": SCHEMA, "checked_at": stamp, "tools": results}
    atomic(home / CACHE, json.dumps(record, indent=2) + "\n")
    return results


def summary(home, profile, display):
    """Describe saved tool results for status. Read the cache only; never use the network."""
    choices = selected(profile)
    if not choices:
        return None
    try:
        record = json.loads((home / CACHE).read_text())
        if record["schema_version"] != SCHEMA:
            return f"not checked yet — run: {display} check"
        cache = record["tools"]
        checked = (
            datetime.datetime.fromisoformat(record["checked_at"]).astimezone().strftime("%H:%M")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return f"not checked yet — run: {display} check"
    behind, parts = False, []
    for tool, value in choices.items():
        result, version = cache.get(tool), recorded(value)
        if version is None:
            continue
        if not isinstance(result, dict) or not isinstance(result.get("newest"), str):
            parts.append(f"{tool} not checked")
        elif newer(result["newest"], version):
            behind = True
            parts.append(f"{tool} {result['newest']} available")
    text = f"{', '.join(parts) or 'none'} (checked {checked})"
    return text + (f" — run: {display} tools update" if behind else "")


def result_line(result, display, remote=False):
    tool, state = result["tool"], result["state"]
    if state == "updated" and remote:
        return (
            f"{tool}: saved {result['newest']} in place of {result['recorded']};"
            " the next update applies it."
        )
    if state == "updated":
        return f"{tool}: updated {result['recorded']} → {result['newest']}."
    if state == "current" and result.get("newest", result["recorded"]) != result["recorded"]:
        return (
            f"{tool}: current at {result['recorded']}; the newest available version,"
            f" {result['newest']}, is older. Nothing changed."
        )
    if state == "current":
        return f"{tool}: current at {result['recorded']}; nothing changed."
    if state == "cancelled":
        return f"{tool}: cancelled; nothing changed."
    if state == "skipped":
        return (
            f"{tool}: skipped; it records several versions."
            f" Choose one with {display} tools set {tool} --version VERSION."
        )
    return f"{tool}: failed: {result['error'].rstrip('.')}."


def summary_line(results, remote=False):
    counts = {}
    for result in results:
        counts[result["state"]] = counts.get(result["state"], 0) + 1
    words = {"updated": "saved" if remote else "updated"}
    order = ["updated", "current", "failed", "cancelled", "skipped"]
    parts = [f"{counts[state]} {words.get(state, state)}" for state in order if counts.get(state)]
    return "Tools: " + ", ".join(parts) + "."


def keep_environment(apply, tool, version):
    """Run one apply. Restore Mise locations that an apply points at its temporary workspace."""
    saved = {name: value for name, value in os.environ.items() if name.startswith("MISE_")}
    try:
        return apply(tool, version)
    except (OSError, ValueError, RuntimeError) as error:
        return "failed", str(error)
    finally:
        for name in [name for name in os.environ if name.startswith("MISE_")]:
            if name not in saved:
                os.environ.pop(name)
        os.environ.update(saved)


def update(home, profile, tool, display, apply, remote=False):
    """Record each selected tool's newest version through apply(tool, version).

    Look up every version before the first apply. One failed tool never stops the others.
    Return 1 when any tool failed, and 0 otherwise.
    """
    choices = selected(profile)
    if tool is not None and tool not in choices:
        raise ValueError(
            f"{tool} is not selected. Select it first with"
            f" {display} tools set {tool} --version VERSION."
        )
    if not choices:
        print(f"No tools are selected. Select one with {display} tools set TOOL --version VERSION.")
        return 0
    run_index.stage("check")
    results = [lookup(home, name, choices[name]) for name in ([tool] if tool else choices)]
    for result in results:
        if "newest" in result:
            print(
                f"{result['tool']}: recorded {result['recorded']};"
                f" newest available {result['newest']}."
            )
    for result in results:
        if result["state"] == "behind":
            result["state"], detail = keep_environment(apply, result["tool"], result["newest"])
            result["error"] = detail
    print()
    for result in results:
        line = result_line(result, display, remote)
        if result["state"] == "failed":
            run_index.failure(line)
        print(line)
    if tool is None:
        print(summary_line(results, remote))
    return int(any(result["state"] == "failed" for result in results))


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
