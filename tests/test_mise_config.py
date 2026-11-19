"""Generate the Mise config in a form that Mise itself can edit.

Mise edits a [tools] table in place. A quoted "tools" inline table makes mise use panic.
Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_mise_config.py --help.
"""

import shutil
import subprocess
import tomllib
import unittest

from common import toml, toml_tables
import test_manager as helpers
from test_manager import command

CONFIG = ".config/mise/config.toml"


class MiseConfigTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    install = helpers.EnrollmentTests.install

    def config(self):
        return (self.home / CONFIG).read_text()

    def mise(self, *arguments):
        """Run the fixture's Mise offline against this home. Return the completed process."""
        location = shutil.which("mise", path=self.env["PATH"])
        if location is None:
            self.skipTest("Mise is not installed; CI installs it in the cold-install step.")
        return subprocess.run(
            [location, *arguments],
            env=dict(self.env, MISE_OFFLINE="1"),
            cwd=self.home,
            text=True,
            capture_output=True,
        )

    def assert_tables(self, text):
        lines = text.splitlines()
        self.assertIn("[tools]", lines)
        self.assertIn("[settings]", lines)
        self.assertFalse([line for line in lines if line.startswith(('"tools" =', '"settings" ='))])

    def assert_mise_use_succeeds(self):
        """Add a scratch pin with Mise's own editor. The system version downloads nothing."""
        before = tomllib.loads(self.config())["tools"]
        result = self.mise("use", "--path", str(self.home / CONFIG), "jq@system")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("panicked", result.stderr)
        after = tomllib.loads(self.config())["tools"]
        self.assertEqual(after.pop("jq"), "system")
        self.assertEqual(after, before)

    def test_writer_emits_table_headers_with_the_same_values(self):
        data = {
            "min_version": "2026.1.0",
            "tools": {
                "node": "26.13.1",
                "npm:@google/gemini-cli": "1.0.0",
                "python": ["3.12.13", "3.14.6"],
                "rust": {"version": "1.90.0", "profile": "minimal"},
            },
            "settings": {"experimental": False},
        }
        text = toml_tables(data)
        self.assert_tables(text)
        self.assertIn('\nnode = "26.13.1"\n', text)
        self.assertEqual(tomllib.loads(text), data)

    def test_generated_config_has_tables_and_mise_can_edit_it(self):
        self.install()
        self.assert_tables(self.config())
        self.assertIs(tomllib.loads(self.config())["settings"]["experimental"], False)
        self.assert_mise_use_succeeds()

    def test_old_inline_form_converts_on_update_and_keeps_pins(self):
        self.install()
        # Record a pin the way enrollment does, so the update must carry it through.
        saved = self.home / ".fleet/profile.toml"
        profile = tomllib.loads(saved.read_text())
        profile["pins"] = {"node": "24.1.0", "python": "3.13.0"}
        saved.write_text(toml(profile))
        self.install(defaults=False)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        rendered = tomllib.loads(self.config())
        self.assertTrue(profile["pins"])
        self.assertEqual(rendered["tools"], profile["pins"])
        # Recreate what earlier Fleet applied: the same values as quoted inline tables.
        old = toml(rendered)
        self.assertIn('"tools" = {', old)
        source = self.home / ".fleet/chezmoi-source/dot_config/mise/config.toml"
        self.assertTrue(source.is_file())
        source.write_text(old)
        command("chezmoi", "apply", "--force", str(self.home / CONFIG), env=self.env, cwd=self.home)
        self.assertEqual(self.config(), old)
        self.install(defaults=False)
        self.assert_tables(self.config())
        self.assertEqual(tomllib.loads(self.config()), rendered)
        after = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(after["pins"], profile["pins"])
        self.assert_mise_use_succeeds()


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
