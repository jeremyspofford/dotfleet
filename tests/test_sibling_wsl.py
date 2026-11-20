"""Enroll and reach a sibling WSL distro on the same Windows host through a fake wsl.exe.

Usage: python3 -m unittest discover -s tests -v. No real distro, wsl.exe, or network is used.
Help: PYTHONPATH=scripts:tests python3 tests/test_sibling_wsl.py --help.
"""

import json
from pathlib import Path
import socket
import tomllib
import unittest

import test_command
import test_deploy

# Each distro is a fixture home. The fake runs a command in the named distro's environment.
FAKE_WSL = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
arguments = sys.argv[1:]
with Path(os.environ["FAKE_WSL_LOG"]).open("a") as log:
    log.write(json.dumps(arguments) + "\n")
homes = json.loads(Path(os.environ["FAKE_WSL_HOMES"]).read_text())
if arguments[:2] == ["--list", "--verbose"]:
    print("  NAME            STATE           VERSION")
    for name in homes:
        mark = "*" if name == os.environ.get("WSL_DISTRO_NAME") else " "
        print(f"{mark} {name}    Running         2")
    sys.exit(0)
distro = arguments[arguments.index("-d") + 1]
command = arguments[-1]
if distro not in homes:
    print("There is no distribution with the supplied name.", file=sys.stderr)
    sys.exit(1)
