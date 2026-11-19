"""Verify the bash bootstrap: find a modern bash by path, ask before installing one, and run on 3.2.

The prompt tests use a pseudo-terminal. The bash 3.2 tests run the real bootstrap in the
bash:3.2 container image. They skip without Docker unless FLEET_REQUIRE_DOCKER=1.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_bash_floor.py --help.
"""

import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FLOOR = ROOT / "scripts/bash_floor.sh"
IMAGE = "bash:3.2"
HOMEBREW = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"


def docker_ready():
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        result = subprocess.run(["docker", "pull", "-q", IMAGE], capture_output=True, timeout=300)
    return result.returncode == 0


DOCKER = docker_ready()
if os.environ.get("FLEET_REQUIRE_DOCKER") == "1" and not DOCKER:
    raise RuntimeError(f"FLEET_REQUIRE_DOCKER=1, but the {IMAGE} image cannot run.")


class Fixture:
    """An old bash on PATH, plus an optional Homebrew prefix whose install logs and succeeds."""

    def __init__(self, base, brew=True, real_bash=None):
        self.base = base
        self.bin = base / "bin"
        self.bin.mkdir()
        self.prefix = base / "homebrew"
        self.brew_log = base / "brew.log"
        self.run_log = base / "resolved.log"
        old = self.bin / "bash"
        old.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = -c ]; then case $2 in *BASH_VERSINFO*) echo 3.2; exit 0 ;; esac; fi\n'
            "exit 97\n"
        )
        old.chmod(0o755)
        # The installed bash reports 5.2 and records every other start, so tests see the exec.
        resolved = (
            "#!/bin/sh\\n"
            'if [ "$1" = -c ]; then case $2 in *BASH_VERSINFO*) echo 5.2; exit 0 ;; esac; fi\\n'
            f'echo "$*" >> {self.run_log}\\n'
        )
        if brew:
            (self.prefix / "bin").mkdir(parents=True)
            fake = self.prefix / "bin/brew"
            fake.write_text(
                "#!/bin/sh\n"
                f'echo "$*" >> {self.brew_log}\n'
                'case "$1" in\n'
                f"--prefix) echo {self.prefix} ;;\n"
                f"install) printf '{resolved}' > {self.prefix}/bin/bash;"
                f" chmod 755 {self.prefix}/bin/bash ;;\n"
                "esac\n"
            )
            fake.chmod(0o755)
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(base),
            "FLEET_BREW_PREFIXES": str(self.prefix),
            "LC_ALL": "C",
        }

    def installs(self):
        calls = self.brew_log.read_text().splitlines() if self.brew_log.exists() else []
        return [call for call in calls if call.startswith("install")]


class BashFloorTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)

    def ensure(self, fixture, accept="no", **options):
        """Run fleet_ensure_bash in POSIX sh. Without a terminal, /dev/tty cannot open."""
        return subprocess.run(
            ["/bin/sh", "-c", f'. "{FLOOR}"; fleet_ensure_bash {accept}'],
            env=fixture.env,
            text=True,
            capture_output=True,
            start_new_session=True,
            timeout=60,
            **options,
        )

    def terminal(self, fixture, answers):
        """Answer prompts through a pseudo-terminal. Return the output and the exit code."""
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["/bin/sh", "-c", f'. "{FLOOR}"; fleet_ensure_bash no'],
            env=fixture.env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            preexec_fn=lambda: __import__("fcntl").ioctl(0, __import__("termios").TIOCSCTTY, 0),
        )
        os.close(slave)
        data, sent, cursor = b"", 0, 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    data += os.read(master, 65536)
                except OSError:
                    break
            if sent < len(answers) and b"Install bash [stop]:" in data[cursor:]:
                os.write(master, answers[sent])
                sent += 1
                cursor = len(data)
        process.wait(timeout=10)
        try:
            while select.select([master], [], [], 0.1)[0]:
                data += os.read(master, 65536)
        except OSError:
            pass
        os.close(master)
        self.assertEqual(sent, len(answers), data)
        return data.decode().replace("\r\n", "\n"), process.returncode

    def test_a_modern_bash_on_path_is_used_and_nothing_is_installed(self):
        fixture = Fixture(self.base)
        real = shutil.which("bash")
        (fixture.bin / "bash").unlink()
        (fixture.bin / "bash").symlink_to(real)
        result = self.ensure(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(fixture.bin / "bash"))
        self.assertEqual(result.stderr, "")
        self.assertEqual(fixture.installs(), [])

    def test_an_existing_homebrew_bash_is_found_by_path_without_a_prompt(self):
        fixture = Fixture(self.base)
        (fixture.prefix / "bin/bash").symlink_to(shutil.which("bash"))
        result = self.ensure(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(fixture.prefix / "bin/bash"))
        self.assertEqual(fixture.installs(), [])

    def test_the_prompt_comes_before_any_install_and_yes_installs(self):
        fixture = Fixture(self.base)
        output, code = self.terminal(fixture, [b"later\n", b"yes\n"])
        self.assertEqual(code, 0, output)
        self.assertIn(f"Found bash 3.2 at {fixture.bin}/bash.", output)
        self.assertIn("Fleet's scripts need bash 4.4 or newer.", output)
        self.assertIn(f"Fleet will install bash with Homebrew: {fixture.prefix}/bin/brew", output)
        self.assertIn("  stop  Stop now. Nothing is installed.", output)
        self.assertIn("Type yes or stop, then press Enter. Enter alone chooses stop.", output)
        self.assertIn("Type yes or stop.\n", output)
        self.assertEqual(fixture.installs(), ["install bash"])
        self.assertTrue(output.rstrip().endswith(f"{fixture.prefix}/bin/bash"), output)

    def test_stop_and_enter_alone_install_nothing(self):
        for answer in (b"stop\n", b"\n"):
            with self.subTest(answer=answer):
                fixture = Fixture(Path(tempfile.mkdtemp(dir=self.base)))
                output, code = self.terminal(fixture, [answer])
                self.assertEqual(code, 1, output)
                self.assertIn("Stopped. Nothing was installed.", output)
                self.assertEqual(fixture.installs(), [])
                self.assertFalse((fixture.prefix / "bin/bash").exists())

    def test_without_a_terminal_consent_is_never_assumed(self):
        fixture = Fixture(self.base)
        result = self.ensure(fixture)
        self.assertEqual(result.returncode, 1)
        self.assertIn("No terminal is attached, so Fleet cannot ask first.", result.stderr)
        self.assertIn("add --update-bash to accept the bash install.", result.stderr)
        self.assertEqual(fixture.installs(), [])
        accepted = self.ensure(fixture, "yes")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), str(fixture.prefix / "bin/bash"))
        self.assertEqual(fixture.installs(), ["install bash"])

    def test_without_homebrew_it_stops_and_names_the_homebrew_command(self):
        fixture = Fixture(self.base, brew=False)
        result = self.ensure(fixture, "yes")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(f'/bin/bash -c "$(curl -fsSL {HOMEBREW})"', result.stderr)
        self.assertIn("Fleet does not install Homebrew", result.stderr)

    def test_help_runs_only_when_executed(self):
        result = subprocess.run(["bash", str(FLOOR), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        sourced = subprocess.run(
            ["/bin/sh", "-c", f'. "{FLOOR}"; echo sourced'], capture_output=True, text=True
        )
        self.assertEqual(sourced.stdout, "sourced\n")


@unittest.skipUnless(DOCKER, f"Docker cannot run the {IMAGE} image.")
class Bash32Tests(unittest.TestCase):
    """Run the bootstrap layer on a real bash 3.2, the bash that macOS ships."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.base.chmod(0o777)

    def run32(self, *command, fixture=None):
        environment = []
        for key, value in (fixture.env if fixture else {"HOME": str(self.base)}).items():
            if key != "PATH":
                environment += ["-e", f"{key}={value}"]
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{ROOT}:/manager:ro",
                "-v",
                f"{self.base}:{self.base}",
                "-w",
                "/manager",
                *environment,
                IMAGE,
                *command,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        for problem in (
            "unbound variable",
            "syntax error",
            "command not found",
            "bad substitution",
        ):
            self.assertNotIn(problem, output)
        return result.returncode, output

    def test_the_image_really_is_bash_3_2(self):
        code, output = self.run32("bash", "-c", "echo $BASH_VERSION")
        self.assertEqual(code, 0, output)
        self.assertTrue(output.startswith("3.2."), output)

    def test_the_floor_functions_run_under_bash_3_2_strict_mode(self):
        script = (
            "set -euo pipefail; . scripts/bash_floor.sh;"
            ' v=$(fleet_bash_version "$BASH"); echo "version=$v";'
            " if fleet_find_bash; then echo found; else echo none; fi"
        )
        code, output = self.run32("bash", "-c", script)
        self.assertEqual(code, 0, output)
        self.assertIn("version=3.2", output)
        self.assertIn("none", output)

    def test_installer_help_works_on_bash_3_2(self):
        code, output = self.run32("bash", "install.sh", "--help")
        self.assertEqual(code, 0, output)
        self.assertIn("--update-bash", output)

    def test_installer_without_homebrew_stops_and_names_the_command(self):
        fixture = Fixture(self.base, brew=False)
        code, output = self.run32("bash", "install.sh", fixture=fixture)
        self.assertEqual(code, 1, output)
        self.assertIn("Found bash 3.2 at /usr/local/bin/bash.", output)
        self.assertIn(HOMEBREW, output)
        self.assertNotIn("Fleet setup", output)

    def test_installer_without_a_terminal_stops_before_installing(self):
        fixture = Fixture(self.base)
        code, output = self.run32("bash", "install.sh", "--defaults", fixture=fixture)
        self.assertEqual(code, 1, output)
        self.assertIn("add --update-bash to accept the bash install.", output)
        self.assertEqual(fixture.installs(), [])

    def test_installer_with_update_bash_installs_then_runs_itself_with_that_bash(self):
        fixture = Fixture(self.base)
        code, output = self.run32("bash", "/manager/install.sh", "--update-bash", fixture=fixture)
        self.assertEqual(code, 0, output)
        self.assertEqual(fixture.installs(), ["install bash"])
        self.assertIn(f"Running Fleet with bash at {fixture.prefix}/bin/bash", output)
        self.assertEqual(fixture.run_log.read_text(), "/manager/install.sh --update-bash\n")

    def test_the_launcher_hands_over_to_a_modern_bash_by_path(self):
        fixture = Fixture(self.base)
        (fixture.prefix / "bin/brew").unlink()
        resolved = fixture.prefix / "bin/bash"
        resolved.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = -c ]; then case $2 in *BASH_VERSINFO*) echo 5.2; exit 0 ;; esac; fi\n'
            f'echo "$*" >> {fixture.run_log}\n'
        )
        resolved.chmod(0o755)
        code, output = self.run32("bash", "/manager/scripts/dotfleet.sh", "status", fixture=fixture)
        self.assertEqual(code, 0, output)
        self.assertEqual(fixture.run_log.read_text(), "/manager/scripts/dotfleet.sh status\n")


if __name__ == "__main__":
    unittest.main()
