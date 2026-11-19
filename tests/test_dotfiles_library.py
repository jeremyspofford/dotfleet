"""Download dotfiles repositories into ~/.fleet/dotfiles and switch the active one.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_dotfiles_library.py --help.
"""

import tomllib
import unittest

import test_command as commands
from test_manager import repository


class DotfilesLibraryTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    terminal = commands.CommandTests.terminal
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def other(self, name="other", parent="repositories"):
        """Create a second content repository: .file differs, .extra is gone, .newfile is new."""
        (self.base / parent).mkdir(exist_ok=True)
        return repository(
            self.base / parent / name,
            {
                "fleet.toml": 'format="plain"\n[domains]\ncore="Other base"\n',
                ".file": "other\n",
                ".newfile": "new\n",
            },
        )

    def profile(self):
        return tomllib.loads((self.home / ".fleet/profile.toml").read_text())

    def library(self):
        return sorted(path.name for path in (self.home / ".fleet/dotfiles").iterdir())

    def applied(self):
        return {
            name: (self.home / name).read_text() if (self.home / name).exists() else None
            for name in (".file", ".extra", ".newfile")
        }

    def test_download_never_activates(self):
        self.enroll()
        before = self.applied()
        output = self.cli("dotfiles", "download", str(self.other()))
        self.assertIn("Downloaded other into ~/.fleet/dotfiles/other. It is not active.", output)
        self.assertEqual(self.library(), ["content", "other"])
        self.assertTrue((self.home / ".fleet/dotfiles/other/.git").is_dir())
        self.assertEqual(self.profile()["dotfiles"], "content")
        self.assertEqual(self.applied(), before)
        listing = self.cli("dotfiles", "list")
        self.assertIn("content", listing)
        self.assertIn(str(self.base / "repositories/other"), listing)
        self.assertEqual(listing.count("active"), 1)
        self.assert_quiet_status()

    def test_use_swaps_applied_files_and_removes_unedited_stale_ones(self):
        self.enroll()
        self.cli("domain", "set", "--name", "personal")
        self.cli("dotfiles", "download", str(self.other()))
        output = self.terminal(["dotfiles", "use", "other"], [(b"Confirm [cancel]:", b"use\n")])
        self.assertIn("Domain personal is not declared by this repository; applying core.", output)
        self.assertEqual(self.applied(), {".file": "other\n", ".extra": None, ".newfile": "new\n"})
        profile = self.profile()
        self.assertEqual(profile["dotfiles"], "other")
        self.assertEqual(profile["domain"], "personal")
        self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), "core\n")
        self.assertEqual(self.library(), ["content", "other"])
        # Later updates on the switched repository keep applying core, not stopping.
        self.cli("update")
        self.assert_quiet_status()

    def test_an_edited_stale_file_survives_with_a_report(self):
        self.enroll()
        (self.home / ".extra").write_text("my own edit\n")
        self.cli("dotfiles", "download", str(self.other()))
        output = self.terminal(["dotfiles", "use", "other"], [(b"Confirm [cancel]:", b"use\n")])
        self.assertIn("Kept ~/.extra: it has local edits", output)
        self.assertEqual((self.home / ".extra").read_text(), "my own edit\n")
        self.assertEqual((self.home / ".file").read_text(), "other\n")
        self.assertEqual(self.profile()["dotfiles"], "other")

    def test_cancelled_use_changes_nothing(self):
        self.enroll()
        self.cli("dotfiles", "download", str(self.other()))
        before = self.applied()
        self.terminal(["dotfiles", "use", "other"], [(b"Confirm [cancel]:", b"cancel\n")])
        self.assertEqual(self.applied(), before)
        self.assertEqual(self.profile()["dotfiles"], "content")

    def test_remove_refuses_the_active_repository(self):
        self.enroll()
        output = self.cli("dotfiles", "remove", "content", expected=1)
        self.assertIn("content is the active repository", output)
        self.assertEqual(self.library(), ["content"])
        self.cli("dotfiles", "download", str(self.other()))
        self.terminal(["dotfiles", "remove", "other"], [(b"Confirm [cancel]:", b"remove\n")])
        self.assertEqual(self.library(), ["content"])

    def test_name_flag_lands_under_the_given_name(self):
        self.enroll()
        source = str(self.other())
        output = self.cli("dotfiles", "download", source, "--name", "work_2")
        self.assertIn("Downloaded work_2", output)
        self.assertEqual(self.library(), ["content", "work_2"])
        bad = self.cli("dotfiles", "download", source, "--name", "../up", expected=1)
        self.assertIn("not a usable name", bad)
        self.assertEqual(self.library(), ["content", "work_2"])

    def test_a_colliding_download_asks_for_another_name_in_a_terminal(self):
        self.enroll()
        clash = self.other("content")
        output = self.terminal(["dotfiles", "download", str(clash)], [(b"Name []:", b"second\n")])
        self.assertIn("A download named content already exists", output)
        self.assertEqual(self.library(), ["content", "second"])
        self.assertEqual((self.home / ".fleet/dotfiles/second/.file").read_text(), "other\n")

    def test_a_collision_without_a_terminal_stops_and_changes_nothing(self):
        self.enroll()
        self.cli("dotfiles", "download", str(self.other()))
        before = (self.home / ".fleet/dotfiles/other/.git/HEAD").read_bytes()
        output = self.cli(
            "dotfiles", "download", str(self.other("other", "again")), "--name", "other", expected=1
        )
        self.assertIn("A download named other already exists", output)
        self.assertEqual(self.library(), ["content", "other"])
        self.assertEqual((self.home / ".fleet/dotfiles/other/.git/HEAD").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
