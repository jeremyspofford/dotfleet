"""Golden human views of immutable run records, plus terminal color checks.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_render_run.py --help.
"""

import copy
import json
import os
from pathlib import Path
import pty
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from render_run import change_marker, render

FIXTURES = ROOT / "tests/fixtures/run-results"


class RenderingTests(unittest.TestCase):
    def record(self, name="changed"):
        return json.loads((FIXTURES / (name + ".json")).read_text())

    def assert_plain_view(self, text):
        self.assertNotIn("/home/example", text)
        self.assertNotRegex(text, r"\b[0-9a-f]{40}\b")
        self.assertNotRegex(text, r'[{}]|\[\s*"|"\w+"\s*:|^\s*\[\w+\]\s*$')
        self.assertNotRegex(text, r"(?m)^\s*[\w.]+\s*=")
        self.assertNotIn("\x1b[", text)
        for line in text.splitlines():
            if len(line) > 100:
                self.assertRegex(line, r"https?://\S{95,}", line)
        self.assertTrue(text.splitlines()[-2].startswith("chezmoi status:"))
        self.assertTrue(text.splitlines()[-1].startswith("Run record:"))

    def test_three_golden_records(self):
        for name in ["no-change", "changed", "manager-upgrade"]:
            with self.subTest(name=name):
                record = self.record(name)
                original = copy.deepcopy(record)
                text = render(record)
                self.assertEqual(text, (FIXTURES / (name + ".txt")).read_text())
                self.assertEqual(record, original)
                self.assert_plain_view(text)

    def test_future_view_uses_same_record_and_categories(self):
        record = self.record()
        record["completed_files"] = []
        record["profile_written"] = False
        past, future = render(record), render(record, future=True)
        self.assertIn("3 · Files — no changes.", past)
        self.assertIn("3 · Files — will be 1 added, 1 changed, 1 removed.", future)
        self.assertIn("4 · Profile changes — 1 will change.", future)
        self.assertIn("  − ~/.config/docker/aliases.zsh", future)
        self.assert_plain_view(future)

    def test_review_uses_planned_command_and_cancellation_keeps_current_command(self):
        record = self.record()
        record["instruction_commands"] = {"before": "dotfleet", "after": "fleet"}
        record["profile_written"] = False
        self.assertIn("Update    fleet update", render(record, future=True))
        self.assertIn("Wizard    fleet configure", render(record, future=True))
        record["result"] = "CANCELLED"
        self.assertIn("Update    dotfleet update", render(record))
        self.assertIn("Wizard    dotfleet configure", render(record))
        record["profile_written"] = True
        self.assertIn("Update    fleet update", render(record))

    def test_golden_labels_the_product_and_preserves_binary_paths(self):
        record = self.record()
        text = render(record)
        self.assertIn("Fleet 0.2.0", text)
        self.assertNotIn("dotfleet 0.2.0", text)
        software = record["software"][-1]
        software.update(action="upgraded", old="0.1.0")
        record["completed_software"] = ["dotfleet"]
        for future in (False, True):
            text = render(record, future=future)
            self.assertIn("Fleet  0.1.0 → 0.2.0 · ~/.local/bin/dotfleet", text)
        self.assertEqual(software["name"], "dotfleet")

    def test_long_values_wrap_without_machine_dumps(self):
        record = self.record()
        record["profile_changes"].append(
            {"name": "git_name", "old": None, "new": "A long human name " * 20}
        )
        record["profile_changes"].append(
            {
                "name": "pins.python",
                "old": None,
                "new": {"version": ["3.13.0", "3.12.0"], "option": True},
            }
        )
        record["warnings"] = ["A warning that needs space " * 10]
        text = render(record)
        self.assert_plain_view(text)
        self.assertIn("version: 3.13.0, 3.12.0; option: on", text)
        self.assertRegex(text, r"Git name\n    not set →")
        self.assertIn("warnings: 1", text.splitlines()[-2])
        for line in text.splitlines():
            self.assertFalse(line.endswith("hum"))

    def test_no_change_view_is_compact_without_dropping_recorded_locations(self):
        record = self.record("no-change")
        original = copy.deepcopy(record)
        text = render(record)
        self.assertEqual(len([line for line in text.splitlines() if line]), 12)
        self.assertEqual(len(text.splitlines()), 18)
        self.assertIn("5 · Locations — recorded in RUN.json.", text)
        self.assertEqual(record, original)
        self.assertIn("~/fleet-backups/20261116-140000/MANIFEST.json", text)
        # The preview must still show every location before any apply.
        review = render(record, future=True)
        self.assertIn("~/.config/fleet/profile.toml", review)
        self.assertIn("~/.local/share/fleet/chezmoi-source", review)

    def test_long_status_and_pointer_obey_the_footer_rules(self):
        record = self.record()
        record["chezmoi_status_after"] = "M ~/.changed\nA ~/.new"
        record["locations"]["Backup folder"] = "/home/example/backups/" + "long folder name " * 10
        text = render(record)
        self.assertIn("chezmoi status: details in RUN.json · warnings: none", text)
        self.assertNotIn("M ~/.changed", text)
        self.assertNotIn("A ~/.new", text)
        self.assertIn("Run record:\n    ~/backups/", text)
        self.assertTrue(text.rstrip().endswith("/RUN.json"))
        self.assertTrue(all(len(line) <= 100 for line in text.splitlines()))
        for status in [
            "Detailed status " * 20,
            '{"status":"clean"}',
            'status = "clean"',
            "status: clean",
        ]:
            record["chezmoi_status_after"] = status
            output = render(record)
            self.assertIn("chezmoi status: details in RUN.json · warnings: none", output)
            self.assertTrue(all(len(line) <= 100 for line in output.splitlines()))

    def test_wrapped_home_paths_are_not_change_markers(self):
        for marker in ["+", "−", "~"]:
            self.assertEqual(change_marker("  " + marker + " ~/.file"), marker)
            self.assertEqual(change_marker("  " + marker), marker)
        self.assertIsNone(change_marker("    ~/.config/example"))
        self.assertIsNone(change_marker("  ~/dotfiles"))

    def test_long_keys_use_a_separate_indented_value_line(self):
        record = self.record()
        label = "a_very_long_tool_name_with_additional_words"
        record["profile_changes"].append({"name": "pins." + label, "old": None, "new": "1.2.3"})
        text = render(record)
        self.assertIn("  Pin " + label + "\n    not set → 1.2.3", text)
        self.assert_plain_view(text)

    def test_summary_color_uses_logger_and_keeps_removals_on_stdout(self):
        script = "import sys; from pathlib import Path; from common import LoggerStream; sys.stdout=LoggerStream(Path(sys.argv[1])); print('  + ~/.new\\n  − ~/.old\\n  ~ ~/.changed\\n    ~/.wrapped')"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), TERM="xterm-256color")
        env.pop("NO_COLOR", None)

        def terminal(environment):
            master, slave = pty.openpty()
            try:
                result = subprocess.run(
                    [sys.executable, "-c", script, str(ROOT)],
                    env=environment,
                    stdout=slave,
                    stderr=subprocess.PIPE,
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
            self.assertEqual(result.stderr, b"")
            return data

        colored = terminal(env)
        for code, marker in [("32", "+"), ("31", "−"), ("33", "~")]:
            self.assertIn(f"\x1b[{code}m  {marker}".encode(), colored)
        self.assertNotIn(b"\x1b[33m    ~/.wrapped", colored)
        for no_color in ["", "1"]:
            plain = terminal(dict(env, NO_COLOR=no_color))
            self.assertNotIn(b"\x1b[", plain)
            for marker in ["+", "−", "~"]:
                self.assertIn(("  " + marker).encode(), plain)
        redirected = subprocess.run(
            [sys.executable, "-c", script, str(ROOT)], env=env, capture_output=True
        )
        self.assertEqual(redirected.returncode, 0)
        self.assertEqual(redirected.stderr, b"")
        self.assertNotIn(b"\x1b[", redirected.stdout)

    def test_readme_uses_the_changed_golden_verbatim(self):
        example = (FIXTURES / "changed.txt").read_text()
        self.assertIn("```text\n" + example + "```", (ROOT / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
