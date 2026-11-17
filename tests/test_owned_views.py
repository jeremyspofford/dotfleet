"""Owned stores have human commands. Secret values never reach the terminal.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_owned_views.py --help.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import owned_views
import registry
from common import read_toml, toml
from render_run import render_view
import test_command as helpers

ROOT = Path(__file__).resolve().parents[1]


def goldens():
    home = Path("/home/example")
    profile = {
        "domain": "personal",
        "domain_chosen": True,
        "git_name": "Example Person",
        "git_email": "example@example.invalid",
        "pins": {"node": "24.14.1"},
        "groups": {"helpers": True},
        "agents": {"codex": False},
    }
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary) / "20261116-120000"
        folder.mkdir()
        (folder / "RUN.json").write_text('{"result":"SUCCESS"}')
        (folder / ".zshrc").write_text("# backup\n")
        backups = owned_views.backup_rows(home, [folder])
    with patch.dict(os.environ, {"EXAMPLE_API_TOKEN": "private-value"}):
        secrets = owned_views.secret_rows(
            home, {"secrets": {"EXAMPLE_API_TOKEN": {}, "UNSET_TOKEN": {}}}
        )
    machine = {
        "hostname": "example-hub",
        "home": str(home),
        "os": "Linux",
        "domain": "personal",
        "domain_chosen": True,
        "content_repo": "https://example.invalid/dotfiles",
        "content_commit": "1234567890abcdef1234567890abcdef12345678",
        "manager_version": "0.1.0",
        "last_run": "2026-11-16T12:00:00+00:00",
        "last_backup": str(home / "fleet-backups/20261116-120000"),
        "last_run_record": str(home / "fleet-backups/20261116-120000/RUN.json"),
        "result": "SUCCESS",
        "enrolled": True,
    }
    with (
        patch.object(
            registry,
            "load",
            return_value={"schema_version": 1, "machines": {"example-hub": machine}},
        ),
        patch.object(socket, "gethostname", return_value="example-hub"),
    ):
        exported = owned_views.export_rows(home, "0.1.0")
    with patch.object(owned_views, "run", return_value=""):
        content = owned_views.content_rows(
            home,
            {"content_repo": machine["content_repo"]},
            lambda path: "12345678 · 0 behind last fetched upstream · clean",
        )
    pairs = {
        "content": content,
        "machine export": exported,
        "profile": owned_views.profile_rows(home, profile),
        "backups": backups,
        "secrets": secrets,
    }
    return {name: render_view(name.capitalize(), rows, str(home)) for name, rows in pairs.items()}


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
        output = self.cli("profile")
        for value in [
            "core",
            "chosen",
            "Command Example",
            "command@example.invalid",
            "~/.config/fleet/profile.toml",
        ]:
            self.assertIn(value, output)
        self.assertIn("SUCCESS", self.cli("backups"))
        self.assertIn("bytes", self.cli("backups"))
        self.assertIn("clean", self.cli("content"))
        self.assertIn("0 behind", self.cli("content"))
        self.assertIn("~/dotfiles", self.cli("content"))
        self.assertIn("This machine", self.cli("machines", "export"))
        self.assertEqual(profile.read_bytes(), before)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)
        for name in ["profile", "backups", "content", "secrets"]:
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
        output = self.cli("secrets")
        self.assertIn("TOKEN", output)
        self.assertIn("set", output)
        self.assertIn("unset", output)
        for private in [secret, "do-not-print", str(len(secret))]:
            self.assertNotIn(private, output)
        self.assertFalse(marker.exists())
        (self.home / ".secrets").write_text("export TOKEN='unterminated-private")
        failed = self.cli("secrets", expected=1)
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
        output = self.cli("machines", "remove", "old-hub")
        self.assertIn("~/.local/share/fleet/registry.toml", output)
        self.assertEqual(
            set(registry.load(self.home)["machines"]), {socket.gethostname(), "other-hub"}
        )
        record, backup = self.record()
        self.assertEqual(record["result"], "SUCCESS")
        self.assertIn("old-hub", read_toml(backup / "home" / registry.REGISTRY)["machines"])
        self.assertIn("active", self.cli("machines", "remove", socket.gethostname(), expected=1))

    def test_human_view_goldens(self):
        for name, rendered in goldens().items():
            self.assertEqual(rendered, (ROOT / f"tests/fixtures/views/{name}.txt").read_text())
            self.assertNotIn("[pins]", rendered)
            for line in rendered.splitlines():
                self.assertLessEqual(len(line), 100)


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
