"""Check the agent deployment tree, its pointers, owned entries, and removal rules.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_deploy.py --help.
"""

import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import agent_deploy
import agent_detection
import agent_table
from common import read_toml
from test_manager import ROOT, command, repository

CATALOG = read_toml(ROOT / "agents/manifest.toml")["agents"]
MANIFEST = """
overlays = ["core", "work"]
[agents]
schema = 1
[agents.rules.style]
description = "Shared writing rules."
file = "agents/rules/style.md"
[agents.mcp.docs]
description = "Search the documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
pass_env = ["DOCS_API_KEY"]
[agents.mcp.issues]
description = "Read work issues."
transport = "http"
url = "https://mcp.example.com/issues"
bearer_token_env = "ISSUES_TOKEN"
domains = ["work"]
[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
agents = ["claude", "codex"]
[agents.hooks.guard]
description = "Stop destructive shell commands."
event = "before-tool"
tools = ["shell"]
command = "agents/hooks/guard.sh"
timeout = 10
"""
FILES = {
    ".agents/skills/notes/SKILL.md": "---\nname: notes\ndescription: Notes.\n---\n",
    "agents/rules/style.md": "Use plain words.\n",
    "agents/hooks/guard.sh": "#!/bin/sh\nexit 0\n",
}
ALL = ["claude", "codex", "gemini", "opencode", "pi"]


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        base = Path(self.folder.name)
        self.home = base / "home"
        self.home.mkdir()
        self.checkout = repository(base / "content", {**FILES, "fleet.toml": MANIFEST})
        command("git", "-C", str(self.checkout), "add", "--chmod=+x", "agents/hooks/guard.sh")
        self.profile = {"domain": "work", "agents": {name: True for name in ALL}}
        detected = patch.object(
            agent_detection, "detected", lambda home, catalog, tools=None: dict(catalog)
        )
        detected.start()
        self.addCleanup(detected.stop)

    def tearDown(self):
        self.folder.cleanup()

    def manifest(self, text=MANIFEST):
        (self.checkout / "fleet.toml").write_text(text)
        return tomllib.loads(text)

    def plan(self, text=MANIFEST):
        table = agent_table.load(self.checkout, self.manifest(text), CATALOG)
        return agent_deploy.plan(self.home, self.checkout, self.profile, table, CATALOG)

    def run_plan(self, text=MANIFEST):
        result = self.plan(text)
        agent_deploy.require_no_conflicts(result)
        agent_deploy.apply(self.home, result)
        return result

    def read_json(self, relative):
        return json.loads((self.home / relative).read_text())

    def test_claude_and_gemini_receive_one_pointer_each_to_the_tree(self):
        self.run_plan()
        tree = self.home / agent_deploy.TREE
        self.assertEqual(os.readlink(self.home / ".claude/skills/fleet"), str(tree / "claude"))
        self.assertEqual(os.readlink(self.home / ".gemini/extensions/fleet"), str(tree / "gemini"))
        servers = self.read_json(f"{agent_deploy.TREE}/claude/.mcp.json")["mcpServers"]
        self.assertEqual(servers["docs"]["env"], {"DOCS_API_KEY": "${DOCS_API_KEY}"})
        self.assertEqual(
            servers["issues"],
            {
                "type": "http",
                "url": "https://mcp.example.com/issues",
                "headers": {"Authorization": "Bearer ${ISSUES_TOKEN}"},
            },
        )
        self.assertEqual(
            servers["calendar"], {"type": "http", "url": "https://mcp.example.com/calendar"}
        )
        self.assertEqual(os.readlink(tree / "claude/skills"), str(self.home / ".agents/skills"))
        rules = self.home / ".claude/rules/fleet.md"
        self.assertIn("Use plain words.", rules.read_text())
        extension = self.read_json(f"{agent_deploy.TREE}/gemini/gemini-extension.json")
        self.assertEqual(extension["contextFileName"], "GEMINI.md")
        self.assertEqual(
            extension["mcpServers"]["issues"]["httpUrl"], "https://mcp.example.com/issues"
        )
        self.assertNotIn("calendar", extension["mcpServers"])

    def test_hooks_run_tree_wrappers_with_agent_variables_and_units(self):
        self.run_plan()
        tree = self.home / agent_deploy.TREE
        wrapper = (tree / "hooks/guard/run-claude").read_text()
        self.assertIn("export FLEET_AGENT=claude", wrapper)
        self.assertIn("export FLEET_HOOK_EVENT=before-tool", wrapper)
        self.assertEqual((tree / "hooks/guard/guard.sh").stat().st_mode & 0o777, 0o700)
        claude = self.read_json(f"{agent_deploy.TREE}/claude/hooks/hooks.json")["hooks"]
        self.assertEqual(claude["PreToolUse"][0]["matcher"], "Bash")
        self.assertEqual(claude["PreToolUse"][0]["hooks"][0]["timeout"], 10)
        gemini = self.read_json(f"{agent_deploy.TREE}/gemini/hooks/hooks.json")["hooks"]
        self.assertEqual(gemini["BeforeTool"][0]["matcher"], "run_shell_command")
        self.assertEqual(gemini["BeforeTool"][0]["hooks"][0]["timeout"], 10000)
        codex = self.read_json(".codex/hooks.json")["hooks"]["PreToolUse"][0]
        self.assertEqual(codex["hooks"][0]["command"], str(tree / "hooks/guard/run-codex"))

    def test_entry_level_agents_receive_owned_entries(self):
        result = self.run_plan()
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text())["mcp_servers"]
        self.assertEqual(codex["docs"]["env_vars"], ["DOCS_API_KEY"])
        self.assertEqual(codex["issues"]["bearer_token_env_var"], "ISSUES_TOKEN")
        opencode = self.read_json(".config/opencode/opencode.json")
        self.assertEqual(opencode["$schema"], agent_deploy.OPENCODE_SCHEMA)
        self.assertEqual(opencode["mcp"]["docs"]["command"], ["npx", "-y", "@example/docs-mcp"])
        self.assertEqual(
            opencode["mcp"]["docs"]["environment"], {"DOCS_API_KEY": "{env:DOCS_API_KEY}"}
        )
        self.assertEqual(
            opencode["instructions"], [str(self.home / agent_deploy.TREE / "rules/opencode.md")]
        )
        pi = self.read_json(".pi/agent/mcp.json")["mcpServers"]
        self.assertEqual(pi["issues"]["headers"], {"Authorization": "Bearer ${ISSUES_TOKEN}"})
        self.assertIn("opencode: hook guard is unsupported for this agent.", result.notes)
        self.assertIn("pi: hook guard is unsupported for this agent.", result.notes)
        self.assertIn("codex: sign in to calendar: run codex mcp login calendar", result.steps)
        self.assertIn("codex: trust new or changed Fleet hooks in /hooks.", result.steps)

    def test_shared_files_keep_other_values_comments_and_agent_added_keys(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text(
            '# My settings\nmodel = "o3"\n\n[profiles.fast]\nmodel = "mini" # quick\n'
        )
        self.run_plan()
        text = config.read_text()
        self.assertTrue(
            text.startswith(
                '# My settings\nmodel = "o3"\n\n[profiles.fast]\nmodel = "mini" # quick\n'
            )
        )
        # The agent adds a key inside Fleet's entry; that key never counts as drift.
        config.write_text(
            text.replace("[mcp_servers.docs]\n", "[mcp_servers.docs]\nenabled = false\n")
        )
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertNotIn(".codex/config.toml", result.files)
        changed = MANIFEST.replace(
            'args = ["-y", "@example/docs-mcp"]', 'args = ["-y", "@example/docs-mcp@2"]'
        )
        self.run_plan(changed)
        docs = tomllib.loads(config.read_text())["mcp_servers"]["docs"]
        self.assertEqual(docs["args"], ["-y", "@example/docs-mcp@2"])
        self.assertFalse(docs["enabled"])

    def test_unrecorded_values_conflict_or_are_adopted(self):
        settings = self.home / ".pi/agent/mcp.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"mcpServers": {"docs": {"command": "other"}}}))
        result = self.plan()
        self.assertIn(
            "pi: mcp.docs in ~/.pi/agent/mcp.json differs from the rendered value.",
            result.conflicts,
        )
        with self.assertRaises(ValueError) as caught:
            agent_deploy.require_no_conflicts(result)
        self.assertIn("Move each existing value aside", str(caught.exception))
        rendered = agent_deploy.server("pi", tomllib.loads(MANIFEST)["agents"]["mcp"]["docs"])
        settings.write_text(json.dumps({"mcpServers": {"docs": dict(rendered, disabled=True)}}))
        result = self.plan()
        self.assertEqual(result.conflicts, [])
        self.assertIn("mcp.docs", [entry.item for entry in result.adopted])

    def test_changed_recorded_entries_stop_and_missing_ones_return(self):
        self.run_plan()
        settings = self.home / ".pi/agent/mcp.json"
        data = json.loads(settings.read_text())
        data["mcpServers"]["docs"]["command"] = "edited"
        settings.write_text(json.dumps(data))
        self.assertIn(
            "pi: mcp.docs in ~/.pi/agent/mcp.json changed since Fleet wrote it.",
            self.plan().conflicts,
        )
        del data["mcpServers"]["docs"]
        settings.write_text(json.dumps(data))
        self.run_plan()
        self.assertEqual(
            self.read_json(".pi/agent/mcp.json")["mcpServers"]["docs"]["command"], "npx"
        )

    def test_removed_declarations_remove_matching_outputs_and_keep_changed_ones(self):
        self.run_plan()
        pi = self.home / ".pi/agent/mcp.json"
        data = json.loads(pi.read_text())
        data["mcpServers"]["issues"]["url"] = "https://edited.example.com"
        pi.write_text(json.dumps(data))
        without = (
            MANIFEST.split("[agents.mcp.issues]")[0]
            + "[agents.connectors"
            + MANIFEST.split("[agents.connectors")[1]
        )
        without = without.replace("[agents.rules.style]\n", "[agents.rules.unused]\n", 0)
        result = self.run_plan(
            without.replace(
                '[agents.rules.style]\ndescription = "Shared writing rules."\nfile = "agents/rules/style.md"\n',
                "",
            )
        )
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text())["mcp_servers"]
        self.assertNotIn("issues", codex)
        self.assertIn("issues", self.read_json(".pi/agent/mcp.json")["mcpServers"])
        self.assertIn(
            "pi: mcp.issues in ~/.pi/agent/mcp.json changed after Fleet wrote it; kept.",
            result.kept,
        )
        self.assertFalse((self.home / ".claude/rules/fleet.md").is_symlink())
        self.assertNotIn("instructions", self.read_json(".config/opencode/opencode.json"))

    def test_domain_changes_remove_items_and_turning_an_agent_off_keeps_outputs(self):
        self.run_plan()
        self.profile["domain"] = "core"
        self.run_plan()
        self.assertNotIn("issues", self.read_json(".pi/agent/mcp.json")["mcpServers"])
        self.profile["agents"]["pi"] = False
        self.profile["domain"] = "work"
        result = self.run_plan()
        self.assertNotIn("issues", self.read_json(".pi/agent/mcp.json")["mcpServers"])
        self.assertIn("docs", self.read_json(".pi/agent/mcp.json")["mcpServers"])
        self.assertEqual([entry for entry in result.stale_entries if entry.agent == "pi"], [])

    def test_exceptions_turn_items_off_or_release_them_to_a_local_value(self):
        self.run_plan()
        self.profile["agent_items"] = {"mcp.docs": "off", "mcp.issues": "local"}
        result = self.run_plan()
        pi = self.read_json(".pi/agent/mcp.json")["mcpServers"]
        self.assertNotIn("docs", pi)
        self.assertIn("issues", pi)
        self.assertIn("pi: mcp.issues released to this machine's own value.", result.notes)
        inventory = json.loads((self.home / agent_deploy.INVENTORY).read_text())
        self.assertNotIn("mcp.issues", [entry["item"] for entry in inventory["entries"]])
        pi["issues"]["url"] = "https://mine.example.com"
        (self.home / ".pi/agent/mcp.json").write_text(json.dumps({"mcpServers": pi}))
        self.assertEqual(self.plan().conflicts, [])

    def test_opencode_skips_servers_that_its_jsonc_file_overrides(self):
        jsonc = self.home / ".config/opencode/opencode.jsonc"
        jsonc.parent.mkdir(parents=True)
        jsonc.write_text(
            '{\n  // mine\n  "mcp": {"docs": {"type": "local", "command": ["x"]},},\n}\n'
        )
        result = self.run_plan()
        self.assertNotIn("docs", self.read_json(".config/opencode/opencode.json")["mcp"])
        self.assertIn(
            "opencode: ~/.config/opencode/opencode.jsonc defines mcp.docs, which overrides Fleet; skipped.",
            result.notes,
        )

    def test_legacy_generator_records_are_removed_or_released(self):
        target = str(self.checkout / "agents/skills/notes")
        (self.home / ".claude/skills").mkdir(parents=True)
        (self.home / ".claude/skills/notes").symlink_to(target)
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text("# Generated from agents/manifest.toml.\n")
        (self.home / ".claude/settings.json").write_text("{}\n")
        legacy = {
            ".claude/skills/notes": {"link": target},
            ".claude/settings.json": {"sha256": agent_deploy.hashlib.sha256(b"{}\n").hexdigest()},
            ".codex/config.toml": {
                "sha256": agent_deploy.hashlib.sha256(
                    b"# Generated from agents/manifest.toml.\n"
                ).hexdigest()
            },
        }
        state = self.home / agent_deploy.INVENTORY
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(legacy))
        result = self.run_plan()
        self.assertFalse((self.home / ".claude/skills/notes").is_symlink())
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertIn(".codex/config.toml", result.released)
        self.assertIn("[mcp_servers.docs]", (self.home / ".codex/config.toml").read_text())

    def test_content_cannot_manage_an_agent_output(self):
        table = agent_table.load(self.checkout, self.manifest(), CATALOG)
        with self.assertRaises(ValueError) as caught:
            agent_deploy.plan(
                self.home, self.checkout, self.profile, table, CATALOG, {".pi/agent/mcp.json"}
            )
        self.assertIn("two sources cannot own it", str(caught.exception))

    def test_uninstall_removes_entries_but_never_deletes_shared_files(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('model = "o3"\n')
        self.run_plan()
        with tempfile.TemporaryDirectory() as workspace:
            paths, shared = agent_deploy.removal_operations(self.home, workspace)
            self.assertIn(".claude/skills/fleet", paths)
            self.assertNotIn(".codex/config.toml", paths)
            self.assertEqual(
                tomllib.loads(shared[".codex/config.toml"].read_text()), {"model": "o3"}
            )
            self.assertEqual(json.loads(shared[".pi/agent/mcp.json"].read_text()), {})

    def test_restore_returns_only_fleet_entries_from_a_backup(self):
        config = self.home / ".pi/agent/mcp.json"
        self.run_plan()
        saved = Path(self.folder.name) / "saved.json"
        saved.write_text(config.read_text())
        data = json.loads(config.read_text())
        data["mcpServers"]["docs"]["command"] = "edited"
        data["mcpServers"]["mine"] = {"command": "mine"}
        config.write_text(json.dumps(data))
        inventory = agent_deploy.load_inventory(self.home)
        with tempfile.TemporaryDirectory() as workspace:
            restored = agent_deploy.restore_shared(
                self.home, ".pi/agent/mcp.json", saved, workspace, inventory
            )
            servers = json.loads(restored.read_text())["mcpServers"]
        self.assertEqual(servers["docs"]["command"], "npx")
        self.assertEqual(servers["mine"], {"command": "mine"})


if __name__ == "__main__":
    unittest.main()
