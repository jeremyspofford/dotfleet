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

import agent_configs
import agent_detection
import enrollment

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

    def test_mise_only_agent_generates_settings_without_path_command(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            tools = {"gemini-cli": [{"version": "1.0.0"}]}
            with (
                patch.object(agent_detection, "inventory", return_value=tools),
                patch.object(agent_detection.shutil, "which", return_value=None),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                planned = agent_configs.generate(
                    home, "linux", True, profile={"agents": {"gemini": True}}
                )
                self.assertIn(".gemini/settings.json", planned)
                agent_configs.generate(home, "linux", False, profile={"agents": {"gemini": True}})
            self.assertEqual((home / ".gemini/settings.json").read_text(), "{}\n")


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
