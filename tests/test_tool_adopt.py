"""Record installed tool versions that the profile does not record yet.

Usage: python3 -m unittest discover -s tests -v. A fake Mise answers; no network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_tool_adopt.py --help.
"""

import json
import tomllib
import unittest

import remote
import test_command as commands
import test_remote
from test_tool_updates import fake_mise

# Mise reports these after node 24.14.1 is recorded. Active marks the version in use.
INSTALLED = {
    "node": [{"version": "22.0.0"}, {"version": "24.14.1", "active": True}],
    "jq": [{"version": "1.7.1", "active": False}],
    "python": [{"version": "3.12.13"}, {"version": "3.14.6", "active": True}],
    "shfmt": [{"version": "3.8.0"}, {"version": "3.13.1"}],
    "pandoc": [{"version": "3.10", "active": True}],
}


def seed(test):
    """Record node, then replace the fake's installed set with the fixture."""
    fake_mise(test, {}, {"node": "24.14.1"})
    items = {
        tool: [dict(item, install_path=f"/opt/{tool}/{item['version']}") for item in versions]
        for tool, versions in INSTALLED.items()
    }
    (test.home / ".mise-installed.json").write_text(json.dumps(items))
    (test.home / ".mise-calls").unlink()


class ToolAdoptTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli

    def profile(self):
        return tomllib.loads((self.home / ".fleet/profile.toml").read_text())

    def test_adopt_records_the_active_version_and_never_guesses(self):
        self.enroll()
        seed(self)
        output = self.cli("tools", "adopt")
        self.assertEqual(
            self.profile()["pins"],
            {"jq": "1.7.1", "node": "24.14.1", "python": "3.14.6"},
        )
        self.assertRegex(output, r"  Adopted\n━+\n  jq      1\.7\.1\n  python  3\.14\.6\n")
        self.assertIn("  pandoc  version 3.10 is not exact\n", output)
        self.assertIn("  shfmt   several versions and none active: 3.8.0, 3.13.1\n", output)
        self.assertTrue(output.endswith("Tools: 2 adopted, 2 skipped.\n"), output)
        # A recorded tool is neither adopted nor listed, and its extra version stays extra.
        self.assertNotIn("node", output.split("Tools:")[0].split("Adopted")[1])
        # Adopt only records: it asks Mise for nothing that installs or uninstalls.
        calls = (self.home / ".mise-calls").read_text()
        self.assertNotRegex(calls, r"(?m)^(install|uninstall|use)\b")

        backups = sorted((self.home / ".fleet/backups").iterdir())
        again = self.cli("tools", "adopt")
        self.assertNotIn("Adopted", again)
        self.assertTrue(again.endswith("Tools: 0 adopted, 2 skipped.\n"), again)
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), backups)

    def test_a_named_tool_adopts_alone_and_a_recorded_one_is_untouched(self):
        self.enroll()
        seed(self)
        output = self.cli("tools", "adopt", "jq")
        self.assertTrue(output.endswith("Tools: 1 adopted, 0 skipped.\n"), output)
        self.assertEqual(self.profile()["pins"], {"jq": "1.7.1", "node": "24.14.1"})
        recorded = self.cli("tools", "adopt", "node")
        self.assertIn("  node  records a version already; adopt never replaces it\n", recorded)
        self.assertTrue(recorded.endswith("Tools: 0 adopted, 1 skipped.\n"), recorded)
        self.assertEqual(self.profile()["pins"]["node"], "24.14.1")
        missing = self.cli("tools", "adopt", "ruby", expected=1)
        self.assertIn("ruby is not installed. Run dotfleet tools list.", missing)

    def test_a_removed_tool_is_skipped_until_it_is_named(self):
        self.enroll()
        seed(self)
        self.cli("tools", "remove", "node")
        output = self.cli("tools", "adopt")
        self.assertIn(
            "  node    removed with tools remove; run dotfleet tools adopt node\n", output
        )
        self.assertNotIn("node", self.profile()["pins"])
        self.cli("tools", "adopt", "node")
        self.assertEqual(self.profile()["pins"]["node"], "24.14.1")
        self.assertNotIn("node", self.profile().get("unpinned_tools", []))


class RemoteToolAdoptTests(unittest.TestCase):
    setUp = test_remote.RemoteTests.setUp
    connect = test_remote.RemoteTests.connect
    call = test_remote.RemoteTests.call
    caller_files = test_remote.RemoteTests.caller_files
    enroll = test_remote.RemoteTests.enroll
    cli = test_remote.RemoteTests.cli

    def test_profile_requests_accept_an_optional_tool(self):
        self.assertEqual(remote.profile_request("tools", "adopt", {})["arguments"], {})
        with self.assertRaisesRegex(ValueError, "Invalid values"):
            remote.profile_request("tools", "adopt", {"tool": "jq", "version": "1.7.1"})

    def test_remote_adopt_saves_on_the_target_for_its_next_update(self):
        self.connect()
        seed(self)
        before = self.caller_files()
        output = self.call("tools", "adopt", "--machine", "beelink")
        self.assertIn("Tools: 2 adopted, 2 skipped.", output)
        self.assertIn("Saved. The next update applies this change: run dotfleet update.", output)
        self.assertIn("beelink: tools adopt succeeded (exit 0).", output)
        self.assertEqual(self.caller_files(), before)
        pins = tomllib.loads((self.home / ".fleet/profile.toml").read_text())["pins"]
        self.assertEqual(pins, {"jq": "1.7.1", "node": "24.14.1", "python": "3.14.6"})
        again = self.call("tools", "adopt", "--machine", "beelink")
        self.assertIn("Tools: 0 adopted, 2 skipped.", again)
        self.assertNotIn("Saved.", again)


if __name__ == "__main__":
    unittest.main()
