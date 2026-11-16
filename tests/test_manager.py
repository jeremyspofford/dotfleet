"""Exercise real conversion, enrollment, updates, and saved machine settings."""
import json
import errno
import fcntl
import pty
import select
import signal
import termios
import time
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_configs
from manager import installed_defaults
from common import toml
from convert import convert, detect


def command(*args, env=None, cwd=None):
    result = subprocess.run(args, env=env, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def repository(path, files):
    path.mkdir()
    command("git", "init", "-b", "main", str(path))
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    command("git", "-C", str(path), "add", ".")
    command("git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "test: fixture")
    return path


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), SHELL="/bin/bash", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_COUNT="1",
                        GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0="*",
                        XDG_CONFIG_HOME=str(self.home / ".config"), XDG_DATA_HOME=str(self.home / ".local/share"),
                        XDG_STATE_HOME=str(self.home / ".local/state"), MISE_TRUSTED_CONFIG_PATHS=str(self.home))
        for key in list(self.env):
            if key.startswith(("CHEZMOI_", "MISE_")) and key != "MISE_TRUSTED_CONFIG_PATHS":
                self.env.pop(key)

    def install(self, repo=None, defaults=True):
        args = ["bash", str(ROOT / "install.sh")]
        if repo:
            args += ["--repo", str(repo)]
        if defaults:
            args += ["--defaults"]
        return command(*args, env=self.env, cwd=self.home)

    def assert_clean(self):
        self.assertEqual(command("bash", "-c", "umask 0022; chezmoi status", env=self.env, cwd=self.home), "")
        self.assertEqual(command("git", "-C", str(self.home / "dotfiles"), "status", "--porcelain"), "")
        self.assertEqual((self.home / ".secrets").stat().st_mode & 0o777, 0o600)
        for shell in ["bash", "zsh"]:
            env = dict(self.env, SHELL=f"/bin/{shell}")
            result = subprocess.run([shell, "-lc", "printf shell-ok"], cwd=self.home, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(result.stdout.endswith("shell-ok"))

    def exercise(self, repo):
        (self.home / ".zshrc").write_text("# existing configuration\n")
        external = self.base / "external"
        external.write_text("# external aliases\n")
        (self.home / ".aliases").symlink_to(external)
        output = self.install(repo)
        self.assertIn("Result: SUCCESS", output)
        self.assert_clean()
        backups = list((self.home / "dotfleet-backups").iterdir())
        saved = backups[0] / "home"
        self.assertEqual((saved / ".zshrc").read_text(), "# existing configuration\n")
        if (self.home / ".aliases").exists():
            self.assertTrue((saved / ".aliases").is_symlink())
            self.assertEqual((backups[0] / "external" / str(external).lstrip("/")).read_bytes(), external.read_bytes())
        state = self.home / ".local/state/dotfleet/outputs.json"
        before = state.read_bytes()
        profile = (self.home / ".config/dotfleet/profile.toml").read_bytes()
        self.assertIn("Result: SUCCESS", self.install(defaults=False))
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual((self.home / ".config/dotfleet/profile.toml").read_bytes(), profile)
        self.assert_clean()

    def test_plain_content_and_groups(self):
        repo = Path(os.environ["DOTFLEET_CONTENT_FIXTURE"])
        self.exercise(repo)
        path = self.home / ".config/dotfleet/profile.toml"
        profile = tomllib.loads(path.read_text())
        aliases = self.home / ".config/dotfiles/aliases/core.zsh"
        original = aliases.read_text()
        profile["groups"]["docker"] = False
        path.write_text(toml(profile))
        self.install(defaults=False)
        self.assertNotIn("dotfleet:group docker", aliases.read_text())
        self.assertIn("dotfleet:group kubectl", aliases.read_text())
        self.assert_clean()
        profile["groups"]["docker"] = True
        path.write_text(toml(profile))
        self.install(defaults=False)
        self.assertEqual(aliases.read_text(), original)
        self.assert_clean()

    def test_real_legacy_stow_archive(self):
        self.exercise(Path(os.environ["DOTFLEET_ARCHIVE_FIXTURE"]))

    def test_native_source(self):
        repo = repository(self.base / "native", {"dot_zshrc": "# native zsh\n", "dot_bashrc": "# native bash\n",
                                                "dot_aliases": "# native aliases\n", ".chezmoidata.toml": "example = true\n"})
        self.exercise(repo)
        config = tomllib.loads((self.home / ".config/chezmoi/chezmoi.toml").read_text())
        self.assertEqual(config["sourceDir"], str(self.home / "dotfiles"))

    def test_migration_reads_all_sources_and_preserves_pins(self):
        for name, data in {
            ".config/chezmoi/chezmoi.toml": {"data": {"domain": "personal", "gitName": "Example Owner", "gitEmail": "owner@example.invalid", "miseTools": {"node": "22", "python": "3.12"}}},
            ".config/mise/config.toml": {"tools": {"node": "24"}},
            ".config/dotfiles/profile.toml": {"pins": {"python": "3.13"}},
        }.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(toml(data))
        (self.home / ".secrets").write_text("# preserved secret placeholder\n")
        output = self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        self.assertIn("old chezmoi data, current Mise tools, saved installer profile", output)
        profile = tomllib.loads((self.home / ".config/dotfleet/profile.toml").read_text())
        self.assertEqual(profile["pins"], {"node": "24", "python": "3.13"})
        self.assertEqual(profile["git_name"], "Example Owner")
        before = (self.home / ".config/dotfleet/profile.toml").read_bytes()
        self.install(defaults=False)
        self.assertEqual((self.home / ".config/dotfleet/profile.toml").read_bytes(), before)
        self.assertEqual((self.home / ".secrets").read_text(), "# preserved secret placeholder\n")
        self.assertEqual(command("chezmoi", "status", env=self.env, cwd=self.home), "")

    def terminal(self, responses, expected=0, configure=False):
        master, slave = pty.openpty()
        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        args = ["bash", str(ROOT / "install.sh"), "--repo", os.environ["DOTFLEET_CONTENT_FIXTURE"]]
        if configure:
            args.append("--configure")
        process = subprocess.Popen(args, cwd=self.home, env=self.env, stdin=subprocess.PIPE,
                                   stdout=slave, stderr=slave, preexec_fn=controlling_terminal)
        os.close(slave)
        process.stdin.write(b"all-overwrite\nWRONG PERSON\n")
        process.stdin.close()
        transcript = b""
        cursor = 0
        answered = 0
        deadline = time.monotonic() + 45
        try:
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], .1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    transcript += chunk
                elif process.poll() is not None:
                    break
                if answered < len(responses) and responses[answered][0] in transcript[cursor:]:
                    os.write(master, responses[answered][1])
                    cursor = len(transcript)
                    answered += 1
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            os.close(master)
        output = transcript.decode(errors="replace")
        self.assertEqual(process.returncode, expected, output)
        self.assertEqual(answered, len(responses), output)
        self.assertNotIn("diff/overwrite/all-overwrite/skip/Skip-conflicts/quit", output)
        return output

    def test_terminal_profile_and_update(self):
        responses = [(b"[core]:", b"invalid\n"), (b"[core]:", b"personal\n"),
                     (b"Git author name", b"Fixture Person\n"), (b"Git author email", b"fixture@example.invalid\n")]
        responses += [(f"{group}:".encode(), b"\n") for group in ["docker", "terraform", "kubectl", "python", "npm"]]
        self.terminal(responses)
        path = self.home / ".config/dotfleet/profile.toml"
        profile = path.read_bytes()
        self.assertEqual(tomllib.loads(profile.decode())["git_name"], "Fixture Person")
        self.terminal([])
        self.assertEqual(path.read_bytes(), profile)
        self.assert_clean()

    def test_terminal_conflict_decisions(self):
        self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        paths = [self.home / ".aliases", self.home / ".bashrc"]
        original = [path.read_bytes() for path in paths]
        for path in paths:
            path.write_bytes(path.read_bytes() + b"\n# local edit\n")
        edited = [path.read_bytes() for path in paths]
        prompt = b"Choose [d/o/a/s/S/q]:"
        for answer in [b"S\n", b"q\n"]:
            self.terminal([(prompt, answer)], expected=1)
            self.assertEqual([path.read_bytes() for path in paths], edited)
        self.terminal([(prompt, b"s\n"), (prompt, b"o\n")], expected=1)
        self.assertEqual(paths[0].read_bytes(), edited[0])
        self.assertEqual(paths[1].read_bytes(), original[1])
        output = self.terminal([(prompt, b"x\n"), (prompt, b"d\n"), (prompt, b"o\n")])
        self.assertIn("diff --git", output)
        self.assertIn("Type one letter, then press Enter.", output)
        self.assert_clean()
        for path, content in zip(paths, edited):
            path.write_bytes(content)
        self.terminal([(prompt, b"a\n")])
        self.assertEqual([path.read_bytes() for path in paths], original)
        self.assert_clean()

    def test_unattended_conflict_keeps_local_edits(self):
        self.install(Path(os.environ["DOTFLEET_CONTENT_FIXTURE"]))
        path = self.home / ".aliases"
        path.write_bytes(path.read_bytes() + b"\n# local edit\n")
        before = path.read_bytes()
        result = subprocess.run(["bash", str(ROOT / "install.sh")], input="all-overwrite\n", env=self.env,
                                cwd=self.home, text=True, capture_output=True, start_new_session=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Apply stopped", result.stdout)
        self.assertEqual(path.read_bytes(), before)

    def test_occupied_checkout_stops(self):
        (self.home / "dotfiles").mkdir()
        marker = self.home / "dotfiles/keep"
        marker.write_text("keep")
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--defaults", "--repo", os.environ["DOTFLEET_CONTENT_FIXTURE"]], env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not the selected clone", result.stdout)
        self.assertEqual(marker.read_text(), "keep")
        self.assertFalse((self.home / ".config/chezmoi").exists())


