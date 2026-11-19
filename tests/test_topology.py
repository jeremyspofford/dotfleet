"""Compare the recorded topology tree and JSON without opening connections.

Usage: python3 -m unittest discover -s tests -v.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_command as commands
import command
from common import atomic
import registry
import topology
import updates

ROOT = Path(__file__).resolve().parents[1]


def seed(home):
    machines = {}
    for name, target, domain in (
        ("example-dell", "", "personal"),
        ("mini-pc", "mini-pc", "personal"),
        ("workstation", "user@lab", "work"),
        ("retired", "", "client"),
    ):
        record = {name: False if kind is bool else "" for name, kind in registry.FIELDS.items()}
        record.update(hostname=name, ssh_target=target, domain=domain)
        machines[name] = record
    atomic(
        home / registry.REGISTRY, registry.serialize({"schema_version": 1, "machines": machines})
    )
    atomic(
        home / topology.CONTACTS,
        json.dumps(
            {
                "schema_version": 1,
                "machines": {
                    "mini-pc": {
                        "target": "mini-pc",
                        "action": "status",
                        "state": "status succeeded",
                        "observed_at": "2026-11-17T14:00:00+00:00",
                        "exit_code": 0,
                    }
                },
            }
        ),
    )
    atomic(
        home / updates.CACHE,
        json.dumps(
            {
                "schema_version": 1,
                "checked_at": "2026-11-17T14:05:00+00:00",
                "sources": [{"name": "manager", "behind": 0}, {"name": "content", "behind": 0}],
            }
        ),
    )


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        seed(self.home)

    def test_tree_and_json_have_the_same_recorded_facts(self):
        before = {str(path): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
        with (
            patch.dict(os.environ, {"HOME": str(self.home)}),
            patch.object(topology.socket, "gethostname", return_value="example-dell"),
            patch("subprocess.run", side_effect=AssertionError("Topology must not query")),
        ):
            view = topology.view(self.home)
            rendered = topology.render(view)
            self.assertEqual(rendered, (ROOT / "tests/fixtures/topology.txt").read_text())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(command.main(["topology", "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue()), view)
        self.assertEqual(len(view["machines"]), 3)
        self.assertIn("never contacted", rendered)
        self.assertIn("no connection", rendered)
        self.assertIn("recorded 2026-11-17T14:00:00+00:00", rendered)
        self.assertIn("from example-dell", rendered)
        self.assertEqual(
            {str(path): path.read_bytes() for path in self.home.rglob("*") if path.is_file()},
            before,
        )
        for line in rendered.splitlines():
            self.assertLessEqual(len(line), 100)

    def test_changed_target_does_not_reuse_old_contact(self):
        data = registry.load(self.home)
        data["machines"]["mini-pc"]["ssh_target"] = "replacement"
        atomic(self.home / registry.REGISTRY, registry.serialize(data))
        with patch.object(topology.socket, "gethostname", return_value="example-dell"):
            machine = next(
                item for item in topology.view(self.home)["machines"] if item["name"] == "mini-pc"
            )
        self.assertIsNone(machine["observed_at"])
        self.assertEqual(machine["state"], "never contacted")

    def test_contact_store_refuses_symlink_and_keeps_private_mode(self):
        topology.remember(self.home, "mini-pc", "mini-pc", "check", "updates available", 1)
        path = self.home / topology.CONTACTS
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            topology.load(self.home)["machines"]["mini-pc"]["state"], "updates available"
        )
        path.unlink()
        elsewhere = self.home / "keep"
        elsewhere.write_text("unchanged")
        path.symlink_to(elsewhere)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            topology.remember(self.home, "mini-pc", "mini-pc", "check", "updates available", 1)
        self.assertIn("Could not save", output.getvalue())
        self.assertEqual(elsewhere.read_text(), "unchanged")

    def test_invalid_observations_never_become_live_claims(self):
        for invalid in ([], {"schema_version": True, "machines": {}}):
            atomic(self.home / topology.CONTACTS, json.dumps(invalid))
            with self.assertRaisesRegex(ValueError, "Invalid contact cache"):
                topology.load(self.home)
        seed(self.home)
        cache = self.home / topology.updates.CACHE
        saved = cache.read_text()
        invalid = json.loads(saved)
        invalid["sources"][0]["behind"] = "unknown"
        cache.write_text(json.dumps(invalid))
        with patch.object(topology.socket, "gethostname", return_value="example-dell"):
            view = topology.view(self.home)
        self.assertNotIn("updates", view["this_machine"]["state"])
        cache.unlink()
        other = self.home / "other.json"
        other.write_text(saved)
        cache.symlink_to(other)
        with patch.object(topology.socket, "gethostname", return_value="example-dell"):
            view = topology.view(self.home)
        self.assertNotIn("updates", view["this_machine"]["state"])


class TopologyCommandTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli

    def test_installed_json_has_no_logger_decorations_or_writes(self):
        self.enroll()
        before = (self.home / registry.REGISTRY).read_bytes()
        backups = list((self.home / ".fleet/backups").iterdir())
        value = json.loads(self.cli("topology", "--json"))
        self.assertEqual(value["machines"], [])
        self.assertIn("from " + value["from"], self.cli("topology"))
        self.assertEqual((self.home / registry.REGISTRY).read_bytes(), before)
        self.assertEqual(list((self.home / ".fleet/backups").iterdir()), backups)


if __name__ == "__main__":
    unittest.main()
