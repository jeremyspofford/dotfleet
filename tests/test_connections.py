"""Check registration flags, SSH guidance, config discovery, and terminal choices.

Usage: python3 -m unittest discover -s tests -v. SSH is always a fixture executable.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_command as commands
import command as cli
import connections
import registry
import remote
import topology
from common import atomic, read_toml, toml


def fake_ssh(home, environment):
    tools = home / "test-ssh-bin"
    tools.mkdir()
    path = tools / "ssh"
    path.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with Path(os.environ["SSH_TEST_LOG"]).open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
failure = os.environ.get("SSH_TEST_FAILURE", "")
if failure:
    print(failure, file=sys.stderr)
    sys.exit(255)
sys.exit(0)
""")
    path.chmod(0o700)
    environment.update(
        PATH=str(tools) + ":" + environment["PATH"], SSH_TEST_LOG=str(home / "ssh-calls.jsonl")
    )
    return path


def seed_config(home):
    atomic(
        home / ".ssh/config",
        "Host mini-pc already *.example ?host !excluded\nInclude hosts/*.conf\nHost mini-pc\n",
    )
    atomic(home / ".ssh/hosts/lab.conf", 'Host lab backup\nInclude "~/ .missing"\nInclude config\n')


class RegistrationTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    terminal = commands.CommandTests.terminal
    record = commands.CommandTests.record

    def prepare(self):
        self.enroll()
        fake_ssh(self.home, self.env)

    def test_one_flag_defaults_in_both_directions_and_legacy_forms(self):
        self.prepare()
        for arguments, name, target in [
            (["--target", "mini-pc"], "mini-pc", "mini-pc"),
            (["--name", "lab"], "lab", "lab"),
            (["--target", "user@192.0.2.4"], "user@192.0.2.4", "user@192.0.2.4"),
            (["--name", "beelink", "--target", "mini-pc"], "beelink", "mini-pc"),
            (["legacy"], "legacy", "legacy"),
            (["old-label", "old-host"], "old-label", "old-host"),
        ]:
            with self.subTest(arguments=arguments):
                output = self.cli("machines", "set-ssh", *arguments)
                self.assertIn("SSH connection test passed", output)
                self.assertEqual(registry.load(self.home)["machines"][name]["ssh_target"], target)
                self.assertEqual(
                    topology.load(self.home)["machines"][name]["state"], "SSH test passed"
                )
                call = json.loads((self.home / "ssh-calls.jsonl").read_text().splitlines()[-1])
                self.assertEqual(call[-3:], ["--", target, "true"])
                self.assertIn("BatchMode=yes", call)

    def test_equal_names_show_once_and_failed_test_keeps_registration(self):
        self.prepare()
        self.env["SSH_TEST_FAILURE"] = (
            "ssh: Could not resolve hostname mini-pc: Name or service not known"
        )
        output = self.cli("machines", "set-ssh", "--target", "mini-pc", expected=2)
        self.assertIn("not in your SSH config", output)
        self.assertIn("Next: use --target user@host or user@IP", output)
        self.assertIn("Saved connection kept", output)
        self.assertEqual(registry.load(self.home)["machines"]["mini-pc"]["ssh_target"], "mini-pc")
        listing = self.cli("machines", "list")
        self.assertEqual(listing.count("mini-pc"), 1)
        self.assertIn("SSH uses this name", listing)
        self.assertEqual(self.record()[0]["result"], "SUCCESS")

    def test_picker_reads_includes_excludes_registered_and_tests_choice(self):
        self.prepare()
        seed_config(self.home)
        self.cli("machines", "set-ssh", "--target", "already")
        self.assertEqual(connections.choices(self.home), (["mini-pc", "lab", "backup"], ""))
        before = {
            str(path): path.read_bytes()
            for path in (self.home / ".ssh").rglob("*")
            if path.is_file()
        }
        output = self.terminal(
            ["machines", "set-ssh"],
            [(b"Host number [cancel]:", b"x\n"), (b"Host number [cancel]:", b"2\n")],
        )
        self.assertIn("1. mini-pc", output)
        self.assertIn("2. lab", output)
        self.assertIn("3. backup", output)
        self.assertNotIn("*.example", output)
        self.assertIn("PASS: lab: SSH connection test passed.", output)
        self.assertEqual(registry.load(self.home)["machines"]["lab"]["ssh_target"], "lab")
        self.assertEqual(
            {
                str(path): path.read_bytes()
                for path in (self.home / ".ssh").rglob("*")
                if path.is_file()
            },
            before,
        )
        self.assertEqual(len((self.home / "ssh-calls.jsonl").read_text().splitlines()), 2)

    def test_no_config_falls_back_to_typing_and_no_terminal_never_prompts(self):
        self.prepare()
        path = self.home / registry.REGISTRY
        before = path.read_bytes()
        output = self.cli("machines", "set-ssh", expected=1)
        self.assertIn("No terminal attached", output)
        self.assertIn("--target", output)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.home / "ssh-calls.jsonl").exists())
        output = self.terminal(
            ["machines", "set-ssh"], [(b"SSH target [cancel]:", b"user@192.0.2.1\n")]
        )
        self.assertIn("No SSH config file found", output)
        self.assertIn("SSH connection test passed", output)
        self.assertIn("user@192.0.2.1", registry.load(self.home)["machines"])

    def test_flagged_command_audit_keeps_the_same_core_actions(self):
        self.prepare()
        self.cli("domain", "set", "--name", "personal")
        self.assertEqual(read_toml(self.home / cli.PROFILE)["domain"], "personal")
        self.cli("groups", "set", "--name", "extra", "--state", "off")
        self.assertFalse((self.home / ".extra").exists())
        self.cli("groups", "set", "--name", "extra", "--state", "on")
        self.assertTrue((self.home / ".extra").exists())
        self.cli("machines", "set-ssh", "--target", "lab")
        self.cli("machines", "remove", "--name", "lab")
        self.assertNotIn("lab", registry.load(self.home)["machines"])
        self.cli("backup")
        _, backup = self.record()
        (self.home / ".file").write_text("local edit")
        self.terminal(["rollback", "--backup", backup.name], [(b"Confirm [cancel]:", b"restore\n")])
        self.assertEqual((self.home / ".file").read_text(), "original\n")
        for arguments in (
            ["groups", "--state", "off"],
            ["pins", "set", "--version", "24.14.1"],
            ["pins", "remove", "--tool", "node", "--version", "1.0.0"],
        ):
            self.cli(*arguments, expected=2)


class ConnectionUnitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def test_failure_classes_have_exact_guidance(self):
        record = {name: False if kind is bool else "" for name, kind in registry.FIELDS.items()}
        record.update(hostname="mini-pc", ssh_target="mini-pc")
        atomic(
            self.home / registry.REGISTRY,
            registry.serialize({"schema_version": 1, "machines": {"mini-pc": record}}),
        )
        environment = dict(os.environ)
        ssh = fake_ssh(self.home, environment)
        for message, reason, next_step in [
            (
                "ssh: Could not resolve hostname mini-pc: Name or service not known",
                "not in your SSH config",
                "--target user@host or user@IP",
            ),
            (
                "mini-pc: Permission denied (publickey).",
                "SSH authentication failed",
                "ssh-keygen if needed, then ssh-copy-id mini-pc",
            ),
            (
                "ssh: connect to host mini-pc port 22: Connection refused",
                "SSH connection refused",
                "awake and reachable",
            ),
        ]:
            with (
                patch.dict(os.environ, dict(environment, SSH_TEST_FAILURE=message)),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(remote.execute(self.home, "mini-pc", "status"), 2)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn(reason, lines[0])
            self.assertIn(next_step, lines[1])
        ssh.unlink()
        for system, install in (
            ("Linux", "Linux package manager"),
            ("WSL", "Linux package manager"),
            ("macOS", "built-in OpenSSH client"),
            ("Windows", "OpenSSH Client optional feature"),
        ):
            with (
                patch.dict(os.environ, dict(environment, PATH=str(ssh.parent))),
                patch.object(registry, "os_name", return_value=system),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(remote.execute(self.home, "mini-pc", "status"), 2)
            self.assertEqual(len(output.getvalue().splitlines()), 2)
            self.assertIn("SSH is not installed", output.getvalue())
            self.assertIn(install, output.getvalue())

    def test_include_cycles_equals_and_empty_configs(self):
        self.assertIn("No SSH config file", connections.choices(self.home)[1])
        atomic(self.home / ".ssh/config", "Host * ?foo !omit\n")
        self.assertIn("No concrete SSH hosts", connections.choices(self.home)[1])
        atomic(self.home / ".ssh/config", 'Host=one TWO one\nInclude="hosts/*.conf"\n')
        atomic(
            self.home / ".ssh/hosts/a.conf",
            'Host three\nInclude config\nMatch exec "touch never-run"\n',
        )
        self.assertEqual(connections.hosts(self.home), ["one", "TWO", "three"])

    def test_connection_test_has_a_total_timeout_and_preserves_saved_link(self):
        with (
            patch.object(connections, "invoke", side_effect=subprocess.TimeoutExpired("ssh", 10)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(remote.test_connection(self.home, "mini-pc", "mini-pc"), 2)
        self.assertIn("timed out", output.getvalue())
        self.assertIn("Saved connection kept", output.getvalue())

    def test_pin_flags_normalize_without_installing_from_help(self):
        import command_arguments

        parser = cli.parser()
        args = command_arguments.normalize(
            parser.parse_args(["pins", "set", "--tool", "node", "--version", "24.14.1"]), parser
        )
        self.assertEqual(args.pin, "node@24.14.1")
        for name, flags in (
            ("machines", ["--name", "--target"]),
            ("groups", ["--name", "--state"]),
            ("domain", ["--name"]),
            ("pins", ["--tool", "--version"]),
            ("rollback", ["--backup"]),
        ):
            with (
                contextlib.redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exit,
            ):
                parser.parse_args([name, "--help"])
            self.assertEqual(exit.exception.code, 0)
            self.assertNotIn("legacy_", output.getvalue())
            for flag in flags:
                self.assertIn(flag, output.getvalue())

    def test_open_interface_can_finish_with_the_old_backup_argument(self):
        import tui_core

        with (
            patch.object(tui_core.ui_channel, "active", return_value=True),
            patch.object(tui_core, "dispatch", return_value=0) as dispatch,
        ):
            self.assertEqual(tui_core.main(["rollback", "/saved/backup"]), 0)
            dispatch.assert_called_once_with("rollback", "/saved/backup", None)


if __name__ == "__main__":
    unittest.main()
