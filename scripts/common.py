"""Shared profile and path helpers. No command-line interface."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib


def run(*args, cwd=None, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def read_toml(path):
    return tomllib.loads(Path(path).read_text()) if Path(path).exists() else {}


def value(item):
    if isinstance(item, str):
        return json.dumps(item, ensure_ascii=True)
    if isinstance(item, bool):
        return str(item).lower()
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, list):
        return '[' + ', '.join(value(x) for x in item) + ']'
    if isinstance(item, dict):
        return '{' + ', '.join(value(k) + ' = ' + value(v) for k, v in item.items()) + '}'
    raise ValueError(f"Unsupported profile value: {type(item).__name__}")


def toml(data):
    # Inline tables preserve tool option tables and version lists without a dependency.
    return ''.join(value(k) + ' = ' + value(v) + '\n' for k, v in data.items())


def atomic(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.dotfleet-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative(name):
    path = Path(name)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError(f"Unsafe content path: {name}")
    return path


class LoggerStream:
    """Send whole lines through the owner's logger before displaying the next prompt."""
    def __init__(self, root, error=False):
        self.root = root
        self.error = error
        self.pending = ''
        self.in_warnings = False

    def write(self, text):
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self.emit(line)
        return len(text)

    def flush(self):
        if self.pending:
            self.emit(self.pending)
            self.pending = ''

    def emit(self, line):
        import sys
        level = 'log_error' if self.error else 'log_info'
        arguments = [line]
        marker = line.lstrip()[:1]
        if line.startswith('  ') and marker in {'+', '−', '~'}:
            level = 'log_change'
            arguments = [{'+': 'added', '−': 'removed', '~': 'changed'}[marker], line]
        elif line.startswith('chezmoi status: clean'):
            level = 'log_success'
        elif line.startswith('Warning:'):
            level = 'log_warn'
        subprocess.run(['bash', '-c', 'source "$1"; shift; "$@"', 'dotfleet-log',
                        str(self.root / 'scripts/logger.sh'), level, *arguments], check=True,
                       stdout=sys.__stdout__, stderr=sys.__stderr__)
