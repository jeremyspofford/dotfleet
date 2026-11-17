"""Owned stores have human commands. Secret values never reach the terminal.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_owned_views.py --help.
"""

import json
from pathlib import Path
import socket
import unittest

import registry
from common import read_toml, toml
import test_command as helpers

ROOT = Path(__file__).resolve().parents[1]


class OwnedViewTests(unittest.TestCase):
    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli
    record = helpers.CommandTests.record

    def test_new_commands_are_human_and_read_only(self):
        self.enroll()
        profile = self.home / ".config/fleet/profile.toml"
        before = profile.read_bytes()
        backups = sorted((self.home / "fleet-backups").iterdir())
        output = self.cli("status")
        for value in [
            "core",
            "chosen",
            "Command Example",
            "command@example.invalid",
            "~/.config/fleet/profile.toml",
        ]:
            self.assertIn(value, output)
        self.assertIn("SUCCESS", self.cli("backups", "list"))
        self.assertIn("bytes", self.cli("backups", "list"))
        self.assertIn("clean", self.cli("content"))
        self.assertIn("0 behind", self.cli("content"))
        self.assertIn("~/dotfiles", self.cli("content"))
        self.assertIn("this machine", self.cli("machines", "export"))
        self.assertEqual(profile.read_bytes(), before)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)
        for name in ["status", "backups", "content", "secrets"]:
            self.assertIn("Usage:", self.cli(name, "--help"))

    def test_secrets_never_print_values_or_execute_assignments(self):
        self.enroll()
        manifest = self.home / "dotfiles/dotfleet.toml"
        manifest.write_text(
            manifest.read_text() + "\n[secrets]\nTOKEN = {}\nEMPTY = {}\nUNSET = {}\n"
        )
        secret = "do-not-print-any-of-this-private-value"
        marker = self.home / "should-not-exist"
        (self.home / ".secrets").write_text(
            f"export TOKEN='{secret} $(touch {marker})'\nexport EMPTY=''\n"
        )
        output = self.cli("secrets", "list")
        self.assertIn("TOKEN", output)
        self.assertIn("set", output)
        self.assertIn("unset", output)
        for private in [secret, "do-not-print", str(len(secret))]:
            self.assertNotIn(private, output)
        self.assertFalse(marker.exists())
        (self.home / ".secrets").write_text("export TOKEN='unterminated-private")
        failed = self.cli("secrets", "list", expected=1)
        self.assertNotIn("unterminated-private", failed)

    def test_machine_export_and_stale_removal_keep_other_records(self):
        self.enroll()
        path = self.home / registry.REGISTRY
        data = registry.load(self.home)
        current = data["machines"][socket.gethostname()]
        data["machines"]["old-hub"] = dict(current, hostname="old-hub")
        data["machines"]["other-hub"] = dict(current, hostname="other-hub")
        path.write_text(registry.serialize(data))
        exported = self.base / "export.toml"
        output = self.cli("machines", "export", "--output", str(exported))
        self.assertNotIn("old-hub", output)
        self.assertNotIn("[machines.", output)
        self.assertEqual(set(read_toml(exported)["machines"]), {socket.gethostname()})
        self.assertEqual(exported.stat().st_mode & 0o777, 0o600)
        self.assertIn(
            "already exists", self.cli("machines", "export", "--output", str(exported), expected=1)
        )
        output = self.cli("machines", "remove", "--name", "old-hub")
        self.assertIn("~/.local/share/fleet/registry.toml", output)
        self.assertEqual(
            set(registry.load(self.home)["machines"]), {socket.gethostname(), "other-hub"}
        )
        record, backup = self.record()
        self.assertEqual(record["result"], "SUCCESS")
        self.assertIn("old-hub", read_toml(backup / "home" / registry.REGISTRY)["machines"])
        self.assertIn(
            "active", self.cli("machines", "remove", "--name", socket.gethostname(), expected=1)
        )


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
