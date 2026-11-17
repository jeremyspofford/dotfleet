"""Enroll with an agents table and check pointers, entries, skills, and uninstall.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_module.py --help.
"""

import json
import os
import subprocess
import tomllib
import unittest

import agent_deploy
import test_command
import test_domains
import test_manager as helpers
from test_manager import command

AGENTS = """[agents]
schema = 1
[agents.skills.personal-notes]
domains = ["personal"]
[agents.mcp.docs]
description = "Search the documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
"""
SKILL = "---\nname: {name}\ndescription: Example skill.\n---\nBody\n"


class AgentModuleTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli
    terminal = test_command.CommandTests.terminal
    commit_content = test_domains.ContentDomainCommandTests.commit_content

    def add_agents(self):
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text('model = "o3"\n')
        manifest = (self.content / "dotfleet.toml").read_text()
        self.commit_content(
            {
                "dotfleet.toml": manifest + AGENTS,
                ".agents/skills/notes/SKILL.md": SKILL.format(name="notes"),
                ".agents/skills/personal-notes/SKILL.md": SKILL.format(name="personal-notes"),
            },
            "feat: declare agent configuration",
        )
        return command(
            "bash",
            str(self.origin / "install.sh"),
            "--defaults",
            "--agents",
            "claude,codex",
            env=self.env,
            cwd=self.home,
        )

    def test_enrollment_deploys_the_tree_pointers_entries_and_skills(self):
        self.enroll()
        output = self.add_agents()
        self.assertIn(
            "codex: sign in to calendar: run codex mcp login calendar", " ".join(output.split())
        )
        tree = self.home / agent_deploy.TREE
        self.assertEqual(os.readlink(self.home / ".claude/skills/fleet"), str(tree / "claude"))
        servers = json.loads((tree / "claude/.mcp.json").read_text())["mcpServers"]
        self.assertEqual(set(servers), {"docs", "calendar"})
        self.assertTrue((self.home / ".agents/skills/notes/SKILL.md").is_file())
        self.assertFalse((self.home / ".agents/skills/personal-notes").exists())
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertEqual(config["model"], "o3")
        self.assertEqual(config["mcp_servers"]["docs"]["command"], "npx")
        self.assertIn("~/.codex/config.toml", output)
        self.assertEqual(self.cli("status").count("Drift             none"), 1)

        self.cli("domain", "set", "--name", "personal")
        self.assertTrue((self.home / ".agents/skills/personal-notes/SKILL.md").is_file())

        output = self.terminal(
            ["uninstall"],
            [(b"Oldest backup [keep]:", b"keep\n"), (b"Confirm [cancel]:", b"uninstall\n")],
        )
        self.assertIn("uninstall", output.lower())
        self.assertFalse((self.home / ".claude/skills/fleet").is_symlink())
        self.assertFalse(tree.exists())
        self.assertEqual(
            tomllib.loads((self.home / ".codex/config.toml").read_text()), {"model": "o3"}
        )

    def test_a_hand_written_value_stops_enrollment_before_backup(self):
        self.enroll()
        (self.home / ".codex").mkdir()
        (self.home / ".codex/config.toml").write_text('[mcp_servers.docs]\ncommand = "my-docs"\n')
        backups = sorted((self.home / "fleet-backups").iterdir())
        manifest = (self.content / "dotfleet.toml").read_text()
        table = AGENTS.replace('[agents.skills.personal-notes]\ndomains = ["personal"]\n', "")
        self.commit_content({"dotfleet.toml": manifest + table}, "feat: declare agents")
        (self.home / ".claude").mkdir()
        process = subprocess.run(
            ["bash", str(self.origin / "install.sh"), "--defaults", "--agents", "codex"],
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
        )
        result = " ".join((process.stdout + process.stderr).split())
        self.assertEqual(process.returncode, 1, result)
        self.assertIn("mcp.docs in ~/.codex/config.toml differs from the rendered value", result)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)
        self.assertIn('command = "my-docs"', (self.home / ".codex/config.toml").read_text())

    def test_declared_plugins_install_through_the_agent_and_show_as_software(self):
        self.enroll()
        log = self.base / "claude.log"
        fake = self.base / "tools/claude"
        fake.write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
        fake.chmod(0o755)
        (self.home / ".claude").mkdir()
        manifest = (self.content / "dotfleet.toml").read_text()
        plugin = (
            "[agents]\nschema = 1\n[agents.plugins.review-kit]\n"
            'description = "Review commands."\n'
            '[agents.plugins.review-kit.sources]\nclaude = { id = "review-kit@example" }\n'
        )
        self.commit_content({"dotfleet.toml": manifest + plugin}, "feat: declare a plugin")
        output = command(
            "bash",
            str(self.origin / "install.sh"),
            "--defaults",
            "--agents",
            "claude",
            env=self.env,
            cwd=self.home,
        )
        self.assertIn("1 · Software — 1 installed.", output)
        self.assertIn("claude plugin review-kit", output)
        self.assertEqual(log.read_text(), "plugin install review-kit@example --scope user\n")
        record = json.loads((self.home / agent_deploy.INVENTORY).read_text())
        self.assertEqual(record["plugins"][0]["id"], "review-kit@example")

    def test_exceptions_turn_items_off_or_local_and_drift_is_reported(self):
        self.enroll()
        self.add_agents()
        self.assertIn("mcp.docs", self.cli("agents", "list"))
        refused = self.cli("agents", "set", "--name", "mcp.absent", "--state", "off", expected=1)
        self.assertIn("Unknown agent item mcp.absent", refused)
        self.assertIn(
            "choose on or off",
            self.cli("agents", "set", "--name", "skills.notes", "--state", "local", expected=1),
        )

        output = self.cli("agents", "set", "--name", "mcp.docs", "--state", "off")
        self.assert_review_golden("agents", output)
        profile = tomllib.loads((self.home / ".config/fleet/profile.toml").read_text())
        self.assertEqual(profile["agent_items"], {"mcp.docs": "off"})
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertNotIn("docs", config["mcp_servers"])

        self.cli("agents", "set", "--name", "mcp.docs", "--state", "on")
        config_path = self.home / ".codex/config.toml"
        config_path.write_text(config_path.read_text().replace('"npx"', '"my-npx"'))
        self.assertIn("present; run dotfleet diff", self.cli("status"))
        self.assertIn("FAIL", self.cli("doctor", expected=1))
        stopped = self.cli("update", expected=1)
        self.assertIn("mcp.docs in ~/.codex/config.toml changed since Fleet wrote it", stopped)
        self.assertIn("agents set --name ITEM --state local", " ".join(stopped.split()))

        self.cli("agents", "set", "--name", "mcp.docs", "--state", "local")
        self.assertIn('"my-npx"', config_path.read_text())
        self.assertIn("Drift             none", self.cli("status"))
        self.assertIn("mcp.docs", self.cli("agents", "list"))
        self.assertIn("local value on this machine", self.cli("agents", "list"))

    assert_review_golden = test_command.CommandTests.assert_review_golden
    assert_golden = test_command.CommandTests.assert_golden
    record = test_command.CommandTests.record


if __name__ == "__main__":
    unittest.main()
