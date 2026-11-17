"""Run a validated Fleet action through the target's own installed manager.

Usage: dotfleet status MACHINE; dotfleet machines set-ssh --target mini-pc.
SSH reads the user's connection settings and keys. Requests travel through stdin,
never through the remote shell command. No files or profiles cross machines.
Help: python3 scripts/remote.py --help.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import registry
import connections
import topology
import style
from render_run import render_view

CAPABLE = ("status", "check", "diff", "doctor", "update")
CONTEXT = "FLEET_REMOTE_RUN"
REASONS = {
    "configure": "configure needs a terminal on that machine",
    "rollback": "rollback is destructive and must run on that machine",
    "uninstall": "uninstall is destructive and must run on that machine",
}
# This literal is the entire remote command. User input only enters the JSON request.
REMOTE_COMMAND = 'export PATH="$HOME/.local/bin:$PATH"; exec "$HOME/.local/bin/dotfleet" _remote'


def validate_target(target):
    if not isinstance(target, str) or not re.fullmatch(
        r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.:-]*", target
    ):
        raise ValueError("SSH target must be a host name or user@host, without spaces or options.")
    return target


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {"version", "action", "check", "stat"}:
        raise ValueError("Invalid remote request. Update Fleet on both machines.")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ValueError("Unsupported remote request version. Update Fleet on both machines.")
    action = request["action"]
    if not isinstance(action, str) or action not in CAPABLE:
        raise ValueError("Only status, check, diff, doctor, and update can run remotely.")
    for option, allowed in (("check", "status"), ("stat", "diff")):
        if type(request[option]) is not bool or request[option] and action != allowed:
            raise ValueError("Invalid remote option for " + action + ".")
    return request


def refusal(machine, action):
    reason = REASONS.get(action, "this command has no remote operation")
    print(f"Error: {machine}: remote {action} refused; {reason}.")
    return 2


def test_connection(home, name, target):
    try:
        result = connections.invoke(target, "true", probe=True)
        if result.returncode:
            raise ConnectionError(
                result.stderr or result.stdout or f"SSH test failed (exit {result.returncode})"
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        reason = connections.report_failure(home, name, target, error)
        topology.remember(home, name, target, "connection test", reason, 2)
        print("Saved connection kept. Retry after fixing access or waking the target.")
        return 2
    print(f"PASS: {name}: SSH connection test passed.")
    topology.remember(home, name, target, "connection test", "SSH test passed", 0)
    return 0


def execute(home, machine, action, *, check=False, stat=False):
    if action not in CAPABLE:
        return refusal(machine, action)
    request = validate_request(dict(version=1, action=action, check=check, stat=stat))
    try:
        record = registry.load(home)["machines"].get(machine)
        if record is None:
            raise ValueError("no saved machine; use machines set-ssh --target HOST first")
        if not record.get("ssh_target"):
            raise ValueError("no connection; use machines set-ssh --name NAME --target HOST first")
        target = validate_target(record["ssh_target"])
    except (OSError, ValueError) as error:
        print(f"Error: {machine}: {connections.one_line(str(error))}.")
        return 2
    try:
        result = connections.invoke(target, REMOTE_COMMAND, json.dumps(request) + "\n")
        if result.returncode == 255:
            raise ConnectionError(result.stderr or "SSH connection failed")
    except OSError as error:
        reason = connections.report_failure(home, machine, target, error)
        topology.remember(home, machine, target, action, reason, 2)
        return 2
    print(render_view("Machine · " + machine, [("Connection", target)], str(home)), end="")
    if isinstance(sys.stdout, style.LoggerStream) and action == "diff":
        sys.stdout.diff = True
    # The target uses the shared renderer, with its own home and its own run record.
    for output in (result.stdout, result.stderr):
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
    if result.returncode == 0:
        state = action + " succeeded"
        print(f"{machine}: {state} (exit 0).")
    elif result.returncode == 1 and (action in ("check", "diff") or check):
        state = "differences found" if action == "diff" else "updates available"
        print(f"{machine}: {state} (exit 1).")
    else:
        state = action + " failed"
        print(f"Error: {machine}: {state} (exit {result.returncode}).")
    topology.remember(home, machine, target, action, state, result.returncode)
    return result.returncode if result.returncode > 0 else int(result.returncode != 0)


def receive():
    """Validate before calling the same local dispatcher. No remote nesting or terminal."""
    import command
    import ui_channel

    try:
        request = validate_request(json.loads(sys.stdin.read(4097)))
        os.environ.pop(ui_channel.CHANNEL, None)
        os.environ.update(
            {
                CONTEXT: "1",
                "NO_COLOR": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "SSH_ASKPASS_REQUIRE": "never",
                "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
            }
        )
        home = Path.home()
        gaps = command.command_profile_gaps(command.profile(home))
        if gaps:
            print("Error: Remote operation needs a complete target profile:")
            for gap in gaps:
                print("- " + gap)
            print("Complete these choices on the target, then retry.")
            return 2
        args = [request["action"]]
        if request["check"]:
            args.append("--check")
        if request["stat"]:
            args.append("--stat")
        return command.main(args)
    except (OSError, ValueError, RuntimeError) as error:
        print("Error: " + str(error))
        return 2


def usage():
    import argparse

    style.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        sys.exit(style.run_cli(usage, Path(__file__).resolve().parents[1]))
