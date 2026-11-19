"""Verify Fleet store moves, legacy rollback, and both content manifest names.

Usage: python3 -m unittest discover -s tests -v.
"""

import json
import os
from pathlib import Path
import tomllib
import unittest

import registry

import test_command as commands
from test_manager import command, repository
from common import toml
from convert import detect
from manager import fingerprint


class StoreMigrationTests(unittest.TestCase):
    setUp = commands.CommandTests.setUp
    enroll = commands.CommandTests.enroll
    cli = commands.CommandTests.cli
    record = commands.CommandTests.record
    terminal = commands.CommandTests.terminal
    assert_quiet_status = commands.CommandTests.assert_quiet_status

    def legacy_stores(self):
        profile_path = self.home / ".config/fleet/profile.toml"
        profile = b"# Keep the owner's formatting.\n" + profile_path.read_bytes()
        profile_path.write_bytes(profile)
        config_path = self.home / ".config/chezmoi/chezmoi.toml"
        config = tomllib.loads(config_path.read_text())
        config["sourceDir"] = str(self.home / ".local/share/dotfleet/chezmoi-source")
        config_path.write_text(toml(config))
        backup = self.home / "fleet-backups/20000101-000000"
        entries = []
        for name, content in [
            (".config/dotfleet/profile.toml", profile),
            (".config/chezmoi/chezmoi.toml", config_path.read_bytes()),
        ]:
            saved = backup / "home" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(content)
            saved.chmod(0o600)
            entries.append({"path": name, "state": "saved", "fingerprint": fingerprint(saved)})
        (backup / "MANIFEST.json").write_text(json.dumps(entries) + "\n")
        (backup / "RUN.json").write_text('{"result":"SUCCESS"}\n')
        registry = self.home / ".local/share/fleet/registry.toml"
        registry.rename(registry.with_name("fleet.toml"))
        stores = [
            (".config/dotfleet", ".config/fleet"),
            ("dotfleet-backups", "fleet-backups"),
            (".local/share/dotfleet", ".local/share/fleet"),
        ]
        inodes = {}
        for old, new in stores:
            path = self.home / new
            inodes[new] = path.stat().st_ino
            path.rename(self.home / old)
        self.assert_quiet_status()
        return profile, stores, inodes

    def assert_migration(self, output, profile, stores, inodes):
        self.assertEqual((self.home / ".config/fleet/profile.toml").read_bytes(), profile)
        for old, new in stores:
            self.assertFalse((self.home / old).exists())
            self.assertEqual((self.home / new).stat().st_ino, inodes[new])
            self.assertIn(f"Store moved: ~/{old} → ~/{new}", output)
        config = tomllib.loads((self.home / ".config/chezmoi/chezmoi.toml").read_text())
        self.assertEqual(config["sourceDir"], str(self.home / ".local/share/fleet/chezmoi-source"))
        self.assertTrue((self.home / ".local/share/fleet/registry.toml").is_file())
        self.assertFalse((self.home / ".local/share/fleet/fleet.toml").exists())
        record, _ = self.record()
        self.assertEqual(len(record["store_moves"]), 3)
        self.assertIn("20000101-000000", self.cli("backups", "restore"))
        self.assert_quiet_status()
        if directory := os.environ.get("FLEET_MIGRATION_EVIDENCE"):
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / "review.txt").write_text(output)

    def test_installer_moves_stores_and_restores_legacy_backup(self):
        self.enroll()
        profile, stores, inodes = self.legacy_stores()
        old_backup = self.home / "dotfleet-backups/20000101-000000"
        manifest = (old_backup / "MANIFEST.json").read_bytes()
        backup_inode = old_backup.stat().st_ino
        profile_inode = (self.home / ".config/dotfleet/profile.toml").stat().st_ino
        output = command(
            "bash",
            str(self.home / ".local/share/dotfleet/manager/install.sh"),
            "--defaults",
            env=self.env,
        )
        self.assert_migration(output, profile, stores, inodes)
        self.assertEqual((self.home / ".config/fleet/profile.toml").stat().st_ino, profile_inode)
        backup = self.home / "fleet-backups/20000101-000000"
        self.assertEqual(backup.stat().st_ino, backup_inode)
        self.assertEqual((backup / "MANIFEST.json").read_bytes(), manifest)
        path = self.home / ".config/fleet/profile.toml"
        data = tomllib.loads(path.read_text())
        data["git_name"] = "Later choice"
        path.write_text(toml(data))
        output = self.terminal(
            ["backups", "restore", "--backup", backup.name], [(b"Confirm [cancel]:", b"restore\n")]
        )
        self.assertIn("~/.config/fleet/profile.toml", output)
        self.assertEqual(path.read_bytes(), profile)
        self.assertFalse((self.home / ".config/dotfleet").exists())
        self.assert_quiet_status()
        self.assertEqual((backup / "MANIFEST.json").read_bytes(), manifest)
        self.assertNotIn("Store moved:", self.cli("update"))

    def test_update_moves_stores_before_reading_profile(self):
        self.enroll()
        profile, stores, inodes = self.legacy_stores()
        from test_updates import advance

        advance(self.origin, 1)
        self.assert_migration(self.cli("update"), profile, stores, inodes)
        self.assertNotIn("Store moved:", self.cli("update"))
        self.assert_quiet_status()

    def test_cancelled_wizard_moves_every_store_back(self):
        self.enroll()
        profile, stores, inodes = self.legacy_stores()
        config_path = self.home / ".config/chezmoi/chezmoi.toml"
        config = config_path.read_bytes()
        registry_bytes = (self.home / ".local/share/dotfleet/fleet.toml").read_bytes()
        output = self.terminal(
            ["configure"],
            [
                (b"Content door [repo]:", b"\n"),
                (b"Content repository [", b"\n"),
                (b"Domain [core]:", b"\n"),
                (b"Git author name [", b"\n"),
                (b"Git author email [", b"\n"),
                (b"Tools [accept]:", b"\n"),
                (b"extra [on]:", b"\n"),
                (b"Review [confirm]:", b"quit\n"),
            ],
        )
        for old, new in stores:
            self.assertIn(f"Store moved back: ~/{new} → ~/{old}", output)
            self.assertEqual((self.home / old).stat().st_ino, inodes[new])
            self.assertFalse((self.home / new).exists())
        self.assertEqual((self.home / ".config/dotfleet/profile.toml").read_bytes(), profile)
        self.assertEqual(config_path.read_bytes(), config)
        self.assertEqual(
            (self.home / ".local/share/dotfleet/fleet.toml").read_bytes(), registry_bytes
        )
        self.assertFalse((self.home / ".local/share/dotfleet/registry.toml").exists())
        runs = sorted((self.home / ".local/share/dotfleet/runs").glob("*.json"))
        self.assertEqual(json.loads(runs[-1].read_text())["result"], "CANCELLED")
        output = self.cli("update")
        self.assert_migration(output, profile, stores, inodes)

    def test_help_leaves_legacy_stores_in_place(self):
        self.enroll()
        profile, stores, _ = self.legacy_stores()
        output = self.cli("status", "--help")
        self.assertIn("Usage: dotfleet status", output)
        for old, new in stores:
            self.assertTrue((self.home / old).is_dir())
            self.assertFalse((self.home / new).exists())
        self.assertEqual((self.home / ".config/dotfleet/profile.toml").read_bytes(), profile)

    def test_conflict_stops_before_moving_any_store(self):
        self.enroll()
        _, _, _ = self.legacy_stores()
        collision = self.home / "fleet-backups"
        collision.mkdir()
        (collision / "keep").write_text("keep")
        before = {
            str(path.relative_to(self.home)): path.stat().st_ino
            for path in [
                self.home / ".config/dotfleet",
                self.home / "dotfleet-backups",
                self.home / ".local/share/dotfleet",
                collision,
            ]
        }
        output = self.cli("update", expected=1)
        self.assertIn("conflict. Nothing was moved", output)
        self.assertNotIn("Traceback", output)
        for name, inode in before.items():
            self.assertEqual((self.home / name).stat().st_ino, inode)
        self.assertFalse((self.home / ".config/fleet").exists())
        self.assertEqual((collision / "keep").read_text(), "keep")

    def test_fresh_install_has_no_store_move_and_old_manifest_enrolls(self):
        output = self.enroll()
        self.assertNotIn("Store moved:", output)
        self.assertTrue((self.home / "dotfiles/dotfleet.toml").exists())
        self.assertTrue((self.home / ".config/fleet/profile.toml").exists())
        self.assert_quiet_status()

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

    def test_migration_only_translates_this_machines_registry_paths(self):
        self.enroll()
        self.legacy_stores()
        path = self.home / ".local/share/dotfleet/fleet.toml"
        data = tomllib.loads(path.read_text())
        current = next(iter(data["machines"].values()))
        current["last_backup"] = str(self.home / "dotfleet-backups/20000101-000000")
        current["last_run_record"] = current["last_backup"] + "/RUN.json"
        other = dict(current, hostname="other-machine")
        data["machines"]["other-machine"] = other
        path.write_text(registry.serialize(data))
        self.cli("update")
        saved = tomllib.loads((self.home / registry.REGISTRY).read_text())
        self.assertEqual(saved["machines"]["other-machine"], other)

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
