"""Record planned actions and confirmed outcomes for one installer run.

This internal module has no command-line interface. Use install.sh --help.
Diff contents stay in the private backup. Reports contain paths and profile choices only.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from common import atomic, read_toml, run
from render_run import render
from installation import command_software, MANAGER

RECORD = '.local/state/dotfleet/last-run.json'


def commit(path):
    if not (path / '.git').exists():
        return None
    return run('git', '-C', str(path), 'rev-parse', 'HEAD').strip()


def version(path):
    result = subprocess.run([str(path), '--version'], text=True, capture_output=True)
    if result.returncode:
        raise ValueError(f'Cannot read software version: {path}: {result.stderr.strip()}')
    match = re.search(r'\d+\.\d+\.\d+[^\s,]*', result.stdout)
    return match.group() if match else result.stdout.splitlines()[0].strip()


def profile_changes(old, new):
    changes = []
    for field in ('domain', 'domain_chosen', 'git_name', 'git_email', 'content_repo', 'format'):
        if old.get(field) != new.get(field):
            changes.append({'name': field, 'old': old.get(field), 'new': new.get(field)})
    for field in ('pins', 'groups', 'agents'):
        for key in sorted(set(old.get(field, {})) | set(new.get(field, {}))):
            before, after = old.get(field, {}).get(key), new.get(field, {}).get(key)
            if before != after:
                changes.append({'name': f'{field}.{key}', 'old': before, 'new': after})
    for tool in sorted(set(old.get('unpinned_tools', [])) ^ set(new.get('unpinned_tools', []))):
        changes.append({'name': 'defaults.' + tool, 'old': tool not in old.get('unpinned_tools', []),
                        'new': tool not in new.get('unpinned_tools', [])})
    return changes


class RunAccount:
    def __init__(self, home, root):
        self.home, self.root = home, root
        saved = home / RECORD
        previous = json.loads(saved.read_text()) if saved.exists() else {}
        self.old_profile = read_toml(home / '.config/dotfleet/profile.toml')
        self.software = []
        for name in ('chezmoi', 'mise'):
            staged = os.environ.get('DOTFLEET_STAGED_' + name.upper())
            executable = staged or shutil.which(name)
            if not executable:
                continue
            destination = home / '.local/bin' / name if staged else Path(executable)
            old = version(destination) if staged and destination.exists() else None
            self.software.append({'name': 'Mise' if name == 'mise' else name, 'version': version(executable),
                                  'path': str(destination), 'old': old,
                                  'action': 'upgraded' if old else 'installed' if staged else 'present'})
        self.software.append(command_software(home, root))
        self.checkouts = [
            {'name': 'Manager', 'path': str(home / MANAGER), 'old': previous.get('manager_commit'), 'new': commit(root)},
            {'name': 'Content', 'path': str(home / 'dotfiles'), 'old': commit(home / 'dotfiles'), 'new': None}]
        self.locations = {'Content checkout': str(home / 'dotfiles'),
                          'Profile file': str(home / '.config/dotfleet/profile.toml'),
                          'Converted source': 'None (not prepared)', 'Backup folder': 'None (not created)'}
        self.files = []
        self.profile = []
        self.applied_files = set()
        self.applied_software = set()
        self.applied_checkouts = set()
        self.profile_written = False
        self.manifest = []
        self.diff_path = None
        self.status = ''
        self.backup = None
        self.result = None
        self.warnings = []
        self.status_after = None
        self.selected_profile = {}
        self.available_defaults = {}
        self.adoption_excluded = []

    def plan(self, plan, checkout, profile, backup_path, manifest):
        self.checkouts[1]['new'] = commit(checkout)
        self.locations['Converted source'] = str(plan['config']['sourceDir'])
        self.locations['Backup folder'] = str(backup_path)
        self.profile = profile_changes(self.old_profile, profile)
        self.selected_profile = {key: profile.get(key) for key in ['content_repo', 'domain', 'git_name', 'git_email', 'pins', 'groups', 'agents']}
        self.available_defaults = plan['available']
        self.adoption_excluded = profile.get('adoption_excluded', [])
        self.manifest = copy.deepcopy(manifest)
        before = {item['path']: item for item in manifest}
        self.files = []
        for name in sorted(plan['changed']):
            if name.startswith('.local/state/dotfleet/') or any(str(self.home / name) == item['path'] and item['action'] != 'present' for item in self.software):
                continue  # Bookkeeping and software have their own records, not home-setting diffs.
            entry = before[name]
            action = 'removed' if name in plan['stale'] or name in plan['removed'] else 'added' if entry['state'] == 'absent' else 'changed'
            self.files.append({'path': str(self.home / name), 'action': action,
                               'before': entry.get('fingerprint'), 'relative': name})
        self.diff_path = plan['diff_path']
        self.status = plan['status']

    def bind_backup(self, path):
        self.backup = path
        recorded = json.loads((path / 'MANIFEST.json').read_text())
        if recorded != self.manifest:
            raise ValueError('Backup records differ from the reviewed plan. Nothing applied; run setup again.')
        atomic(path / 'CHEZMOI-DIFF.patch', self.diff_path.read_bytes())
        self.save()

    def record(self):
        return {
            'home': str(self.home), 'software': self.software, 'checkouts': self.checkouts, 'files': self.files,
            'profile_changes': self.profile, 'locations': self.locations, 'backup_manifest': self.manifest,
            'chezmoi_status_before': self.status, 'completed_files': sorted(self.applied_files),
            'completed_software': sorted(self.applied_software), 'completed_checkouts': sorted(self.applied_checkouts),
            'profile_written': self.profile_written, 'backup_created': bool(self.backup),
            'result': self.result, 'warnings': self.warnings, 'chezmoi_status_after': self.status_after,
            'selected_profile': self.selected_profile, 'available_defaults': self.available_defaults,
            'adoption_excluded': self.adoption_excluded}

    def save(self):
        if self.backup:
            atomic(self.backup / 'RUN.json', json.dumps(self.record(), indent=2) + '\n')

    def confirm_files(self, fingerprint):
        # Verify recorded operations, including partial apply. Never infer new paths or categories here.
        for item in self.files:
            current = fingerprint(Path(item['path']))
            if current != item['before'] and (item['action'] != 'removed' or current is None):
                self.applied_files.add(item['path'])
        self.save()

    def lines(self, future=False):
        return render(self.record(), future=future).rstrip().splitlines()

    def finish(self, result, warnings=None, status=None):
        self.result = result
        self.warnings = warnings or []
        self.status_after = status
        self.save()
        print(render(self.record()), end='')
        marker = os.environ.get('DOTFLEET_SUMMARY_MARKER')
        if marker:
            Path(marker).touch()
