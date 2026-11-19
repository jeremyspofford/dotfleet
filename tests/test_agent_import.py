"""Print agents-table entries from an agent's settings without writing or revealing secrets.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_import.py --help.
"""

import json
from pathlib import Path
import tempfile
import tomllib
import unittest

import agent_import
import agent_table
from common import read_toml
from test_manager import ROOT

CATALOG = read_toml(ROOT / "agents/manifest.toml")["agents"]
TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name)

    def write(self, relative, data):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))

    def imported(self, agent):
        before = sorted(self.home.rglob("*"))
        text = agent_import.run(self.home, agent)
        self.assertEqual(sorted(self.home.rglob("*")), before)  # The import writes nothing.
        self.assertNotIn(TOKEN, text)
        return text, tomllib.loads(text)

    def assert_valid(self, table):
        for kind in ("mcp", "plugins"):
            for name, spec in table["agents"].get(kind, {}).items():
                agent_table.check_item(kind, name, spec, {}, [], CATALOG, [])

    def test_claude_servers_plugins_and_hooks_become_neutral_entries(self):
        self.write(
            ".claude.json",
            {
                "mcpServers": {
                    "Docs Search": {
                        "command": "npx",
                        "args": ["-y", "@example/docs"],
                        "env": {"REGION": "us", "API_KEY": TOKEN, "HOME_DIR": "${HOME}"},
                    },
                    "issues": {
                        "type": "http",
                        "url": "https://mcp.example.com/issues",
                        "headers": {"Authorization": "Bearer ${ISSUES_TOKEN}", "X-Team": "core"},
                    },
                    "fleet": {"command": "/home/example/.fleet/agents/gateway/run-claude"},
                }
            },
        )
        self.write(
            ".claude/settings.json",
            {
                "enabledPlugins": {
                    "review-kit@example": True,
                    "fleet@skills-dir": True,
                    "off@x": False,
                },
                "extraKnownMarketplaces": {"example": {"source": {"repo": "example/plugins"}}},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "guard.sh"}]}]},
            },
        )
        text, table = self.imported("claude")
        agents = table["agents"]
        docs = agents["mcp"]["docs-search"]
        self.assertEqual(docs["env"], {"REGION": "us"})
        self.assertEqual(docs["pass_env"], ["DOCS_SEARCH_API_KEY", "HOME"])
        self.assertIn("set DOCS_SEARCH_API_KEY in your environment", text)
        issues = agents["mcp"]["issues"]
        self.assertEqual(
            (issues["bearer_token_env"], issues["headers"]), ("ISSUES_TOKEN", {"X-Team": "core"})
        )
        self.assertNotIn("fleet", agents["mcp"])
        self.assertEqual(
            agents["plugins"]["review-kit"]["sources"],
            {"claude": {"id": "review-kit@example", "marketplace": "example/plugins"}},
        )
        self.assertEqual(set(agents["plugins"]), {"review-kit"})
        self.assertIn('declare event = "before-tool"', text)
        self.assert_valid(table)
        golden = ROOT / "tests/fixtures/views/agents import.txt"
        self.assertEqual(text.replace(str(self.home), "/home/example"), golden.read_text())

    def test_codex_opencode_and_pi_shapes_import(self):
        self.write(
            ".codex/config.toml",
            '[mcp_servers.docs]\ncommand = "npx"\nenv_vars = ["DOCS_KEY"]\n'
            '[mcp_servers.remote]\nurl = "https://mcp.example.com/r"\nbearer_token_env_var = "R_TOKEN"\n',
        )
        _, table = self.imported("codex")
        self.assertEqual(table["agents"]["mcp"]["docs"]["pass_env"], ["DOCS_KEY"])
        self.assertEqual(table["agents"]["mcp"]["remote"]["bearer_token_env"], "R_TOKEN")
        self.assert_valid(table)
        self.write(
            ".config/opencode/opencode.jsonc",
            '{\n  // mine\n  "mcp": {"local": {"type": "local", "command": ["uvx", "tool"],'
            ' "environment": {"KEY": "{env:KEY}"}}},\n  "plugin": ["opencode-review"],\n}\n',
        )
        text, table = self.imported("opencode")
        self.assertEqual(table["agents"]["mcp"]["local"]["args"], ["tool"])
        self.assertEqual(table["agents"]["mcp"]["local"]["pass_env"], ["KEY"])
        self.assertIn("opencode-review", table["agents"]["plugins"])
        self.assertIn("hooks are code plugins", text)
        self.write(
            ".pi/agent/mcp.json",
            {
                "mcpServers": {
                    "x": {
                        "url": "https://example.com",
                        "headers": {"Authorization": "Bearer " + TOKEN},
                    }
                }
            },
        )
        text, table = self.imported("pi")
        self.assertEqual(table["agents"]["mcp"]["x"]["bearer_token_env"], "X_TOKEN")
        self.assertIn("Authorization header was withheld", text)
        self.assert_valid(table)

    def test_missing_or_invalid_settings_produce_an_empty_table_with_notes(self):
        self.write(".gemini/settings.json", "{not json")
        text, table = self.imported("gemini")
        self.assertEqual(table["agents"], {"schema": 1})
        self.assertIn("is not valid JSON; skipped.", text)
        with self.assertRaisesRegex(ValueError, "Import reads claude"):
            agent_import.run(self.home, "grok")


if __name__ == "__main__":
    unittest.main()
