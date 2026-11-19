"""Verify Fleet store moves, legacy rollback, and both content manifest names.

Usage: python3 -m unittest discover -s tests -v.
"""

import json
import os
import tomllib
import unittest

import registry

import test_command as commands
from test_manager import command, repository
from common import toml
from convert import detect
from manager import fingerprint
from store_migration import OLD_STORES

# Where the release before ~/.fleet kept each piece. Unlisted pieces lived in ~/.local/share/fleet.
OLDER = {
    "profile.toml": ".config/fleet/profile.toml",
    "backups": "fleet-backups",
    "outputs.json": ".local/state/dotfleet/outputs.json",
    "agents.json": ".local/state/dotfleet/agents.json",
    "last-run.json": ".local/state/dotfleet/last-run.json",
    "deploy": ".cache/fleet-deploy",
    "tui": ".local/share/fleet/tui-venv",
}
FIRST = {
    ".config/fleet/": ".config/dotfleet/",
    ".local/share/fleet/registry.toml": ".local/share/dotfleet/fleet.toml",
    ".local/share/fleet/": ".local/share/dotfleet/",
    "fleet-backups": "dotfleet-backups",
}


def relative_to_first(name):
    """Name the first-generation place, before the dotfleet folders took fleet names."""
    for newer, older in FIRST.items():
        if name.startswith(newer):
            return older + name[len(newer) :]
    return name


def relocated_name(old):
    from store_migration import relative_path

    return relative_path(old)


class StoreMigrationTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    terminal = commands.CommandTests.terminal
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def older_layout(self, names=None, generation=2):
        """Move pieces of ~/.fleet back to where an older release kept them.

        Return {old home-relative place: (new home-relative place, inode)}.
        Generation 1 also uses the dotfleet names that came before the fleet names.
        """
        root = self.home / ".fleet"
        deploy = root / "deploy"
        deploy.mkdir(exist_ok=True)
        (deploy / "run.sh").write_text("#!/usr/bin/env bash\n")
        python = root / "mise/installs/python/3.12.9"
        (python / "bin").mkdir(parents=True, exist_ok=True)
        (python / "bin/python3").write_text("")
        if not (root / "python").is_symlink():
            (root / "python").symlink_to(python)
        names = sorted(names or (path.name for path in root.iterdir()))
        moved = {}
        if "dotfiles" in names:
            # Before the library, the one content checkout lived at ~/dotfiles.
            names.remove("dotfiles")
            profile_path = root / "profile.toml"
            profile = tomllib.loads(profile_path.read_text())
            checkout = root / "dotfiles" / profile.pop("dotfiles")
            moved["dotfiles"] = (str(checkout.relative_to(self.home)), checkout.stat().st_ino)
            checkout.rename(self.home / "dotfiles")
            (root / "dotfiles").rmdir()
            profile_path.write_text(toml(profile))
        for name in names:
            old = OLDER.get(name, ".local/share/fleet/" + name)
            if generation == 1:
                old = relative_to_first(old)
            source, destination = root / name, self.home / old
            destination.parent.mkdir(parents=True, exist_ok=True)
            moved[old] = (relocated_name(old), source.lstat().st_ino)
            source.rename(destination)
        if not any(root.iterdir()):
            root.rmdir()
        # Older layouts named their own places in links, configs, and inventories.
        for link in (
            self.home / ".local/share/fleet/python",
            self.home / ".local/share/dotfleet/python",
        ):
            if link.is_symlink():
                link.unlink()
                link.symlink_to(
                    str(python).replace(
                        "/.fleet/", "/" + str(link.parent.relative_to(self.home)) + "/"
                    )
                )
        config = self.home / ".config/chezmoi/chezmoi.toml"
        data = tomllib.loads(config.read_text())
        source = data["sourceDir"]
        for old, (new, _) in moved.items():
            if source.startswith(str(self.home / new)):
                data["sourceDir"] = str(self.home / old) + source[len(str(self.home / new)) :]
        config.write_text(toml(data))
        return moved

    def assert_moved(self, moved):
        for old, (new, inode) in moved.items():
            self.assertFalse((self.home / old).exists() or (self.home / old).is_symlink(), old)
            path = self.home / new
            if new.endswith("profile.toml") and "dotfiles" in moved:
                # Moving the content checkout rewrites the profile to name the active download.
                self.assertTrue(path.is_file(), new)
            elif path.is_symlink():
                # A relinked link is a new link; its target must name the new place.
                self.assertTrue(path.resolve().is_relative_to(self.home / ".fleet"), new)
            else:
                self.assertEqual(path.lstat().st_ino, inode, new)
        self.assert_no_orphans()

    def assert_no_orphans(self):
        for name in OLD_STORES:
            self.assertFalse((self.home / name).exists(), name)
        for parent in (".config", ".local/share", ".local/state", ".cache"):
            folder = self.home / parent
            names = [path.name for path in folder.iterdir()] if folder.is_dir() else []
            self.assertFalse([name for name in names if "fleet" in name], parent)

    def snapshot(self):
        root = self.home / ".fleet"
        return {str(path.relative_to(root)): path.lstat().st_ino for path in root.iterdir()}

    def migrate_and_rerun(self, moved):
        self.cli("status")
        self.assert_moved(moved)
        before = self.snapshot()
        config = (self.home / ".config/chezmoi/chezmoi.toml").read_bytes()
        self.cli("status")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.home / ".config/chezmoi/chezmoi.toml").read_bytes(), config)
        self.assert_quiet_status()

    def test_fresh_install_creates_only_the_fleet_layout(self):
        output = self.enroll()
        self.assertNotIn("Store moved:", output)
        self.assert_no_orphans()
        names = {path.name for path in (self.home / ".fleet").iterdir()}
        self.assertTrue({"manager", "profile.toml", "registry.toml", "backups"} <= names, names)
        self.assertFalse((self.home / ".local/state/dotfleet").exists())
        self.assertFalse((self.home / "dotfiles").exists())
        library = self.home / ".fleet/dotfiles"
        self.assertEqual([path.name for path in library.iterdir()], ["content"])
        self.assertTrue((library / "content/.git").is_dir())
        self.assertTrue((self.home / ".local/bin/dotfleet").is_file())

    def test_content_checkout_moves_into_the_library_as_active(self):
        self.enroll()
        moved = self.older_layout(["dotfiles"])
        self.assertTrue((self.home / "dotfiles/.git").is_dir())
        self.migrate_and_rerun(moved)
        profile = tomllib.loads((self.home / ".fleet/profile.toml").read_text())
        self.assertEqual(profile["dotfiles"], "content")
        self.assertIn("content", self.cli("dotfiles", "list"))
        self.assertIn("active", self.cli("dotfiles", "list"))

    def test_profile_moves_from_config(self):
        self.enroll()
        self.migrate_and_rerun(self.older_layout(["profile.toml"]))

    def test_manager_store_moves_from_local_share(self):
        self.enroll()
        moved = self.older_layout(
            [
                path.name
                for path in (self.home / ".fleet").iterdir()
                if path.name not in OLDER or path.name == "tui"
            ]
            + ["python", "mise", "deploy"]
        )
        self.cli("status")
        self.assert_moved(
            {old: item for old, item in moved.items() if old != ".cache/fleet-deploy"}
        )
        link = self.home / ".fleet/python"
        self.assertEqual(os.readlink(link), str(self.home / ".fleet/mise/installs/python/3.12.9"))
        config = tomllib.loads((self.home / ".config/chezmoi/chezmoi.toml").read_text())
        self.assertTrue(config["sourceDir"].startswith(str(self.home / ".fleet/")))
        self.assertTrue((self.home / ".fleet/registry.toml").is_file())
        self.assert_quiet_status()

    def test_state_records_move_from_local_state(self):
        self.enroll()
        names = [
            name
            for name in ("outputs.json", "agents.json", "last-run.json")
            if (self.home / ".fleet" / name).exists()
        ]
        self.assertIn("outputs.json", names)
        self.migrate_and_rerun(self.older_layout(names))

    def test_backups_move_from_home(self):
        self.enroll()
        moved = self.older_layout(["backups"])
        self.migrate_and_rerun(moved)
        self.assertIn("20", self.cli("backups", "list"))

    def test_deploy_staging_moves_from_cache(self):
        self.enroll()
        self.migrate_and_rerun(self.older_layout(["deploy"]))
        self.assertTrue((self.home / ".fleet/deploy/run.sh").is_file())

    def marketplace_packages(self, old):
        """Create an older marketplace package folder at old. Return the package inode."""
        package = self.home / old / "market"
        package.mkdir(parents=True)
        (package / "fleet-marketplace.toml").write_text("schema = 1\n")
        return package.stat().st_ino

    def assert_marketplace_packages_moved(self, inode):
        package = self.home / ".fleet/marketplace-packages/market"
        self.assertEqual(package.stat().st_ino, inode)
        self.assertEqual((package / "fleet-marketplace.toml").read_text(), "schema = 1\n")
        self.assertFalse((self.home / ".fleet/library").exists())
        self.assert_no_orphans()

    def test_marketplace_library_folder_moves_to_marketplace_packages(self):
        self.enroll()
        inode = self.marketplace_packages(".fleet/library")
        self.cli("update")
        self.assert_marketplace_packages_moved(inode)
        # A second run finds nothing left to move.
        before = self.snapshot()
        self.cli("status")
        self.assertEqual(self.snapshot(), before)
        self.assert_quiet_status()

    def test_older_marketplace_library_moves_straight_to_marketplace_packages(self):
        self.enroll()
        inode = self.marketplace_packages(".local/share/fleet/library")
        self.cli("update")
        self.assert_marketplace_packages_moved(inode)

    def test_first_generation_dotfleet_stores_move(self):
        self.enroll()
        moved = self.older_layout(generation=1)
        self.assertTrue((self.home / ".local/share/dotfleet/fleet.toml").is_file())
        self.cli("status")
        self.assert_moved(
            {
                old: item
                for old, item in moved.items()
                if old != ".local/share/dotfleet/registry.toml"
            }
        )
        self.assertTrue((self.home / ".fleet/registry.toml").is_file())

    def test_cancelled_wizard_restores_every_old_location(self):
        self.enroll()
        moved = self.older_layout()
        before = {
            str(path.relative_to(self.home)): (
                path.lstat().st_ino,
                path.read_bytes()
                if path.is_file() and not path.is_symlink()
                else os.readlink(path)
                if path.is_symlink()
                else None,
            )
            for old in moved
            for path in [self.home / old]
        }
        config = (self.home / ".config/chezmoi/chezmoi.toml").read_bytes()
        output = self.terminal(
            ["configure"],
            [
                (b"Content source [repo]:", b"\n"),
                (b"Content repository [", b"\n"),
                (b"Domain [core]:", b"\n"),
                (b"Git author name [", b"\n"),
                (b"Git author email [", b"\n"),
                (b"Tools [accept]:", b"\n"),
                (b"extra [on]:", b"\n"),
                (b"Review [confirm]:", b"quit\n"),
            ],
        )
        self.assertIn("Store moved back:", output)
        self.assertFalse((self.home / ".fleet").exists())
        for name, (inode, content) in before.items():
            path = self.home / name
            # The profile is rewritten and restored, so compare its bytes, not its inode.
            if not path.is_symlink() and not name.endswith("profile.toml"):
                self.assertEqual(path.lstat().st_ino, inode, name)
            if content is not None:
                current = path.read_bytes() if not path.is_symlink() else os.readlink(path)
                self.assertEqual(current, content, name)
        self.assertEqual((self.home / ".config/chezmoi/chezmoi.toml").read_bytes(), config)
        runs = sorted((self.home / ".local/share/fleet/runs").glob("*.json"))
        self.assertEqual(json.loads(runs[-1].read_text())["result"], "CANCELLED")
        self.cli("status")
        self.assert_moved({old: (new, inode) for old, (new, inode) in moved.items()})

    def test_help_leaves_older_stores_in_place(self):
        self.enroll()
        moved = self.older_layout()
        self.assertIn("Usage: dotfleet status", self.cli("status", "--help"))
        for old in moved:
            self.assertTrue((self.home / old).exists(), old)
        self.assertFalse((self.home / ".fleet").exists())

    def test_conflict_stops_before_moving_any_store(self):
        self.enroll()
        moved = self.older_layout(["profile.toml", "backups"])
        (self.home / ".fleet/backups").mkdir()
        (self.home / ".fleet/backups/keep").write_text("keep")
        inodes = {old: (self.home / old).lstat().st_ino for old in moved}
        output = self.cli("status", expected=1)
        self.assertIn("conflict. Nothing was moved", output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(len([line for line in output.splitlines() if line.strip()]), 1, output)
        for old, inode in inodes.items():
            self.assertEqual((self.home / old).lstat().st_ino, inode)
        self.assertEqual((self.home / ".fleet/backups/keep").read_text(), "keep")

    def test_old_backups_restore_into_the_new_layout(self):
        self.enroll()
        profile = self.home / ".fleet/profile.toml"
        saved = profile.read_bytes()
        backup = self.home / ".fleet/backups/20000101-000000"
        copy = backup / "home/.config/dotfleet/profile.toml"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(saved)
        copy.chmod(0o600)
        entry = {
            "path": ".config/dotfleet/profile.toml",
            "state": "saved",
            "fingerprint": fingerprint(copy),
        }
        (backup / "MANIFEST.json").write_text(json.dumps([entry]) + "\n")
        (backup / "RUN.json").write_text('{"result":"SUCCESS"}\n')
        data = tomllib.loads(profile.read_text())
        data["git_name"] = "Later choice"
        profile.write_text(toml(data))
        output = self.terminal(
            ["backups", "restore", "--backup", backup.name], [(b"Confirm [cancel]:", b"restore\n")]
        )
        self.assertIn("~/.fleet/profile.toml", output)
        self.assertEqual(profile.read_bytes(), saved)
        self.assert_no_orphans()

    def test_migration_only_translates_this_machines_registry_paths(self):
        self.enroll()
        self.older_layout()
        path = self.home / ".local/share/fleet/registry.toml"
        data = tomllib.loads(path.read_text())
        current = next(iter(data["machines"].values()))
        current["last_backup"] = str(self.home / "fleet-backups/20000101-000000")
        current["last_run_record"] = current["last_backup"] + "/RUN.json"
        other = dict(current, hostname="other-machine")
        data["machines"]["other-machine"] = other
        path.write_text(registry.serialize(data))
        self.cli("status")
        saved = tomllib.loads((self.home / registry.REGISTRY).read_text())
        self.assertEqual(saved["machines"]["other-machine"], other)

    def test_preferred_manifest_wins_for_enrollment_and_commands(self):
        self.enroll()
        preferred = self.content / "fleet.toml"
        preferred.write_text(
            'format="plain"\n[groups.extra]\ndescription="Preferred group"\nfiles=[".extra"]\n[domains]\ncore="Preferred base"\n'
        )
        command("git", "-C", str(self.content), "add", "fleet.toml")
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
            "feat: new manifest name",
        )
        self.cli("update")
        self.assertIn("Preferred group", self.cli("groups", "list"))
        self.assertIn("Preferred base", self.cli("domain", "list"))
        self.assert_quiet_status()

    def test_invalid_new_manifest_does_not_fall_back_to_old_one(self):
        repo = repository(
            self.base / "content",
            {
                "fleet.toml": 'format="wrong"\n',
                "dotfleet.toml": 'format="plain"\n',
                ".zshrc": "# shell\n",
            },
        )
        with self.assertRaisesRegex(ValueError, "Unknown content format"):
            detect(repo)


if __name__ == "__main__":
    unittest.main()
