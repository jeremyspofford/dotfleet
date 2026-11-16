"""Exercise enrollment doors, review, provenance, and flags through a real terminal."""
import json
import os
from pathlib import Path
import subprocess
import tomllib
import unittest

import test_manager as helpers
from test_manager import ROOT, command, repository
from common import toml
from enrollment import scan


class WizardTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    terminal = helpers.EnrollmentTests.terminal
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status
    seed_profile = helpers.EnrollmentTests.seed_profile

    def answers(self, door='defaults', repo=None):
        result = [(b'Content door [', (door + '\n').encode())]
        if repo:
            result.append((b'Content repository [', (str(repo) + '\n').encode()))
        result += [(b'Domain [core]:', b'\n'), (b'Git author name [', b'Wizard Person\n'),
                   (b'Git author email [', b'wizard@example.invalid\n'), (b'Pins [accept]:', b'\n')]
        if door != 'adopt' and repo != os.environ['DOTFLEET_ARCHIVE_FIXTURE']:
            result += [(f'{name} [on]:'.encode(), b'\n') for name in ['docker', 'terraform', 'kubectl', 'python', 'npm']]
        result += [(b'Agents [none]:', b'none\n'), (b'Review [quit]:', b'confirm\n')]
        return result

    def defaults_origin(self):
        # Exercise the real defaults URL anonymously without downloading during each test.
        self.env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_1=f"url.{os.environ['DOTFLEET_CONTENT_FIXTURE']}.insteadOf",
                        GIT_CONFIG_VALUE_1='https://github.com/jeremyspofford/dotfiles')

    def snapshot(self):
        result = {}
        for path in self.home.rglob('*'):
            name = str(path.relative_to(self.home))
            value = ('link', os.readlink(path)) if path.is_symlink() else ('dir',) if path.is_dir() else ('file', path.read_bytes())
            result[name] = (value, path.lstat().st_mode & 0o777)
        return result

    def test_defaults_door_bare_home_and_silent_rerun(self):
        self.defaults_origin()
        output = self.terminal(self.answers(), flags=[], auto_wizard=False)
        self.assertIn('Recommended: defaults.', output)
        self.assertIn('7. Group: docker NEW', output)
        self.assertIn('3 · Files', output)
        self.assertIn('will be', output)
        self.assertIn('chezmoi status: clean', output)
        self.assert_quiet_status()
        profile = (self.home / '.config/dotfleet/profile.toml').read_bytes()
        output = self.terminal([], flags=[], auto_wizard=False)
        self.assertNotIn('9. Review', output)
        self.assertEqual((self.home / '.config/dotfleet/profile.toml').read_bytes(), profile)
        self.assert_quiet_status()

    def test_adopt_excludes_secret_and_preserves_every_adopted_byte(self):
        (self.home / '.zshrc').write_bytes(b'# lived-in shell\nexport ADOPTED=yes\n')
        (self.home / '.aliases').write_bytes(b"alias here='pwd'\n")
        (self.home / '.settings.example').write_text('Preserve and manage this existing file.\n')
        (self.home / '.linked').symlink_to('.aliases')
        folder = self.home / '.config/example'
        folder.mkdir(parents=True)
        (folder / 'settings.ini').write_bytes(b'layout = compact\n')
        secret = self.home / '.service-config'
        secret.write_bytes(b'export SERVICE_API_TOKEN="fake-secret-for-this-test"\n')
        before = {name: (self.home / name).read_bytes() for name in scan(self.home)[0]}
        output = self.terminal(self.answers('adopt'), flags=[], auto_wizard=False)
        self.assertIn('Recommended: adopt.', output)
        self.assertIn('Excluded from adoption: .service-config', output)
        self.assertNotIn('fake-secret-for-this-test', output)
        checkout = self.home / 'dotfiles'
        names = command('git', '-C', str(checkout), 'ls-files').splitlines()
        self.assertNotIn('.service-config', names)
        self.assertEqual(set(names), set(before) - {'.service-config'} | {'dotfleet.toml'})
        for name in set(names) - {'dotfleet.toml'}:
            self.assertEqual((self.home / name).read_bytes(), before[name], name)
        self.assertEqual(secret.read_bytes(), before['.service-config'])
        managed = command('chezmoi', 'managed', '--path-style', 'relative', env=self.env, cwd=self.home).splitlines()
        self.assertIn('.settings.example', managed)
        self.assertEqual(command('git', '-C', str(checkout), 'remote'), '')
        self.assert_quiet_status()
        self.terminal([], flags=[], auto_wizard=False)
        self.assert_quiet_status()

    def test_repo_door_legacy_stow(self):
        repo = os.environ['DOTFLEET_ARCHIVE_FIXTURE']
        output = self.terminal(self.answers('repo', repo), flags=[], auto_wizard=False)
        self.assertIn('chezmoi status: clean', output)
        self.assertTrue((self.home / '.zshrc').is_file())
        self.assert_quiet_status()

    def test_review_quit_leaves_entire_home_unchanged(self):
        self.defaults_origin()
        (self.home / '.zshrc').write_text('# keep exactly\n')
        (self.home / '.link').symlink_to('.zshrc')
        before = self.snapshot()
        answers = self.answers()
        answers[-1] = (b'Review [quit]:', b'quit\n')
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn('chezmoi status: cancelled', output)
        self.assertEqual(self.snapshot(), before)

    def test_review_go_back_changes_identity_before_apply(self):
        self.defaults_origin()
        answers = self.answers()
        answers[-1] = (b'Review [quit]:', b'identity\n')
        answers += [(b'Git author name [Wizard Person]:', b'Changed Person\n'),
                    (b'Git author email [wizard@example.invalid]:', b'\n'), (b'Pins [accept]:', b'\n')]
        answers += [(f'{name} [on]:'.encode(), b'\n') for name in ['docker', 'terraform', 'kubectl', 'python', 'npm']]
        answers += [(b'Agents [none]:', b'\n'), (b'Review [quit]:', b'confirm\n')]
        self.terminal(answers, flags=[], auto_wizard=False)
        profile = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertEqual(profile['git_name'], 'Changed Person')
        self.assert_quiet_status()

    def test_carried_noncore_domain_still_requires_human_choice(self):
        path = self.seed_profile(legacy=True, complete=True)
        profile = tomllib.loads(path.read_text())
        profile['domain'] = 'personal'
        path.write_text(toml(profile))
        args = ['bash', str(ROOT / 'install.sh'), '--defaults']
        failed = subprocess.run(args, env=self.env, cwd=self.home, capture_output=True, text=True, start_new_session=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Domain was never explicitly chosen (recorded: personal)', failed.stdout + failed.stderr)
        self.assertFalse((self.home / 'dotfiles').exists())
        self.terminal([(b'Domain [personal]:', b'personal\n'), (b'Git author name [', b'\n'),
                       (b'Git author email [', b'\n')], flags=[])
        recorded = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertTrue(recorded['domain_chosen'])
        self.terminal([], flags=[], auto_wizard=False)

    def test_scripted_flags_select_groups_and_agents_without_prompts(self):
        args = ['bash', str(ROOT / 'install.sh'), '--repo', os.environ['DOTFLEET_CONTENT_FIXTURE'], '--defaults',
                '--domain', 'core', '--git-name', 'Flag Person', '--git-email', 'flag@example.invalid',
                '--group', 'docker=off', '--agents', 'none', '--pin', 'node=24.14.1']
        output = command(*args, env=self.env, cwd=self.home)
        self.assertNotIn('Review [', output)
        profile = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertFalse(profile['groups']['docker'])
        self.assertFalse(any(profile['agents'].values()))
        self.assertNotIn('dotfleet:group docker', (self.home / '.config/dotfiles/aliases/core.zsh').read_text())
        self.assert_quiet_status()

    def test_review_abort_with_missing_tools_keeps_home_unchanged(self):
        self.defaults_origin()
        self.env['PATH'] = '/usr/bin:/bin'
        (self.home / '.zshrc').write_text('# preserve before tool downloads\n')
        before = self.snapshot()
        answers = self.answers()
        answers[-1] = (b'Review [quit]:', b'quit\n')
        output = self.terminal(answers, flags=[], auto_wizard=False, timeout=120)
        self.assertIn('Preparing chezmoi', output)
        self.assertIn('Preparing Mise', output)
        self.assertEqual(self.snapshot(), before)

    def test_agent_selection_generates_reviewed_settings_and_skill_links(self):
        bin_dir = self.home / '.local/bin'
        bin_dir.mkdir(parents=True)
        executable = bin_dir / 'opencode'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
        self.env['PATH'] = str(bin_dir) + ':' + self.env['PATH']
        answers = self.answers('repo', os.environ['DOTFLEET_CONTENT_FIXTURE'])
        answers[-2] = (b'Agents [none]:', b'opencode\n')
        output = self.terminal(answers, flags=[], auto_wizard=False)
        self.assertIn('.config/opencode/opencode.json', output)
        self.assertEqual(json.loads((self.home / '.config/opencode/opencode.json').read_text()), {})
        self.assertTrue((self.home / '.config/opencode/skills/review-change').is_symlink())
        backup = next((self.home / 'dotfleet-backups').iterdir())
        inventory = json.loads((backup / 'MANIFEST.json').read_text())
        self.assertIn('.config/opencode/opencode.json', {item['path'] for item in inventory})
        self.assert_quiet_status()
        self.terminal([], flags=[], auto_wizard=False)

    def test_unknown_format_is_asked_and_recorded(self):
        repo = repository(self.base / 'unknown', {'README.md': 'No home files yet.'})
        answers = [(b'Content door [', b'repo\n'), (b'Content repository [', (str(repo) + '\n').encode()),
                   (b'Format [plain]:', b'plain\n'), (b'Domain [core]:', b'\n'),
                   (b'Git author name [', b'Wizard Person\n'), (b'Git author email [', b'wizard@example.invalid\n'),
                   (b'Pins [accept]:', b'\n'), (b'Agents [none]:', b'none\n'), (b'Review [quit]:', b'confirm\n')]
        self.terminal(answers, flags=[], auto_wizard=False)
        profile = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertEqual(profile['format'], 'plain')
        self.terminal([], flags=[], auto_wizard=False)
        self.assert_quiet_status()

    def test_adopt_existing_enrollment_preserves_previous_checkout(self):
        self.defaults_origin()
        self.terminal(self.answers(), flags=[], auto_wizard=False)
        previous_commit = command('git', '-C', str(self.home / 'dotfiles'), 'rev-parse', 'HEAD').strip()
        before = (self.home / '.zshrc').read_bytes()
        answers = self.answers('adopt')
        answers[1] = (b'Domain [core]:', b'\n')
        self.terminal(answers, flags=['--configure'], auto_wizard=False)
        self.assertEqual((self.home / '.zshrc').read_bytes(), before)
        old = list((self.home / 'dotfleet-backups').glob('*/previous-content'))
        self.assertEqual(len(old), 1)
        self.assertEqual(command('git', '-C', str(old[0]), 'rev-parse', 'HEAD').strip(), previous_commit)
        self.assert_quiet_status()

    def test_secret_scan_checks_names_assignments_and_links(self):
        for name, data in {'.env': b'harmless\n', '.config-one': b'OPENAI_API_KEY=FAKEKEY123456\n',
                           '.config-two': b'password: not-a-real-password\n', '.config-three': b'-----BEGIN OPENSSH PRIVATE KEY-----\n'}.items():
            (self.home / name).write_bytes(data)
        (self.home / '.linked').symlink_to('.config-one')
        found, excluded = scan(self.home)
        self.assertEqual(set(found), set(excluded))


if __name__ == '__main__':
    unittest.main()
