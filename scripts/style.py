"""Mirror logger.sh for Python commands and terminal prompts.

Usage: import section, paint, LoggerStream, or ArgumentParser from this module.
Color reinforces text. Redirected output, NO_COLOR, and TERM=dumb disable color.
"""

import argparse
from contextlib import contextmanager
import datetime
import os
import re
import sys
import textwrap
import traceback

RULE = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
COLORS = {
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
}
BOLD = "\033[1m"
CLEAR = "\033[0m"
ACTIVE_RECORD = None


def paint(color, text, stream=None, bold=False):
    stream = stream or sys.stdout
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb" or not stream.isatty():
        return text
    prefix = COLORS[color] + (BOLD if bold else "")
    return "\n".join(prefix + line + CLEAR for line in text.split("\n"))


def section(title):
    lines = textwrap.wrap(title, 98, break_long_words=False, break_on_hyphens=False)
    return "\n".join([RULE, *("  " + line for line in lines), RULE])


def line_palette(line, diff=False):
    if diff and line.startswith(("+", "-")):
        return "GREEN" if line.startswith("+") else "RED"
    if re.match(r"^  [+−~](?: |$)", line):
        return {"+": "GREEN", "−": "RED", "~": "YELLOW"}[line[2]]
    if re.search(r"\bFAIL\b|^Error:|^ERROR:", line):
        return "RED"
    if re.search(r"\bUNCHOSEN\b|^Warning:|^Choose ", line):
        return "YELLOW"
    if re.search(r"\bPASS\b|^chezmoi status: clean", line):
        return "GREEN"
    return "BLUE"


class ArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        arguments = list(sys.argv[1:] if args is None else args)
        selected = self
        for word in arguments:
            children = next(
                (
                    action.choices
                    for action in selected._actions
                    if isinstance(action, argparse._SubParsersAction)
                ),
                {},
            )
            if word in children:
                selected = children[word]
        self.failure_parser = selected
        return super().parse_args(arguments, namespace)

    def error(self, message):
        selected = getattr(self, "failure_parser", self)
        options = sorted(
            {
                name
                for action in selected._actions
                for name in action.option_strings
                if name.startswith("--") and action.help is not argparse.SUPPRESS
            }
        )
        subjects = [
            action.metavar or action.dest.upper()
            for action in selected._actions
            if not action.option_strings and action.help is not argparse.SUPPRESS
        ]
        accepted = ", ".join([*subjects, *options]) or "no arguments"
        if message.startswith("unrecognized arguments: "):
            problem = "Does not accept " + message.removeprefix("unrecognized arguments: ")
        elif message.startswith("the following arguments are required: "):
            problem = "Supply " + message.removeprefix("the following arguments are required: ")
        elif "invalid choice:" in message:
            value = message.split("invalid choice:", 1)[1].split(" (choose from", 1)[0].strip()
            problem = "Unknown choice " + value
        elif "expected one argument" in message:
            problem = "Give a value for " + message.removeprefix("argument ").split(":", 1)[0]
        else:
            problem = message.removeprefix("argument ").rstrip(".")
        text = section(selected.prog) + "\n" + selected.format_usage()
        text += (
            "\n".join(
                textwrap.wrap(
                    f"Error: {problem}; accepts {accepted}.",
                    100,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
            + "\n"
        )
        self._print_message(text, sys.stderr)
        self.exit(2)

    @contextmanager
    def sorted_help(self):
        def key(action):
            names = [name for name in action.option_strings if name.startswith("--")]
            return (
                not action.option_strings,
                (names[0] if names else action.dest).lstrip("-").casefold(),
            )

        lists = [self._actions, *[group._group_actions for group in self._action_groups]]
        lists += [
            action._choices_actions
            for action in self._actions
            if hasattr(action, "_choices_actions")
        ]
        original = [(actions, actions[:]) for actions in lists]
        try:
            for actions, _ in original:
                actions.sort(key=key)
            yield
        finally:
            for actions, saved in original:
                actions[:] = saved

    def format_help(self):
        with self.sorted_help():
            return section(self.prog) + "\n" + super().format_help()

    def format_usage(self):
        with self.sorted_help():
            return super().format_usage()


class LoggerStream:
    """Apply the logger palette without starting a shell for every output line."""

    def __init__(self, root=None, error=False):
        self.error = error
        self.stream = sys.__stderr__ if error else sys.__stdout__
        self.pending = ""
        self.diff = False
        self.in_section = False

    def isatty(self):
        return self.stream.isatty()

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.emit(line)
        return len(text)

    def flush(self):
        if self.pending:
            self.emit(self.pending)
            self.pending = ""
        self.stream.flush()

    def emit(self, line):
        if re.match(r"^\d+\. [A-Z]", line):
            self.stream.write(paint("CYAN", section(line), self.stream, bold=True) + "\n")
            self.stream.flush()
            return
        color, bold = ("RED" if self.error else "BLUE"), False
        if line == RULE:
            self.in_section = not self.in_section
            color, bold = "CYAN", True
        elif self.in_section or re.match(r"^\d+ · ", line):
            color, bold = "CYAN", True
        else:
            selected = line_palette(line, self.diff)
            color = "RED" if self.error and selected == "BLUE" else selected
        prefix = (
            datetime.datetime.now().strftime("[%H:%M:%S] ")
            if os.environ.get("LOG_TIMESTAMP") == "1"
            else ""
        )
        self.stream.write(paint(color, prefix + line, self.stream, bold) + "\n")
        self.stream.flush()


def record_failure():
    if ACTIVE_RECORD is not None and ACTIVE_RECORD.backup:
        ACTIVE_RECORD.debug_details = traceback.format_exc()
        ACTIVE_RECORD.result = "FAILED"
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
        print("Error: Interrupted. Check the last run before retrying.", file=sys.stderr)
        return 130
    except Exception as error:
        record_failure()
        print("Error: " + str(error), file=sys.stderr)
        return 1


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    from style import ArgumentParser

    argument_parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    argument_parser.parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from pathlib import Path
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
