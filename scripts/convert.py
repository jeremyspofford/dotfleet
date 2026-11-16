"""Convert plain content into a chezmoi source, the files chezmoi reads.

Usage: python3 scripts/convert.py CHECKOUT OUTPUT [--profile FILE] [--format FORMAT]
Parameters: CHECKOUT is a Git copy; OUTPUT is an empty staging directory.
--profile reads local group selections. --format selects plain or chezmoi when detection is unclear.
-h/--help prints help. Example: python3 scripts/convert.py ~/dotfiles /tmp/source
Exit codes: 0 success/help; 1 invalid content; 2 invalid arguments.
Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION.
"""

import argparse
import style
from pathlib import Path
import re
import subprocess
import sys

from common import atomic, read_toml, relative, run

ROOT = Path(__file__).resolve().parents[1]
FURNITURE = {
    ".git",
    ".github",
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".editorconfig",
    ".pre-commit-config.yaml",
    ".DS_Store",
}
RESERVED = {
    "docs",
    "scripts",
    "tests",
    "README.md",
    "LICENSE",
    "dotfleet.toml",
    "AGENTS.md",
    "VERSION",
    "CHANGELOG.md",
    "package.json",
}
GENERATED = {
    ".secrets",
    ".config/git/local.gitconfig",
    ".config/dotfiles/domain",
    ".config/mise/config.toml",
}


def tracked(checkout):
    entries = {}
    for record in run("git", "-C", str(checkout), "ls-files", "--stage", "-z").split("\0"):
        if record:
            meta, name = record.split("\t", 1)
            mode, _, stage = meta.split()
            if stage != "0":
                raise ValueError("Resolve content merge conflicts first.")
            entries[name] = mode
    return entries


def detect(checkout, explicit=None):
    manifest = read_toml(checkout / "dotfleet.toml")
    names = tracked(checkout)
    kind = manifest.get("format") or explicit
    if not kind:
        if any(n.startswith((".chezmoi", "dot_", "private_dot_")) for n in names):
            kind = "chezmoi"
        elif any(
            eligible(n) or n.startswith("packages/") and eligible("/".join(Path(n).parts[2:]))
            for n in names
        ):
            kind = "plain"
    if kind not in ("plain", "chezmoi"):
        raise ValueError(
            (
                "Unknown content format. Choose --format plain (real names) or chezmoi "
                "(native sources)."
            )
        )
    return kind, manifest


def eligible(name):
    parts = Path(name).parts
    return bool(
        parts
        and parts[0].startswith(".")
        and parts[0] not in FURNITURE
        and not parts[0].startswith((".chezmoi", ".release-please"))
        and not name.endswith(".example")
    )


def component(name):
    prefix = "dot_" if name.startswith(".") else ""
    literal = name[1:] if prefix else name
    attributes = (
        "after_",
        "before_",
        "create_",
        "dot_",
        "empty_",
        "encrypted_",
        "external_",
        "exact_",
        "executable_",
        "literal_",
        "modify_",
        "once_",
        "onchange_",
        "private_",
        "readonly_",
        "remove_",
        "run_",
        "symlink_",
    )
    if literal.startswith(attributes):
        literal = "literal_" + literal
    return prefix + literal


def source_name(name, mode="100644"):
    parts = list(relative(name).parts)
    parts = [component(part) for part in parts]
    if mode == "120000":
        parts[-1] = "symlink_" + parts[-1]
    elif mode == "100755":
        parts[-1] = "executable_" + parts[-1]
    if parts[-1].endswith((".tmpl", ".literal")):
        parts[-1] += ".literal"
    return Path(*parts)


def selected_paths(checkout, manifest, groups):
    declared = [relative(p).as_posix() for p in manifest.get("paths", [])]
    for path in declared:
        if Path(path).parts[0] in FURNITURE | RESERVED:
            raise ValueError(f"Repository furniture cannot be content: {path}")
    output = {}
    for name, mode in tracked(checkout).items():
        parts = Path(name).parts
        # Stow packages each contain a home-shaped subtree. No Stow executable is needed.
        destination = "/".join(parts[2:]) if len(parts) > 2 and parts[0] == "packages" else name
        if not (
            eligible(destination) or any(name == p or name.startswith(p + "/") for p in declared)
        ):
            continue
        relative(destination)
        if mode not in ("100644", "100755", "120000"):
            raise ValueError(f"Unsupported Git mode at {name}: {mode}")
        if destination in output:
            raise ValueError(
                f"Two Stow packages own {destination}. Select one in the content repository."
            )
        output[destination] = (name, mode)
    for group, spec in manifest.get("groups", {}).items():
        if groups.get(group, True):
            continue
        for excluded in spec.get("files", []):
            excluded = relative(excluded).as_posix()
            output = {
                k: v
                for k, v in output.items()
                if k != excluded and not k.startswith(excluded + "/")
            }
    return output


def filter_blocks(content, name, manifest, groups):
    text = content.decode()
    for group, spec in manifest.get("groups", {}).items():
        if name not in spec.get("blocks", []):
            continue
        begin = f"# dotfleet:group {group} begin"
        end = f"# dotfleet:group {group} end"
        if text.count(begin) != 1 or text.count(end) != 1:
            raise ValueError(f"Missing or repeated block markers for {group} in {name}")
        pattern = re.compile(
            "^" + re.escape(begin) + r"\n.*?^" + re.escape(end) + r"(?:\n|$)", re.M | re.S
        )
        if not pattern.search(text):
            raise ValueError(f"Invalid block order for {group} in {name}")
        if not groups.get(group, True):
            text = pattern.sub("", text)
    return text.encode()


def convert(checkout, output, profile=None, explicit=None):
    checkout = Path(checkout).resolve()
    kind, manifest = detect(checkout, explicit)
    if kind == "chezmoi":
        return checkout, None, manifest
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Conversion output must be empty; never overwrite a source checkout.")
    output.mkdir(parents=True, exist_ok=True)
    items = selected_paths(checkout, manifest, (profile or {}).get("groups", {}))
    for destination, (name, mode) in items.items():
        # Read Git blobs, never dereference links or include uncommitted content.
        blob = subprocess.check_output(["git", "-C", str(checkout), "show", f":{name}"])
        if any(
            destination in spec.get("blocks", []) for spec in manifest.get("groups", {}).values()
        ):
            blob = filter_blocks(blob, destination, manifest, (profile or {}).get("groups", {}))
        mapped = source_name(destination, mode)
        if not blob and mode != "120000":
            mapped = mapped.with_name("empty_" + mapped.name)
        atomic(output / mapped, blob, 0o700 if mode == "100755" else 0o600)
    return output, items, manifest


def main():
    parser = style.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Version: " + (ROOT / "VERSION").read_text().strip(),
    )
    parser.add_argument("checkout", type=Path, help="content Git checkout")
    parser.add_argument("output", type=Path, help="empty converted-source directory")
    parser.add_argument("--profile", type=Path, help="machine profile TOML file")
    parser.add_argument(
        "--format",
        choices=["plain", "chezmoi"],
        help="plain=real names; chezmoi=native source names",
    )
    args = parser.parse_args()
    try:
        source, _, _ = convert(
            args.checkout, args.output, read_toml(args.profile) if args.profile else {}, args.format
        )
        print(source)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Conversion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(style.run_cli(main, ROOT))
