"""Keep help short, raw, alphabetized, and specific to each command, without exit codes.

Usage: python3 -m unittest discover -s tests -v.
"""

import argparse
import ast
import contextlib
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

import command
import command_arguments

ROOT = Path(__file__).resolve().parents[1]
# Help never shows exit codes, so match labels, statuses, and numbered or nonzero exits.
EXIT_CODES = re.compile(r"exit\s+codes?|exit\s+status|\bexits?\s+(?:\d|nonzero)", re.IGNORECASE)


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
        self.assertIn("  fleet check", check.splitlines())

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

    def test_no_command_help_mentions_exit_codes(self):
        # Every help route reaches argparse: flags, the help word, help COMMAND, and bare groups.
        routes = [["--help"], ["-h"], ["help"]]
        for name in command.DESCRIPTIONS:
            routes += [[name, "--help"], [name, "help"], ["help", name]]
            for action in command_arguments.GROUPS.get(name, {}):
                routes += [[name, action, "--help"], [name, action, "help"], ["help", name, action]]
            if name in command_arguments.GROUPS:
                routes.append([name])
        for route in routes:
            with self.subTest(route=route):
                output = io.StringIO()
                with (
                    patch.object(command, "current_command", return_value="fleet"),
                    contextlib.redirect_stdout(output),
                    contextlib.suppress(SystemExit),
                ):
                    command.main(route)
                self.assertIn("Usage: fleet", output.getvalue())
                self.assertIsNone(EXIT_CODES.search(output.getvalue()), output.getvalue())
        for name, parser in self.screens.items():
            with self.subTest(screen=name):
                self.assertIsNone(EXIT_CODES.search(parser.format_help()), name)

    def test_no_script_help_mentions_exit_codes(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), NO_COLOR="1")
        shells = [ROOT / "install.sh", *sorted((ROOT / "scripts").glob("*.sh"))]
        pythons = sorted((ROOT / "scripts").glob("*.py"))
        for path in [*shells, *pythons]:
            with self.subTest(script=path.name):
                program = ["bash"] if path.suffix == ".sh" else [sys.executable]
                result = subprocess.run(
                    [*program, str(path), "--help"],
                    env=environment,
                    text=True,
                    capture_output=True,
                    timeout=60,
                )
                output = result.stdout + result.stderr
                if path.name == "tui.py" and "ModuleNotFoundError: No module named" in output:
                    # Without Textual, check the module text that its help prints.
                    output = ast.get_docstring(ast.parse(path.read_text()))
                else:
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn("usage", output.lower())
                self.assertIsNone(EXIT_CODES.search(output), output)
        for path in sorted((ROOT / "scripts").glob("*.cjs")):
            with self.subTest(script=path.name):
                # Release helpers need npm packages to run, so read their usage text directly.
                usage = re.search(
                    r"function usage\(\) \{\s+console\.log\(`(.*?)`\);", path.read_text(), re.S
                )
                self.assertIsNotNone(usage, path.name)
                self.assertIsNone(EXIT_CODES.search(usage.group(1)), usage.group(1))

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
