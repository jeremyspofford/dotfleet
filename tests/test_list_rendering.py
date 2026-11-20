"""Check that lists show a heading and one aligned row per item, with no airy blank lines.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_list_rendering.py --help.
"""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import command_views
import enrollment
import marketplaces
import registry
import test_style
import tool_updates
import updates
from common import atomic

ROOT = Path(__file__).resolve().parents[1]
GOLDENS = ROOT / "tests/fixtures/views"

# The owner's shape: a few recorded tools, many installed extras, one tool with two extras.
PINS = {"gh": "2.105.0", "herdr": "0.9.5", "node": "26.13.1", "uv": ["0.11.23", "0.10.0"]}
INSTALLED = {
    "1password-cli": ["2.34.1"],
    "aws-cli": ["2.35.9"],
    "bat": ["0.26.1"],
    "eza": ["0.23.4"],
    "fd": ["10.4.2"],
    "fzf": ["0.73.1"],
    "gh": ["2.95.0", "2.105.0"],
    "go": ["1.27.0"],
    "jq": ["1.8.2"],
    "lazygit": ["0.62.2"],
    "node": ["26.3.1", "26.13.1"],
    "npm:@google/gemini-cli": ["0.50.0"],
    "python": ["3.12.13", "3.14.6"],
    "ripgrep": ["15.1.0"],
    "shfmt": ["3.8.0", "3.13.1"],
    "uv": ["0.11.23"],
}


def installed(versions):
    return {tool: [{"version": version} for version in items] for tool, items in versions.items()}


def printed(function, *arguments, **options):
    with contextlib.redirect_stdout(io.StringIO()) as output:
        result = function(*arguments, **options)
    return result, output.getvalue()


class ListRenderingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def golden(self, name, output):
        self.assertEqual(output, (GOLDENS / (name + ".txt")).read_text())
        self.assertNotIn("\n\n\n", output)
        for line in output.splitlines():
            self.assertLessEqual(len(line), 100)

    def tools(self, pins, versions):
        with patch.object(enrollment, "installed", return_value=installed(versions)):
            return command_views.tool_view(self.home, {"pins": pins}, "No tool versions recorded.")

    def test_tools_list_shows_recorded_rows_and_wraps_the_rest(self):
        output = self.tools(PINS, INSTALLED)
        self.golden("tools many", output)
        self.assertIn("  node   26.13.1          installed · also 26.3.1\n", output)
        self.assertIn("  herdr  0.9.5            not installed\n", output)
        self.assertIn("not installed: 0.10.0", output)
        self.assertIn("python: 3.12.13, 3.14.6", output)
        # Recorded tools stay out of the wrapped run, and no list prints an empty field.
        self.assertNotIn("node:", output)
        self.assertNotIn("none", output)

    def test_tools_list_without_extras_or_records(self):
        self.assertNotIn("Also installed", self.tools({"node": "26.13.1"}, {"node": ["26.13.1"]}))
        # A selection that records no version is not a recorded tool.
        self.assertNotIn("installed", self.tools({"node": []}, {}))
        output = self.tools({}, {"jq": ["1.8.2"]})
        self.assertIn("No tool versions recorded.\n\n", output)
        self.assertIn("  jq: 1.8.2\n", output)

    def test_check_lists_every_source_as_consecutive_lines(self):
        atomic(self.home / ".fleet/profile.toml", "")
        marketplace = {
            "current": "abcdef0123456789abcdef0123456789abcdef01",
            "behind": 2,
            "pin": "v1.0.0",
        }
        sources = [
            {"behind": 3, "local_only": False, "current": "1234567890abcdef"},
            {"behind": 0, "local_only": False, "current": "abcdef0123456789"},
        ]
        newest = {"gh": "2.105.0", "herdr": "0.9.5", "node": "26.14.0"}
        profile = {"content_repo": "https://example.invalid/content", "pins": PINS}
        with (
            patch.object(updates, "validate_content"),
            patch.object(updates, "compare", side_effect=sources),
            patch.object(marketplaces, "load_records", return_value={"marketplaces": {"m": {}}}),
            patch.object(marketplaces, "check", return_value={"agent-market": marketplace}),
            patch.object(tool_updates, "newest", side_effect=lambda home, tool: newest[tool]),
        ):
            result, output = printed(
                updates.check, self.home, profile, marketplaces=True, tools=True
            )
        self.assertEqual(result, 1)
        self.golden("check many", output)
        # Tools with nothing newer share one summary line.
        self.assertIn("  Tools                     up to date: gh 2.105.0, herdr 0.9.5\n", output)

    def test_machines_keep_blocks_up_to_five_and_use_rows_beyond(self):
        empty = {key: False if kind is bool else "" for key, kind in registry.FIELDS.items()}
        for count, expected in [(5, False), (6, True)]:
            machines = {
                f"box-{number}": dict(
                    empty,
                    hostname=f"box-{number}",
                    ssh_target=f"box-{number}",
                    os="Linux",
                    domain="work",
                    domain_chosen=number % 2 == 0,
                    manager_version="0.10.1",
                    result="SUCCESS",
                    last_run=f"2026-11-1{number}T21:13:38+00:00",
                    enrolled=True,
                )
                for number in range(count)
            }
            # This machine's own record is saved, so the view never reads the test host.
            machines["this-box"] = machines.pop("box-0") | {
                "hostname": "this-box",
                "ssh_target": "",
            }
            data = {"schema_version": 1, "machines": machines, "removed": {}}
            atomic(self.home / registry.REGISTRY, registry.serialize(data))
            with patch.dict(os.environ, {"FLEET_MACHINE_NAME": "this-box"}):
                entities = registry.entities(self.home, "0.10.1")
                rows = command_views.uses_rows("machines", entities)
                _, output = printed(
                    command_views.directory, "Machines", entities, self.home, rows=rows
                )
            self.assertEqual(rows, expected)
            self.assertNotIn("\n\n\n", output)
        self.golden("machines rows", output)
        self.assertIn(
            "  this-box  this machine · Linux · work · Fleet 0.10.1 · no connection · ", output
        )
        self.assertIn(
            "  box-1     Linux · work UNCHOSEN · Fleet 0.10.1 · ssh box-1 · SUCCESS 2026-11-11\n",
            output,
        )

    def test_backups_without_a_run_result_get_their_own_section(self):
        import owned_views

        folders = []
        for name, record in [("20261116-120000", '{"result":"SUCCESS"}'), ("20261117-120000", "")]:
            folder = self.home / ".fleet/backups" / name
            folder.mkdir(parents=True)
            (folder / "MANIFEST.json").write_text("{}")
            if record:
                (folder / "RUN.json").write_text(record)
            folders.append(folder)
        entities = owned_views.backup_entities(self.home, folders)
        _, output = printed(command_views.directory, "Backups", entities, self.home, rows=True)
        self.assertRegex(output, r"\n  20261116-120000  SUCCESS  \d+ bytes\n\n")
        self.assertRegex(
            output, r"Backups without a run result\n━+\n  20261117-120000  \d+ bytes\n$"
        )
        self.assertNotIn("\n\n\n", output)

    def test_a_short_row_starts_its_last_cell_in_the_last_column(self):
        from render_run import render_rows

        output = render_rows("Rows", [("a", "1", "x"), ("bbbbbb", "y")])
        self.assertTrue(output.endswith("  a       1  x\n  bbbbbb     y\n"), output)

    def test_no_list_output_has_two_consecutive_blank_lines(self):
        for name, output in test_style.command_goldens().items():
            with self.subTest(name):
                self.assertNotIn("\n\n\n", output)


if __name__ == "__main__":
    unittest.main()
