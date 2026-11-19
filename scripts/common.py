"""Shared profile and path helpers. No command-line interface.

Usage: import this module from Fleet.
Help: python3 scripts/common.py --help.
Direct execution without help keeps its existing behavior.
"""

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


def bash():
    """Return the bash for Fleet's scripts. The launcher and installer resolve it by path."""
    return os.environ.get("FLEET_BASH") or "bash"


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
        return "[" + ", ".join(value(x) for x in item) + "]"
    if isinstance(item, dict):
        return "{" + ", ".join(value(k) + " = " + value(v) for k, v in item.items()) + "}"
    raise ValueError(f"Unsupported profile value: {type(item).__name__}")


def toml(data):
    # Inline tables preserve tool option tables and version lists without a dependency.
    return "".join(value(k) + " = " + value(v) + "\n" for k, v in data.items())


def atomic(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dotfleet-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content.encode() if isinstance(content, str) else content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative(name):
    path = Path(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Unsafe content path: {name}")
    return path


# Keep the existing import path for callers and integrations.
from style import LoggerStream


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
