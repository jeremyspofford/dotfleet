"""Summarize enrollment candidates without printing private or transient paths."""
import re

SENSITIVE_NAME = re.compile(r'secret|key|token|credential', re.I)
TRANSIENT = re.compile(r'(^|[._/-])(?:logs?|telemetry|caches?|history|histories|sessions?|snapshots?|receipts?)(?:[._/-]|$)|(?:^|/)\.?[a-z]*_history|(?:^|/)\.zcompdump|(?:^|/)\.bash_sessions', re.I)
SHELL = {'.zshrc', '.zprofile', '.zshenv', '.zlogin', '.zlogout', '.bashrc', '.bash_profile',
         '.bash_login', '.bash_logout', '.profile', '.aliases', '.commonrc', '.inputrc'}


def transient(name):
    return bool(TRANSIENT.search(name))


def sensitive(name):
    return bool(SENSITIVE_NAME.search(name))


def category(name):
    if name in SHELL or name.startswith('.config/fish/'):
        return 'Shell'
    if name == '.gitconfig' or name.startswith('.config/git/'):
        return 'Git'
    if name.startswith('.config/') or name in {'.npmrc', '.pypirc', '.tool-versions', '.vimrc', '.tmux.conf'}:
        return 'Tools'
    return None


def lines(record, pins, agents):
    """Keep the inventory in the record. Show at most fifteen relevant file lines."""
    from render_run import human, wrap
    output = ['2. Scan', 'OS: ' + record['os'], 'Existing enrollment: ' + (', '.join(record['enrollment']) or 'none'),
              f"Installed tools: {len(record['tools'])}"]
    selected = ', '.join(f'{name} {human(value)}' for name, value in sorted(pins.items())) or 'none'
    output += wrap('Pinned tools: ' + selected)
    output += wrap('Detected agents: ' + (', '.join(agents) or 'none'))
    candidates = record['candidates']
    output.append(f'Home configuration candidates: {len(candidates)}')
    relevant = sorted(((category(name), name) for name in candidates if category(name)),
                      key=lambda item: (['Shell', 'Git', 'Tools'].index(item[0]), item[1]))
    file_lines = []
    for group, name in relevant:
        file_lines += wrap('~/' + name, f'  {group:5}  ')
    output += file_lines[:15]
    if len(file_lines) > 15:
        output.append(f'  + {len(file_lines) - 15} more')
    if any(sensitive(name) or 'secret' in reason or 'credential' in reason
           for name, reason in record['excluded'].items()):
        output.append('Sensitive files are excluded.')
    return output


def exclusions(excluded):
    """Report exclusions without naming files that could reveal credentials."""
    output = []
    hidden = False
    for name, reason in excluded.items():
        if sensitive(name) or 'secret' in reason or 'credential' in reason:
            hidden = True
        else:
            output.append(f'Excluded from adoption: ~/{name} ({reason})')
    if hidden:
        output.append('Sensitive files are excluded.')
    return output
