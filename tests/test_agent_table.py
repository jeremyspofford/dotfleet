"""Validate the agents table that content declares in fleet.toml.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_table.py --help.
"""

import os
from pathlib import Path
import tempfile
import tomllib
import unittest

import agent_table
import test_command
import test_domains
import test_manager as helpers
from common import read_toml
from test_manager import ROOT, command, repository

CATALOG = read_toml(ROOT / "agents/manifest.toml")["agents"]
FILES = {
    ".agents/skills/release-notes/SKILL.md": "---\nname: release-notes\ndescription: Notes.\n---\n",
    "agents/rules/style.md": "Use plain words.\n",
    "agents/hooks/guard.sh": "#!/bin/sh\nexit 0\n",
}
VALID = """
overlays = ["core", "personal", "work"]
[agents]
schema = 1
[agents.skills.release-notes]
domains = ["work"]
[agents.rules.style]
description = "Shared writing rules."
file = "agents/rules/style.md"
[agents.mcp.docs]
description = "Search the documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
env = { DOCS_REGION = "us" }
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
[agents.plugins.review-kit]
description = "Review commands."
[agents.plugins.review-kit.sources]
claude = { id = "review-kit@example", marketplace = "example/plugins" }
[agents.hooks.guard]
description = "Stop destructive shell commands."
event = "before-tool"
tools = ["shell"]
command = "agents/hooks/guard.sh"
timeout = 10
"""


class AgentTableTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.checkout = repository(Path(self.folder.name) / "content", FILES)
        os.chmod(self.checkout / "agents/hooks/guard.sh", 0o755)
        command("git", "-C", str(self.checkout), "add", "--chmod=+x", "agents/hooks/guard.sh")

    def tearDown(self):
        self.folder.cleanup()

    def load(self, text):
        return agent_table.load(self.checkout, tomllib.loads(text), CATALOG)

    def rejects(self, text, message):
        with self.assertRaises(ValueError) as caught:
            self.load(text)
        self.assertIn(message, str(caught.exception))

    def test_complete_example_validates_and_names_each_item(self):
        table = self.load(VALID)
        self.assertEqual(
            [item.key for item in table.items],
            [
                "skills.release-notes",
                "rules.style",
                "mcp.docs",
                "mcp.issues",
                "connectors.calendar",
                "plugins.review-kit",
                "hooks.guard",
            ],
        )
        self.assertEqual(table.warnings, [])

    def test_content_without_the_table_declares_nothing(self):
        table = self.load('format = "plain"\n')
        self.assertEqual((table.items, table.warnings), ([], []))

    def test_unknown_keys_tables_and_schema_numbers_fail(self):
        self.rejects(VALID.replace("schema = 1", "schema = 2"), "reads schema 1")
        self.rejects(VALID + "[agents.providers.openrouter]\n", "unknown key providers")
        self.rejects(
            VALID.replace("timeout = 10", "timeout = 10\nretries = 2"), "unknown key retries"
        )

    def test_bad_names_and_types_fail(self):
        self.rejects(VALID.replace("[agents.mcp.docs]", "[agents.mcp.Docs]"), "lowercase letters")
        self.rejects(
            VALID.replace('args = ["-y", "@example/docs-mcp"]', 'args = "-y"'), "expected list"
        )
        self.rejects(VALID.replace("timeout = 10", "timeout = 0"), "positive number")

    def test_transport_keys_must_match_the_transport(self):
        self.rejects(VALID.replace('command = "npx"\n', ""), "stdio transport needs command")
        self.rejects(
            VALID.replace('url = "https://mcp.example.com/issues"\n', ""),
            "http transport needs url",
        )
        self.rejects(
            VALID.replace(
                'bearer_token_env = "ISSUES_TOKEN"',
                'bearer_token_env = "ISSUES_TOKEN"\ncommand = "x"',
            ),
            "cannot apply to the http transport",
        )
        self.rejects(VALID.replace('transport = "stdio"', 'transport = "sse"'), "stdio or http")

    def test_secret_values_fail_and_variable_names_pass(self):
        self.rejects(
            VALID.replace('DOCS_REGION = "us"', 'DOCS_API_KEY = "abcd1234efgh"'),
            "looks like a secret",
        )
        self.rejects(
            VALID.replace('pass_env = ["DOCS_API_KEY"]', 'pass_env = ["DOCS-KEY"]'), "variable name"
        )

    def test_paths_must_be_tracked_relative_and_hooks_executable(self):
        self.rejects(VALID.replace("agents/rules/style.md", "../style.md"), "relative path")
        self.rejects(
            VALID.replace("agents/rules/style.md", "agents/rules/missing.md"), "Git tracks"
        )
        command("git", "-C", str(self.checkout), "add", "--chmod=-x", "agents/hooks/guard.sh")
        self.rejects(VALID, "executable in Git")

    def test_domains_must_be_declared_by_the_content(self):
        self.rejects(
            VALID.replace(
                'domains = ["work"]\n[agents.rules', 'domains = ["client"]\n[agents.rules'
            ),
            "does not declare domain client",
        )

    def test_skill_limits_need_a_tracked_skill_folder(self):
        self.rejects(
            VALID.replace("[agents.skills.release-notes]", "[agents.skills.absent]"),
            "no tracked skill folder",
        )

    def test_hook_events_and_tool_classes_are_fixed(self):
        self.rejects(
            VALID.replace('event = "before-tool"', 'event = "on-save"'), "event must be one of"
        )
        self.rejects(VALID.replace('tools = ["shell"]', 'tools = ["web"]'), "tools accepts")
        self.rejects(
            VALID.replace('event = "before-tool"', 'event = "stop"'), "only to before-tool"
        )

    def test_unknown_agents_warn_and_are_skipped(self):
        table = self.load(
            VALID.replace('agents = ["claude", "codex"]', 'agents = ["claude", "nova"]')
        )
        self.assertEqual(
            table.warnings,
            ["agents.connectors.calendar: unknown agent nova; this manager skips it."],
        )

    def test_selection_follows_domain_agents_and_the_exception_list(self):
        table = self.load(VALID)
        work = {"domain": "work"}
        keys = [item.key for item in agent_table.selected(table, work, "codex")]
        self.assertIn("mcp.issues", keys)
        self.assertIn("connectors.calendar", keys)
        self.assertNotIn("plugins.review-kit", keys)
        self.assertNotIn(
            "mcp.issues", [i.key for i in agent_table.selected(table, {"domain": "core"})]
        )
        self.assertNotIn(
            "connectors.calendar", [i.key for i in agent_table.selected(table, work, "gemini")]
        )
        self.assertIn(
            "plugins.review-kit", [i.key for i in agent_table.selected(table, work, "claude")]
        )
        off = dict(work, agent_items={"mcp.docs": "off", "mcp.issues": "local"})
        keys = [item.key for item in agent_table.selected(table, off)]
        self.assertNotIn("mcp.docs", keys)
        self.assertIn("mcp.issues", keys)

    def test_skill_folders_are_direct_children_with_skill_files(self):
        self.assertEqual(agent_table.skills(self.checkout), ["release-notes"])


