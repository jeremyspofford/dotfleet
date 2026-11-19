"""Check shared core dispatch, private runtime records, and terminal-free confirmations.

Usage: python3 -m unittest discover -s tests -v. No Textual import is required.
"""

import asyncio
import os
import fcntl
import pty
import select
import signal
import shutil
import struct
import subprocess
import sys
import termios
import time
import unittest

import dotfiles_library
from unittest.mock import patch

import test_command as commands
import test_manager as helpers
from test_manager import command
import tui_environment
from tui_session import CoreSession


class RuntimeTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record

    def test_missing_environment_prints_alternatives_and_changes_nothing(self):
        self.enroll()
        shutil.rmtree(self.home / tui_environment.ENVIRONMENT)
        profile = (self.home / ".fleet/profile.toml").read_bytes()
        backups = sorted((self.home / ".fleet/backups").iterdir())
        output = self.cli("tui")
        for name in (
            "status",
            "diff",
            "doctor",
            "check",
            "update",
            "backups",
            "backups restore",
            "machines",
            "configure",
        ):
            self.assertIn("dotfleet " + name, output)
        self.assertIn("missing", output)
        self.assertEqual((self.home / ".fleet/profile.toml").read_bytes(), profile)
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), backups)
        self.assertFalse((self.home / tui_environment.ENVIRONMENT).exists())

    def test_runtime_is_recorded_and_unchanged_on_rerun(self):
        self.enroll()
        record, _ = self.record()
        runtime = next(item for item in record["software"] if item["name"] == tui_environment.NAME)
        self.assertEqual(runtime["version"], tui_environment.required_version(helpers.ROOT))
        path = self.home / tui_environment.ENVIRONMENT
        before = (path / "pyvenv.cfg").read_bytes(), (path / "pyvenv.cfg").stat().st_mtime_ns
        command("bash", str(self.origin / "install.sh"), "--defaults", env=self.env)
        record, _ = self.record()
        self.assertEqual(
            next(
                item["action"]
                for item in record["software"]
                if item["name"] == tui_environment.NAME
            ),
            "present",
        )
        self.assertEqual(
            ((path / "pyvenv.cfg").read_bytes(), (path / "pyvenv.cfg").stat().st_mtime_ns), before
        )

    def test_failed_runtime_upgrade_restores_previous_environment(self):
        from types import SimpleNamespace

        target = self.home / tui_environment.ENVIRONMENT
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        (target / "old").write_text("previous runtime")
        backup = self.home / "backup"
        backup.mkdir()
        account = SimpleNamespace(
            software=[{"name": tui_environment.NAME, "action": "upgraded", "version": "8.2.8"}],
            applied_software=set(),
        )
        with patch.object(
            tui_environment.venv.EnvBuilder, "create", side_effect=RuntimeError("fixture failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                tui_environment.install(self.home, helpers.ROOT, backup, account)
        self.assertEqual((target / "old").read_text(), "previous runtime")
        self.assertEqual(account.applied_software, set())

    def test_cli_and_bridge_import_without_textual(self):
        output = command(
            sys.executable,
            "-S",
            "-c",
            "import sys; import command, tui_core, tui_environment, tui_session; assert 'textual' not in sys.modules; print('standard library only')",
            env=dict(self.env, PYTHONPATH=str(helpers.ROOT / "scripts")),
        )
        self.assertEqual(output.strip(), "standard library only")

    def test_installed_command_opens_and_quits_in_a_real_terminal(self):
        self.enroll()
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

        def terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = subprocess.Popen(
            [str(self.exe), "tui"],
            env=dict(self.env, TERM="xterm-256color"),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=terminal,
        )
        os.close(slave)
        captured = b""
        sent = False
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        captured += os.read(master, 65536)
                    except OSError:
                        break
                if b"this machine" in captured and not sent:
                    os.write(master, b"q")
                    sent = True
            if process.poll() is None:
                process.wait(timeout=3)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            os.close(master)
        self.assertTrue(sent, captured.decode(errors="replace"))
        self.assertEqual(process.returncode, 0, captured.decode(errors="replace"))
        self.assertNotIn(b"Traceback", captured)


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record

    async def run_core(self, action, answer="cancel", backup=None, selected=None):
        output, events, reviews = [], [], []

        async def confirm(request):
            reviews.append(request)
            return answer(request) if callable(answer) else answer

        with patch.dict(os.environ, self.env, clear=True):
            result = await CoreSession(helpers.ROOT).run(
                action, output.append, events.append, confirm, backup, selected=selected
            )
        return result, "\n".join(output), events, reviews

    async def test_rollback_uses_real_core_review_confirmation_and_record(self):
        self.enroll()
        self.cli("backups", "create")
        _, backup = self.record()
        original = (self.home / ".file").read_text()
        (self.home / ".file").write_text("local edit\n")
        folders = sorted((self.home / ".fleet/backups").iterdir())
        result, output, _, reviews = await self.run_core("rollback", "wrong", str(backup))
        self.assertEqual(result, 0, output)
        self.assertEqual(reviews[0]["word"], "restore")
        self.assertEqual((self.home / ".file").read_text(), "local edit\n")
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), folders)
        result, output, _, reviews = await self.run_core("rollback", "restore", str(backup))
        self.assertEqual(result, 0, output)
        self.assertEqual((self.home / ".file").read_text(), original)
        record, _ = self.record()
        self.assertEqual(record["result"], "SUCCESS")
        self.assertIn("3 · Files", output)
        self.assertIn(str(self.home / ".file"), record["completed_files"])

    async def test_current_update_has_no_review_or_backup_in_the_terminal_interface(self):
        import updates

        self.enroll()
        before = sorted((self.home / ".fleet/backups").iterdir())
        result, output, _, reviews = await self.run_core("update", "confirm")
        self.assertEqual(result, 0, output)
        self.assertIn(updates.CURRENT_MESSAGE, output)
        self.assertEqual(reviews, [])
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), before)

    async def test_cleanup_transports_selected_names_to_the_shared_core(self):
        self.enroll()
        self.cli("backups", "create")
        _, latest = self.record()
        before = sorted((self.home / ".fleet/backups").iterdir())
        result, output, events, reviews = await self.run_core(
            "backup-delete-selected", "delete", selected=[latest.name]
        )
        self.assertEqual(result, 0, output)
        self.assertEqual(reviews[0]["word"], "delete")
        self.assertFalse(latest.exists())
        self.assertEqual(sorted((self.home / ".fleet/backups").iterdir()), before[:-1])
        self.assertTrue(any(event["kind"] == "backups" for event in events))
        self.assertIn("Deleted backups cannot be rolled back to.", output)

    async def test_update_uses_shared_core_and_confirmed_run_record(self):
        self.enroll()
        (self.content / ".file").write_text("updated content\n")
        command("git", "-C", str(self.content), "add", ".")
        command(
            "git",
            "-C",
            str(self.content),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fix: update fixture",
        )
        result, output, _, reviews = await self.run_core("update", "cancel")
        self.assertEqual(result, 0, output)
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        self.assertEqual(reviews[0]["word"], "confirm")
        result, output, _, reviews = await self.run_core("update", "confirm")
        self.assertEqual(result, 0, output)
        self.assertEqual((self.home / ".file").read_text(), "updated content\n")
        self.assertEqual(self.record()[0]["result"], "SUCCESS")
        self.assertIn("6 · Next steps", output)

    async def test_update_conflict_stops_without_guessing_or_terminal_reads(self):
        self.enroll()
        (self.home / ".file").write_text("keep my local change\n")
        (self.content / ".file").write_text("new remote content\n")
        command("git", "-C", str(self.content), "add", ".")
        command(
            "git",
            "-C",
            str(self.content),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fix: conflicting fixture",
        )
        result, output, _, reviews = await asyncio.wait_for(self.run_core("update", "confirm"), 30)
        self.assertEqual(result, 1, output)
        self.assertTrue(reviews)
        self.assertEqual((self.home / ".file").read_text(), "keep my local change\n")
        self.assertEqual(self.record()[0]["result"], "FAILED")
        self.assertIn("Apply stopped.", output)

    async def test_configuration_actions_keep_the_real_apply_and_run_records(self):
        self.enroll()

        def answer(request):
            if request["kind"] == "review":
                return "confirm"
            field = request["fields"][0]
            values = [value for label, value in field["choices"]]
            return {
                "choice": "personal"
                if "personal" in values
                else "extra"
                if "extra" in values
                else "off"
            }

        result, output, _, reviews = await self.run_core("domain-set", answer)
        self.assertEqual(result, 0, output)
        self.assertIn("personal", (self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(self.record()[0]["result"], "SUCCESS")
        result, output, _, reviews = await self.run_core("group-set", answer)
        self.assertEqual(result, 0, output)
        self.assertFalse((self.home / ".extra").exists())
        self.assertTrue(any(item["kind"] == "review" for item in reviews))
        self.assertIn("6 · Next steps", output)

    async def test_uninstall_uses_the_same_typed_confirmation_and_real_record(self):
        self.enroll()
        profile = self.home / ".fleet/profile.toml"
        before = profile.read_bytes()
        result, output, _, reviews = await self.run_core(
            "uninstall",
            lambda request: {"choice": "keep"} if request["kind"] == "form" else "wrong",
        )
        self.assertEqual(result, 0, output)
        self.assertEqual(profile.read_bytes(), before)
        self.assertEqual(reviews[-1]["word"], "uninstall")
        result, output, events, reviews = await self.run_core(
            "uninstall",
            lambda request: {"choice": "keep"} if request["kind"] == "form" else "uninstall",
        )
        self.assertEqual(result, 0, output)
        self.assertFalse(profile.exists())
        self.assertFalse((self.home / ".local/bin/dotfleet").exists())
        self.assertTrue(any((self.home / ".fleet/dotfiles").glob("*/.git")))
        self.assertEqual(self.record()[0]["result"], "SUCCESS")
        self.assertIn("3 · Files", output)
        self.assertIn({"kind": "uninstalled", "value": True}, events)


if __name__ == "__main__":
    unittest.main()
