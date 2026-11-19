"""Deploy Fleet to a second home over a fake SSH and register both machines.

Each SSH target maps to its own home and environment, so two homes on one host act as two
machines. FLEET_MACHINE_NAME gives each home its own fleet name. No network connection is used.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_deploy.py --help.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import unittest

import deploy
import registry
import test_command
import test_manager as helpers
from test_manager import ROOT

FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
arguments = sys.argv[1:]
split = arguments.index("--")
target, command = arguments[split + 1], arguments[split + 2]
interactive = "-t" in arguments[:split]
with Path(os.environ["FAKE_SSH_LOG"]).open("a") as log:
    log.write(json.dumps({"target": target, "command": command, "tty": interactive}) + "\n")
homes = json.loads(Path(os.environ["FAKE_SSH_HOMES"]).read_text())
if target not in homes:
    print(f"ssh: Could not resolve hostname {target}: Name or service not known", file=sys.stderr)
    sys.exit(255)
result = subprocess.run(["/bin/sh", "-c", command], env=homes[target], stdin=sys.stdin)
sys.exit(result.returncode)
"""

# Serve the published installer's downloads from the fixture's manager origin.
FAKE_CURL = r"""#!/usr/bin/env python3
import os, sys
from pathlib import Path
arguments, output, url = sys.argv[1:], None, None
index = 0
while index < len(arguments):
    if arguments[index] in ("-o", "--max-time"):
        if arguments[index] == "-o":
            output = arguments[index + 1]
        index += 2
        continue
    if not arguments[index].startswith("-"):
        url = arguments[index]
    index += 1
with Path(os.environ["FAKE_CURL_LOG"]).open("a") as log:
    log.write(url + "\n")
raw = os.environ["FAKE_CURL_RAW"] + "/"
if url == "https://github.com":
    data = b""
elif url.startswith(raw) and (Path(os.environ["FAKE_CURL_ROOT"]) / url[len(raw):]).is_file():
    data = (Path(os.environ["FAKE_CURL_ROOT"]) / url[len(raw):]).read_bytes()
else:
    print(f"curl: (22) The requested URL returned error: 404 {url}", file=sys.stderr)
    sys.exit(22)
if output:
    Path(output).write_bytes(data)
else:
    sys.stdout.buffer.write(data)
"""


class DeployTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def prepare(self, target_path=None, manager=None):
        """Enroll the caller, then create an empty target home that no record names."""
        self.enroll()
        self.target = self.base / "target-home"
        self.target.mkdir()
        tools = self.base / "ssh-tools"
        tools.mkdir()
        (tools / "ssh").write_text(FAKE_SSH)
        (tools / "ssh").chmod(0o700)
        (self.home / ".ssh").mkdir(exist_ok=True)
        (self.home / ".ssh/config").write_text("Host mini-pc\n  HostName 192.0.2.10\n")
        self.ssh_log = self.base / "ssh.log"
        caller = dict(self.env, FLEET_MACHINE_NAME="caller-box")
        caller["PATH"] = str(tools) + ":" + caller["PATH"]
        target = dict(
            self.env,
            HOME=str(self.target),
            XDG_CONFIG_HOME=str(self.target / ".config"),
            XDG_DATA_HOME=str(self.target / ".local/share"),
            XDG_STATE_HOME=str(self.target / ".local/state"),
            MISE_TRUSTED_CONFIG_PATHS=str(self.target),
            FLEET_MACHINE_NAME="mini-pc",
            FAKE_SSH_LOG=str(self.ssh_log),
        )
        target["PATH"] = target_path or (str(tools) + ":" + self.env["PATH"])
        homes = self.base / "homes.json"
        caller.update(
            FAKE_SSH_LOG=str(self.ssh_log),
            FAKE_SSH_HOMES=str(homes),
            FLEET_DEPLOY_MANAGER=str(manager or self.origin),
        )
        target["FAKE_SSH_HOMES"] = str(homes)
        homes.write_text(json.dumps({"mini-pc": target, "caller": caller}))
        self.caller_env = caller

    def call(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/command.py"), *arguments],
            env=self.caller_env,
            cwd=self.home,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertNotIn("Traceback", output)
        return output

    def deploy(self, *extra, expected=0):
        return self.call(
            "machines",
            "install",
            "mini-pc",
            "--domain",
            "core",
            "--git-name",
            "Target Person",
            "--git-email",
            "target@example.invalid",
            "--repo",
            str(self.content),
            *extra,
            expected=expected,
        )

    def registry(self, home):
        path = home / ".local/share/fleet/registry.toml"
        return tomllib.loads(path.read_text())["machines"] if path.exists() else {}

    def run_entries(self, home):
        folder = home / ".local/share/fleet/runs"
        return [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]

    def test_deploy_enrolls_the_target_and_registers_both_machines(self):
        self.prepare()
        output = self.deploy("--return-target", "caller")
        self.assertIn(
            f"mini-pc: reachable · {registry.os_name()} · floor present (bash, curl, git, tar, gzip).",
            output,
        )
        self.assertIn("mini-pc: running the Fleet installer on the target.", output)
        self.assertIn("PASS: mini-pc reaches caller-box through caller.", output)
        self.assertIn("Machine · mini-pc", output)
        caller = self.registry(self.home)["mini-pc"]
        self.assertEqual(
            (caller["ssh_target"], caller["domain"], caller["os"], caller["enrolled"]),
            ("mini-pc", "core", registry.os_name(), True),
        )
        self.assertTrue(caller["recorded_at"])
        target = self.registry(self.target)
        self.assertTrue(target["mini-pc"]["enrolled"])
        self.assertEqual(target["caller-box"]["ssh_target"], "caller")
        self.assertIn("mini-pc", self.call("machines", "list"))
        commands = [json.loads(line)["command"] for line in self.ssh_log.read_text().splitlines()]
        self.assertIn("sh -s", commands)
        # User values never enter a remote command; only fixed programs run there.
        self.assertFalse(any("Target Person" in command for command in commands))
        self.assertTrue(
            any(entry.get("command") == "machines install" for entry in self.run_entries(self.home))
        )
        self.assertTrue(
            any(entry.get("command") == "machines join" for entry in self.run_entries(self.target))
        )

    def test_deploy_runs_the_published_installer_with_no_local_manager(self):
        """Stage no manager, so the target pipes install.sh from curl as a real deploy does."""
        self.prepare()
        downloads = self.base / "curl-tools"
        downloads.mkdir()
        (downloads / "curl").write_text(FAKE_CURL)
        (downloads / "curl").chmod(0o700)
        homes = json.loads((self.base / "homes.json").read_text())
        target = homes["mini-pc"]
        curl_log = self.base / "curl.log"
        target.update(
            PATH=str(downloads) + ":" + target["PATH"],
            FAKE_CURL_LOG=str(curl_log),
            FAKE_CURL_RAW=deploy.RAW,
            FAKE_CURL_ROOT=str(self.origin),
            # The published manager clone resolves to the fixture's origin.
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_1=f"url.{self.origin}.insteadOf",
            GIT_CONFIG_VALUE_1="https://github.com/jeremyspofford/fleet.git",
        )
        (self.base / "homes.json").write_text(json.dumps(homes))
        self.caller_env["FLEET_DEPLOY_MANAGER"] = ""
        output = self.deploy("--return-target", "caller")
        self.assertNotIn("ModuleNotFoundError", output)
        self.assertIn("PASS: mini-pc reaches caller-box through caller.", output)
        fetched = curl_log.read_text().splitlines()
        self.assertIn(deploy.RAW + "/install.sh", fetched)
        self.assertIn(deploy.RAW + "/scripts/run_index.py", fetched)
        self.assertTrue((self.target / ".local/share/fleet/manager/scripts/command.py").is_file())
        self.assertEqual((self.target / ".file").read_text(), "original\n")
        self.assertTrue(self.registry(self.target)["mini-pc"]["enrolled"])
        self.assertTrue(self.registry(self.home)["mini-pc"]["enrolled"])
        self.assertTrue(
            any(entry.get("command") == "install" for entry in self.run_entries(self.target))
        )

    def test_a_failed_reverse_test_is_reported_and_never_blocks_enrollment(self):
        self.prepare()
        output = self.deploy("--return-target", "nowhere")
        self.assertIn("Warning: reverse test failed", output)
        self.assertIn("the enrollment stands", output)
        self.assertTrue(self.registry(self.home)["mini-pc"]["enrolled"])
        self.assertEqual(self.registry(self.target)["caller-box"]["ssh_target"], "nowhere")

    def test_an_unreachable_target_leaves_no_entry(self):
        self.prepare()
        output = self.call(
            "machines",
            "install",
            "far-box",
            "--target",
            "far-box",
            "--domain",
            "core",
            "--git-name",
            "A",
            "--git-email",
            "a@example.invalid",
            expected=2,
        )
        self.assertIn("deploy failed (unreachable)", output)
        self.assertNotIn("far-box", self.registry(self.home))

    def test_a_missing_floor_names_each_item_and_its_install_command(self):
        limited = self.base / "limited"
        limited.mkdir(parents=True)
        for tool in (
            "sh",
            "bash",
            "uname",
            "grep",
            "cat",
            "mkdir",
            "curl",
            "tar",
            "gzip",
            "python3",
            "sha256sum",
        ):
            location = shutil.which(tool)
            if location:
                (limited / tool).symlink_to(location)
        self.prepare(target_path=str(limited))
        output = self.deploy(expected=2)
        self.assertIn("deploy failed (missing floor)", output)
        self.assertIn("the target lacks the deploy floor: git.", output)
        self.assertIn("git: install git with the system package manager", output)
        self.assertIn("Fleet installs no system packages and uses no admin rights.", output)
        self.assertNotIn("mini-pc", self.registry(self.home))

    def fake_manager(self, code):
        manager = self.base / f"fake-manager-{code}"
        manager.mkdir()
        (manager / "install.sh").write_text(
            f"#!/usr/bin/env bash\necho fake installer\nexit {code}\n"
        )
        return manager

    def test_an_installer_that_stops_without_enrolling_is_a_wizard_abort(self):
        self.base.mkdir(exist_ok=True)
        self.prepare(manager=self.fake_manager(0))
        output = self.deploy(expected=1)
        self.assertIn("deploy failed (wizard aborted)", output)
        self.assertIn("Nothing was registered on either machine.", output)
        self.assertNotIn("mini-pc", self.registry(self.home))
        self.assertEqual(self.registry(self.target), {})

    def test_a_failed_installer_is_an_enrollment_failure(self):
        self.prepare(manager=self.fake_manager(1))
        output = self.deploy(expected=1)
        self.assertIn("deploy failed (enrollment failed)", output)
        self.assertNotIn("mini-pc", self.registry(self.home))

    def test_no_terminal_and_no_flags_names_the_gaps_before_connecting(self):
        self.prepare()
        output = self.call("machines", "install", "mini-pc", expected=2)
        self.assertIn("Supply --domain, --git-name, --git-email for a scripted deploy.", output)
        self.assertFalse(self.ssh_log.exists())

    def test_the_return_target_prompt_repeats_on_invalid_input_and_accepts_a_target(self):
        self.prepare()
        self.env = self.caller_env
        output = test_command.CommandTests.terminal(
            self,
            [
                "machines",
                "install",
                "mini-pc",
                "--domain",
                "core",
                "--git-name",
                "Target Person",
                "--git-email",
                "target@example.invalid",
                "--repo",
                str(self.content),
            ],
            [
                (b"Return target [skip]:", b"not a target\n"),
                (b"Return target [skip]:", b"caller\n"),
            ],
        )
        self.assertIn("Type skip, or press Enter, to save the caller without a connection.", output)
        self.assertIn("SSH target must be a host name or user@host", output)
        self.assertIn("PASS: mini-pc reaches caller-box through caller.", output)
        self.assertEqual(self.registry(self.target)["caller-box"]["ssh_target"], "caller")
        logged = [json.loads(line) for line in self.ssh_log.read_text().splitlines()]
        self.assertTrue(any(entry["tty"] for entry in logged))

    def test_skip_saves_the_caller_without_a_connection_or_reverse_test(self):
        self.prepare()
        self.env = self.caller_env
        output = test_command.CommandTests.terminal(
            self,
            [
                "machines",
                "install",
                "mini-pc",
                "--domain",
                "core",
                "--git-name",
                "Target Person",
                "--git-email",
                "target@example.invalid",
                "--repo",
                str(self.content),
            ],
            [(b"Return target [skip]:", b"\n")],
        )
        self.assertIn("saved caller-box without a connection; no reverse test ran.", output)
        self.assertEqual(self.registry(self.target)["caller-box"]["ssh_target"], "")

    def test_an_old_python_bootstraps_fleet_python_through_mise(self):
        old = self.base / "old-python"
        old.mkdir(parents=True)
        (old / "python3").write_text("#!/bin/sh\necho 3.9\n")
        (old / "python3").chmod(0o755)
        self.prepare(target_path=f"{old}:{self.base / 'ssh-tools'}:{self.env['PATH']}")
        mise = self.target / ".local/bin/mise"
        mise.parent.mkdir(parents=True)
        python = self.base / "mise-python"
        (python / "bin").mkdir(parents=True)
        (python / "bin/python3").symlink_to(shutil.which("python3"))
        # Only the Python bootstrap is faked; the installer's other Mise calls reach real Mise.
        mise.write_text(
            "#!/bin/sh\n"
            'case "$1 $2" in\n'
            f'"install python@3.12") echo "$* $MISE_DATA_DIR" >> {self.base / "mise.log"}; exit 0 ;;\n'
            f'"where python@3.12") echo {python}; exit 0 ;;\n'
            "esac\n"
            f'exec {shutil.which("mise")} "$@"\n'
        )
        mise.chmod(0o755)
        output = self.deploy("--return-target", "caller")
        self.assertIn(
            "Python 3.11 or newer is missing (found 3.9). Installing Python 3.12 with Mise.", output
        )
        calls = (self.base / "mise.log").read_text().splitlines()
        self.assertEqual(calls[0], f"install python@3.12 {self.target}/.local/share/fleet/mise")
        link = self.target / ".local/share/fleet/python"
        self.assertEqual(os.readlink(link), str(python))
        self.assertTrue(self.registry(self.home)["mini-pc"]["enrolled"])

    def test_a_missing_certificate_bundle_is_a_floor_item(self):
        facts = {f"have_{tool}": "yes" for tool in deploy.FLOOR} | {"python": "3.12", "os": "Linux"}
        result = subprocess.CompletedProcess(
            [],
            0,
            "\n".join(
                f"{k}={v}" for k, v in (facts | {"https_code": "77", "packager": "apt-get"}).items()
            ),
            "",
        )
        from unittest.mock import patch

        with patch.object(deploy.connections, "invoke", return_value=result):
            with self.assertRaises(deploy.DeployError) as caught:
                deploy.probe(self.home, "box", "box")
        self.assertEqual(caught.exception.kind, "missing floor")
        self.assertIn(
            "  ca-certificates: sudo apt-get install -y ca-certificates", caught.exception.lines
        )
        offline = subprocess.CompletedProcess(
            [], 0, result.stdout.replace("https_code=77", "https_code=6"), ""
        )
        with patch.object(deploy.connections, "invoke", return_value=offline):
            self.assertEqual(deploy.probe(self.home, "box", "box")["system"], "Linux")

    def test_floor_hints_name_one_command_per_item(self):
        self.assertEqual(
            deploy.install_hint("git", "apt-get", "Linux"), "sudo apt-get install -y git"
        )
        self.assertEqual(
            deploy.install_hint("sha256sum", "dnf", "Linux"), "sudo dnf install -y coreutils"
        )
        self.assertEqual(deploy.install_hint("git", "", "Darwin"), "xcode-select --install")
        self.assertTrue(deploy.python_ready({"python": "3.12"}))
        self.assertFalse(deploy.python_ready({"python": "3.9"}))
        self.assertTrue(deploy.python_ready({"python": "3.9", "fleet_python": "yes"}))


if __name__ == "__main__":
    unittest.main()