environment = homes[distro]
result = subprocess.run(["/bin/sh", "-c", command], env=environment, cwd=environment["HOME"])
sys.exit(result.returncode)
"""


def without_wsl(path):
    """Drop PATH entries that hold a real wsl.exe, such as the Windows folders inside WSL."""
    return ":".join(part for part in path.split(":") if not (Path(part) / "wsl.exe").exists())


class SiblingTests(unittest.TestCase):
    setUp = test_deploy.DeployTests.setUp
    enroll = test_deploy.DeployTests.enroll
    prepare = test_deploy.DeployTests.prepare
    call = test_deploy.DeployTests.call
    registry = test_deploy.DeployTests.registry

    def siblings(self, extra=None):
        """Make the caller distro Ubuntu-26.04 and the empty target distro AlertVenture."""
        self.prepare()
        tools = self.base / "wsl-tools"
        tools.mkdir()
        (tools / "wsl.exe").write_text(FAKE_WSL)
        (tools / "wsl.exe").chmod(0o700)
        self.wsl_log = self.base / "wsl.log"
        homes = self.base / "distros.json"
        # Both distros report this host's one host name. Only the saved names tell them apart.
        caller, target = dict(self.caller_env), json.loads((self.base / "homes.json").read_text())
        target = target["mini-pc"]
        for env, distro in ((caller, "Ubuntu-26.04"), (target, "AlertVenture")):
            env.pop("FLEET_MACHINE_NAME", None)
            env.update(
                WSL_DISTRO_NAME=distro,
                FAKE_WSL_LOG=str(self.wsl_log),
                FAKE_WSL_HOMES=str(homes),
                PATH=str(tools) + ":" + without_wsl(env["PATH"]),
            )
        # A distro without Fleet answers, but it holds no Fleet record.
        bare = dict(target, HOME=str(self.base / "debian-home"), WSL_DISTRO_NAME="Debian")
        (self.base / "debian-home").mkdir()
        distros = {"Ubuntu-26.04": caller, "AlertVenture": target, "Debian": bare} | (extra or {})
        homes.write_text(json.dumps(distros))
        self.caller_env = caller

    def install(self, *arguments, expected=0):
        return self.call(
            "machines",
            "install",
            *arguments,
            "--domain",
            "core",
            "--git-name",
            "Target Person",
            "--git-email",
            "target@example.invalid",
            "--repo",
            str(self.content),
            expected=expected,
        )

    def wsl_calls(self):
        if not self.wsl_log.exists():
            return []
        return [json.loads(line) for line in self.wsl_log.read_text().splitlines()]

    def test_two_distros_on_one_host_enroll_distinctly_and_list_each_other(self):
        self.siblings()
        own = (self.home / ".fleet/machine-name").read_text().strip()
        output = self.install("AlertVenture", "--machine-name", "dell-wsl-alertventure")
        self.assertIn("PASS: dell-wsl-alertventure reaches", output)
        caller, target = self.registry(self.home), self.registry(self.target)
        self.assertEqual(sorted(caller), sorted([own, "dell-wsl-alertventure"]))
        self.assertEqual(sorted(target), sorted([own, "dell-wsl-alertventure"]))
        sibling, back = caller["dell-wsl-alertventure"], target[own]
        self.assertEqual(sibling["ssh_target"], "wsl:AlertVenture")
        self.assertEqual(back["ssh_target"], "wsl:Ubuntu-26.04")
        self.assertEqual(sibling["distro"], "AlertVenture")
        # The same host name, two distros, two IDs, and no collision key.
        self.assertEqual(sibling["system_hostname"], socket.gethostname())
        self.assertEqual(target["dell-wsl-alertventure"]["system_hostname"], socket.gethostname())
        self.assertNotEqual(sibling["machine_id"], caller[own]["machine_id"])
        self.assertFalse([key for key in [*caller, *target] if "~" in key])
        # The target met its siblings at enrollment; Debian has no Fleet record, so it is no machine.
        self.assertIn(["-d", "Debian"], [call[:2] for call in self.wsl_calls()])
        self.assertNotIn("Debian", json.dumps([caller, target]))
        self.assertEqual(
            (self.target / ".fleet/machine-name").read_text().strip(), "dell-wsl-alertventure"
        )
        listing = self.call("machines", "list")
        self.assertRegex(listing, r"Connection +wsl:AlertVenture\n")
        self.assertRegex(listing, r"Distro +AlertVenture\n")
        self.assertFalse((self.base / "ssh.log").exists(), "a sibling deploy never uses SSH")

    def test_an_update_dispatched_to_a_sibling_uses_wsl_exe_and_never_ssh(self):
        self.siblings()
        self.install("AlertVenture", "--machine-name", "dell-wsl-alertventure")
        self.wsl_log.unlink()
        output = self.call("update", "dell-wsl-alertventure")
        self.assertIn("dell-wsl-alertventure: update succeeded through wsl.exe (exit 0).", output)
        calls = self.wsl_calls()
        self.assertTrue(calls)
        self.assertTrue(all(call[:2] == ["-d", "AlertVenture"] for call in calls), calls)
        # --exec runs sh directly, so the command's own quoting reaches the distro unchanged.
        self.assertTrue(all(call[4:7] == ["--exec", "sh", "-c"] for call in calls), calls)
        self.assertFalse((self.base / "ssh.log").exists(), "a sibling update never uses SSH")
        everyone = self.call("update", "--all")
        self.assertIn("· sibling dispatch through wsl.exe", everyone)

    def test_a_name_that_is_a_distro_and_an_ssh_host_stops_for_the_flag(self):
        self.siblings(extra={"mini-pc": {"HOME": "/nonexistent"}})
        output = self.install("mini-pc", expected=1)
        self.assertIn("mini-pc names a WSL distro and an SSH config host.", output)
        self.assertIn("Add --via wsl or --via ssh.", output)
        self.assertEqual([call for call in self.wsl_calls() if "-d" in call], [])
        self.assertFalse((self.base / "ssh.log").exists())

    def test_a_scripted_wsl_deploy_needs_a_chosen_name(self):
        self.siblings()
        output = self.install("AlertVenture", expected=1)
        self.assertIn("A scripted WSL deploy needs --machine-name", output)
        self.assertFalse((self.target / ".fleet").exists())


class ExistingEnrollmentTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def test_an_existing_enrollment_backfills_its_distro_and_keeps_its_name(self):
        import registry
        from common import atomic

        self.enroll()
        path = self.home / ".fleet/registry.toml"
        # Make the record an older version's: no saved name and no new facts.
        (self.home / ".fleet/machine-name").unlink()
        data = registry.load(self.home)
        for record in data["machines"].values():
            record.pop("distro", None)
            record.pop("system_hostname", None)
        atomic(path, registry.serialize(data))
        before = tomllib.loads(path.read_text())["machines"]
        (name,) = before
        self.env.update(WSL_DISTRO_NAME="Ubuntu-26.04", PATH=without_wsl(self.env["PATH"]))
        self.cli("check")
        after = tomllib.loads(path.read_text())["machines"]
        self.assertEqual(list(after), [name])
        self.assertEqual(after[name]["distro"], "Ubuntu-26.04")
        self.assertEqual(after[name]["system_hostname"], socket.gethostname())
        facts = ("distro", "system_hostname")
        unchanged = {key: item for key, item in after[name].items() if key not in facts}
        self.assertEqual(unchanged, before[name])
        self.assertEqual((self.home / ".fleet/machine-name").read_text().strip(), name)
        # Another check changes nothing more.
        self.cli("check")
        self.assertEqual(tomllib.loads(path.read_text())["machines"], after)


class InteropTests(unittest.TestCase):
    setUp = test_command.CommandTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def test_enrollment_without_interop_succeeds_with_one_notice(self):
        self.env.update(WSL_DISTRO_NAME="AlertVenture", PATH=without_wsl(self.env["PATH"]))
        self.env.pop("FLEET_MACHINE_NAME", None)
        output = self.enroll()
        notice = [line for line in output.splitlines() if "Siblings not checked" in line]
        self.assertEqual(
            notice,
            [
                "Siblings not checked: wsl.exe is unavailable. Next: enable interop in"
                " /etc/wsl.conf ([interop] enabled = true), then restart WSL."
            ],
        )
        machines = tomllib.loads((self.home / ".fleet/registry.toml").read_text())["machines"]
        self.assertEqual(len(machines), 1)
        self.assertEqual(next(iter(machines.values()))["distro"], "AlertVenture")
        self.assertIn("SUCCESS", self.cli("logs", "list"))


if __name__ == "__main__":
    unittest.main()
