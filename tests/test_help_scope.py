"""Keep help short, raw, alphabetized, and specific to each command.

Usage: python3 -m unittest discover -s tests -v.
"""

import argparse
import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import command
import command_arguments

ROOT = Path(__file__).resolve().parents[1]


def parsers(parser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                yield from parsers(child)


class HelpScopeTests(unittest.TestCase):
    def setUp(self):
        with patch.object(command, "current_command", return_value="fleet"):
            self.parser = command.parser()
        self.screens = {parser.prog: parser for parser in parsers(self.parser)}

    def test_top_and_two_command_goldens_keep_authored_lines(self):
        for name, path in (
            ("fleet", "views/help.txt"),
            ("fleet check", "help/check.txt"),
            ("fleet backups create", "help/backups-create.txt"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.screens[name].format_help(), (ROOT / "tests/fixtures" / path).read_text()
                )
        top = self.parser.format_help()
        for removed in ("Exit codes:", "Examples:", "Profile:", "First install", "Glossary"):
            self.assertNotIn(removed, top)
        self.assertTrue(top.rstrip().endswith("show manager and content versions, then exit"))
        check = self.screens["fleet check"].format_help()
        for line in (
            "  0  Manager and content are current.",
            "  1  Updates are available.",
            "  2  Could not check updates, or invalid arguments.",
            "  fleet check",
        ):
            self.assertIn(line, check.splitlines())

    def test_every_screen_preserves_structured_text_within_100_columns(self):
        for name, parser in self.screens.items():
            with self.subTest(name=name):
                self.assertTrue(issubclass(parser.formatter_class, argparse.RawTextHelpFormatter))
                lines = parser.format_help().splitlines()
                for authored in (parser.description or "", parser.epilog or ""):
                    for line in authored.splitlines():
                        if line:
                            self.assertIn(line, lines)
                for line in lines:
                    self.assertLessEqual(len(line), 100, line)
                if name != "fleet":
                    examples = [line for line in lines if line.startswith("  fleet ")]
                    self.assertTrue(examples, name)
                    self.assertTrue(all(";" not in line for line in examples))

    def test_readme_defines_all_removed_terms(self):
        text = (ROOT / "README.md").read_text().lower()
        for definition in (
            "profile stores machine choices",
            "tools use exact selected versions",
            "groups select optional files or alias blocks",
            "domain selects content additions",
            "drift means that managed files differ",
            "commit identifies a saved git version",
            "checkout is a local git copy",
        ):
            self.assertIn(definition, text)

    def test_backup_is_an_action_and_old_top_level_form_is_rejected(self):
        args = self.parser.parse_args(["backups", "create"])
        self.assertEqual((args.command, args.action), ("backups", "create"))
        self.assertIn("create", command_arguments.GROUPS["backups"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            self.parser.parse_args(["backup"])
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
