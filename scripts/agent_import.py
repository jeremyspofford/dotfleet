"""Print agents-table entries from one agent's existing settings. Write nothing.

An agent is an installed coding assistant. The agents table in fleet.toml declares its configuration.
The import reads the agent's user-level settings and prints neutral TOML for review.
It never prints a secret value. A value that looks secret becomes a variable name to set yourself.

Usage: import entries from the command dispatcher: fleet agents import --agent NAME.
Help: python3 scripts/agent_import.py --help.
"""

import json
from pathlib import Path
import re
import tomllib

from common import value

IMPORTABLE = ("claude", "codex", "gemini", "opencode", "pi")
REFERENCE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$|^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")
BEARER = re.compile(r"^Bearer\s+(\S+)$")
EVENTS = {
    "SessionStart": "session-start",
    "SessionEnd": "session-end",
    "UserPromptSubmit": "prompt-submit",
    "BeforeAgent": "prompt-submit",
    "PreToolUse": "before-tool",
    "BeforeTool": "before-tool",
    "PostToolUse": "after-tool",
    "AfterTool": "after-tool",
    "Stop": "stop",
    "AfterAgent": "stop",
}
GATEWAY = "fleet"


def name_for(text):
    """Turn a native server or plugin name into a valid item name."""
    name = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return name or "item"


def variable_for(server, key):
    return re.sub(r"[^A-Z0-9]+", "_", f"{server}_{key}".upper()).strip("_")


def secret(text):
    from enrollment import SECRET

    # A long run of letters and digits with no path or address punctuation looks like a token.
    token = re.fullmatch(r"[A-Za-z0-9_+=-]{24,}", text)
    return bool(SECRET.search(text.encode())) or bool(
        token and re.search(r"\d", text) and re.search(r"[A-Za-z]", text)
    )


