"""Keep command views and prompt colors consistent with the shell logger.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_style.py --help.
"""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import command
import registry
import owned_views
import updates
import agent_table
import datetime
import style
from common import toml

ROOT = Path(__file__).resolve().parents[1]


def command_goldens():
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        for path in [
            "dotfiles/.git",
            ".local/share/fleet/manager/.git",
            ".local/share/fleet/chezmoi-source",
            ".config/chezmoi",
        ]:
            (home / path).mkdir(parents=True)
        (home / ".config/chezmoi/chezmoi.toml").write_text(
            toml({"sourceDir": str(home / ".local/share/fleet/chezmoi-source")})
        )
        profile = {
            "domain": "personal",
            "domain_chosen": True,
            "git_name": "Example Person",
            "git_email": "example@example.invalid",
            "pins": {"node": "26.13.1"},
            "groups": {"helpers": True, "docker": False},
            "agents": {"codex": False},
            "agent_items": {"mcp.docs": "local"},
            "content_repo": "https://example.invalid/dotfiles",
        }
        root = home / "manager-fixture"
        root.mkdir()
        (root / "VERSION").write_text("0.4.0\n")
        (home / "dotfiles/fleet.toml").write_text(
            'format = "plain"\noverlays = ["core", "personal", "work", "client"]\n'
            '[domains]\ncore = "Base files only."\npersonal = "Personal shortcuts."\n'
            '[groups.helpers]\ndescription = "Fleet command shortcut."\n'
            '[groups.docker]\ndescription = "Container shortcuts."\n'
            "[secrets]\nEXAMPLE_API_TOKEN = {}\nUNSET_TOKEN = {}\n"
            '[agents]\nschema = 1\n[agents.mcp.docs]\ndescription = "Search the documentation."\n'
            'transport = "stdio"\ncommand = "npx"\n'
            '[agents.mcp.issues]\ndescription = "Read work issues."\ntransport = "http"\n'
            'url = "https://mcp.example.com/issues"\ndomains = ["work"]\n'
        )
        (home / ".config/fleet").mkdir(parents=True)
        (home / ".config/fleet/profile.toml").write_text(toml(profile))
        cache = home / updates.CACHE
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checked_at": datetime.datetime(2026, 11, 17, 9, 2).astimezone().isoformat(),
                    "sources": [{"name": "manager", "behind": 0}, {"name": "content", "behind": 0}],
                }
            )
        )
        backup = home / "fleet-backups/20261116-120000"
        backup.mkdir(parents=True)
        (backup / "RUN.json").write_text('{"result":"SUCCESS"}')
        machine = dict(
            hostname="example-hub",
            home=str(home),
            os="Linux",
            domain="personal",
            domain_chosen=True,
            content_repo="https://example.invalid/dotfiles",
            content_commit="1234567890abcdef1234567890abcdef12345678",
            manager_version="0.1.0",
            last_run="2026-11-16T12:00:00+00:00",
            last_backup=str(backup),
            last_run_record=str(backup / "RUN.json"),
            result="SUCCESS",
            enrolled=True,
        )
        empty = {key: False if kind is bool else "" for key, kind in registry.FIELDS.items()}
        empty.update(hostname="mini-pc", ssh_target="mini-pc", result="not recorded")
        outputs = {}
        with (
            patch("command_display.shutil.which", return_value=None),
            patch.dict(os.environ, {"HOME": str(home), "EXAMPLE_API_TOKEN": "private-value"}),
            patch.object(command, "ROOT", root),
            patch.object(command, "commit", return_value=machine["content_commit"]),
            patch.object(owned_views, "run", return_value=""),
            patch.object(command, "run", return_value=""),
            patch.object(updates, "managed_edits", return_value=[]),
            patch.object(command, "records", return_value=[backup]),
            patch.object(
                command.enrollment,
                "installed",
                return_value={"node": [{"version": "26.3.1"}, {"version": "26.13.1"}]},
            ),
            patch.object(
                command,
                "checkout_state",
                return_value="12345678 · 0 behind last fetched upstream · clean",
            ),
            patch.object(
                registry,
                "load",
                return_value={
                    "schema_version": 1,
                    "machines": {"example-hub": machine, "mini-pc": empty},
                },
            ),
            patch.object(socket, "gethostname", return_value="example-hub"),
            patch.object(
                agent_table,
                "tracked",
                return_value={".agents/skills/notes/SKILL.md": "100644"},
            ),
        ):
            for name in [
                "agents",
                "status",
                "doctor",
                "machines",
                "backups",
                "secrets",
                "content",
                "domain",
                "groups",
                "tools",
                "addons",
                "version",
            ]:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = command.read_view(argparse.Namespace(command=name), home, profile)
                assert result == 0, output.getvalue()
                outputs[name] = output.getvalue()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                command.main(["machines", "export"])
            outputs["machine export"] = output.getvalue()
            outputs["help"] = command.parser().format_help()
            with (
                patch.object(updates, "validate_content"),
                patch.object(
                    updates,
                    "compare",
                    side_effect=[
                        {"behind": 3, "local_only": False, "current": "1234567890abcdef"},
                        {"behind": 0, "local_only": False, "current": "abcdef0123456789"},
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                assert command.main(["check"]) == 1
            outputs["check"] = output.getvalue()
            import tui_environment

            with (
                patch.object(tui_environment, "installed_version", return_value=None),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                assert tui_environment.launch(home, root) == 0
            outputs["tui missing"] = output.getvalue()
        return outputs


class StyleTests(unittest.TestCase):
    def test_command_goldens(self):
        for name, output in command_goldens().items():
            self.assertEqual(output, (ROOT / f"tests/fixtures/views/{name}.txt").read_text())
            for line in output.splitlines():
                self.assertLessEqual(len(line), 100)
            self.assertNotIn("[machines.", output)
            self.assertNotIn("private-value", output)
        machines = command_goldens()["machines"]
        self.assertNotIn("RUN.json", machines)
        self.assertNotIn("Scope", machines)
        self.assertNotIn("mini-pc → mini-pc", machines)
        self.assertEqual(machines.count("No local data recorded for this machine."), 1)

    def test_python_and_shell_palette_and_section_rule_match(self):
        env = dict(os.environ, TERM="xterm-256color")
        env.pop("NO_COLOR", None)
        for disabled in [False, True]:
            current = dict(env, NO_COLOR="") if disabled else env
            master, slave = pty.openpty()
            try:
                code = 'source "$1"; log_section Title; log_info Info; log_success Success; log_change removed Removed; log_warning Warning'
                result = subprocess.run(
                    ["bash", "-c", code, "test", str(ROOT / "scripts/logger.sh")],
                    env=current,
                    stdout=slave,
                    stderr=slave,
                )
                os.close(slave)
                data = b""
                while True:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    data += chunk
            finally:
                os.close(master)
            self.assertEqual(result.returncode, 0)

            class Terminal(io.StringIO):
                def isatty(self):
                    return True

            stream = Terminal()
            with patch.dict(os.environ, current, clear=True):
                expected = (
                    "\n".join(
                        style.paint("CYAN", line, stream, True)
                        for line in style.section("Title").splitlines()
                    )
                    + "\n"
                )
                for color, text in [
                    ("BLUE", "Info"),
                    ("GREEN", "Success"),
                    ("RED", "Removed"),
                    ("YELLOW", "Warning"),
                ]:
                    expected += style.paint(color, text, stream) + "\n"
            self.assertEqual(data.decode().replace("\r\n", "\n"), expected)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(style.paint("RED", "Error", io.StringIO()), "Error")

    def test_unexpected_error_is_plain_and_nonzero(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys,style; "
                'sys.exit(style.run_cli(lambda: 1/0, Path(".")))',
            ],
            env=dict(os.environ, PYTHONPATH=str(ROOT / "scripts")),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"))
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    argument_parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        usage()
