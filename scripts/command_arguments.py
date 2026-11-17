"""Define flagged command parameters and retain hidden positional compatibility.

Usage: import add_parameters and normalize from the command dispatcher.
Help: python3 scripts/command_arguments.py --help.
"""

import argparse
from pathlib import Path
import sys

import remote

EXAMPLES = {
    "machines": "machines set-ssh --target mini-pc; machines set-ssh --name mini-pc; machines set-ssh --name beelink --target mini-pc",
    "domain": "domain --name personal",
    "groups": "groups --name docker --state off",
    "pins": "pins set --tool node --version 24.14.1; pins remove --tool node",
    "rollback": "rollback --backup BACKUP",
    "topology": "topology; topology --json",
}


def flagged(parser, name, help_text, *, flag=None, choices=None):
    parser.add_argument("legacy_" + name, nargs="?", help=argparse.SUPPRESS)
    parser.add_argument(flag or "--" + name, dest=name, choices=choices, help=help_text)


def add_parameters(parser, name):
    if name == "machines":
        parser.add_argument(
            "action",
            nargs="?",
            choices=["export", "remove", "set-ssh"],
            help="export shows this record; remove deletes a stale record; set-ssh saves and tests SSH",
        )
        flagged(
            parser,
            "hostname",
            "saved machine name; defaults to --target for set-ssh",
            flag="--name",
        )
        flagged(
            parser,
            "ssh_target",
            "SSH host or user@host; defaults to --name; omit both to pick a host",
            flag="--target",
        )
        parser.add_argument(
            "--output",
            metavar="FILE",
            help="with export: save one private TOML record for transfer; FILE must not exist",
        )
    elif name == "status":
        parser.add_argument(
            "--check",
            action="store_true",
            help="fetch and cache updates first; exit 0 current, 1 updates, 2 check failed",
        )
    elif name == "diff":
        parser.add_argument(
            "--stat",
            action="store_true",
            help="print changed paths only; retain the same exit codes",
        )
    elif name == "rollback":
        flagged(
            parser, "backup", "backup folder name or path; omit to list and choose in a terminal"
        )
        parser.add_argument("machine", nargs="?", help=argparse.SUPPRESS)
    elif name == "domain":
        flagged(parser, "name", "domain from the listed choices; omit to list domains")
    elif name == "groups":
        flagged(parser, "name", "content group from the manifest; omit both flags to list groups")
        flagged(parser, "state", "on includes content; off removes it", choices=["on", "off"])
    elif name == "pins":
        parser.add_argument(
            "action",
            nargs="?",
            choices=["set", "remove"],
            help="set installs an exact version; remove unpins it",
        )
        parser.add_argument("pin", nargs="?", help=argparse.SUPPRESS)
        parser.add_argument("--tool", help="tool name; omit the action and flags to list pins")
        parser.add_argument("--version", help="exact tool version for set, such as 24.14.1")
    elif name == "topology":
        parser.add_argument(
            "--json",
            action="store_true",
            help="print the same view as JSON, a structured format for scripts",
        )
    if name in remote.CAPABLE:
        parser.add_argument(
            "machine", nargs="?", help="one registered machine; omit to use this machine"
        )
    elif name in ("configure", "uninstall"):
        parser.add_argument("machine", nargs="?", help=argparse.SUPPRESS)


def normalize(args, parser):
    for name in tuple(vars(args)):
        if not name.startswith("legacy_"):
            continue
        value = getattr(args, name)
        destination = name.removeprefix("legacy_")
        if value is not None:
            if getattr(args, destination) is not None:
                parser.error("Do not combine a flagged value with its old positional form.")
            setattr(args, destination, value)
    if args.command == "groups" and args.state and not args.name:
        parser.error("groups --state needs --name")
    if args.command == "pins":
        if args.tool is not None or args.version is not None:
            if args.pin is not None:
                parser.error("Do not combine pin flags with an old positional pin.")
            if args.action == "set" and args.tool and args.version:
                args.pin = args.tool + "@" + args.version
            elif args.action == "remove" and args.tool and args.version is None:
                args.pin = args.tool
            else:
                parser.error(
                    "Use pins set --tool TOOL --version VERSION, or pins remove --tool TOOL."
                )
    return args


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
