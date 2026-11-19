"""Check that content declares the domains a machine can choose.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_domains.py --help.
"""

import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import test_command
import test_manager as helpers
from convert import LEGACY_DOMAINS, content_domains, require_domain
from manager import domain_details
from test_manager import ROOT, command, repository


class ContentDomainTests(unittest.TestCase):
    def test_declared_domains_combine_overlays_and_descriptions_with_core_first(self):
        manifest = {"overlays": ["lab", "core"], "domains": {"home": "Home use.", "lab": "Lab."}}
        self.assertEqual(content_domains(manifest), ["core", "lab", "home"])

    def test_core_always_applies_even_when_content_omits_it(self):
        self.assertEqual(content_domains({"domains": {"lab": "Lab."}}), ["core", "lab"])
        self.assertEqual(content_domains({"overlays": []}), ["core"])

    def test_content_without_declarations_keeps_the_earlier_four_names(self):
        self.assertEqual(content_domains({}), list(LEGACY_DOMAINS))

    def test_invalid_domain_names_fail_validation(self):
        for name in ["Lab", "lab_one", "-lab", "lab--one", "", 4]:
            with self.assertRaises(ValueError, msg=repr(name)):
                content_domains({"overlays": [name]})

    def test_undeclared_saved_domain_names_the_declared_choices_and_the_fix(self):
        with self.assertRaises(ValueError) as caught:
            require_domain({"overlays": ["core", "lab"]}, "work", "fleet")
        message = str(caught.exception)
        self.assertIn("does not declare domain work", message)
        self.assertIn("Declared domains: core, lab", message)
        self.assertIn("fleet domain set --name NAME", message)
        require_domain({"overlays": ["core", "lab"]}, "lab")

    def test_domain_prompt_lists_only_declared_domains(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = repository(
                Path(temp) / "content",
                {
                    "fleet.toml": 'overlays = ["core", "lab"]\n[domains]\nlab = "Lab machines."\n',
                    ".config/dotfiles/aliases/lab.zsh": "alias bench='pwd'\n",
                },
            )
            detail = domain_details(checkout)
        self.assertIn("lab       Command names: bench.", detail)
        self.assertIn("Lab machines.", detail)
        self.assertNotIn("personal", detail)
        self.assertNotIn("client", detail)


class ContentDomainCommandTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli

    def commit_content(self, files, message):
        for name, text in files.items():
            path = self.content / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        command("git", "-C", str(self.content), "add", ".")
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
            message,
        )

    def test_content_adds_and_removes_a_domain(self):
        self.enroll()
        refused = self.cli("domain", "set", "--name", "lab", expected=1)
        self.assertIn("does not declare domain lab", refused)
        self.assertIn("Declared domains: core, personal", refused)
        self.assertNotIn("work", self.cli("domain", "list"))

        manifest = (self.content / "dotfleet.toml").read_text()
        self.commit_content(
            {
                "dotfleet.toml": manifest + 'lab="Lab bench shortcuts"\n',
                ".config/dotfiles/aliases/lab.zsh": 'alias chosen="echo lab"\n',
            },
            "feat: add lab domain",
        )
        self.cli("update")
        self.assertIn("Lab bench shortcuts", self.cli("domain", "list"))
        self.cli("domain", "set", "--name", "lab")
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["domain"], "lab")
        self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), "lab\n")

        self.commit_content({"dotfleet.toml": manifest}, "feat: remove lab domain")
        stopped = self.cli("update", expected=1)
        self.assertIn("does not declare domain lab", stopped)
        self.assertEqual((self.home / ".config/dotfiles/domain").read_text(), "lab\n")

    def test_installer_rejects_a_malformed_domain_name(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--domain", "Lab_One"],
            env=self.env,
            cwd=self.home,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--domain needs a domain name", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