class CatalogTests(unittest.TestCase):
    def test_antigravity_and_grok_are_detected_but_not_rendered(self):
        for name in ("antigravity", "grok"):
            self.assertTrue(CATALOG[name]["supported"], name)
            self.assertFalse(CATALOG[name]["render"], name)
            self.assertIn("tested install", CATALOG[name]["render_reason"])
        self.assertEqual(CATALOG["antigravity"]["command"], "agy")

    def test_rendered_agents_have_render_on(self):
        for name in ("claude", "codex", "gemini", "opencode", "pi"):
            self.assertTrue(CATALOG[name]["render"], name)

    def test_pi_detects_its_renamed_package(self):
        self.assertIn("@earendil-works/pi-coding-agent", CATALOG["pi"]["mise_packages"])

    def test_opencode_lists_the_settings_file_that_shadows_fleet_entries(self):
        self.assertIn(".config/opencode/opencode.jsonc", CATALOG["opencode"]["shadow_paths"])


class AgentTableCommandTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli
    commit_content = test_domains.ContentDomainCommandTests.commit_content

    def test_an_invalid_table_stops_update_before_backup(self):
        self.enroll()
        backups = sorted((self.home / "fleet-backups").iterdir())
        manifest = (self.content / "dotfleet.toml").read_text()
        self.commit_content(
            {"dotfleet.toml": manifest + "[agents]\nschema = 1\n[agents.mcp.Docs]\n"},
            "feat: add a malformed agents table",
        )
        output = self.cli("update", expected=1)
        self.assertIn("Invalid agents table at agents.mcp.Docs", output)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)


if __name__ == "__main__":
    unittest.main()
