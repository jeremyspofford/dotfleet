"""Exchange registry digests on contact and remove a member remotely.

Usage: python3 -m unittest discover -s tests -v. No network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_fleet_exchange.py --help.
"""

import tomllib
import unittest

import registry
import test_command
import test_deploy
from common import atomic


def member(name, when, **facts):
    fact = {"hostname": name, "ssh_target": name, "domain": "core", "os": "Linux", "enrolled": True}
    return fact | {"recorded_at": when} | facts


def data(*records, removed=None):
    machines = {}
    for fact in records:
        record = {key: False if kind is bool else "" for key, kind in registry.FIELDS.items()}
        record.update(fact)
        machines[fact["hostname"]] = record
    return {"schema_version": 1, "machines": machines, "removed": dict(removed or {})}


class MergeTests(unittest.TestCase):
    def test_an_older_digest_without_a_version_keeps_the_saved_version(self):
        import remote

        local = data(member("a", "2026-11-01"), member("b", "2026-11-01", manager_version="0.7.0"))
        older = member("b", "2026-11-09", domain="work")
        remote.validate_digest({"members": [older], "removed": {}})
        registry.merge(local, {"members": [older], "removed": {}}, "a")
        self.assertEqual(local["machines"]["b"]["domain"], "work")
        self.assertEqual(local["machines"]["b"]["manager_version"], "0.7.0")
        newer = member("b", "2026-11-10", manager_version="0.8.0")
        remote.validate_digest({"members": [newer], "removed": {}})
        registry.merge(local, {"members": [newer], "removed": {}}, "a")
        self.assertEqual(local["machines"]["b"]["manager_version"], "0.8.0")

    def test_the_union_of_members_wins_and_the_newest_fact_wins(self):
        local = data(member("a", "2026-11-01"), member("b", "2026-11-05", domain="work"))
        received = {
            "members": [member("b", "2026-11-03", domain="client"), member("c", "2026-11-02")],
            "removed": {},
        }
        changes = registry.merge(local, received, "a")
        self.assertEqual(changes, {"added": ["c"], "updated": [], "dropped": []})
        self.assertEqual(local["machines"]["b"]["domain"], "work")
        newer = {"members": [member("b", "2026-11-09", domain="client")], "removed": {}}
        self.assertEqual(registry.merge(local, newer, "a")["updated"], ["b"])
        self.assertEqual(local["machines"]["b"]["domain"], "client")

    def test_an_empty_target_never_erases_a_saved_connection(self):
        local = data(member("a", "2026-11-01"), member("b", "2026-11-01", ssh_target="me@b"))
        received = {"members": [member("b", "2026-11-09", ssh_target="")], "removed": {}}
        registry.merge(local, received, "a")
        self.assertEqual(local["machines"]["b"]["ssh_target"], "me@b")

    def test_a_removal_marker_beats_a_stale_member_fact(self):
        local = data(member("a", "2026-11-01"), removed={"gone": "2026-11-10"})
        stale = {"members": [member("gone", "2026-11-04")], "removed": {}}
        self.assertEqual(registry.merge(local, stale, "a")["added"], [])
        self.assertNotIn("gone", local["machines"])
        peer = data(member("p", "2026-11-01"), member("gone", "2026-11-04"))
        dropped = registry.merge(peer, {"members": [], "removed": local["removed"]}, "p")
        self.assertEqual(dropped["dropped"], ["gone"])

    def test_a_newer_deploy_beats_an_older_marker_and_nobody_overrides_itself(self):
        local = data(member("a", "2026-11-01"), removed={"back": "2026-11-02"})
        fresh = {"members": [member("back", "2026-11-12")], "removed": {"a": "2026-11-20"}}
        changes = registry.merge(local, fresh, "a")
        self.assertEqual(changes["added"], ["back"])
        self.assertNotIn("back", local["removed"])
        self.assertIn("a", local["machines"])
        self.assertNotIn("a", local["removed"])


class ExchangeTests(unittest.TestCase):
    setUp = test_deploy.DeployTests.setUp
    enroll = test_deploy.DeployTests.enroll
    prepare = test_deploy.DeployTests.prepare
    call = test_deploy.DeployTests.call
    deploy = test_deploy.DeployTests.deploy
    registry = test_deploy.DeployTests.registry

    def add_member(self, home, fact):
        loaded = registry.load(home)
        record = {key: False if kind is bool else "" for key, kind in registry.FIELDS.items()}
        record.update(fact)
        loaded["machines"][fact["hostname"]] = record
        atomic(home / registry.REGISTRY, registry.serialize(loaded))

    def test_contact_spreads_members_newest_facts_and_removals(self):
        self.prepare()
        self.deploy("--return-target", "caller")
        self.add_member(self.target, member("lab-box", "2026-11-10T00:00:00+00:00"))
        output = self.call("status", "mini-pc")
        self.assertIn("Fleet members updated from mini-pc: added lab-box.", output)
        self.assertEqual(self.registry(self.home)["lab-box"]["ssh_target"], "lab-box")

        self.add_member(self.home, member("lab-box", "2026-11-12T00:00:00+00:00", domain="work"))
        self.call("doctor", "mini-pc")
        self.assertEqual(self.registry(self.target)["lab-box"]["domain"], "work")

        self.call("machines", "remove", "--name", "lab-box")
        saved = tomllib.loads((self.home / registry.REGISTRY).read_text())
        self.assertIn("lab-box", saved["removed"])
        self.call("status", "mini-pc")
        self.assertNotIn("lab-box", self.registry(self.home))
        self.assertNotIn("lab-box", self.registry(self.target))

    def test_remote_uninstall_confirms_here_and_removes_the_member(self):
        self.prepare()
        self.deploy("--return-target", "caller")
        self.env = self.caller_env
        cancelled = test_command.CommandTests.terminal(
            self, ["machines", "uninstall", "mini-pc"], [(b"Confirm [cancel]:", b"cancel\n")]
        )
        self.assertIn("Cancelled. Nothing removed.", cancelled)
        self.assertTrue((self.target / ".local/bin/dotfleet").exists())
        output = test_command.CommandTests.terminal(
            self, ["machines", "uninstall", "mini-pc"], [(b"Confirm [cancel]:", b"uninstall\n")]
        )
        self.assertIn("mini-pc left the fleet. Peers drop it at their next contact.", output)
        self.assertFalse((self.target / ".local/bin/dotfleet").exists())
        self.assertTrue((self.target / "dotfiles/.git").is_dir())
        self.assertTrue(any((self.target / "fleet-backups").iterdir()))
        self.assertNotIn("mini-pc", self.registry(self.home))
        saved = tomllib.loads((self.home / registry.REGISTRY).read_text())
        self.assertIn("mini-pc", saved["removed"])
        self.assertIn(
            "is not a fleet member", self.call("machines", "uninstall", "mini-pc", expected=1)
        )


if __name__ == "__main__":
    unittest.main()
