"""Exercise SSH routing through a fake executable and a real enrolled target store.

Usage: python3 -m unittest discover -s tests -v. No network connection is used.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_command as commands
from test_manager import ROOT, command
from common import atomic, read_toml, toml
import registry
import remote


class RemoteTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def connect(self):
        self.enroll()
        self.controller = self.base / "controller"
        self.controller.mkdir()
        atomic(self.controller / ".config/fleet/profile.toml", toml({"git_name": "Caller only"}))
        saved = registry.load(self.home)["machines"][socket.gethostname()].copy()
        saved.update(hostname="beelink", ssh_target="mini-pc")
        atomic(
            self.controller / registry.REGISTRY,
            registry.serialize({"schema_version": 1, "machines": {"beelink": saved}}),
        )
        target_env = self.base / "target-env.json"
        target_env.write_text(json.dumps(self.env))
        self.ssh_log = self.base / "ssh-log.jsonl"
        tools = self.base / "ssh-tools"
        tools.mkdir()
        fake = tools / "ssh"
        fake.write_text("""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
payload = sys.stdin.read()
with Path(os.environ["FAKE_SSH_LOG"]).open("a") as stream:
    stream.write(json.dumps({"args": sys.argv[1:], "request": payload}) + "\\n")
if os.environ.get("FAKE_SSH_OFFLINE"):
    print("ssh: connect to host mini-pc port 22: Connection refused", file=sys.stderr)
    print("Connection closed", file=sys.stderr)
    sys.exit(255)
environment = json.loads(Path(os.environ["FAKE_TARGET_ENV"]).read_text())
result = subprocess.run(["/bin/sh", "-c", sys.argv[-1]], env=environment,
                        input=payload, text=True, start_new_session=True)
