"""Send core reviews to a terminal interface through a private inherited socket.

Usage: import active, send, and review. Plain CLI calls need no channel.
Help: python3 scripts/ui_channel.py --help.
"""

import json
import os
from pathlib import Path
import socket
import sys

CHANNEL = "FLEET_UI_FD"


def descriptors():
    return (int(os.environ[CHANNEL]),) if CHANNEL in os.environ else ()


def active():
    return bool(descriptors())


def send(event, reply=False):
    if not active():
        raise ValueError("No terminal interface channel is attached.")
    with socket.socket(fileno=os.dup(descriptors()[0])) as connection:
        connection.sendall((json.dumps(event) + "\n").encode())
        if reply:
            with connection.makefile("r") as reader:
                line = reader.readline()
            if not line:
                raise ValueError(
                    "The terminal interface closed before confirmation. Nothing confirmed."
                )
            return json.loads(line).get("answer")


def review(record, word="confirm", explanation="Apply the reviewed changes."):
    answer = send(
        {"kind": "review", "record": record, "word": word, "explanation": explanation}, reply=True
    )
    return answer == word


def usage():
    import argparse
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
