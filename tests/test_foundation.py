"""Prove that only managed content edits block an update.

The foundation is the tracked content that Fleet installs. Unmanaged files stay invisible.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_foundation.py --help.
"""

import unittest

import dotfiles_library

import test_updates
from test_updates import advance


class FoundationTests(unittest.TestCase):
    setUp = test_updates.UpdateTests.setUp
    enroll = test_updates.UpdateTests.enroll
    cli = test_updates.UpdateTests.cli
    snapshot = test_updates.UpdateTests.snapshot

    def test_unmanaged_local_files_never_block_check_or_update(self):
        self.enroll()
        content = dotfiles_library.active(self.home)
        (content / "scratch.txt").write_text("my notes\n")
        (content / ".untracked-dotfile").write_text("not managed\n")
        self.assertIn("· clean", self.cli("status"))
        self.assertIn("Content checkout PASS · clean", " ".join(self.cli("doctor").split()))
        advance(self.content, 1, ".file", "updated")
        output = self.cli("check", expected=1)
        self.assertIn("Content  1 commit behind", output)
        update = self.cli("update")
        self.assertNotIn("scratch.txt", update)
        self.assertEqual((self.home / ".file").read_text(), "updated 1\n")
        self.assertEqual((content / "scratch.txt").read_text(), "my notes\n")
        self.assertFalse((self.home / ".untracked-dotfile").exists())

    def test_uncommitted_edits_to_managed_files_still_block(self):
        self.enroll()
        before = self.snapshot()
        (dotfiles_library.active(self.home) / ".file").write_text("hand edit\n")
        for action in ("check", "update"):
            output = self.cli(action, expected=2)
            self.assertIn("uncommitted edits to managed files: .file", output)
        self.assertIn("local edits", self.cli("status"))
        self.assertEqual(before, self.snapshot())

    def test_an_update_that_changes_an_unmanaged_local_file_stops_before_backup(self):
        self.enroll()
        (dotfiles_library.active(self.home) / "notes.md").write_text("local draft\n")
        advance(self.content, 1, "notes.md", "upstream")
        before = self.snapshot()
        for action in ("check", "update"):
            output = self.cli(action, expected=2)
            self.assertIn(
                "changes files with local edits in the active dotfiles repository: notes.md", output
            )
        self.assertEqual(before, self.snapshot())
        self.assertEqual(
            (dotfiles_library.active(self.home) / "notes.md").read_text(), "local draft\n"
        )


if __name__ == "__main__":
    unittest.main()
