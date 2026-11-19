"""Check live update gates before backups, applies, or run records.

Usage: python3 -m unittest discover -s tests -v.
"""

import json
import unittest

import test_command as helpers
from test_updates import advance
import updates


class UpdateGateTests(unittest.TestCase):
    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli
    terminal = helpers.CommandTests.terminal
    record = helpers.CommandTests.record
    assert_quiet_status = helpers.CommandTests.assert_quiet_status

    def backups(self):
        return sorted((self.home / ".fleet/backups").glob("*/RUN.json"))

    def test_three_current_updates_refresh_checks_without_events(self):
        self.enroll()
        before = {path: path.read_bytes() for path in self.backups()}
        profile = (self.home / ".fleet/profile.toml").read_bytes()
        previous_check_time = 0
        for _ in range(3):
            self.assertEqual(self.cli("update"), updates.CURRENT_MESSAGE + "\n")
            self.assertEqual({path: path.read_bytes() for path in self.backups()}, before)
            cache = json.loads((self.home / updates.CACHE).read_text())
            self.assertTrue(cache["checked_at"])
            checked_time = (self.home / updates.CACHE).stat().st_mtime_ns
            self.assertGreater(checked_time, previous_check_time)
            previous_check_time = checked_time
            self.assertTrue(all(source["behind"] == 0 for source in cache["sources"]))
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), profile)

    def test_content_and_drift_trigger_one_backed_up_run_each(self):
        self.enroll()
        count = len(self.backups())
        advance(self.content, 1, ".file", "new content")
        output = self.cli("update")
        self.assertIn("Update triggered by: content update.", output)
        self.assertEqual(self.record()[0]["update_triggers"], ["content update"])
        self.assertEqual(len(self.backups()), count + 1)
        (self.home / ".file").write_text("local change\n")
        output = self.terminal(["update"], [(b"Choose [", b"o\n")])
        self.assertIn("Update triggered by: drift repair.", output)
        self.assertEqual(self.record()[0]["update_triggers"], ["drift repair"])
        self.assertEqual(len(self.backups()), count + 2)
        self.assertTrue((self.home / ".file").is_file())
        self.assert_quiet_status()

    def test_fetch_failure_preserves_profile_backups_and_files(self):
        self.enroll()
        self.cli("check")
        cache = (self.home / updates.CACHE).read_bytes()
        before = {path: path.read_bytes() for path in self.backups()}
        (self.home / ".file").unlink()
        self.content.rename(self.base / "offline-content")
        output = self.cli("update", expected=2)
        self.assertIn("Could not check content", output)
        self.assertFalse((self.home / ".file").exists())
        self.assertEqual((self.home / updates.CACHE).read_bytes(), cache)
        self.assertEqual({path: path.read_bytes() for path in self.backups()}, before)


if __name__ == "__main__":
    unittest.main()
