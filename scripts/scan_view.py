"""Summarize enrollment candidates without printing private or transient paths.

Usage: import this module from Fleet.
Help: python3 scripts/scan_view.py --help.
Direct execution without help keeps its existing behavior.
"""

import re

SENSITIVE_NAME = re.compile(r"secret|key|token|credential", re.I)
TRANSIENT = re.compile(
    r"(^|[._/-])(?:logs?|telemetry|caches?|history|histories|sessions?|snapshots?|receipts?)(?:[._/-]|$)|(?:^|/)\.?[a-z]*_history|(?:^|/)\.zcompdump|(?:^|/)\.bash_sessions",
    re.I,
)
SHELL = {
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".zlogin",
    ".zlogout",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".bash_logout",
    ".profile",
    ".aliases",
    ".commonrc",
    ".inputrc",
}


def transient(name):
    return bool(TRANSIENT.search(name))


def sensitive(name):
    return bool(SENSITIVE_NAME.search(name))


TOOL_FILES = {".npmrc", ".pypirc", ".tool-versions", ".vimrc", ".tmux.conf"}
BROWSERS = re.compile(
    r"^\.config/(?:google-chrome[^/]*|chromium|BraveSoftware|microsoft-edge[^/]*|vivaldi[^/]*"
    r"|opera[^/]*|mozilla|epiphany)/|^\.mozilla/",
    re.I,
)
ORDER = ("Shell", "Git", "App settings", "Browser data", "Other")
EXAMPLES = 3


def category(name):
    if name in SHELL or name.startswith(".config/fish/"):
        return "Shell"
    if name.startswith((".gitconfig", ".gitignore", ".gitattributes", ".config/git/")):
        return "Git"
    if BROWSERS.search(name):
        return "Browser data"
    if name.startswith(".config/") or name in TOOL_FILES:
        return "App settings"
    return "Other"


def place(name):
    """Name what a person recognizes: an app's folder for nested files, else the file."""
    parts = name.split("/")
    if parts[0] == ".config" and len(parts) > 2:
        return "~/.config/" + parts[1] + "/"
    if parts[0] != ".config" and len(parts) > 1:
        return "~/" + parts[0] + "/"
    return "~/" + name


def summary(candidates):
    """Return (category, count text, examples) rows. Examples are the largest places first."""
    groups = {}
    for name in candidates:
        places = groups.setdefault(category(name), {})
        places[place(name)] = places.get(place(name), 0) + 1
    rows = []
    for group in ORDER:
        places = groups.get(group)
        if not places:
            continue
        count = sum(places.values())
        examples = sorted(places, key=lambda item: (-places[item], item))[:EXAMPLES]
        text = ", ".join(examples)
        if len(places) > EXAMPLES:
            text += f", + {len(places) - EXAMPLES} more"
        rows.append((group, f"{count:,} file" + ("" if count == 1 else "s"), text))
    return rows


def lines(record, pins, agents):
    """Keep the inventory in the record. Show one line per category with a few examples."""
    from render_run import human, wrap

    output = [
        "2. Scan",
        "OS: " + record["os"],
        "Existing enrollment: " + (", ".join(record["enrollment"]) or "none"),
        f"Installed tools: {len(record['tools'])}",
    ]
    selected = ", ".join(f"{name} {human(value)}" for name, value in sorted(pins.items())) or "none"
    output += wrap("Selected tools: " + selected)
    output += wrap("Detected agents: " + (", ".join(agents) or "none"))
    candidates = record["candidates"]
    output.append(f"Home configuration candidates: {len(candidates):,} files")
    rows = summary(candidates)
    if rows:
        group_width = max(len(group) for group, _, _ in rows)
        count_width = max(len(count) for _, count, _ in rows)
        for group, count, examples in rows:
            output += wrap(examples, f"  {group:<{group_width}}  {count:>{count_width}}  ")
    if any(
        sensitive(name) or "secret" in reason or "credential" in reason
        for name, reason in record["excluded"].items()
    ):
        output.append("Sensitive files are excluded.")
    return output


def exclusions(excluded):
    """Report exclusions without naming files that could reveal credentials."""
    output = []
    hidden = False
    for name, reason in excluded.items():
        if sensitive(name) or "secret" in reason or "credential" in reason:
            hidden = True
        else:
            output.append(f"Excluded from adoption: ~/{name} ({reason})")
    if hidden:
        output.append("Sensitive files are excluded.")
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