class Importer:
    def __init__(self, home):
        self.home = home
        self.items = {}
        self.notes = []

    def read_json(self, relative, comments=False):
        path = self.home / relative
        if not path.is_file():
            return {}
        text = path.read_text()
        if comments:
            from agent_deploy import strip_jsonc

            text = strip_jsonc(text)
        try:
            data = json.loads(text)
        except ValueError:
            self.notes.append(f"~/{relative} is not valid JSON; skipped.")
            return {}
        return data if isinstance(data, dict) else {}

    def read_toml(self, relative):
        path = self.home / relative
        if not path.is_file():
            return {}
        try:
            return tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError:
            self.notes.append(f"~/{relative} is not valid TOML; skipped.")
            return {}

    def mapping(self, server, values, kind):
        """Split values into plain ones and variable names. Withhold anything secret."""
        plain, names = {}, {}
        for key, text in (values or {}).items():
            text = str(text)
            reference = REFERENCE.match(text)
            if reference:
                names[key] = reference.group(1) or reference.group(2)
            elif secret(text) or secret(f"{key}={text}"):
                variable = variable_for(server, key)
                names[key] = variable
                self.notes.append(
                    f"{server}: the {kind} value for {key} looks secret. It was withheld;"
                    f" set {variable} in your environment."
                )
            else:
                plain[key] = text
        return plain, names

    def server(self, native, spec, connector=False):
        """Convert one native server entry into an mcp item."""
        if native == GATEWAY:
            return  # Fleet's own gateway entry is an output, not a declaration.
        name = name_for(native)
        item = {"description": f"Imported from the {native} server."}
        command = spec.get("command")
        if isinstance(command, list):
            command, args = command[0], command[1:]
        else:
            args = spec.get("args", [])
        url = spec.get("url") or spec.get("httpUrl") or spec.get("serverUrl")
        if command:
            item.update(transport="stdio", command=str(command))
            if args:
                item["args"] = [str(argument) for argument in args]
            env = spec.get("env") or spec.get("environment") or {}
            plain, names = self.mapping(name, env, "environment")
            if plain:
                item["env"] = plain
            passed = sorted(set(names.values()) | set(spec.get("env_vars", [])))
            if passed:
                item["pass_env"] = passed
        elif url:
            item.update(transport="http", url=str(url))
            headers = dict(spec.get("headers") or spec.get("http_headers") or {})
            token = spec.get("bearer_token_env_var")
            authorization = headers.pop("Authorization", None)
            if authorization:
                bearer = BEARER.match(str(authorization))
                reference = REFERENCE.match(bearer.group(1)) if bearer else None
                if reference:
                    token = reference.group(1) or reference.group(2)
                else:
                    token = variable_for(name, "token")
                    self.notes.append(
                        f"{name}: the Authorization header was withheld; set {token} in your environment."
                    )
            plain, names = self.mapping(name, headers, "header")
            names.update(spec.get("env_http_headers", {}))
            if plain:
                item["headers"] = plain
            if names:
                item["header_env"] = names
            if token:
                item["bearer_token_env"] = token
        else:
            self.notes.append(f"{native}: no command or url; skipped.")
            return
        self.items[("mcp", name)] = item

    def plugin(self, agent, identifier, marketplace=None):
        name = name_for(identifier.split("@")[0].rsplit("/", 1)[-1])
        entry = self.items.setdefault(
            ("plugins", name), {"description": f"Imported plugin {identifier}.", "sources": {}}
        )
        source = {"id": identifier}
        if marketplace:
            source["marketplace"] = marketplace
        entry["sources"][agent] = source

    def hooks(self, agent, hooks):
        for event, groups in (hooks or {}).items():
            neutral = EVENTS.get(event)
            for group in groups if isinstance(groups, list) else []:
                for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                    command = hook.get("command", "")
                    if "/.local/share/fleet/agents/" in command:
                        continue  # Fleet's own hook wrapper.
                    self.notes.append(
                        f"{agent}: hook {event} runs {command!r}."
                        + (
                            f' Put it in a tracked script and declare event = "{neutral}".'
                            if neutral
                            else " This event has no neutral name."
                        )
                    )

    def claude(self):
        for native, spec in self.read_json(".claude.json").get("mcpServers", {}).items():
            self.server(native, spec)
        settings = self.read_json(".claude/settings.json")
        markets = settings.get("extraKnownMarketplaces", {})
        for identifier, enabled in settings.get("enabledPlugins", {}).items():
            if enabled and not identifier.startswith("fleet@"):
                market = identifier.split("@", 1)[1] if "@" in identifier else ""
                source = markets.get(market, {}).get("source", {})
                location = source.get("repo") or source.get("url") or source.get("path")
                self.plugin("claude", identifier, location)
        self.hooks("claude", settings.get("hooks"))

    def codex(self):
        config = self.read_toml(".codex/config.toml")
        for native, spec in config.get("mcp_servers", {}).items():
            self.server(native, spec)
        for identifier, spec in config.get("plugins", {}).items():
            if not isinstance(spec, dict) or spec.get("enabled", True):
                self.plugin("codex", identifier)
        self.hooks("codex", self.read_json(".codex/hooks.json").get("hooks"))

    def gemini(self):
        settings = self.read_json(".gemini/settings.json")
        for native, spec in settings.get("mcpServers", {}).items():
            self.server(native, spec)
        self.hooks("gemini", settings.get("hooks"))
        extensions = self.home / ".gemini/extensions"
        for folder in sorted(extensions.iterdir()) if extensions.is_dir() else []:
            install = folder / ".gemini-extension-install.json"
            if folder.name != "fleet" and install.is_file():
                source = self.read_json(str(install.relative_to(self.home))).get("source")
                if source:
                    self.plugin("gemini", source)

    def opencode(self):
        for relative in (".config/opencode/opencode.json", ".config/opencode/opencode.jsonc"):
            settings = self.read_json(relative, comments=True)
            for native, spec in settings.get("mcp", {}).items():
                self.server(native, spec)
            for module in settings.get("plugin", []):
                self.plugin("opencode", str(module))
        self.notes.append("opencode: hooks are code plugins; the import cannot read them.")

    def pi(self):
        for native, spec in self.read_json(".pi/agent/mcp.json").get("mcpServers", {}).items():
            self.server(native, spec)
        for package in self.read_json(".pi/agent/settings.json").get("packages", []):
            source = package.get("source") if isinstance(package, dict) else package
            if source:
                self.plugin("pi", str(source))
        self.notes.append("pi: hooks are code extensions; the import cannot read them.")

    def render(self, agent):
        lines = [
            f"# Imported from {agent}'s user-level settings. Review before adding to fleet.toml.",
            "# Secret values were withheld. Set each named variable in your environment.",
            "[agents]",
            "schema = 1",
        ]
        for (kind, name), item in sorted(self.items.items()):
            lines += ["", f"[agents.{kind}.{name}]"]
            nested = {key: entry for key, entry in item.items() if key == "sources"}
            for key, entry in item.items():
                if key not in nested:
                    lines.append(f"{key} = {value(entry)}")
            for key, entry in nested.items():
                lines += ["", f"[agents.{kind}.{name}.{key}]"]
                lines += [f"{agent_name} = {value(source)}" for agent_name, source in entry.items()]
        if self.notes:
            lines += ["", "# Notes:"] + [f"# - {note}" for note in self.notes]
        return "\n".join(lines) + "\n"


def run(home, agent):
    if agent not in IMPORTABLE:
        raise ValueError("Import reads claude, codex, gemini, opencode, or pi.")
    importer = Importer(Path(home))
    getattr(importer, agent)()
    return importer.render(agent)


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
