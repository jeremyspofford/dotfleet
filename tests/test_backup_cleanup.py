"""Delete only reviewed backups, while keeping the cleanup audit outside that store.

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

import backup_cleanup
from render_run import render
import command
from common import atomic, toml


class BackupCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        atomic(self.home / command.PROFILE, toml({"domain": "core", "domain_chosen": True}))
        self.folders = []
        for day in range(1, 6):
            folder = self.home / ".fleet/backups" / f"202611{day:02d}-120000"
            atomic(folder / "MANIFEST.json", "[]")
            atomic(folder / "RUN.json", '{"result":"SUCCESS"}')
            atomic(folder / "home/.file", "backup bytes")
            self.folders.append(folder)
        self.environment = patch.dict(os.environ, HOME=str(self.home))
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def call(self, *arguments, answer="delete"):
        with (
            patch.object(command.manager, "has_terminal", return_value=True),
            patch.object(command.manager, "ask", return_value=answer) as asked,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = command.main(["backups", "delete", *arguments])
        self.assertEqual(result, 0, output.getvalue())
        return output.getvalue(), asked

    def test_name_deletes_one_and_records_dates_sizes_and_warning(self):
        output, asked = self.call("--name", self.folders[2].name)
        self.assertFalse(self.folders[2].exists())
        self.assertTrue(
            all(folder.exists() for index, folder in enumerate(self.folders) if index != 2)
        )
        self.assertEqual(asked.call_count, 1)
        for text in ("2026-11-03 12:00:00", "bytes", "1 backup", backup_cleanup.WARNING):
            self.assertIn(text, output)
        paths = list(self.home.glob(backup_cleanup.STORE + "/*/RUN.json"))
        self.assertEqual(len(paths), 1)
        record = json.loads(paths[0].read_text())
        self.assertEqual(record["completed_files"], [str(self.folders[2])])
        self.assertEqual(record["result"], "SUCCESS")
        self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)

    def test_keep_two_then_all_leaves_an_empty_store(self):
        output, _ = self.call("--keep", "2")
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), self.folders[-2:])
        self.assertIn("3 backups", output)
        output, asked = self.call("--all")
        self.assertEqual(list((self.home / ".fleet/backups").iterdir()), [])
        self.assertIn("2 backups", output)
        self.assertEqual(asked.call_args.args[2], ["delete", "cancel"])

    def test_decline_changes_nothing_and_invalid_selectors_stop(self):
        before = {path: path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
        output, _ = self.call("--all", answer="cancel")
        self.assertIn("No backups deleted", output)
        import run_index

        (entry,) = run_index.entries(self.home)
        self.assertEqual(entry["result"], "CANCELLED")
        self.assertEqual(
            {
                path: path.read_bytes()
                for path in self.home.rglob("*")
                if path.is_file() and not path.is_relative_to(run_index.folder(self.home))
            },
            before,
        )
        for flags in (
            [],
            ["--all", "--keep", "2"],
            ["--name", self.folders[0].name, "--all"],
            ["--keep", "-1"],
        ):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    command.main(["backups", "delete", *flags])
                self.assertEqual(stopped.exception.code, 2)

    def test_cleanup_review_and_result_goldens(self):
        record = backup_cleanup.planned_record(self.home, [self.folders[0].name])
        record = json.loads(json.dumps(record).replace(str(self.home), "/home/example"))
        record["record_path"] = "/home/example/.fleet/cleanup-runs/example/RUN.json"
        for future, name in ((True, "backup deletion plan"), (False, "backup deletion result")):
            if not future:
                record["completed_files"] = [record["files"][0]["path"]]
                record["result"] = "SUCCESS"
            text = render(record, future=future)
            path = command.ROOT / "tests/fixtures/views" / (name + ".txt")
            self.assertEqual(text, path.read_text())
            self.assertTrue(all(len(line) <= 100 for line in text.splitlines()))

    def test_links_never_delete_external_targets_and_changed_plans_stop(self):
        outside = self.home / "outside"
        outside.mkdir()
        atomic(outside / "keep", "keep")
        (self.folders[0] / "external-link").symlink_to(outside, target_is_directory=True)
        self.call("--name", self.folders[0].name)
        self.assertEqual((outside / "keep").read_text(), "keep")

        def changed(*args):
            atomic(self.folders[1] / "new", "new bytes")
            return True

        with (
            patch.object(command, "confirm", side_effect=changed),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "changed after review"):
                backup_cleanup.delete_selected(self.home, [self.folders[1].name])
        self.assertTrue(self.folders[1].exists())


class BackupCleanupTerminalTests(unittest.TestCase):
    import test_command as helpers

    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli
    terminal = helpers.CommandTests.terminal
    record = helpers.CommandTests.record

    def test_terminal_decline_invalid_answer_and_delete(self):
        self.enroll()
        _, folder = self.record()
        args = ["backups", "delete", "--name", folder.name]
        output = self.terminal(args, [(b"Confirm [cancel]:", b"cancel\n")])
        self.assertIn("No backups deleted", output)
        self.assertTrue(folder.is_dir())
        output = self.terminal(
            args,
            [(b"Confirm [cancel]:", b"d\n"), (b"Confirm [cancel]:", b"delete\n")],
        )
        self.assertIn("Deleted backups cannot be rolled back to.", output)
        self.assertFalse(folder.exists())
        self.assertTrue(list(self.home.glob(backup_cleanup.STORE + "/*/RUN.json")))


if __name__ == "__main__":
    unittest.main()
