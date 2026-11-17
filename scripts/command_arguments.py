"""Define command groups, named parameters, and consistent help spellings.

Usage: import add_parameters, add_groups, and help_arguments from the dispatcher.
Help: python3 scripts/command_arguments.py --help.
"""

import argparse
from pathlib import Path
import sys

import remote

GROUPS = {
    "machines": {
        "list": "List saved machines and their connections. Make no network requests.",
        "set-ssh": "Save and test an SSH target. Omit flags to pick a host in a terminal.",
        "export": "Show this machine's saved record, or save a private copy with --output.",
        "remove": "Remove a stale machine record. Keep the active machine.",
    },
    "backups": {"list": "List saved copies with their dates, results, sizes, and locations."},
    "secrets": {"list": "List declared variable names as set or unset. Never print a value."},
    "pins": {
        "list": "List recorded tool versions and whether they are installed.",
        "set": "Install and record an exact tool version.",
        "remove": "Remove a recorded version without uninstalling the tool.",
    },
    "groups": {
        "list": "List content groups, their state, and what each adds.",
        "set": "Turn one content group on or off, then apply the change.",
    },
    "domain": {
        "list": "List content domains and show the chosen domain.",
        "set": "Choose a domain and apply its content additions.",
    },
    "content": {"show": "Show the content checkout, repository, commit, and local Git state."},
    "profile": {"show": "Show saved machine choices and the profile file's location."},
    "addons": {"list": "List installed add-on records. No add-ons ship with Fleet."},
}

EXAMPLES = {
    "machines": "machines list; machines set-ssh --target mini-pc; machines set-ssh --name beelink --target mini-pc",
    "domain": "domain list; domain set --name personal",
    "groups": "groups list; groups set --name docker --state off",
    "pins": "pins list; pins set --tool node --version 24.14.1; pins remove --tool node",
    "backups": "backups list",
    "secrets": "secrets list",
    "content": "content show",
    "profile": "profile show",
    "addons": "addons list",
    "rollback": "rollback --backup BACKUP",
    "topology": "topology; topology --json",
    "help": "help machines; machines set-ssh help",
}


def add_groups(parser, name, formatter, epilog):
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    parser.set_defaults(help_parser=parser)
    for action, description in GROUPS[name].items():
        child = actions.add_parser(
            action,
            prog=parser.prog + " " + action,
            description=description
            + (
                " Either flag supplies both fields unless the values differ."
                " Test SSH once in batch mode, without password prompts, with a ten-second timeout."
                " A failed test keeps the saved target and exits 2. Fleet never edits SSH configuration."
                if action == "set-ssh"
                else ""
            ),
            help=description,
            formatter_class=formatter,
            epilog=epilog,
        )
        child.set_defaults(help_parser=child)
        add_action_parameters(child, name, action)


def add_action_parameters(parser, name, action):
    if name == "machines":
        if action == "set-ssh":
            # Keep the specifically promised old registration syntax out of help.
            parser.add_argument("legacy_name", nargs="?", help=argparse.SUPPRESS)
            parser.add_argument("legacy_target", nargs="?", help=argparse.SUPPRESS)
            parser.add_argument("--name", dest="hostname", help="saved name; defaults to --target")
            parser.add_argument(
                "--target", dest="ssh_target", help="SSH host or user@host; defaults to --name"
            )
        elif action == "remove":
            parser.add_argument("--name", dest="hostname", required=True, help="saved machine name")
        elif action == "export":
            parser.add_argument("--output", help="new private TOML file; omit to show the record")
    elif name == "domain" and action == "set":
        parser.add_argument("--name", required=True, help="domain from domain list")
    elif name == "groups" and action == "set":
        parser.add_argument("--name", required=True, help="content group from groups list")
        parser.add_argument(
            "--state", required=True, choices=["on", "off"], help="include or remove content"
        )
    elif name == "pins" and action in ("set", "remove"):
        parser.add_argument("--tool", required=True, help="tool name, such as node")
        if action == "set":
            parser.add_argument(
                "--version", required=True, help="exact tool version, such as 24.14.1"
            )


def add_parameters(parser, name):
    if name == "diff":
        parser.add_argument(
            "--stat", action="store_true", help="show paths only; keep the same exit codes"
        )
    elif name == "rollback":
        parser.add_argument("legacy_backup", nargs="?", help=argparse.SUPPRESS)
        parser.add_argument("--backup", help="saved folder name or path; omit to list and choose")
        parser.add_argument("machine", nargs="?", help=argparse.SUPPRESS)
    elif name == "topology":
        parser.add_argument(
            "--json", action="store_true", help="print JSON, a structured format for scripts"
        )
    if name in remote.CAPABLE:
        parser.add_argument(
            "machine", nargs="?", help="one registered machine; omit to use this machine"
        )
    elif name in ("configure", "uninstall"):
        parser.add_argument("machine", nargs="?", help=argparse.SUPPRESS)


def normalize(args, parser):
    if args.command == "machines" and args.action == "set-ssh":
        for old, name in (("legacy_name", "hostname"), ("legacy_target", "ssh_target")):
            value = getattr(args, old)
            if value is not None:
                if getattr(args, name) is not None:
                    parser.error("Do not combine a flagged value with its old positional form.")
                setattr(args, name, value)
    if args.command == "rollback" and args.legacy_backup is not None:
        if args.backup is not None:
            parser.error("Do not combine --backup with an old positional backup.")
        args.backup = args.legacy_backup
    if args.command == "pins" and args.action in ("set", "remove"):
        args.pin = args.tool + "@" + args.version if args.action == "set" else args.tool
    return args


def help_arguments(arguments, commands):
    """Treat help as a command word, never as the value of a named flag."""
    arguments = list(arguments)
    if not arguments:
        return ["--help"]
    if arguments == ["--version"]:
        return ["version"]
    if arguments[0] == "help":
        path = arguments[1:]
        if not path or path in (["help"], ["-h"], ["--help"]):
            return ["--help"]
        if path[0] not in commands or len(path) > 2:
            raise ValueError("Help needs a command and, optionally, one of its actions.")
        if len(path) == 2 and path[1] not in GROUPS.get(path[0], {}):
            raise ValueError("Unknown action for " + path[0] + ". Run help " + path[0] + ".")
        return path + ["--help"]
    if arguments[0] in commands:
        depth = 1
        if len(arguments) > 1 and arguments[1] in GROUPS.get(arguments[0], {}):
            depth = 2
        if len(arguments) == depth + 1 and arguments[-1] == "help":
            return arguments[:-1] + ["--help"]
    return arguments


def usage():
    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
