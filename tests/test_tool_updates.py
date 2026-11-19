"""Record the newest available tool versions, and report newer versions in check and status.

Usage: python3 -m unittest discover -s tests -v. A fake Mise answers; no network connection is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_tool_updates.py --help.
"""

import json
import re
import tomllib
import unittest

import remote
import test_command as commands
import test_remote

# The fake answers latest from a file of newest versions, and logs every call.
FAKE_MISE = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
home = Path.home()
store, newest = home / ".mise-installed.json", home / ".mise-newest.json"
args = sys.argv[1:]
with (home / ".mise-calls").open("a") as stream:
    stream.write(" ".join(args) + "\\n")
items = json.loads(store.read_text()) if store.exists() else {}
if args[0] == "--version":
    print("2026.8.5")
elif args[0] == "ls":
    print(json.dumps(items))
elif args[0] == "install":
    tool, version = args[1].rsplit("@", 1)
    path = home / ".local/share/mise/installs" / tool / version
    path.mkdir(parents=True, exist_ok=True)
    items.setdefault(tool, []).append({"version": version, "install_path": str(path)})
    store.write_text(json.dumps(items))
elif args[0] == "latest" and "--installed" in args:
    tool, version = args[-1].rsplit("@", 1)
    if any(item["version"] == version for item in items.get(tool, [])):
        print(version)
    else:
        sys.exit(1)
elif args[0] == "latest":
    version = json.loads(newest.read_text()).get(args[-1])
    if version is None:
        print("mise ERROR no versions found for " + args[-1], file=sys.stderr)
        sys.exit(1)
    print(version)
else:
    sys.exit(1)
