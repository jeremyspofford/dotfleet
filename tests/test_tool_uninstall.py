"""Check extra-version removal, typed decisions, and command-specific argument errors.

Usage: python3 -m unittest discover -s tests -v.
"""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

import command as cli
from common import read_toml, toml
import test_command as helpers
from test_manager import ROOT


class ToolTests(unittest.TestCase):
    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli
    terminal = helpers.CommandTests.terminal
    record = helpers.CommandTests.record

    def prepare(self):
        self.enroll()
        self.profile = self.home / ".fleet/profile.toml"
        saved = read_toml(self.profile)
        saved["pins"] = {"node": "26.13.1"}
        self.profile.write_text(toml(saved))
        items = []
        for version in ("26.3.1", "26.13.1"):
            path = self.home / ".local/share/mise/installs/node" / version
            path.mkdir(parents=True)
            (path / "node").write_text("fixture executable")
            items.append(dict(version=version, install_path=str(path)))
        (self.home / "installed.json").write_text(json.dumps({"node": items}))
        mise = self.home / ".local/bin/mise"
        mise.write_text("""#!/usr/bin/env python3
import json, shutil, sys
from pathlib import Path
store = Path.home() / 'installed.json'
items = json.loads(store.read_text())
if sys.argv[1] == '--version': print('2026.8.5')
elif sys.argv[1] == 'ls': print(json.dumps(items))
elif sys.argv[1] == 'uninstall':
    assert '--yes' in sys.argv and '--cd' in sys.argv
    tool, version = sys.argv[2].rsplit('@', 1)
    selected = next(item for item in items[tool] if item['version'] == version)
    shutil.rmtree(selected['install_path'])
    items[tool].remove(selected)
    store.write_text(json.dumps(items))
    (Path.home() / 'uninstalled.txt').write_text(sys.argv[2])
else: sys.exit(1)
""")
        mise.chmod(0o700)

    def test_extra_version_requires_exact_confirm_and_writes_software_review(self):
        self.prepare()
        before = self.profile.read_bytes()
        args = ["tools", "uninstall", "node", "--version", "26.3.1"]
        output = self.terminal(args, [(b"Confirm [cancel]:", b"cancel\n")])
        self.assertFalse((self.home / "uninstalled.txt").exists())
        self.assertIn("cancelled", output)
        output = self.terminal(
            args, [(b"Confirm [cancel]:", b"yes\n"), (b"Confirm [cancel]:", b"uninstall\n")]
        )
        self.assertEqual((self.home / "uninstalled.txt").read_text(), "node@26.3.1")
        self.assertTrue((self.home / ".local/share/mise/installs/node/26.13.1").is_dir())
        self.assertFalse((self.home / ".local/share/mise/installs/node/26.3.1").exists())
        self.assertEqual(self.profile.read_bytes(), before)
        import run_index

        entry = run_index.entries(self.home)[0]
        self.assertEqual((entry["command"], entry["result"]), ("tools uninstall", "SUCCESS"))
        self.assertIn("1 · Software — 1 removed.", output)
        record, folder = self.record()
        self.assertEqual(record["completed_software"], ["node"])
        self.assertEqual(record["software"][0]["action"], "removed")
        self.assertTrue((folder / "home/.fleet/profile.toml").is_file())

    def test_recorded_version_is_refused_before_backup_or_mise_mutation(self):
        self.prepare()
        before = set((self.home / ".fleet/backups").iterdir())
        output = self.cli("tools", "uninstall", "node", "--version", "26.13.1", expected=1)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("~/.fleet/profile.toml", output)
        self.assertIn("tools remove node", output)
        self.assertIn("tools set node --version VERSION", output)
        self.assertFalse((self.home / "uninstalled.txt").exists())
        self.assertEqual(set((self.home / ".fleet/backups").iterdir()), before)

    def test_list_labels_extras_and_recorded_versions(self):
        self.prepare()
        output = self.cli("tools", "list")
        self.assertIn("node  26.13.1  installed · also 26.3.1\n", output)


class ArgumentErrorTests(unittest.TestCase):
    def test_bad_flags_use_failing_command_usage_without_argparse_banner(self):
        for args, label in (
            (["tools", "remove", "node", "--version", "26.3.1"], "tools remove"),
            (["backups", "list", "--wrong"], "backups list"),
        ):
            with (
                patch.object(cli, "current_command", return_value="fleet"),
                contextlib.redirect_stderr(io.StringIO()) as errors,
                self.assertRaises(SystemExit) as stopped,
            ):
                cli.main(args)
            output = errors.getvalue()
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn("Usage: fleet " + label, output)
            self.assertIn("Error: Does not accept", output)
            self.assertIn("accepts ", output)
            self.assertNotIn("Usage: fleet [-h]", output)
            self.assertNotIn("unrecognized arguments", output)
            self.assertNotIn("error:", output)
            path = ROOT / "tests/fixtures/views" / (label + " error.txt")
            self.assertEqual(output, path.read_text())

    def test_missing_values_and_bad_choices_are_plain_and_subcommand_specific(self):
        for args, expected in (
            (["tools", "set", "node"], "Supply --version"),
            (["backups", "delete", "--keep", "many"], "Use a whole number for --keep"),
            (["backups", "delete", "--keep", "2", "--all"], "Choose either"),
            (["groups", "set", "--name", "helpers", "--state", "maybe"], "Unknown choice"),
            (["backups", "restore", "--backup"], "Give a value for --backup"),
        ):
            with (
                contextlib.redirect_stderr(io.StringIO()) as output,
                self.assertRaises(SystemExit) as stopped,
            ):
                cli.main(args)
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("unrecognized arguments", output.getvalue())


if __name__ == "__main__":
    unittest.main()
