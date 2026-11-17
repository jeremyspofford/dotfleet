"""Keep consolidated commands, saved tool data, and local status aligned.

Usage: python3 -m unittest discover -s tests -v.
"""

import argparse
import contextlib
import io
import re
import unittest
from unittest.mock import patch

import command
from test_style import command_goldens


class ConsolidationTests(unittest.TestCase):
    def output(self, arguments):
        with (
            patch.object(command, "current_command", return_value="fleet"),
            contextlib.redirect_stderr(io.StringIO()) as error,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = command.main(arguments)
        return result, output.getvalue() + error.getvalue()

    def test_removed_forms_name_their_successor_without_side_effects(self):
        for old, successor in (
            (["backup"], "backups create"),
            (["rollback"], "backups restore"),
            (["pins", "set", "--tool", "node"], "tools"),
            (["profile", "show"], "status"),
            (["content", "show"], "content"),
        ):
            for arguments in (old, ["help", *old[:1]] if old[0] != "content" else old):
                with self.subTest(arguments=arguments), patch.object(command, "profile") as profile:
                    result, text = self.output(arguments)
                    self.assertEqual(result, 2)
                    self.assertEqual(text, "Error: Use fleet " + successor + ".\n")
                    profile.assert_not_called()

    def test_subject_tool_and_restore_group_use_existing_engine_fields(self):
        parser = command.parser()
        for arguments, tool, version in (
            (["tools", "set", "node", "--version", "26.13.1"], "node", "26.13.1"),
            (["tools", "remove", "node"], "node", None),
        ):
            args = command.command_arguments.normalize(parser.parse_args(arguments), parser)
            self.assertEqual(args.tool, tool)
            self.assertEqual(args.pin, tool + "@" + version if version else tool)
        args = parser.parse_args(["backups", "restore", "--backup", "saved"])
        self.assertEqual(args.backup, "saved")
        self.assertEqual(parser.parse_args(["content"]).command, "content")
        names = next(
            item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
        ).choices
        self.assertEqual(len(names), 18)
        self.assertTrue({"backups", "tools", "status", "content"}.issubset(names))
        self.assertFalse({"backup", "rollback", "pins", "profile"}.intersection(names))

    def test_installer_tool_flag_keeps_legacy_data_and_hides_the_old_flag(self):
        import manager

        args = manager.parse_arguments(["--tool", "node=26.13.1"])
        old = manager.parse_arguments(["--pin", "node=26.13.1"])
        self.assertEqual(args.pin, [("node", "26.13.1")])
        self.assertEqual(old.pin, args.pin)
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            manager.parse_arguments(["--help"])
        self.assertNotIn("--pin", output.getvalue())
        self.assertIn("--tool", output.getvalue())

    def test_status_contains_agents_and_cached_updates_inside_its_fields(self):
        status = command_goldens()["status"]
        self.assertIn("  Tool node — 26.13.1 · installed: 26.3.1, 26.13.1\n", status)
        self.assertIn("  Agents            codex off\n", status)
        self.assertIn("  Updates           none (checked 09:02)\n", status)
        self.assertNotIn("\nUpdates:", status)
        self.assertIn("~/.config/fleet/profile.toml", status)
        self.assertFalse(re.search(r"\b(?:pin|pins|pinned|unpin)\b", status, re.I))


if __name__ == "__main__":
    unittest.main()
