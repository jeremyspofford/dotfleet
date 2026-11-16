"""Mirror logger.sh for Python commands and terminal prompts.

Usage: import section, paint, LoggerStream, or ArgumentParser from this module.
Color reinforces text. Redirected output, NO_COLOR, and TERM=dumb disable color.
"""
import argparse
import datetime
import os
import re
import sys
import textwrap
import traceback

RULE = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
COLORS = {'RED': '\033[31m', 'GREEN': '\033[32m', 'YELLOW': '\033[33m',
          'BLUE': '\033[34m', 'MAGENTA': '\033[35m', 'CYAN': '\033[36m'}
BOLD = '\033[1m'
CLEAR = '\033[0m'
ACTIVE_RECORD = None


def paint(color, text, stream=None, bold=False):
    stream = stream or sys.stdout
    if 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb' or not stream.isatty():
        return text
    prefix = COLORS[color] + (BOLD if bold else '')
    return '\n'.join(prefix + line + CLEAR for line in text.split('\n'))


def section(title):
    lines = textwrap.wrap(title, 98, break_long_words=False, break_on_hyphens=False)
    return '\n'.join([RULE, *('  ' + line for line in lines), RULE])


class ArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        return section(self.prog) + '\n' + super().format_help()


class LoggerStream:
    """Apply the logger palette without starting a shell for every output line."""
    def __init__(self, root=None, error=False):
        self.error = error
        self.stream = sys.__stderr__ if error else sys.__stdout__
        self.pending = ''
        self.diff = False
        self.in_section = False

    def isatty(self):
        return self.stream.isatty()

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
        self.stream.flush()

    def emit(self, line):
        if re.match(r'^\d+\. [A-Z]', line):
            self.stream.write(paint('CYAN', section(line), self.stream, bold=True) + '\n')
            self.stream.flush()
            return
        color, bold = ('RED' if self.error else 'BLUE'), False
        if line == RULE:
            self.in_section = not self.in_section
            color, bold = 'CYAN', True
        elif self.in_section or re.match(r'^\d+ · ', line):
            color, bold = 'CYAN', True
        elif self.diff and line.startswith(('+', '-')):
            color = 'GREEN' if line.startswith('+') else 'RED'
        elif re.match(r'^  [+−~](?: |$)', line):
            color = {'+': 'GREEN', '−': 'RED', '~': 'YELLOW'}[line[2]]
        elif re.search(r'\bFAIL\b|^Error:|^ERROR:', line):
            color = 'RED'
        elif re.search(r'\bUNCHOSEN\b|^Warning:|^Choose ', line):
            color = 'YELLOW'
        elif re.search(r'\bPASS\b|^chezmoi status: clean', line):
            color = 'GREEN'
        prefix = datetime.datetime.now().strftime('[%H:%M:%S] ') if os.environ.get('LOG_TIMESTAMP') == '1' else ''
        self.stream.write(paint(color, prefix + line, self.stream, bold) + '\n')
        self.stream.flush()


def record_failure():
    if ACTIVE_RECORD is not None and ACTIVE_RECORD.backup:
        ACTIVE_RECORD.debug_details = traceback.format_exc()
        ACTIVE_RECORD.result = 'FAILED'
        try:
            ACTIVE_RECORD.save()
        except OSError:
            pass  # The original error remains visible if its private record cannot be saved.


def run_cli(function, root):
    sys.stdout = LoggerStream(root)
    sys.stderr = LoggerStream(root, error=True)
    try:
        return function()
    except KeyboardInterrupt:
        print('Error: Interrupted. Check the last run before retrying.', file=sys.stderr)
        return 130
    except Exception as error:
        record_failure()
        print('Error: ' + str(error), file=sys.stderr)
        return 1
