"""Golden human views of immutable run records, plus terminal color checks."""
import copy
import json
import os
from pathlib import Path
import pty
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from render_run import render

FIXTURES = ROOT / 'tests/fixtures/run-results'


class RenderingTests(unittest.TestCase):
    def record(self, name='changed'):
        return json.loads((FIXTURES / (name + '.json')).read_text())

    def assert_plain_view(self, text):
        self.assertNotIn('/home/example', text)
        self.assertNotRegex(text, r'\b[0-9a-f]{40}\b')
        self.assertNotRegex(text, r'[{}]|\[\s*"|"\w+"\s*:|^\s*\[\w+\]\s*$')
        self.assertNotRegex(text, r'(?m)^\s*[\w.]+\s*=')
        self.assertNotIn('\x1b[', text)
        for line in text.splitlines():
            if len(line) > 100:
                self.assertRegex(line, r'https?://\S{95,}', line)
        self.assertTrue(text.splitlines()[-2].startswith('chezmoi status:'))
        self.assertTrue(text.splitlines()[-1].startswith('Run record:'))

    def test_three_golden_records(self):
        for name in ['no-change', 'changed', 'manager-upgrade']:
            with self.subTest(name=name):
                record = self.record(name)
                original = copy.deepcopy(record)
                text = render(record)
                self.assertEqual(text, (FIXTURES / (name + '.txt')).read_text())
                self.assertEqual(record, original)
                self.assert_plain_view(text)

    def test_future_view_uses_same_record_and_categories(self):
        record = self.record()
        record['completed_files'] = []
        record['profile_written'] = False
        past, future = render(record), render(record, future=True)
        self.assertIn('3 · Files — no changes.', past)
        self.assertIn('3 · Files — will be 1 added, 1 changed, 1 removed.', future)
        self.assertIn('4 · Profile changes — 1 will change.', future)
        self.assertIn('  − ~/.config/docker/aliases.zsh', future)
        self.assert_plain_view(future)

    def test_long_values_wrap_without_machine_dumps(self):
        record = self.record()
        record['profile_changes'].append({'name':'git_name','old':None,'new':'A long human name ' * 20})
        record['profile_changes'].append({'name':'pins.python','old':None,'new':{'version':['3.13.0','3.12.0'],'option':True}})
        record['warnings'] = ['A warning that needs space ' * 10]
        text = render(record)
        self.assert_plain_view(text)
        self.assertIn('version: 3.13.0, 3.12.0; option: on', text)
        self.assertRegex(text, r'Git name\n    not set →')
        self.assertIn('warnings: 1', text.splitlines()[-2])
        for line in text.splitlines():
            self.assertFalse(line.endswith('hum'))

    def test_summary_color_uses_logger_and_keeps_removals_on_stdout(self):
        script = "import sys; from pathlib import Path; from common import LoggerStream; sys.stdout=LoggerStream(Path(sys.argv[1])); print('  + ~/.new\\n  − ~/.old\\n  ~ ~/.changed')"
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'scripts'), TERM='xterm-256color')
        env.pop('NO_COLOR', None)
        master, slave = pty.openpty()
        try:
            result = subprocess.run([sys.executable, '-c', script, str(ROOT)], env=env, stdout=slave, stderr=subprocess.PIPE)
            os.close(slave)
            data = b''
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(master)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b'')
        for code, marker in [('32','+'),('31','−'),('33','~')]:
            self.assertIn(f'\x1b[{code}m  {marker}'.encode(), data)
        for no_color in ['', '1']:
            plain = subprocess.run([sys.executable, '-c', script, str(ROOT)], env=dict(env, NO_COLOR=no_color), capture_output=True)
            self.assertNotIn(b'\x1b[', plain.stdout)
            self.assertEqual(plain.stderr, b'')
        redirected = subprocess.run([sys.executable, '-c', script, str(ROOT)], env=env, capture_output=True)
        self.assertNotIn(b'\x1b[', redirected.stdout)

    def test_readme_uses_the_changed_golden_verbatim(self):
        example = (FIXTURES / 'changed.txt').read_text()
        self.assertIn('```text\n' + example + '```', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