sys.exit(result.returncode)
""")
        fake.chmod(0o700)
        self.controller_env = dict(
            self.env,
            HOME=str(self.controller),
            PATH=str(tools) + ":" + self.env["PATH"],
            XDG_CONFIG_HOME=str(self.controller / ".config"),
            XDG_DATA_HOME=str(self.controller / ".local/share"),
            XDG_STATE_HOME=str(self.controller / ".local/state"),
            FAKE_SSH_LOG=str(self.ssh_log),
            FAKE_TARGET_ENV=str(target_env),
        )

    def call(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/command.py"), *arguments],
            env=self.controller_env,
            cwd=self.controller,
            text=True,
            capture_output=True,
            timeout=45,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertNotIn("Traceback", output)
        return output

    def caller_files(self):
        return {
            str(path.relative_to(self.controller)): path.read_bytes()
            for path in self.controller.rglob("*")
            if path.is_file()
        }

    def change_content(self):
        (self.content / ".file").write_text("remote content update\n")
        command("git", "-C", str(self.content), "add", ".file")
        command(
            "git",
            "-C",
            str(self.content),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fix: remote fixture",
        )

    def test_status_check_diff_and_doctor_use_only_the_target(self):
        self.connect()
        before = self.caller_files()
        backups = sorted((self.home / "fleet-backups").iterdir())
        output = self.call("status", "beelink")
        self.assertIn("Machine · beelink", output)
        self.assertIn("Command Example", output)
        self.assertNotIn("Caller only", output)
        self.assertIn("beelink: status succeeded", output)
        self.call("check", "beelink")
        self.call("status", "--check", "beelink")
        self.assertIn("No differences.", self.call("diff", "beelink"))
        self.assertIn("PASS", self.call("doctor", "beelink"))
        self.change_content()
        self.assertIn("updates available", self.call("check", "beelink", expected=1))
        diff = self.call("diff", "beelink", expected=1)
        self.assertIn("~/.file", diff)
        self.assertIn("+remote content update", diff)
        stat = self.call("diff", "--stat", "beelink", expected=1)
        self.assertIn("~/.file", stat)
        self.assertNotIn("+remote content update", stat)
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        self.assertEqual(self.caller_files(), before)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)
        for item in map(json.loads, self.ssh_log.read_text().splitlines()):
            self.assertEqual(item["args"][-3:], ["--", "mini-pc", remote.REMOTE_COMMAND])
            for option in ("BatchMode=yes", "ConnectTimeout=10", "ConnectionAttempts=1"):
                self.assertIn(option, item["args"])
            request = json.loads(item["request"])
            self.assertEqual(set(request), {"version", "action", "check", "stat"})

    def test_update_returns_review_and_writes_only_target_record(self):
        self.connect()
        before = self.caller_files()
        _, previous = self.record()
        self.change_content()
        output = self.call("update", "beelink")
        self.assertIn("Machine · beelink", output)
        self.assertIn("3 · Files", output)
        self.assertIn("~ ~/.file", output)
        self.assertIn("6 · Next steps", output)
        self.assertIn("beelink: update succeeded (exit 0).", output)
        record, folder = self.record()
        self.assertNotEqual(previous, folder)
        self.assertEqual(record["result"], "SUCCESS")
        self.assertTrue(folder.is_relative_to(self.home))
        self.assertEqual((self.home / ".file").read_text(), "remote content update\n")
        self.assertEqual(self.caller_files(), before)
        self.assert_quiet_status()

    def test_unreachable_is_one_line_one_attempt_and_no_local_writes(self):
        self.connect()
        before = self.caller_files()
        self.controller_env["FAKE_SSH_OFFLINE"] = "1"
        output = self.call("status", "beelink", expected=2)
        self.assertEqual(len(output.splitlines()), 1)
        self.assertIn("beelink: ssh: connect", output)
        self.assertIn("Connection refused", output)
        self.assertEqual(len(self.ssh_log.read_text().splitlines()), 1)
        self.assertEqual(self.caller_files(), before)

    def test_incomplete_target_stops_and_names_all_gaps(self):
        self.connect()
        path = self.home / ".config/fleet/profile.toml"
        profile = read_toml(path)
        profile.update(git_name="", git_email="", pins={"node": "latest"})
        profile.pop("domain_chosen")
        atomic(path, toml(profile))
        before = path.read_bytes()
        backups = sorted((self.home / "fleet-backups").iterdir())
        output = self.call("update", "beelink", expected=2)
        for gap in (
            "never explicitly chosen",
            "Git name is empty",
            "Git email is empty",
            "node pin is floating",
        ):
            self.assertIn(gap, output)
        self.assertIn("Complete these choices on the target", output)
        self.assertNotIn("Confirm", output)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted((self.home / "fleet-backups").iterdir()), backups)

    def test_unattended_conflict_stops_without_overwrite(self):
        self.connect()
        (self.home / ".file").write_text("keep target edit\n")
        self.change_content()
        output = self.call("update", "beelink", expected=1)
        self.assertIn("Apply stopped", output)
        self.assertIn("beelink: update failed", output)
        self.assertEqual((self.home / ".file").read_text(), "keep target edit\n")
        self.assertEqual(self.record()[0]["result"], "FAILED")

    def test_forbidden_commands_and_unsafe_targets_never_start_ssh(self):
        self.connect()
        for action, reason in (
            ("rollback", "destructive"),
            ("uninstall", "destructive"),
            ("configure", "terminal"),
        ):
            output = self.call(action, "beelink", expected=2)
            self.assertEqual(len(output.splitlines()), 1)
            self.assertIn(reason, output)
            self.assertFalse(self.ssh_log.exists())
        output = self.call("status", "missing", expected=2)
        self.assertIn("no saved machine", output)
        path = self.controller / registry.REGISTRY
        for target in ("", "-oProxyCommand=bad", "mini-pc; touch /tmp/unsafe", "mini\npc"):
            data = registry.load(self.controller)
            data["machines"]["beelink"]["ssh_target"] = target
            atomic(path, registry.serialize(data))
            self.call("status", "beelink", expected=2)
            self.assertFalse(self.ssh_log.exists())

    def test_set_ssh_creates_records_preserves_facts_and_survives_refresh(self):
        self.enroll()
        self.assertIn("no connection", self.cli("machines"))
        output = self.cli("machines", "set-ssh", "beelink", "mini-pc")
        self.assertIn("~ ~/.local/share/fleet/registry.toml", output)
        self.assertEqual(registry.load(self.home)["machines"]["beelink"]["ssh_target"], "mini-pc")
        local = socket.gethostname()
        before = registry.load(self.home)["machines"][local]
        self.cli("machines", "set-ssh", local, "example@host")
        after = registry.load(self.home)["machines"][local]
        self.assertEqual(after["content_commit"], before["content_commit"])
        self.cli("update")
        self.assertEqual(registry.load(self.home)["machines"][local]["ssh_target"], "example@host")
        self.assertIn("mini-pc", self.cli("machines"))
        self.assertEqual((self.home / registry.REGISTRY).stat().st_mode & 0o777, 0o600)


class RequestTests(unittest.TestCase):
    def test_receiver_refuses_unlisted_commands_and_extra_data_before_dispatch(self):
        request = dict(version=1, action="status", check=False, stat=False)
        invalid = [
            dict(request, action="rollback"),
            dict(request, action="status; touch unsafe"),
            dict(request, machine="nested"),
            dict(request, stat=True),
            dict(request, check="--command"),
            dict(request, version=2),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                remote.validate_request(value)
        with (
            patch("sys.stdin", io.StringIO(json.dumps(invalid[0]))),
            patch("command.main") as dispatch,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(remote.receive(), 2)
            dispatch.assert_not_called()
            self.assertIn("Only status", output.getvalue())


if __name__ == "__main__":
    unittest.main()
