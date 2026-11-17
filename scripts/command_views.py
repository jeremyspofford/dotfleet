"""Render command results with one named block per entity.

Usage: import read_view from the command dispatcher. Views do not fetch updates.
Help: python3 scripts/command_views.py --help.
"""

from pathlib import Path
import sys

import enrollment
import owned_views
import registry
from render_run import render_entities


def directory(title, entities, home, empty="None recorded."):
    print(render_entities(title, entities, str(home), empty), end="")


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
    if name == "tools":
        from tool_actions import installed_labels

        installed = enrollment.installed(home)
        return [
            {
                "name": tool,
                "fields": [
                    ("Version", command.format_pin(version)),
                    ("Installed", installed_labels(installed.get(tool, []), version)),
                ],
            }
            for tool, version in {
                tool: profile.get("pins", {}).get(tool, "none")
                for tool in sorted(set(profile.get("pins", {})) | set(installed))
            }.items()
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