class ConverterTests(unittest.TestCase):
    def test_modes_furniture_and_manifest_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(root / "repo", {".zshrc": "hello\n", "README.md": "furniture", ".github/workflows/test.yml": "furniture",
                                             "agents/skills/demo/SKILL.md": "skill", "dotfleet.toml": 'format="plain"\npaths=["agents/skills"]\n'})
            (repo / ".zshrc").chmod(0o755)
            (repo / ".link").symlink_to(".zshrc")
            command("git", "-C", str(repo), "add", ".")
            source, items, _ = convert(repo, root / "stage")
            self.assertEqual(set(items), {".zshrc", ".link", "agents/skills/demo/SKILL.md"})
            self.assertTrue((source / "executable_dot_zshrc").exists())
            self.assertEqual((source / "symlink_dot_link").read_text(), ".zshrc")
            self.assertFalse((source / "README.md").exists())

    def test_literal_names_and_empty_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = repository(root / "repo", {".config/run_example": "literal", ".config/example.tmpl": "{{ untouched }}",
                                             ".empty": "", ".config/literal_example": "literal"})
            source, _, _ = convert(repo, root / "stage")
            home = root / "home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home))
            command("chezmoi", "--source", str(source), "--destination", str(home), "--config", str(root / "missing.toml"), "apply", env=env)
            for name in [".config/run_example", ".config/example.tmpl", ".empty", ".config/literal_example"]:
                self.assertEqual((home / name).read_bytes(), (repo / name).read_bytes())

    def test_defaults_only_fill_installed_gaps(self):
        with patch("manager.run", side_effect=[json.dumps({"node": [{}], "python": [{}]}), "3.13.0\n"]):
            pins, available = installed_defaults({"node": "24", "python": "3.13", "uv": "0.12"}, {"node": "22"}, Path("/tmp"))
        self.assertEqual(pins, {"node": "22", "python": "3.13"})
        self.assertEqual(available, {"uv": "0.12"})

    def test_agent_generation_keeps_conflict_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            skill = home / "dotfiles/agents/skills/review-change/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Review changes")
            with patch.object(shutil, "which", side_effect=lambda name: "/bin/fake" if name == "claude" else None):
                agent_configs.generate(home, "linux", True)
                self.assertFalse((home / ".claude").exists())
                agent_configs.generate(home, "linux", False)
                self.assertEqual((home / ".claude/skills/review-change").resolve(), skill.parent)
                agent_configs.generate(home, "linux", False)
                config = home / ".claude/settings.json"
                config.write_text('{"local": true}\n')
                with self.assertRaisesRegex(ValueError, "modified config"):
                    agent_configs.generate(home, "linux", False)
                self.assertEqual(json.loads(config.read_text()), {"local": True})

    def test_native_manifest_all_platforms(self):
        manifest = tomllib.loads((ROOT / "agents/manifest.toml").read_text())
        self.assertEqual(set(manifest["agents"]), {"claude", "codex", "gemini", "grok", "pi", "opencode", "ollama"})
        for agent in manifest["agents"].values():
            self.assertEqual(set(agent["config_paths"]), {"darwin", "linux", "windows", "wsl"})
            self.assertEqual(set(agent["skills_paths"]), {"darwin", "linux", "windows", "wsl"})

    def test_shell_help_and_unknown_flags(self):
        for path in [ROOT / "install.sh", *sorted((ROOT / "scripts").glob("*.sh"))]:
            for flag in ["--help", "-h"]:
                output = command("bash", str(path), flag)
                for word in ["Usage:", "Example", "Exit codes:"]:
                    self.assertIn(word, output, str(path))
            result = subprocess.run(["bash", str(path), "--unknown"], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, str(path))
            self.assertIn("Usage:", result.stderr)

    def test_unknown_format_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = repository(Path(temp) / "repo", {"README.md": "nothing"})
            with self.assertRaisesRegex(ValueError, "Unknown content format"):
                detect(repo)


if __name__ == "__main__":
    unittest.main()
