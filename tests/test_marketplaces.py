"""Add, render, and delete marketplaces of agent packages.

Usage: python3 -m unittest discover -s tests -v.
Help: PYTHONPATH=scripts:tests python3 tests/test_marketplaces.py --help.
"""

import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest

import agent_deploy
import agent_table
import marketplaces
import test_agent_module
import test_command
import test_domains
import test_manager as helpers
from common import read_toml
from test_manager import ROOT, command, repository

AGENTS = read_toml(ROOT / "agents/manifest.toml")["agents"]
CATALOG = """schema = 1
description = "Example agent packages."
[packages.review-kit]
description = "Review commands and checklists."
version = "1.2.0"
split = true
[packages.review-kit.skills.pr-review]
description = "Review a pull request."
path = "review-kit/skills/pr-review"
[packages.review-kit.mcp.review-docs]
description = "Search review guides."
transport = "stdio"
command = "npx"
args = ["-y", "@example/review-docs"]
[packages.notes]
description = "Note-taking rules."
[packages.notes.rules.notes-style]
description = "Write short notes."
file = "notes/rules/style.md"
"""
FILES = {
    "fleet-marketplace.toml": CATALOG,
    "review-kit/skills/pr-review/SKILL.md": "---\nname: pr-review\ndescription: Review.\n---\nBody\n",
    "notes/rules/style.md": "Write short notes.\n",
}


def market(path, files=FILES):
    root = repository(path, files)
    command("git", "-C", str(root), "tag", "v1.0.0")
    return root


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = market(Path(self.folder.name) / "market")

    def test_packages_live_apart_from_the_dotfiles_library(self):
        import dotfiles_library

        self.assertEqual(marketplaces.LIBRARY, ".fleet/marketplace-packages")
        self.assertNotEqual(marketplaces.LIBRARY, dotfiles_library.LIBRARY)

    def test_packages_validate_and_items_keep_their_package_root(self):
        catalog = marketplaces.read_catalog(self.root)
        items = marketplaces.package_items(
            self.root, "review-kit", catalog["packages"]["review-kit"], AGENTS
        )
        self.assertEqual(sorted(items), ["mcp.review-docs", "skills.pr-review"])
        self.assertEqual(items["skills.pr-review"].root, self.root)

    def test_bad_catalogs_fail_with_their_reason(self):
        for text, message in [
            ("schema = 2\n", "needs schema = 1"),
            ("schema = 1\n", "declares no packages"),
            ("schema = 1\nowner = 'x'\n", "unknown key owner"),
        ]:
            (self.root / marketplaces.CATALOG).write_text(text)
            with self.assertRaisesRegex(ValueError, message):
                marketplaces.read_catalog(self.root)
        catalog = tomllib.loads(CATALOG.replace("review-kit/skills/pr-review", "missing"))
        with self.assertRaisesRegex(ValueError, "missing/SKILL.md is not tracked"):
            marketplaces.package_items(
                self.root, "review-kit", catalog["packages"]["review-kit"], AGENTS
            )

    def test_selection_takes_all_packages_named_packages_or_split_features(self):
        catalog = tomllib.loads(CATALOG)
        self.assertEqual(
            marketplaces.selection(catalog, {"all": True}), {"review-kit": None, "notes": None}
        )
        self.assertEqual(marketplaces.selection(catalog, {"packages": ["notes"]}), {"notes": None})
        self.assertEqual(
            marketplaces.selection(catalog, {"features": ["review-kit/mcp.review-docs"]}),
            {"review-kit": {"mcp.review-docs"}},
        )
        with self.assertRaisesRegex(ValueError, "does not allow taking single features"):
            marketplaces.selection(catalog, {"features": ["notes/rules.notes-style"]})
        with self.assertRaisesRegex(ValueError, "Unknown package absent"):
            marketplaces.selection(catalog, {"packages": ["absent"]})

    def test_pulled_items_cannot_share_a_content_item_name(self):
        home = Path(self.folder.name) / "home"
        (home / marketplaces.LIBRARY).mkdir(parents=True)
        command("git", "clone", "-q", str(self.root), str(home / marketplaces.LIBRARY / "market"))
        record = {
            "source": str(self.root),
            "pin": "",
            "commit": "",
            "version": "",
            "take": {"all": True},
        }
        (home / marketplaces.RECORD).write_text(
            json.dumps({"schema_version": 1, "marketplaces": {"market": record}})
        )
        table = agent_table.Table([], [])
        with self.assertRaisesRegex(ValueError, "same name as a content item"):
            marketplaces.extend(home, table, ["pr-review"], AGENTS)
        self.assertEqual(len(marketplaces.extend(home, table, [], AGENTS).items), 3)


class MarketplaceCommandTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    enroll = test_command.CommandTests.enroll
    cli = test_command.CommandTests.cli
    record = test_command.CommandTests.record
    commit_content = test_domains.ContentDomainCommandTests.commit_content
    assert_review_golden = test_command.CommandTests.assert_review_golden
    assert_golden = test_command.CommandTests.assert_golden

    def setup_agents(self):
        self.enroll()
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        command(
            "bash",
            str(self.origin / "install.sh"),
            "--defaults",
            "--agents",
            "claude,codex",
            env=self.env,
            cwd=self.home,
        )
        self.market = market(self.base / "market")

    def test_add_installs_packages_into_the_package_folder_and_the_tree(self):
        self.setup_agents()
        output = self.cli(
            "marketplaces", "add", "--source", str(self.market), "--packages", "review-kit"
        )
        tree = self.home / agent_deploy.TREE
        self.assertIn("~/.fleet/marketplaces.json", output)
        self.assertTrue((tree / "skills/pr-review/SKILL.md").is_file())
        self.assertEqual(
            os.readlink(self.home / ".agents/skills/pr-review"), str(tree / "skills/pr-review")
        )
        served = json.loads((tree / "gateway/codex.json").read_text())["servers"]
        self.assertEqual(served["review-docs"]["command"], "npx")
        self.assertFalse((tree / "rules/claude.md").exists())
        packages = self.home / ".fleet/marketplace-packages/market"
        self.assertTrue((packages / marketplaces.CATALOG).is_file())
        saved = json.loads((self.home / marketplaces.RECORD).read_text())["marketplaces"]["market"]
        self.assertEqual(saved["take"], {"packages": ["review-kit"]})
        self.assertEqual(saved["version"], "v1.0.0")
        listing = self.cli("marketplaces", "list")
        self.assertRegex(
            listing, r"\n  market +v1\.0\.0 @ \w{8}  review-kit · updates not checked yet\n"
        )
        self.assertIn("v1.0.0 @", listing)
        refused = self.cli("marketplaces", "add", "--source", str(self.market), expected=1)
        self.assertIn("Marketplace market exists", refused)

        edited = tree / "skills/pr-review/SKILL.md"
        edited.write_text("my edit\n")
        output = self.cli("agents", "set", "--name", "mcp.review-docs", "--state", "on")
        self.assertIn(
            "Replaced edited ~/.fleet/agents/skills/pr-review/SKILL.md",
            " ".join(output.split()),
        )
        self.assertIn("Body", edited.read_text())
        backups = sorted((self.home / ".fleet/backups").iterdir())
        saved_copy = backups[-1] / "home" / agent_deploy.TREE / "skills/pr-review/SKILL.md"
        self.assertEqual(saved_copy.read_text(), "my edit\n")

        self.cli("marketplaces", "delete", "--name", "market")
        self.assertFalse((self.home / ".agents/skills/pr-review").is_symlink())
        self.assertFalse(packages.exists())
        self.assertFalse((tree / "gateway/codex.json").exists())
        self.assertIn("No marketplaces", self.cli("marketplaces", "list"))

    def test_features_take_single_items_and_bad_selections_change_nothing(self):
        self.setup_agents()
        refused = self.cli(
            "marketplaces",
            "add",
            "--source",
            str(self.market),
            "--features",
            "notes/rules.notes-style",
            expected=1,
        )
        self.assertIn("does not allow taking single features", refused)
        self.assertFalse((self.home / marketplaces.RECORD).exists())
        self.cli(
            "marketplaces",
            "add",
            "--source",
            str(self.market),
            "--features",
            "review-kit/mcp.review-docs",
        )
        self.assertFalse((self.home / ".agents/skills/pr-review").exists())
        served = self.home / agent_deploy.TREE / "gateway/codex.json"
        self.assertIn("review-docs", json.loads(served.read_text())["servers"])

    def publish(self, files, tag):
        for name, text in files.items():
            path = self.market / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        command("git", "-C", str(self.market), "add", ".")
        command(
            "git",
            "-C",
            str(self.market),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "feat: " + tag,
        )
        command("git", "-C", str(self.market), "tag", tag)

    def test_a_pin_blocks_updates_but_not_information(self):
        self.setup_agents()
        self.cli("marketplaces", "add", "--source", str(self.market), "--pin", "v1.0.0")
        catalog = CATALOG + (
            '[packages.review-kit.skills.release-notes]\ndescription = "Notes."\n'
            'path = "review-kit/skills/release-notes"\n'
        )
        self.publish(
            {
                "fleet-marketplace.toml": catalog,
                "review-kit/skills/release-notes/SKILL.md": "---\nname: release-notes\n---\n",
            },
            "v1.1.0",
        )
        checked = self.cli("check")
        self.assertIn("Marketplace market  1 commit behind", checked)
        self.assertIn(" · the pin v1.0.0 blocks the update\n", checked)
        self.assertIn("market +1, held at v1.0.0", self.cli("status"))
        self.assertIn("the pin blocks them", self.cli("marketplaces", "list"))
        output = self.cli("marketplaces", "update")
        self.assertIn("pinned at v1.0.0; the pin blocks updates", output)
        self.assertIn("Marketplaces are current", output)
        self.assertFalse((self.home / ".agents/skills/release-notes").exists())

        self.cli("marketplaces", "update", "--name", "market", "--unpin")
        self.assertTrue((self.home / ".agents/skills/release-notes").is_symlink())
        saved = json.loads((self.home / marketplaces.RECORD).read_text())["marketplaces"]["market"]
        self.assertEqual((saved["pin"], saved["version"]), ("", "v1.1.0"))

        self.publish({"notes/rules/style.md": "Write shorter notes.\n"}, "v1.2.0")
        self.assertIn("Marketplace market  1 commit behind", self.cli("check", expected=1))
        self.cli("marketplaces", "update")
        self.assertIn(
            "Write shorter notes.", (self.home / agent_deploy.TREE / "rules/claude.md").read_text()
        )

        self.cli("marketplaces", "update", "--name", "market", "--pin", "v1.0.0")
        self.assertFalse((self.home / ".agents/skills/release-notes").exists())
        self.assertIn("Marketplaces are current", self.cli("marketplaces", "update"))


if __name__ == "__main__":
    unittest.main()
