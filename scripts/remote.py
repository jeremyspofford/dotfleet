"""Run a validated Fleet action through the target's own installed manager.

Usage: dotfleet status MACHINE; dotfleet machines set-ssh MACHINE SSH_TARGET.
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


def one_line(text):
    # SSH diagnostics can contain several lines. Keep connection failures to one report line.
    return " ".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).split())


def execute(home, machine, action, *, check=False, stat=False):
    if action not in CAPABLE:
        return refusal(machine, action)
    request = validate_request(dict(version=1, action=action, check=check, stat=stat))
    try:
        record = registry.load(home)["machines"].get(machine)
        if record is None:
            raise ValueError("no saved machine; use machines set-ssh NAME SSH_TARGET first")
        if not record.get("ssh_target"):
            raise ValueError("no connection; use machines set-ssh NAME SSH_TARGET first")
        target = validate_target(record["ssh_target"])
        result = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "ConnectionAttempts=1",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=2",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ForwardX11=no",
                "--",
                target,
                REMOTE_COMMAND,
            ],
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            env=dict(os.environ, SSH_ASKPASS_REQUIRE="never"),
        )
    except (OSError, ValueError) as error:
        print(f"Error: {machine}: {one_line(str(error))}.")
        return 2
    if result.returncode == 255:
        reason = one_line(result.stderr) or "SSH connection failed"
        print(f"Error: {machine}: {reason}")
        return 2
    print(render_view("Machine · " + machine, [("Connection", target)], str(home)), end="")
    if isinstance(sys.stdout, style.LoggerStream) and action == "diff":
        sys.stdout.diff = True
    # The target uses the shared renderer, with its own home and its own run record.
    for output in (result.stdout, result.stderr):
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
    if result.returncode == 0:
        print(f"{machine}: {action} succeeded (exit 0).")
    elif result.returncode == 1 and (action in ("check", "diff") or check):
        detail = "differences found" if action == "diff" else "updates available"
        print(f"{machine}: {detail} (exit 1).")
    else:
        print(f"Error: {machine}: {action} failed (exit {result.returncode}).")
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
