"""Detect installed assistants and ask one explicit on/off question per agent.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_agent_detection.py --help.
"""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_detection
import enrollment
from test_manager import command

ROOT = Path(__file__).resolve().parents[1]


class AgentDetectionTests(unittest.TestCase):
    def test_each_detection_source_and_mise_package_names(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / ".codex").mkdir()
            tools = {
                "npm:@google/gemini-cli": [{"version": "1.2.3"}],
                "opencode": [{"version": "1.2.0"}],
                "npm:@mariozechner/pi-coding-agent": [{"version": "0.40.0"}],
                "grok": [],
            }
            with patch.object(
                agent_detection.shutil,
                "which",
                side_effect=lambda name: "/bin/claude" if name == "claude" else None,
            ):
                detected = agent_detection.detected(home, enrollment.agent_manifest(), tools)
            self.assertEqual(set(detected), {"claude", "codex", "gemini", "opencode", "pi"})
            with patch.object(agent_detection.shutil, "which", return_value=None):
                self.assertIn(
                    "gemini",
                    agent_detection.detected(
                        home, enrollment.agent_manifest(), {"gemini-cli": [{"version": "1.0.0"}]}
                    ),
                )

    def test_agents_golden_has_one_toggle_per_agent_and_enter_keeps_state(self):
        detected = {
            name: enrollment.agent_manifest()[name] for name in ["claude", "codex", "gemini"]
        }
        profile = {"agents": {"claude": True, "codex": False, "grok": True}}
        output = io.StringIO()
        prompts = []

        def ask(name, default, choices, heading, detail):
            prompts.append((name, default, choices))
            print("\n" + heading + "\n" + detail + f"\n{name} [{default}]:")
            return default

        with (
            contextlib.redirect_stdout(output),
            patch.object(enrollment, "detected_agents", return_value=detected),
            patch.object(enrollment.manager, "ask", side_effect=ask),
        ):
            enrollment.choose_agents(Path("/fixture"), profile)
        self.assertEqual(output.getvalue(), (ROOT / "tests/fixtures/agents.txt").read_text())
        self.assertEqual(
            prompts,
            [
                ("claude", "on", ["on", "off"]),
                ("codex", "off", ["on", "off"]),
                ("gemini", "off", ["on", "off"]),
            ],
        )
        self.assertEqual(
            profile["agents"], {"claude": True, "codex": False, "gemini": False, "grok": True}
        )
        self.assertNotIn("commas", output.getvalue())

    def agents_step(self, files, manifest, packages=None):
        """Run the Agents step on a git checkout that tracks FILES; return its output."""
        detected = {"claude": enrollment.agent_manifest()["claude"]}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            checkout = home / ".fleet/dotfiles/default"
            checkout.mkdir(parents=True)
            command("git", "init", "-q", str(checkout))
            for name in files:
                (checkout / name).parent.mkdir(parents=True, exist_ok=True)
                (checkout / name).write_text("fixture\n")
            if files:
                command("git", "-C", str(checkout), "add", *files)
            patches = [
                patch.object(enrollment, "detected_agents", return_value=detected),
                patch.object(
                    enrollment.manager,
                    "ask",
                    side_effect=lambda name, default, choices, heading, detail: print(
                        heading + "\n" + detail
                    ),
                ),
            ]
            if packages is not None:
                patches.append(
                    patch.object(enrollment, "marketplace_packages", return_value=packages)
                )
            with contextlib.ExitStack() as stack, contextlib.redirect_stdout(output):
                for active in patches:
                    stack.enter_context(active)
                enrollment.choose_agents(home, {}, checkout, manifest)
        return output.getvalue()

    def test_agents_step_says_when_content_declares_no_agent_configuration(self):
        output = self.agents_step([], {"groups": {}})
        self.assertIn(
            "This content declares no agent configuration yet. On or off changes nothing today.\n"
            "On means this machine receives agent configuration at a later update, "
            "when you add some.\n",
            output,
        )
        self.assertNotRegex(output, r"declares \d")
        self.assertLess(output.index("no agent configuration"), output.index("Agent: claude"))

    def test_agents_step_counts_declared_agent_configuration(self):
        manifest = {
            "agents": {
                "schema": 1,
                "mcp": {"docs": {"description": "Docs", "transport": "stdio"}},
                "rules": {
                    "base": {"description": "Base", "file": "rules/base.md"},
                    "tests": {"description": "Tests", "file": "rules/tests.md"},
                },
            }
        }
        files = [".agents/skills/review/SKILL.md", "rules/base.md", "rules/tests.md"]
        output = self.agents_step(files, manifest)
        self.assertIn("This content declares 1 skill, 1 MCP entry, and 2 rules.\n", output)
        self.assertNotIn("no agent configuration", output)
        self.assertNotIn("changes nothing", output)
        output = self.agents_step(files, manifest, packages=2)
        self.assertIn(
            "This content declares 1 skill, 1 MCP entry, and 2 rules. "
            "This machine also takes 2 marketplace packages.\n",
            output,
        )

    def test_marketplace_packages_alone_are_not_called_empty(self):
        output = self.agents_step([], {}, packages=1)
        self.assertIn(
            "This content declares no agent configuration. This machine takes 1 marketplace package.",
            output,
        )
        self.assertNotIn("changes nothing", output)

    def test_mise_only_agent_is_detected_and_rendered_without_path_command(self):
        import agent_deploy
        import agent_table

        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            checkout = home / ".fleet/dotfiles/default"
            checkout.mkdir(parents=True)
            command("git", "init", "-q", str(checkout))
            tools = {"gemini-cli": [{"version": "1.0.0"}]}
            catalog = enrollment.agent_manifest()
            with (
                patch.object(agent_detection, "inventory", return_value=tools),
                patch.object(agent_detection.shutil, "which", return_value=None),
            ):
                table = agent_table.load(checkout, {}, catalog)
                result = agent_deploy.plan(
                    home, checkout, {"agents": {"gemini": True}}, table, catalog
                )
                agent_deploy.apply(home, result)
            self.assertTrue((home / ".gemini/extensions/fleet").is_symlink())
            self.assertFalse((home / ".gemini/settings.json").exists())


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    argument_parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        usage()
