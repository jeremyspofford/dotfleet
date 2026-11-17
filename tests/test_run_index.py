"""Verify bounded attempts, early failures, nested updates, and read-only log views.

Usage: python3 -m unittest discover -s tests -v.
"""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import command
import run_index
import run_logs
from common import atomic
import test_command as command_tests
import test_manager as manager_tests
from test_manager import ROOT
from test_updates import advance


def fixture_entries(home):
    result = []
    for minute, operation, outcome, remote in (
        (0, "install", "SUCCESS", False),
        (1, "update", "SUCCESS", True),
        (2, "configure", "FAILED", False),
    ):
        entry = {
            "schema_version": 1,
            "id": f"20261117-090{minute}00-000000-00000000000{minute}",
            "time": f"2026-11-17T09:0{minute}:00.000000+00:00",
            "command": operation,
            "machine": "mini-pc" if remote else "example-hub",
            "remote": remote,
            "result": outcome,
            "stage": "profile" if outcome == "FAILED" else "complete",
            "error": "No Git email recorded." if outcome == "FAILED" else "",
            "backup": None,
            "record_path": None,
        }
        atomic(run_index.path_for(home, entry["id"]), json.dumps(entry))
        result.append(entry)
    return result


class IndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.environment = patch.dict(os.environ, {"HOME": str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_preapply_failure_keeps_stage_and_error_without_backup(self):
        def fail():
            run_index.stage("plan")
            raise ValueError("Fixture content is invalid.")

        with self.assertRaisesRegex(ValueError, "Fixture content"):
            run_index.run(self.home, "install", fail)
        (entry,) = run_index.entries(self.home)
        self.assertEqual(
            (entry["command"], entry["result"], entry["stage"]), ("install", "FAILED", "plan")
        )
        self.assertEqual(entry["error"], "Fixture content is invalid.")
        self.assertIsNone(entry["backup"])
        path = run_index.path_for(self.home, entry["id"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_101st_entry_prunes_the_oldest_and_nested_cores_share_one(self):
        first = run_index.begin(self.home, "install")
        for _ in range(100):
            run_index.begin(self.home, "update")
        self.assertEqual(len(run_index.entries(self.home)), 100)
        self.assertFalse(run_index.path_for(self.home, first).exists())
        before = set(run_index.files(self.home))
        run_index.run(
            self.home, "configure", lambda: run_index.run(self.home, "install", lambda: 0)
        )
        self.assertEqual(len(set(run_index.files(self.home)) - before), 1)
        self.assertEqual(run_index.entries(self.home)[0]["command"], "configure")

    def test_json_matches_entry_and_backup_record_even_with_timestamp_logging(self):
        entries = fixture_entries(self.home)
        entry = entries[-1]
        backup = self.home / "fleet-backups/20261117-090200"
        record = {"result": "FAILED", "files": [], "home": str(self.home)}
        atomic(backup / "RUN.json", json.dumps(record))
        run_index.update(
            self.home, entry["id"], backup=str(backup), record_path=str(backup / "RUN.json")
        )
        args = argparse.Namespace(action="show", name=backup.name, json=True)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_logs.show_command(self.home, args), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["entry"], run_index.entries(self.home)[0])
        self.assertEqual(payload["run"], record)
        self.assertEqual(run_logs.records(self.home, entry["time"]), payload)
        shutil.rmtree(backup)
        payload = run_logs.records(self.home, entry["id"])
        self.assertIsNone(payload["run"])
        self.assertIn("removed with its backup", run_logs.render_detail(payload, self.home))

    def test_list_and_failure_detail_goldens_newest_first(self):
        fixture_entries(self.home)
        listed = run_logs.listed(self.home)
        for name, output in (
            ("logs", run_logs.render_list(listed)),
            ("log failure", run_logs.render_detail(run_logs.records(self.home), self.home)),
        ):
            self.assertEqual(output, (ROOT / "tests/fixtures/views" / (name + ".txt")).read_text())
            self.assertTrue(all(len(line) <= 100 for line in output.splitlines()))
        self.assertEqual([entry["command"] for entry in listed], ["configure", "update", "install"])
        self.assertIn("mini-pc", run_logs.render_list(listed))
        self.assertEqual(run_logs.listed(self.home, 1), listed[:1])
        with self.assertRaisesRegex(ValueError, "positive"):
            run_logs.listed(self.home, 0)

    def test_diagnostic_stores_keep_100_and_remote_text_is_size_bounded(self):
        import backup_cleanup
        import remote_history
        from types import SimpleNamespace

        for store in (backup_cleanup.STORE, remote_history.STORE):
            for number in range(101):
                atomic(self.home / store / f"{number:03}" / "RUN.json", "{}")
            run_index.prune_diagnostics(self.home, store)
            self.assertEqual(len(list((self.home / store).glob("*/RUN.json"))), 100)
            self.assertFalse((self.home / store / "000").exists())
        result = SimpleNamespace(stdout="x" * 100000, stderr="y" * 100000, returncode=255)
        path = remote_history.save(self.home, "mini-pc", "mini-pc", "status", "probe", result)
        value = json.loads(path.read_text())
        self.assertLess(len(value["stdout"]), 66000)
        self.assertLess(len(value["stderr"]), 66000)
        self.assertEqual(len(list((self.home / remote_history.STORE).glob("*/RUN.json"))), 100)
        self.assertEqual(run_index.entries(self.home), [])

    def test_queries_without_index_create_nothing_and_unknown_names_fail(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(command.main(["logs", "list"]), 0)
            self.assertEqual(command.main(["logs", "show"]), 0)
        self.assertEqual(list(self.home.iterdir()), [])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            command.main(["logs", "list", "--limit", "0"])
        self.assertEqual(stopped.exception.code, 2)
        with self.assertRaisesRegex(ValueError, "recorded timestamp"):
            run_logs.records(self.home, "missing")

    def test_contact_cache_keeps_only_latest_100_machine_observations(self):
        import topology

        for number in range(101):
            topology.remember(self.home, f"host-{number}", f"host-{number}", "status", "success", 0)
        records = topology.load(self.home)["machines"]
        self.assertEqual(len(records), 100)
        self.assertNotIn("host-0", records)
        self.assertIn("host-100", records)
        self.assertEqual(run_index.entries(self.home), [])

    def test_index_refuses_links_outside_home(self):
        with tempfile.TemporaryDirectory() as outside:
            parent = self.home / ".local/share"
            parent.mkdir(parents=True)
            (parent / "fleet").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "manager store"):
                run_index.begin(self.home, "install")
            self.assertEqual(list(Path(outside).iterdir()), [])


class IndexIntegrationTests(unittest.TestCase):
    setUp = manager_tests.EnrollmentTests.setUp
    enroll = command_tests.CommandTests.enroll
    cli = command_tests.CommandTests.cli

    def test_install_failure_before_apply_is_readable_without_a_profile(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--defaults"],
            env=self.env,
            input="",
            text=True,
            capture_output=True,
            start_new_session=True,
        )
        self.assertNotEqual(result.returncode, 0)
        (entry,) = run_index.entries(self.home)
        self.assertEqual(
            (entry["command"], entry["stage"], entry["result"]), ("install", "profile", "FAILED")
        )
        self.assertIn("Git", entry["error"])
        self.assertIsNone(entry["backup"])
        self.assertFalse((self.home / "fleet-backups").exists())
        result = subprocess.run(
            ["python3", str(ROOT / "scripts/command.py"), "logs", "show", "--json"],
            env=dict(self.env, LOG_TIMESTAMP="1"),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["entry"], entry)

    def test_success_update_noop_and_queries_share_the_expected_lifecycle(self):
        self.enroll()
        (entry,) = run_index.entries(self.home)
        self.assertEqual((entry["command"], entry["result"]), ("install", "SUCCESS"))
        self.assertTrue(Path(entry["backup"]).is_dir())
        before = {path: path.read_bytes() for path in run_index.files(self.home)}
        for arguments in (("status",), ("check",), ("diff",), ("logs", "list"), ("logs", "show")):
            self.cli(*arguments)
        self.assertEqual({path: path.read_bytes() for path in run_index.files(self.home)}, before)
        backups = set((self.home / "fleet-backups").iterdir())
        self.cli("update")
        self.assertEqual(run_index.entries(self.home)[0]["result"], "NO_CHANGE")
        self.assertEqual(set((self.home / "fleet-backups").iterdir()), backups)
        advance(self.content, 1, ".file", "update content")
        self.cli("update")
        self.assertEqual(len(run_index.entries(self.home)), 3)
        entry = run_index.entries(self.home)[0]
        self.assertEqual((entry["command"], entry["result"]), ("update", "SUCCESS"))
        self.assertTrue(Path(entry["record_path"]).is_file())
        output = self.cli("logs", "show")
        self.assertIn("1 · Software", output)
        self.assertIn("3 · Files", output)
        self.assertIn("content update", output)

    def test_missing_python_still_keeps_a_private_bounded_preflight_entry(self):
        binary = self.base / "preflight-bin"
        binary.mkdir()
        for name in (
            "bash",
            "cat",
            "chmod",
            "curl",
            "date",
            "dirname",
            "mkdir",
            "mktemp",
            "mv",
            "rm",
        ):
            (binary / name).symlink_to(shutil.which(name))
        environment = dict(self.env, PATH=str(binary))
        for _ in range(2):
            result = subprocess.run(
                [shutil.which("bash"), str(ROOT / "install.sh"), "--defaults"],
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
        entries = run_index.entries(self.home)
        self.assertEqual(len(entries), 2)
        self.assertTrue(
            all(entry["stage"] == "preflight" and entry["result"] == "FAILED" for entry in entries)
        )
        self.assertTrue(
            all(
                "Missing required commands:" in entry["error"] and "python3" in entry["error"]
                for entry in entries
            )
        )
        self.assertTrue(
            all(path.stat().st_mode & 0o777 == 0o600 for path in run_index.files(self.home))
        )


if __name__ == "__main__":
    unittest.main()
