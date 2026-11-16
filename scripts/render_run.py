"""Render a recorded run for people. No file reads, writes, or terminal escapes.

The installer supplies the same record that it saves as RUN.json.
Use future=True for the wizard's planned actions. See install.sh --help.
"""
import re
import textwrap

UPDATE = 'curl -fsSL https://raw.githubusercontent.com/jeremyspofford/dotfleet/main/install.sh | bash'
WIZARD = UPDATE + ' -s -- --configure'
WIDTH = 100


def human(value, home=''):
    if value is None:
        return 'not set'
    if isinstance(value, bool):
        return 'on' if value else 'off'
    if isinstance(value, dict):
        return '; '.join(f'{key}: {human(item, home)}' for key, item in value.items()) or 'none'
    if isinstance(value, list):
        return ', '.join(human(item, home) for item in value) or 'none'
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    if home:
        text = re.sub(r'(?<![\w/])' + re.escape(home.rstrip('/')) + r'(?=/|$)', '~', text)
    return text[:8] if re.fullmatch(r'[0-9a-f]{40}', text) else text


def wrap(text, indent='', continuation='    '):
    # A URL may exceed the width. An indivisible non-URL value stays in the record.
    words = text.split()
    words = [word if len(word) <= WIDTH - len(continuation) or '://' in word else '[long value in RUN.json]'
             for word in words]
    return textwrap.wrap(' '.join(words), WIDTH, initial_indent=indent, subsequent_indent=continuation,
                         break_long_words=False, break_on_hyphens=False) or [indent.rstrip()]


def block(pairs, home):
    width = min(max((len(key) for key, _ in pairs), default=0), 28)
    lines = []
    for key, value in pairs:
        text = human(value, home)
        prefix = '  ' + key.ljust(width) + '  '
        if len(prefix + text) <= WIDTH:
            lines.append(prefix + text)
        else:
            lines.extend(wrap(key, '  '))
            # Shell pipelines can wrap after | without changing the copyable command.
            if text in (UPDATE, WIZARD):
                left, right = text.split(' | ', 1)
                lines += ['    ' + left + ' |', '    ' + right]
            else:
                lines.extend(wrap(text, '    '))
    return lines


def profile_key(name):
    labels = {'git_name': 'Git name', 'git_email': 'Git email', 'domain': 'Domain',
              'domain_chosen': 'Domain confirmed', 'content_repo': 'Content repo', 'format': 'Content format'}
    for prefix, label in [('pins.', 'Pin'), ('groups.', 'Group'), ('agents.', 'Agent')]:
        if name.startswith(prefix):
            return label + ' ' + name[len(prefix):]
    return labels.get(name, name.replace('_', ' '))


def render(record, future=False):
    """Return the complete plain-text view without changing the input record."""
    home = record.get('home', '')
    sections = []
    software = record.get('software', [])
    changed = [item for item in software if item['action'] != 'present'
               and (future or item['name'] in record.get('completed_software', []))]
    counts = {kind: sum(item['action'] == kind for item in changed) for kind in ('installed', 'upgraded')}
    title = ', '.join(f'{count} {kind}' for kind, count in counts.items() if count)
    lines = ['1 · Software — ' + (('will be ' if future else '') + title + '.' if changed else 'no changes.')]
    for item in changed:
        label = item['name']
        value = (item['old'] + ' → ' if item.get('old') else '') + item['version'] + ' · ' + item['path']
        lines += block([(label, value)], home)
    present = [item['name'] + ' ' + item['version'] for item in software if item['action'] == 'present']
    if present:
        lines += wrap('Already present: ' + ', '.join(present), '  ')
    if future and record.get('available_defaults'):
        lines += block([('Available, not installed', human(record['available_defaults'], home))], home)
    sections.append(lines)

    changed = [item for item in record.get('checkouts', []) if item.get('new') and item.get('old') != item['new']
               and (future or item['name'] in record.get('completed_checkouts', []))]
    lines = ['2 · Manager and content — ' + (f'{len(changed)} ' + ('will update.' if future else 'updated.') if changed else 'no changes.')]
    lines += block([(item['name'], f"{human(item.get('old'), home)} → {human(item['new'], home)} · {item['path']}") for item in changed], home)
    sections.append(lines)

    files = [item for item in record.get('files', []) if future or item['path'] in record.get('completed_files', [])]
    counts = {kind: sum(item['action'] == kind for item in files) for kind in ('added', 'changed', 'removed')}
    title = ', '.join(f'{count} {kind}' for kind, count in counts.items() if count)
    lines = ['3 · Files — ' + (('will be ' if future else '') + title + '.' if files else 'no changes.')]
    markers = {'added': '+', 'changed': '~', 'removed': '−'}
    for kind in markers:
        for item in files:
            if item['action'] == kind:
                text = human(item['path'], home)
                if len(text) + 4 <= WIDTH:
                    lines.append('  ' + markers[kind] + ' ' + text)
                else:
                    lines.append('  ' + markers[kind])
                    lines += wrap(text, '    ')
    sections.append(lines)

    changes = record.get('profile_changes', []) if future or record.get('profile_written') else []
    lines = ['4 · Profile changes — ' + (f'{len(changes)} ' + ('will change.' if future else 'changed.') if changes else 'no changes.')]
    lines += block([(profile_key(item['name']), human(item['old'], home) + ' → ' + human(item['new'], home)) for item in changes], home)
    if future and record.get('selected_profile'):
        lines += ['  Selected choices:']
        lines += block([(profile_key(key), value) for key, value in record['selected_profile'].items()], home)
    sections.append(lines)

    locations = record.get('locations', {})
    backup = locations.get('Backup folder') if future or record.get('backup_created') else None
    lines = ['5 · Locations'] + block([
        ('Content', locations.get('Content checkout')), ('Profile', locations.get('Profile file')),
        ('Source', locations.get('Converted source')), ('Backup', backup or 'not created')], home)
    sections.append(lines)
    sections.append(['6 · Next steps'] + block([
        ('Update', UPDATE), ('Rollback', str(backup) + '/MANIFEST.json' if backup else 'not needed; nothing applied'),
        ('Wizard', WIZARD)], home))

    lines = []
    for section in sections:
        if lines:
            lines.append('')
        lines.extend(section)
    warnings = record.get('warnings', [])
    if warnings:
        lines += ['', 'Warnings:']
        for warning in warnings:
            lines += wrap(human(warning, home), '  ')
    exclusions = record.get('adoption_excluded', [])
    if exclusions:
        lines += ['', 'Excluded from adoption:']
        for path in exclusions:
            lines += wrap(human(path, home), '  ')
    status = 'not applied (review)' if future else record.get('chezmoi_status_after') or (record.get('result') or 'not checked').lower()
    if status == 'CLEAN (no differences)':
        status = 'clean'
    lines += ['', f"chezmoi status: {status} · warnings: {len(warnings) if warnings else 'none'}"]
    record_path = str(backup) + '/RUN.json' if backup else None
    lines.append('Run record: ' + human(record_path, home) if record_path else 'Run record: not created; nothing applied.')
    return '\n'.join(lines) + '\n'
