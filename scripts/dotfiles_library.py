"""Keep downloaded dotfiles repositories in ~/.fleet/dotfiles. One of them is active.

Each download is a real Git clone at ~/.fleet/dotfiles/NAME. The profile names the active one.
Downloading never activates a repository; use activates it through a reviewed apply.

Usage: import this module from Fleet.
Help: python3 scripts/dotfiles_library.py --help.
"""

from pathlib import Path
import re
import shutil
import subprocess

from common import read_toml

LIBRARY = ".fleet/dotfiles"
PROFILE = ".fleet/profile.toml"
STARTER = "default"
ADOPTED = "adopted"
NAME = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_REPO = "https://github.com/jeremyspofford/dotfiles"


def repo_id(url):
    return (
        url.removesuffix(".git")
        .rstrip("/")
        .replace("git@github.com:", "https://github.com/")
        .replace("ssh://git@github.com/", "https://github.com/")
    )


def check_name(name):
    """Accept one plain path segment: letters, numbers, dashes, and underscores."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            f"{name!r} is not a usable name. Use only letters, numbers, dashes, and underscores."
        )
    return name


def same_source(first, second):
    return repo_id(str(first)) == repo_id(str(second))


def name_for(source):
    """Name a download after its repository. The starter pack is always named default."""
    if same_source(source, DEFAULT_REPO):
        return STARTER
    text = str(source).rstrip("/").removesuffix(".git")
    return re.split(r"[/:]", text)[-1] or "dotfiles"


def folder(home):
    return home / LIBRARY


def path(home, name):
    return folder(home) / check_name(name)


def names(home):
    root = folder(home)
    if not root.is_dir():
        return []
    return sorted(
        item.name
        for item in root.iterdir()
        if item.is_dir() and not item.is_symlink() and NAME.fullmatch(item.name)
    )


def active_name(home, profile=None):
    if profile is None:
        profile = read_toml(home / PROFILE) if (home / PROFILE).is_file() else {}
    return profile.get("dotfiles", "")


def active(home, profile=None):
    """Return the active checkout. Before any activation, return where the starter pack goes."""
    return folder(home) / (active_name(home, profile) or STARTER)


def source(checkout):
    """Return a checkout's origin URL, or local when it has no remote."""
    result = subprocess.run(
        ["git", "-C", str(checkout), "config", "--get", "remote.origin.url"],
        text=True,
        capture_output=True,
    )
    return result.stdout.strip() or "local"


def remote_urls(checkout):
    """Return every remote URL of a checkout. An upstream may use any remote name."""
    result = subprocess.run(
        ["git", "-C", str(checkout), "config", "--get-regexp", r"^remote\..*\.url$"],
        text=True,
        capture_output=True,
    )
    return [line.split(" ", 1)[1] for line in result.stdout.splitlines() if " " in line]


def holds(checkout, repo):
    """Tell whether a checkout is a clone of repo, through any of its remotes."""
    return any(same_source(url, repo) for url in remote_urls(checkout))


def free_name(home, wanted, terminal, reuse=None):
    """Return a usable, unused name. Ask for another name in a terminal; never rename silently.

    Reuse names a source whose existing download may keep its name, as the wizard does.
    """
    import manager

    name = wanted
    while True:
        problem = ""
        if not NAME.fullmatch(name or ""):
            problem = (
                f"{name!r} is not a usable name. Use letters, numbers, dashes, and underscores."
            )
        elif (folder(home) / name).exists():
            if reuse is not None and (
                holds(folder(home) / name, reuse)
                or reuse == "local"
                and source(folder(home) / name) == "local"
            ):
                return name
            problem = f"A download named {name} already exists in ~/{LIBRARY}."
        if not problem:
            return name
        if not terminal:
            raise ValueError(problem + " Run again with --name NAME. Nothing was downloaded.")
        print(problem)
        name = manager.ask(
            "Name",
            "",
            detail=(
                "Type a different name for this download: letters, numbers, dashes, and"
                " underscores.\nPress Enter alone to ask again."
            ),
        )


def name_for_source(home, repo, profile, terminal):
    """Name the download that holds repo: the active one when it matches, else a free name."""
    current = active_name(home, profile)
    if current and (folder(home) / current).is_dir():
        checkout = folder(home) / current
        if str(repo) == str(checkout) or holds(checkout, repo):
            return current
    if Path(str(repo)).is_relative_to(folder(home)) and Path(str(repo)).is_dir():
        return Path(str(repo)).name
    return free_name(home, name_for(repo), terminal, reuse=repo)


def clone(home, url, name):
    """Clone into the library under a temporary name, then give it its name."""
    root = folder(home)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / name
    staging = root / f".download-{name}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        result = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(url), str(staging)],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode:
            raise ValueError(
                "Download failed: "
                + (result.stderr.strip().splitlines() or ["git clone failed"])[-1]
            )
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def download(home, url, name=None, terminal=False):
    """Clone url into the library. Return its name. No applied file changes."""
    wanted = name if name is not None else name_for(url)
    if name is not None:
        check_name(name)
    chosen = free_name(home, wanted, terminal)
    clone(home, url, chosen)
    return chosen


def remove(home, name):
    """Delete one download. The active repository can never be removed."""
    target = path(home, name)
    if name == active_name(home):
        raise ValueError(
            f"{name} is the active repository. Use another repository first, then remove {name}."
        )
    if not target.is_dir() or target.is_symlink():
        raise ValueError(f"No download named {name}. Run dotfiles list to see the downloads.")
    shutil.rmtree(target)


def usage():
    """Describe this module when help is requested directly."""
    import argparse

    from style import ArgumentParser

    ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    ).parse_args()


if __name__ == "__main__":
    import sys

    if any(argument in ("--help", "-h") for argument in sys.argv[1:]):
        from style import run_cli

        sys.exit(run_cli(usage, Path(__file__).resolve().parents[1]))
