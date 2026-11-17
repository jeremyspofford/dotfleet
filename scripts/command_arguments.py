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
        "list": "List saved machines and their connections.\nMake no network requests.",
        "set-ssh": "Save and test an SSH target.\nOmit flags to pick a host in a terminal.",
        "export": "Show this machine's saved record, or save a private copy with --output.",
        "remove": "Remove a stale machine record.\nKeep the active machine.",
    },
    "backups": {
        "restore": "List backups or restore one after typing restore to confirm.\n"
        "Restore the saved profile too. Press Enter after each answer.",
        "delete": "Delete saved backups after typing delete to confirm.\n"
        "Deleted backups cannot be rolled back to.",
        "create": "Copy managed files and the profile into a new private backup.\n"
        "Change no managed files.\nPrint the backup location and run record.",
        "list": "List saved copies with their dates, results, sizes, and locations.",
    },
    "secrets": {"list": "List declared variable names as set or unset.\nNever print a value."},
    "tools": {
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
    "addons": {"list": "List installed add-on records.\nNo add-ons ship with Fleet."},
}

EXAMPLES = {
    "machines": "machines list; machines set-ssh --target mini-pc; machines set-ssh --name beelink --target mini-pc; machines export --output FILE; machines remove --name NAME",
    "domain": "domain list; domain set --name personal",
    "groups": "groups list; groups set --name docker --state off",
    "tools": "tools list; tools set node --version 26.13.1; tools remove node",
    "backups": "backups create; backups list; backups restore --backup BACKUP; backups delete --name BACKUP; backups delete --keep 2; backups delete --all",
    "secrets": "secrets list",
    "content": "content",
    "addons": "addons list",
    "topology": "topology; topology --json",
    "help": "help machines; machines set-ssh help",
}


def help_epilog(name, display, action=None):
    codes = ["0  Success, help, or cancellation.", "1  Operation failed.", "2  Invalid arguments."]
    if name == "check":
        codes = [
            "0  Manager and content are current.",
            "1  Updates are available.",
            "2  Could not check updates, or invalid arguments.",
        ]
    elif name == "diff":
        codes = [
            "0  No differences.",
            "1  Differences exist.",
            "2  Preview failed, or invalid arguments.",
        ]
    elif name == "doctor":
        codes[1] = "1  One or more checks failed."
    elif name == "machines" and action == "set-ssh":
        codes[2] = "2  Connection test failed, or invalid arguments. The saved target stays."
    codes.append("130  Interrupted.")
    choices = EXAMPLES.get(name, name).split("; ")
    if action:
        choices = [value for value in choices if value.split()[:2] == [name, action]]
    examples = ["  " + display + " " + value for value in choices]
    return (
        "Exit codes:\n"
        + "\n".join("  " + code for code in codes)
        + ("\n\nExamples:\n" + "\n".join(examples) if examples else "")
    )


def add_groups(parser, name, formatter, display):
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    parser.set_defaults(help_parser=parser)
    for action, description in GROUPS[name].items():
        child = actions.add_parser(
            action,
            prog=parser.prog + " " + action,
            description=description
            + (
                "\nEither flag supplies both fields unless the values differ."
                "\nTest SSH once in batch mode, without password prompts, with a ten-second timeout."
                "\nA failed test keeps the saved target and exits 2."
                "\nFleet never edits SSH configuration."
                if action == "set-ssh"
                else ""
            ),
            help=description.splitlines()[0],
            formatter_class=formatter,
            epilog=help_epilog(name, display, action),
        )
        child.set_defaults(help_parser=child)
        add_action_parameters(child, name, action)


def add_action_parameters(parser, name, action):
    if name == "backups" and action == "delete":
        selectors = parser.add_mutually_exclusive_group(required=True)
        selectors.add_argument(
            "--all", dest="all_backups", action="store_true", help="delete every saved backup"
        )
        selectors.add_argument(
            "--keep", type=int, help="keep the newest N backups; delete older ones; zero keeps none"
        )
        selectors.add_argument("--name", help="one folder name from backups list")
    elif name == "backups" and action == "restore":
        parser.add_argument("--backup", help="saved folder name or path; omit to list and choose")
    elif name == "machines":
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
    elif name == "tools" and action in ("set", "remove"):
        parser.add_argument("tool", help="tool name, such as node")
        if action == "set":
            parser.add_argument(
                "--version", required=True, help="exact tool version, such as 24.14.1"
            )


def add_parameters(parser, name):
    if name == "diff":
        parser.add_argument(
            "--stat", action="store_true", help="show paths only; keep the same exit codes"
        )
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
    if args.command == "tools" and args.action in ("set", "remove"):
        args.pin = args.tool + "@" + args.version if args.action == "set" else args.tool
    return args


def successor(arguments):
    path = arguments[1:] if arguments[:1] == ["help"] else arguments
    if not path:
        return None
    renamed = {
        "backup": "backups create",
        "rollback": "backups restore",
        "pins": "tools",
        "profile": "status",
    }
    if path[0] in renamed:
        return renamed[path[0]]
    if path[:2] == ["content", "show"]:
        return "content"
    return None


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
