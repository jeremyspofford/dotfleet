"""Diff prints patches without writing enrollment state or backups."""
import os
from pathlib import Path
import unittest

import test_command as helpers
from test_manager import command


class DiffTests(unittest.TestCase):
    setUp = helpers.CommandTests.setUp
    enroll = helpers.CommandTests.enroll
    cli = helpers.CommandTests.cli

    def snapshot(self):
        return {str(path.relative_to(self.home)): ('link', os.readlink(path)) if path.is_symlink()
                else ('file', path.read_bytes(), path.stat().st_mode) if path.is_file() else ('directory',)
                for path in self.home.rglob('*')}

    def test_clean_changed_and_stat_are_read_only(self):
        self.enroll()
        before = self.snapshot()
        clean = self.cli('diff')
        sha = command('git', '-C', str(self.content), 'rev-parse', 'HEAD').strip()[:8]
        self.assertEqual(clean, f'No differences. Your files match {self.content} @ {sha}.\n')
        self.assertEqual(self.snapshot(), before)
        Path('/tmp/dotfleet-diff-clean.txt').write_text(clean)
        (self.home / '.file').write_text('local change\n')
        before = self.snapshot()
        changed = self.cli('diff', expected=1)
        self.assertIn('~/.file\n', changed)
        self.assertIn('-local change\n', changed)
        self.assertIn('+original\n', changed)
        self.assertNotIn('Locations', changed)
        self.assertNotIn('Selected choices', changed)
        self.assertEqual(self.snapshot(), before)
        Path('/tmp/dotfleet-diff-changed.txt').write_text(changed)
        self.assertEqual(self.cli('diff', '--stat', expected=1), '~/.file\n')
        self.assertEqual(self.snapshot(), before)

    def test_update_preview_includes_removed_file_and_generated_identity(self):
        self.enroll()
        (self.content / '.extra').unlink()
        command('git', '-C', str(self.content), 'add', '.')
        command('git', '-C', str(self.content), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                'commit', '-m', 'fix: retire extra file')
        (self.home / '.config/git/local.gitconfig').write_text('[user]\nname = Changed\n')
        before = self.snapshot()
        output = self.cli('diff', expected=1)
        self.assertIn('~/.extra\n', output)
        self.assertIn('deleted file mode', output)
        self.assertIn('-group\n', output)
        self.assertIn('~/.config/git/local.gitconfig\n', output)
        self.assertIn('Command Example', output)
        self.assertEqual(self.snapshot(), before)
