"""Exercise the installed command, including terminal confirmations and saved records."""
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import shutil
import subprocess
import termios
import time
import tomllib
import unittest

import test_manager as helpers
from test_manager import ROOT, command, repository
from common import toml


class CommandTests(unittest.TestCase):
    setUp = helpers.EnrollmentTests.setUp
    assert_quiet_status = helpers.EnrollmentTests.assert_quiet_status

    def enroll(self):
        self.origin = self.base / 'manager-origin'
        command('git', 'clone', '--no-hardlinks', str(ROOT), str(self.origin))
        for name in command('git', '-C', str(ROOT), 'ls-files', '-z').split('\0'):
            if name and (ROOT / name).is_file():
                target = self.origin / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, target)
        command('git', '-C', str(self.origin), 'add', '.')
        command('git', '-C', str(self.origin), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                'commit', '--allow-empty', '-m', 'feat: command fixture')
        command('git', '-C', str(self.origin), 'remote', 'set-url', 'origin', str(self.origin))
        self.content = repository(self.base / 'content', {
            'dotfleet.toml': 'format="plain"\n[groups.extra]\ndescription="Extra file"\nfiles=[".extra"]\n[domains]\ncore="Base only"\npersonal="Personal shortcut"\n',
            '.file': 'original\n', '.extra': 'group\n',
            '.zshrc': 'source "$HOME/.aliases"\n', '.bashrc': 'source "$HOME/.aliases"\n',
            '.aliases': 'domain=$(cat "$HOME/.config/dotfiles/domain"); source "$HOME/.config/dotfiles/aliases/$domain.zsh"\n',
            '.config/dotfiles/aliases/core.zsh': 'alias chosen="echo core"\n',
            '.config/dotfiles/aliases/personal.zsh': 'alias chosen="echo personal"\n',
        })
        (self.home / '.file').write_text('before enrollment\n')
        output = command('bash', str(self.origin / 'install.sh'), '--repo', str(self.content), '--defaults',
                         '--domain', 'core', '--git-name', 'Command Example', '--git-email', 'command@example.invalid', env=self.env)
        self.exe = self.home / '.local/bin/dotfleet'
        self.assertTrue(self.exe.is_file())
        self.assertIn('dotfleet', output)
        self.env['PATH'] = str(self.home / '.local/bin') + ':' + self.env['PATH']
        return output

    def cli(self, *args, expected=0):
        result = subprocess.run([str(self.exe), *args], env=self.env, cwd=self.home, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def record(self):
        paths = sorted((self.home / 'dotfleet-backups').glob('*/RUN.json'))
        return json.loads(paths[-1].read_text()), paths[-1].parent

    def terminal(self, args, answers):
        master, slave = pty.openpty()
        def setup():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        process = subprocess.Popen([str(self.exe), *args], env=self.env, cwd=self.home,
                                   stdin=subprocess.PIPE, stdout=slave, stderr=slave, preexec_fn=setup)
        os.close(slave)
        process.stdin.close()
        data, cursor, sent = b'', 0, 0
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    data += chunk
                if sent < len(answers) and answers[sent][0] in re.sub(rb'\x1b\[[0-9;]*m', b'', data[cursor:]):
                    os.write(master, answers[sent][1])
                    sent += 1
                    cursor = len(data)
                if process.poll() is not None:
                    break
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        finally:
            os.close(master)
        text = re.sub(r'\x1b\[[0-9;]*m', '', data.decode()).replace('\r\n', '\n')
        self.assertEqual(process.returncode, 0, text)
        self.assertEqual(sent, len(answers), text)
        return text

    def test_all_help_and_invalid_arguments(self):
        from command import DESCRIPTIONS
        for name in [None, *DESCRIPTIONS]:
            args = [name, '--help'] if name else ['--help']
            output = command('bash', str(ROOT / 'scripts/dotfleet.sh'), *args, env=self.env)
            self.assertIn('help', output)
            self.assertIn('exit codes' if name else 'Exit codes', output)
            if name:
                self.assertIn(DESCRIPTIONS[name].split('.')[0].split()[0], output)
        result = subprocess.run(['bash', str(ROOT / 'scripts/dotfleet.sh'), '--unknown'], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_update_status_diff_and_version(self):
        self.enroll()
        # A real manager change is fetched from a local remote and reported by commit.
        (self.origin / 'README.md').write_text((self.origin / 'README.md').read_text() + '\nFixture update.\n')
        command('git', '-C', str(self.origin), 'add', '.')
        command('git', '-C', str(self.origin), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'docs: fixture update')
        before = (self.home / '.file').read_bytes()
        preview = self.cli('diff')
        self.assertIn('will update', preview)
        self.assertEqual((self.home / '.file').read_bytes(), before)
        output = self.cli('update')
        self.assertIn('2 · Manager and content — 1 updated.', output)
        self.assertTrue(self.record()[0]['completed_checkouts'])
        self.assert_quiet_status()
        status = self.cli('status')
        self.assertIn('core · chosen', status)
        self.assertIn('0 behind last fetched upstream', status)
        Path('/tmp/dotfleet-command-status.txt').write_text(status)
        self.assertIn('Content commit', self.cli('version'))
        self.assertIn('needs a terminal', self.cli('configure', expected=1))
        self.assertIn('no changes.', self.cli('update'))

    def test_domain_and_groups_use_saved_profile(self):
        self.enroll()
        self.assertIn('Personal shortcut', self.cli('domain'))
        self.cli('domain', 'personal')
        p = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertEqual(p['domain'], 'personal')
        self.assertTrue(p['domain_chosen'])
        output = command('zsh', '-ic', 'chosen', env=self.env, cwd=self.home)
        self.assertIn('personal', output)
        self.cli('groups', 'extra', 'off')
        self.assertFalse((self.home / '.extra').exists())
        self.assertIn('− ~/.extra', self.record_text())
        self.cli('groups', 'extra', 'on')
        self.assertEqual((self.home / '.extra').read_text(), 'group\n')
        self.assert_quiet_status()

    def record_text(self):
        from render_run import render
        return render(self.record()[0])

    def test_backup_rollback_and_confirmation(self):
        self.enroll()
        self.cli('backup')
        _, saved = self.record()
        original_profile = (self.home / '.config/dotfleet/profile.toml').read_bytes()
        (self.home / '.file').write_text('local edit\n')
        output = self.cli('rollback', saved.name, expected=1)
        self.assertIn('needs a terminal', output)
        self.assertEqual((self.home / '.file').read_text(), 'local edit\n')
        output = self.terminal(['rollback', saved.name], [(b'Confirm [cancel]:', b'restore\n')])
        self.assertIn('~ ~/.file', output)
        self.assertEqual((self.home / '.file').read_text(), 'original\n')
        self.assertEqual((self.home / '.config/dotfleet/profile.toml').read_bytes(), original_profile)
        self.assert_quiet_status()

    def test_doctor_names_all_gaps(self):
        self.enroll()
        self.assertNotIn('FAIL', self.cli('doctor'))
        path = self.home / '.config/dotfleet/profile.toml'
        p = tomllib.loads(path.read_text())
        p.update(domain_chosen=False, git_name='', git_email='', pins={'node': 'latest'})
        path.write_text(toml(p))
        output = self.cli('doctor', expected=1)
        for text in ['never explicitly chosen', 'Git name is empty', 'Git email is empty', 'node pin is floating']:
            self.assertIn(text, output)

    def test_explicit_pin_installs_exact_version_and_remove_blocks_defaults(self):
        self.enroll()
        mise = self.home / '.local/bin/mise'
        mise.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
home=Path.home(); store=home/'.pin-test.json'; args=sys.argv[1:]
items=json.loads(store.read_text()) if store.exists() else {}
if args[0]=='--version': print('2026.8.5')
elif args[0]=='ls': print(json.dumps(items))
elif args[0]=='install':
    tool,version=args[1].rsplit('@',1)
    path=home/'.local/share/mise/installs'/tool/version
    path.mkdir(parents=True,exist_ok=True)
    items[tool]=[{'version':version,'install_path':str(path)}]
    store.write_text(json.dumps(items))
elif args[0]=='latest':
    tool,version=args[-1].rsplit('@',1)
    if any(x['version']==version for x in items.get(tool,[])): print(version)
    else: sys.exit(1)
else: sys.exit(1)
""")
        mise.chmod(0o700)
        output = self.cli('pins', 'set', 'node@24.14.1')
        self.assertIn('24.14.1', output)
        record, _ = self.record()
        self.assertIn('node', record['completed_software'])
        p = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertEqual(p['pins']['node'], '24.14.1')
        self.assertIn('installed: 24.14.1', self.cli('pins'))
        self.cli('pins', 'remove', 'node')
        p = tomllib.loads((self.home / '.config/dotfleet/profile.toml').read_text())
        self.assertNotIn('node', p['pins'])
        self.assertIn('node', p['unpinned_tools'])
        self.assertNotIn('node', tomllib.loads((self.home / '.config/mise/config.toml').read_text())['tools'])
        self.assertTrue((self.home / '.local/share/mise/installs/node/24.14.1').exists())
        self.cli('pins', 'set', 'node@latest', expected=2)
        self.assert_quiet_status()

    def test_rollback_rejects_changed_backup_and_unsafe_paths(self):
        self.enroll()
        self.cli('backup')
        _, saved = self.record()
        (saved / 'home/.file').write_text('tampered\n')
        output = self.cli('rollback', saved.name, expected=1)
        self.assertIn('failed its recorded fingerprint', output)
        self.assertEqual((self.home / '.file').read_text(), 'original\n')
        self.assertIn('directly inside', self.cli('rollback', '/tmp', expected=1))


    def test_uninstall_keeps_content_and_backups_and_can_restore_oldest(self):
        self.enroll()
        self.cli('uninstall', expected=1)
        cancelled = self.terminal(['uninstall'], [(b'Oldest backup [keep]:', b'keep\n'), (b'Confirm [cancel]:', b'cancel\n')])
        self.assertIn('cancelled', cancelled)
        self.assertTrue(self.exe.exists())
        output = self.terminal(['uninstall'], [(b'Oldest backup [keep]:', b'restore\n'), (b'Confirm [cancel]:', b'uninstall\n')])
        self.assertIn('− ~/.local/bin/dotfleet', output)
        self.assertFalse(self.exe.exists())
        self.assertFalse((self.home / '.local/share/dotfleet/manager').exists())
        self.assertTrue((self.home / 'dotfiles/.git').exists())
        self.assertTrue((self.home / 'dotfleet-backups').exists())
        self.assertEqual((self.home / '.file').read_text(), 'before enrollment\n')


if __name__ == '__main__':
    unittest.main()
