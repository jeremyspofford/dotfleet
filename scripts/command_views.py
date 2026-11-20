"""Render command results as lists of rows, or one named block per entity.

Usage: import read_view from the command dispatcher. Views do not fetch updates.
Help: python3 scripts/command_views.py --help.
"""

from pathlib import Path
import sys

import enrollment
import owned_views
import registry
from render_run import render_entities, render_rows


# A small fleet keeps one block per machine; beyond this count each machine gets a row.
MACHINE_BLOCKS = 5


def uses_rows(name, entities):
    return (
        name in ("backups", "marketplaces") or name == "machines" and len(entities) > MACHINE_BLOCKS
    )


def directory(title, entities, home, empty="None recorded.", rows=False):
    """Print entities as blocks, or as aligned rows grouped into sections by their group."""
    if not rows:
        print(render_entities(title, entities, str(home), empty), end="")
        return
    groups = {title: []}
    for entity in entities:
        groups.setdefault(entity.get("group", title), []).append(entity)
    sections = [
        render_rows(
            name,
            [entity["row"] for entity in items],
            str(home),
            empty,
            {entity["row"][0]: entity.get("notes", []) for entity in items},
        )
        for name, items in groups.items()
        if items or name == title and len(groups) == 1
    ]
    print("\n".join(sections), end="")


KIND_LABELS = {
    "skills": "Skill",
    "rules": "Rule set",
    "mcp": "MCP server",
    "connectors": "Connector",
    "plugins": "Plugin",
    "hooks": "Hook",
}


def agent_entities(home, profile):
    import agent_table
    import command

    exceptions = agent_table.exceptions(profile)
    entities = []
    for key, item in command.agent_items(home).items():
        spec = item.spec
        state = {"off": "off on this machine", "local": "local value on this machine"}.get(
            exceptions.get(key), "on"
        )
        if "domains" in spec and profile.get("domain", "core") not in spec["domains"]:
            state = "not in domain " + profile.get("domain", "core")
        agents = spec.get("agents") or (sorted(spec["sources"]) if item.kind == "plugins" else [])
        fields = [
            ("Kind", KIND_LABELS[item.kind]),
            *([("Source", "marketplace " + item.origin)] if item.origin != "content" else []),
            ("Adds", spec.get("description", f"Skill folder .agents/skills/{item.name}")),
            ("Agents", ", ".join(agents) or "every selected agent that supports it"),
            ("Domains", ", ".join(spec.get("domains", [])) or "every domain"),
            ("State", state),
        ]
        entities.append({"name": key, "fields": fields})
    return entities


def tool_view(home, profile, empty):
    """Show recorded tools as rows and other installed tools as one wrapped run."""
    import command
    from render_run import packed, render_rows
    from tool_actions import recorded_versions

    installed = enrollment.installed(home)
    # A selection that records no version is not a recorded tool.
    pins = {
        tool: value
        for tool, value in profile.get("pins", {}).items()
        if recorded_versions(value) - {""}
    }
    rows = []
    for tool, value in sorted(pins.items()):
        versions = recorded_versions(value)
        present = [item["version"] for item in installed.get(tool, [])]
        missing = [version for version in sorted(versions) if version not in present]
        state = (
            "installed"
            if not missing
            else "not installed"
            if len(missing) == len(versions)
            else "not installed: " + ", ".join(missing)
        )
        extra = [version for version in present if version not in versions]
        rows.append(
            (
                tool,
                command.format_pin(value),
                state + (" · also " + ", ".join(extra) if extra else ""),
            )
        )
    text = render_rows("Recorded", rows, str(home), empty)
    others = [
        tool + ": " + ", ".join(item["version"] for item in items)
        for tool, items in sorted(installed.items())
        if tool not in pins and items
    ]
    if others:
        text += "\n" + packed("Also installed — not recorded", others)
    return text


def collection(name, home, profile, manifest):
    import command

    if name == "machines":
        return registry.entities(home, (command.ROOT / "VERSION").read_text().strip())
    if name == "backups":
        return owned_views.backup_entities(home, command.records(home))
    if name == "agents":
        return agent_entities(home, profile)
    if name == "marketplaces":
        import marketplaces

        return marketplaces.entities(home)
    if name == "groups":
        return [
            {
                "name": group,
                "fields": [
                    ("State", "on" if profile.get("groups", {}).get(group, True) else "off"),
                    ("Adds", spec["description"]),
                ],
            }
            for group, spec in manifest.get("groups", {}).items()
        ]
    if name == "domain":
        return [
            {
                "name": domain,
                "fields": [("Adds", description)],
                "notes": [
                    (
                        "Current · chosen"
                        if profile.get("domain_chosen", False)
                        else "Current · UNCHOSEN"
                    )
                ]
                if domain == profile.get("domain", "core")
                else [],
            }
            for domain, description in command.domain_choices(home).items()
        ]
    if name == "secrets":
        return [
            {"name": name, "fields": [("State", state)]}
            for name, state in owned_views.secret_rows(home, manifest)
            if name != "Declared variables"
        ]
    if name == "addons":
        from addons import installed_addons

        return [
            {
                "name": item["id"],
                "fields": [("Version", item["version"]), ("Description", item["description"])],
            }
            for item in installed_addons(home, (command.ROOT / "VERSION").read_text().strip())
        ]
    raise ValueError("Unknown collection: " + name)


def usage():
    import argparse
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
