"""Keep the terminal scan short and make source defaults match recommendations.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_scan.py --help.
"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import enrollment
import scan_view
import test_manager as helpers
import test_wizard as wizard_helpers
from common import read_toml

ROOT = Path(__file__).resolve().parents[1]


class ScanTests(unittest.TestCase):
    def fixture(self, home):
        files = [
            ".zshrc",
            ".bashrc",
            ".gitconfig",
            ".config/mise/config.toml",
            ".config/starship.toml",
            ".install.log",
            ".zsh_history",
            ".zcompdump-host",
            ".config/telemetry/events.json",
            ".config/app/cache/state.json",
            ".config/app/sessions/one.json",
            ".config/app/receipts/install.json",
            ".config/app/access-token.json",
            ".config/app/private-key.pem",
        ]
        for name in files:
            target = home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# safe fixture\n")

    def test_scan_golden_excludes_noise_and_keeps_inventory_in_record(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            self.fixture(home)
            tools = {
                "node": [{"version": "24.14.1"}],
                "python": [{"version": "3.13.0"}],
                "other": [{"version": "1.2.3"}],
            }
            record = {}
            output = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                patch.object(enrollment, "installed", return_value=tools),
                patch.object(enrollment, "detected_agents", return_value={"codex": {}}),
            ):
                found, excluded = enrollment.welcome(
                    home, [], {"pins": {"node": "24.14.1"}}, record
                )
            record["os"] = "Linux"
            rendered = "\n".join(scan_view.lines(record, {"node": "24.14.1"}, ["codex"])) + "\n"
            self.assertEqual(rendered, (ROOT / "tests/fixtures/scan.txt").read_text())
            self.assertLess(len(rendered.splitlines()), 25)
            self.assertEqual(record["tools"], tools)
            self.assertEqual(len(record["candidates"]), 5)
            for name in [
                ".install.log",
                ".zsh_history",
                ".zcompdump-host",
                "telemetry",
                "sessions",
                "receipts",
                "cache",
            ]:
                self.assertFalse(any(name in path for path in found))
                self.assertNotIn(name, rendered)
            for name in excluded:
                self.assertNotIn(name, rendered)
            self.assertNotIn("1.2.3", rendered)
            self.assertIn("Sensitive files are excluded.", rendered)

    def test_scan_summarizes_each_category_with_a_few_examples(self):
        browser = [f".config/google-chrome/Default/Cache{i}/data" for i in range(3000)]
        record = {
            "os": "Linux",
            "enrollment": [],
            "tools": {},
            "excluded": {},
            "candidates": [".zshrc"]
            + [f".config/tool{i:02}/config.toml" for i in range(20)]
            + browser,
        }
        rendered = scan_view.lines(record, {}, [])
        self.assertIn("Home configuration candidates: 3,021 files", rendered)
        self.assertIn(
            "  App settings     20 files  ~/.config/tool00/, ~/.config/tool01/,"
            " ~/.config/tool02/, + 17 more",
            rendered,
        )
        self.assertIn("  Browser data  3,000 files  ~/.config/google-chrome/", rendered)
        self.assertIn("  Shell              1 file  ~/.zshrc", rendered)
        self.assertNotIn("Cache", "\n".join(rendered))
        self.assertLess(len(rendered), 15)

    def test_default_always_matches_recommendation(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for found, excluded, expected in [
                ([], {}, "example"),
                ([".zshrc"], {}, "adopt"),
                ([".token"], {".token": "credential filename"}, "example"),
            ]:
                choices, default, detail = enrollment.door_options(home, {}, found, excluded)
                self.assertEqual(default, expected)
                self.assertIn(default, choices)
                self.assertIn("(Recommended.)", detail.split(f"  {default} ", 1)[1].splitlines()[0])
                self.assertIn(f"Press Enter alone to choose {default}.", detail)
                self.assertNotIn("door", detail)
            (home / ".config/fleet").mkdir(parents=True)
            (home / ".config/fleet/profile.toml").touch()
            for prior in ["repo", "example", "adopt"]:
                profile = {"content_repo": "https://example.invalid/content", "source_door": prior}
                _, default, detail = enrollment.door_options(home, profile, [".zshrc"], {})
                self.assertEqual(default, "repo")
                self.assertIn("(Recommended.)", detail.split("  repo ", 1)[1].splitlines()[0])
                self.assertIn("Current source: https://example.invalid/content", detail)


class ScanIntegrationTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    answers = wizard_helpers.WizardTests.answers
    defaults_origin = wizard_helpers.WizardTests.defaults_origin

    def test_enrolled_enter_keeps_content_and_records_scan(self):
        self.defaults_origin()
        self.terminal(self.answers(), flags=[], auto_wizard=False)
        before = read_toml(self.home / ".config/fleet/profile.toml")
        answers = self.answers()
        answers[0] = (b"Content source [repo]:", b"\n")
        answers.insert(1, (b"Content repository [", b"\n"))
        output = self.terminal(answers, flags=["--configure"], auto_wizard=False)
        self.assertIn("Press Enter alone to choose repo.", output)
        self.assertNotIn("Press Enter alone to choose adopt.", output)
        after = read_toml(self.home / ".config/fleet/profile.toml")
        self.assertEqual(before["content_repo"], after["content_repo"])
        record = json.loads(
            sorted((self.home / "fleet-backups").glob("*/RUN.json"))[-1].read_text()
        )
        self.assertIn("tools", record["scan"])
        self.assertIn(".zshrc", record["scan"]["candidates"])


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
