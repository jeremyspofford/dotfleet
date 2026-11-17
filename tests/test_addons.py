"""Check the declarative add-on boundary without installing any add-on.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_addons.py --help.
"""

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from addons import STORE, installed_addons, validate_manifest, validate_receipt
from common import toml

FIXTURE = ROOT / "tests/fixtures/addons/example"


class AddonTests(unittest.TestCase):
    def manifest(self):
        return tomllib.loads((FIXTURE / "addon.toml").read_text())

    def receipt(self):
        return tomllib.loads((FIXTURE / "receipt.toml").read_text())

    def test_fixture_validates_without_mutation(self):
        manifest, receipt = self.manifest(), self.receipt()
        original = copy.deepcopy(manifest)
        self.assertEqual(validate_manifest(manifest, "0.2.0"), original)
        self.assertEqual(validate_receipt(receipt, manifest), receipt)
        self.assertEqual(manifest, original)

    def test_unknown_reads_and_unsafe_outputs_fail(self):
        for field, value in [
            ("profile", "secret_values"),
            ("profile", "git_credentials"),
            ("machine", "home_contents"),
        ]:
            manifest = self.manifest()
            manifest["reads"][field].append(value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_manifest(manifest)
        for path in [
            "../outside",
            "/etc/passwd",
            ".config/other/file",
            "dotfiles/.zshrc",
            ".local/share/fleet/registry.toml",
        ]:
            manifest = self.manifest()
            manifest["install"]["outputs"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_manifest(manifest)
        manifest = self.manifest()
        manifest["entrypoint"] = "run.sh"
        with self.assertRaises(ValueError):
            validate_manifest(manifest)

    def test_compatibility_dependencies_and_teardown_are_required(self):
        for version in ["latest", "1.0.0", "0.0.9"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                validate_manifest(self.manifest(), version)
        for name in ["preserve_edits", "preserve_credentials", "owned_only", "no_unbounded_delete"]:
            manifest = self.manifest()
            manifest["teardown"][name] = False
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_manifest(manifest)
        manifest = self.manifest()
        manifest["install"]["outputs"][0]["kind"] = []
        with self.assertRaises(ValueError):
            validate_manifest(manifest)
        manifest = self.manifest()
        manifest["dependencies"]["addons"] = ["fixture-example"]
        with self.assertRaises(ValueError):
            validate_manifest(manifest)
        manifest = self.manifest()
        manifest["install"]["tools"] = {"node": "latest"}
        with self.assertRaises(ValueError):
            validate_manifest(manifest)

    def test_listing_requires_receipt_and_never_creates_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(installed_addons(home, "0.2.0"), [])
            self.assertEqual(list(home.iterdir()), [])
            folder = home / STORE / "fixture-example"
            folder.mkdir(parents=True)
            shutil.copy2(FIXTURE / "addon.toml", folder / "addon.toml")
            self.assertEqual(installed_addons(home, "0.2.0"), [])
            shutil.copy2(FIXTURE / "receipt.toml", folder / "receipt.toml")
            self.assertEqual(
                installed_addons(home, "0.2.0"),
                [
                    {
                        "id": "fixture-example",
                        "version": "0.1.0",
                        "description": self.manifest()["description"],
                        "outputs": [".config/fixture-example/settings.toml"],
                    }
                ],
            )
            self.assertFalse((home / ".config").exists())

    def test_receipt_checks_opt_in_identity_and_fingerprints(self):
        for key, value in [
            ("opted_in", False),
            ("id", "different"),
            ("version", "0.2.0"),
            ("outputs", {}),
        ]:
            receipt = self.receipt()
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_receipt(receipt, self.manifest())
        receipt = self.receipt()
        receipt["outputs"][".config/fixture-example/settings.toml"]["sha256"] = "not-a-hash"
        with self.assertRaises(ValueError):
            validate_receipt(receipt, self.manifest())

    def test_duplicate_ownership_and_linked_records_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            first = home / STORE / "fixture-example"
            first.mkdir(parents=True)
            for name in ["addon.toml", "receipt.toml"]:
                shutil.copy2(FIXTURE / name, first / name)
            second = home / STORE / "another-example"
            second.mkdir()
            manifest, receipt = self.manifest(), self.receipt()
            manifest["id"] = receipt["id"] = "another-example"
            (second / "addon.toml").write_text(toml(manifest))
            (second / "receipt.toml").write_text(toml(receipt))
            with self.assertRaisesRegex(ValueError, "same output"):
                installed_addons(home, "0.2.0")
            shutil.rmtree(second)
            (first / "receipt.toml").unlink()
            (first / "receipt.toml").symlink_to(FIXTURE / "receipt.toml")
            with self.assertRaisesRegex(ValueError, "real manifest and receipt"):
                installed_addons(home, "0.2.0")


if __name__ == "__main__":
    unittest.main()