"""


def fake_mise(test, newest, selections):
    """Replace Mise for one enrolled home, then select each tool with tools set."""
    mise = test.home / ".local/bin/mise"
    mise.write_text(FAKE_MISE)
    mise.chmod(0o700)
    (test.home / ".mise-newest.json").write_text(json.dumps(newest))
    for tool, version in selections.items():
        test.cli("tools", "set", tool, "--version", version)


class ToolUpdateTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def pins(self):
        return tomllib.loads((self.home / ".fleet/profile.toml").read_text())["pins"]

    def snapshot(self):
        return (
            (self.home / ".fleet/profile.toml").read_bytes(),
            sorted((self.home / ".fleet/backups").iterdir()),
        )

    def test_update_records_the_exact_resolved_version_and_installs_it(self):
        self.enroll()
        fake_mise(self, {"node": "24.15.0"}, {"node": "24.14.1"})
        output = self.cli("tools", "update", "node")
        self.assertIn("node: recorded 24.14.1; newest available 24.15.0.", output)
        self.assertIn("24.14.1 → 24.15.0", output)
        self.assertIn("node: updated 24.14.1 → 24.15.0.", output)
        self.assertNotIn("Tools:", output)
        self.assertEqual(self.pins()["node"], "24.15.0")
        self.assertTrue((self.home / ".local/share/mise/installs/node/24.15.0").is_dir())
        self.assertIn("Installed  24.14.1 (extra), 24.15.0 (recorded)", self.cli("tools", "list"))
        self.assert_quiet_status()

    def test_current_tool_changes_nothing_and_unselected_tool_names_tools_set(self):
        self.enroll()
        fake_mise(self, {"node": "24.14.1", "ruby": "3.4.1"}, {"node": "24.14.1"})
        before = self.snapshot()
        output = self.cli("tools", "update", "node")
        self.assertIn("node: recorded 24.14.1; newest available 24.14.1.", output)
        self.assertIn("node: current at 24.14.1; nothing changed.", output)
        self.assertNotIn("Backup:", output)
        self.assertEqual(self.snapshot(), before)
        refused = self.cli("tools", "update", "ruby", expected=1)
        self.assertIn(
            "Error: ruby is not selected. Select it first with"
            " dotfleet tools set ruby --version VERSION.",
            refused,
        )
        # The selection check stops before any lookup.
        self.assertNotIn("ruby", (self.home / ".mise-calls").read_text())
        self.assertEqual(self.snapshot(), before)

    def test_every_tool_form_continues_past_one_failure(self):
        self.enroll()
        fake_mise(
            self,
            {"node": "24.15.0", "python": "3.13.1"},
            {"go": "1.22.0", "node": "24.14.1", "python": "3.13.1"},
        )
        output = self.cli("tools", "update", expected=1)
        lines = output.splitlines()
        self.assertIn("go: failed: mise failed: mise ERROR no versions found for go.", lines)
        self.assertIn("node: updated 24.14.1 → 24.15.0.", lines)
        self.assertIn("python: current at 3.13.1; nothing changed.", lines)
        self.assertEqual(lines[-1], "Tools: 1 updated, 1 current, 1 failed.")
        self.assertEqual(self.pins(), {"go": "1.22.0", "node": "24.15.0", "python": "3.13.1"})

    def test_check_reports_a_behind_tool_and_status_reads_only_the_cache(self):
        self.enroll()
        fake_mise(self, {"node": "24.15.0"}, {"node": "24.14.1"})
        output = self.cli("check", expected=1)
        self.assertIn("Content: up to date", output)
        self.assertTrue(output.endswith("\n\nTool node: 24.15.0 available @ 24.14.1\n"), output)
        calls = self.home / ".mise-calls"
        calls.unlink()
        status = self.cli("status")
        self.assertRegex(
            status,
            r"Tool updates +node 24\.15\.0 available \(checked \d\d:\d\d\)"
            r" — run: dotfleet tools update",
        )
        self.assertFalse(
            calls.exists() and re.search(r"^latest --cd", calls.read_text(), re.M),
            "status must not ask Mise for the newest version",
        )
        self.cli("tools", "update")
        self.assertRegex(self.cli("status"), r"Tool updates +none \(checked \d\d:\d\d\)\n")
        self.assertIn("Tool node: up to date @ 24.15.0", self.cli("check"))


class RemoteToolUpdateTests(unittest.TestCase):
    setUp = test_remote.RemoteTests.setUp
    connect = test_remote.RemoteTests.connect
    call = test_remote.RemoteTests.call
    caller_files = test_remote.RemoteTests.caller_files
    enroll = test_remote.RemoteTests.enroll
    cli = test_remote.RemoteTests.cli

    def test_profile_requests_accept_an_optional_tool(self):
        self.assertEqual(remote.profile_request("tools", "update", {})["arguments"], {})
        request = remote.profile_request("tools", "update", {"tool": "node"})
        self.assertEqual(request["arguments"], {"tool": "node"})
        for arguments, message in [
            ({"tool": "node", "version": "1.0.0"}, "Invalid values"),
            ({"tool": "-x"}, "Invalid tool"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                remote.profile_request("tools", "update", arguments)
        with self.assertRaisesRegex(ValueError, "Invalid values"):
            remote.profile_request("tools", "set", {"tool": "node"})

    def test_remote_update_saves_on_the_target_for_its_next_update(self):
        self.connect()
        fake_mise(self, {"node": "24.15.0"}, {"node": "24.14.1"})
        before = self.caller_files()
        output = self.call("tools", "update", "--machine", "beelink")
        self.assertIn("node: recorded 24.14.1; newest available 24.15.0.", output)
        self.assertIn(
            "node: saved 24.15.0 in place of 24.14.1; the next update applies it.", output
        )
        self.assertIn("Tools: 1 saved.", output)
        self.assertIn("Saved. The next update applies this change: run dotfleet update.", output)
        self.assertIn("beelink: tools update succeeded (exit 0).", output)
        self.assertEqual(self.caller_files(), before)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["pins"]["node"], "24.15.0")
        self.assertTrue((self.home / ".local/share/mise/installs/node/24.15.0").is_dir())
        requests = [json.loads(line)["request"] for line in self.ssh_log.read_text().splitlines()]
        request = next(item for item in reversed(requests) if '"version": 2' in item)
        self.assertEqual(json.loads(request)["arguments"], {})
        current = self.call("tools", "update", "node", "--machine", "beelink")
        self.assertIn("node: current at 24.15.0; nothing changed.", current)
        self.assertNotIn("Saved.", current)
        self.assertIn("Update triggered by: profile change.", self.call("update", "beelink"))


if __name__ == "__main__":
    unittest.main()
