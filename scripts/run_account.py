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

UPDATE = 'curl -fsSL https://raw.githubusercontent.com/jeremyspofford/dotfleet/main/install.sh | bash'
CONFIGURE = UPDATE + ' -s -- --configure'
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
    return changes


def display(value):
    return 'None' if value is None else json.dumps(value, ensure_ascii=False)


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
        self.checkouts = [
            {'name': 'Manager', 'path': str(root), 'old': previous.get('manager_commit'), 'new': commit(root)},
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

    def plan(self, plan, checkout, profile, backup_path, manifest):
        self.checkouts[1]['new'] = commit(checkout)
        self.locations['Converted source'] = str(plan['config']['sourceDir'])
        self.locations['Backup folder'] = str(backup_path)
        self.profile = profile_changes(self.old_profile, profile)
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

    def save(self):
        if self.backup:
            atomic(self.backup / 'RUN.json', json.dumps({
                'software': self.software, 'checkouts': self.checkouts, 'files': self.files,
                'profile_changes': self.profile, 'locations': self.locations, 'backup_manifest': self.manifest,
                'chezmoi_status_before': self.status, 'completed_files': sorted(self.applied_files),
                'completed_software': sorted(self.applied_software), 'completed_checkouts': sorted(self.applied_checkouts),
                'profile_written': self.profile_written}, indent=2) + '\n')

    def confirm_files(self, fingerprint):
        # Verify recorded operations, including partial apply. Never infer new paths or categories here.
        for item in self.files:
            current = fingerprint(Path(item['path']))
            if current != item['before'] and (item['action'] != 'removed' or current is None):
                self.applied_files.add(item['path'])
        self.save()

    def lines(self, future=False):
        lines = ['A profile saves machine choices. Pins select tool versions; groups select optional content.',
                 'Commit IDs identify saved Git versions. None means no change or no recorded value.', '']
        lines += ['1. Software']
        changes = [item for item in self.software if item['action'] != 'present'
                   and (future or item['name'] in self.applied_software)]
        if not changes:
            lines.append('Software changes: None')
        for kind, label in [('installed', 'Will install' if future else 'Installed new'),
                            ('present', 'Already present'), ('upgraded', 'Will upgrade' if future else 'Upgraded')]:
            lines.append(label + ':')
            selected = [item for item in self.software if item['action'] == kind
                        and (kind == 'present' or future or item['name'] in self.applied_software)]
            if not selected:
                lines.append('  None')
            for item in selected:
                number = (item['old'] + ' → ' if item['old'] else '') + item['version']
                path = ' — ' + item['path'] if kind != 'present' else ''
                lines.append(f"  {item['name']} {number}{path}")
        lines += ['', '2. Manager and content', 'Checkout changes:']
        changes = [item for item in self.checkouts if item['new'] and item['old'] != item['new']
                   and (future or item['name'] in self.applied_checkouts)]
        if not changes:
            lines.append('  None')
        for item in changes:
            verb = 'Will update' if future else 'Updated'
            if item['old'] is None:
                verb = 'Will record' if future else 'Recorded'
            lines.append(f"  {verb} {item['name']}: {item['old'] or 'None'} → {item['new']} — {item['path']}")
        for item in self.checkouts:
            if item['new'] and item['old'] == item['new']:
                lines.append(f"{item['name']}: unchanged, {item['new']} — {item['path']}")
        lines += ['', '3. Files']
        for action in ('added', 'changed', 'removed'):
            label = ('Will be ' + action) if future else action.capitalize()
            selected = [item['path'] for item in self.files if item['action'] == action and (future or item['path'] in self.applied_files)]
            lines.append(label + ':')
            lines += ['  ' + path for path in selected] or ['  None']
        lines += ['', '4. Profile changes']
        changes = self.profile if future or self.profile_written else []
        if not changes:
            lines.append('  None')
        for item in changes:
            lines.append(f"  {'Will change' if future else 'Changed'} {item['name']}: {display(item['old'])} → {display(item['new'])}")
        lines += ['', '5. Locations']
        for name, location in self.locations.items():
            if name == 'Backup folder' and not future and not self.backup:
                location = 'None (not created)'
            lines.append(f'{name}: {location}')
        lines += ['', '6. Next steps', 'Update: ' + UPDATE,
                  'Rollback: ' + (str(self.backup or self.locations['Backup folder']) + '/MANIFEST.json' if future or self.backup else 'None (no files were applied)'),
                  'Restore saved home/ files and recorded external targets. Remove only new paths marked absent.',
                  'Keep user edits and the backup. Restore the profile before running setup again.',
                  'Reopen the wizard: ' + CONFIGURE]
        return lines

    def finish(self, result, warnings=None, status=None):
        print('END SUMMARY')
        print('Result: ' + result)
        print('\n'.join(self.lines()))
        if status is not None:
            print('chezmoi status: ' + status)
        print('Warnings:')
        for warning in warnings or ['None.']:
            print('  ' + warning)
        if self.backup:
            print('Run record: ' + str(self.backup / 'RUN.json'))
            self.save()
        marker = os.environ.get('DOTFLEET_SUMMARY_MARKER')
        if marker:
            Path(marker).touch()
