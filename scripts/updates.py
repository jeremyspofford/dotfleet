"""Check the same upstream commits that update stages, and cache successful results.

Usage: dotfleet check; dotfleet status.
An upstream is the remote branch tracked by a checkout's current branch.
Fetches stop after 10 seconds. No checkout, profile, or managed file is applied here.
Help: python3 scripts/updates.py --help.
"""

import datetime

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from common import atomic
from installation import MANAGER

CACHE = ".local/share/fleet/check.json"
FETCH_TIMEOUT = 10
CURRENT_MESSAGE = (
    "Fleet is up to date — manager and content match upstream, checked just now. No backup taken."
)


class CheckError(RuntimeError):
    """A source cannot be compared or safely fast-forwarded."""


def git(path, *arguments, optional=False, fetch=False, raw=False):
    environment = dict(
        os.environ,
        GIT_TERMINAL_PROMPT="0",
        GCM_INTERACTIVE="Never",
        GIT_ASKPASS="true",
        SSH_ASKPASS_REQUIRE="never",
        GIT_OPTIONAL_LOCKS="0",
    )
    environment.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=5")
    with subprocess.Popen(
        ["git", "-C", str(path), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    ) as process:
        try:
            stdout, _ = process.communicate(timeout=FETCH_TIMEOUT if fetch else None)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The process can exit between the timeout and signal.
            process.communicate()
            raise CheckError(f"remote fetch timed out after {FETCH_TIMEOUT} seconds") from None
    if process.returncode and not optional:
        if fetch:
            raise CheckError("remote fetch failed; check your connection and repository access")
        raise CheckError("Git could not read the checkout or its upstream")
    if process.returncode:
        return ""
    # Porcelain status lines start with a space; raw output keeps them intact.
    return stdout if raw else stdout.strip()


def repo_id(url):
    return (
        url.removesuffix(".git")
        .rstrip("/")
        .replace("git@github.com:", "https://github.com/")
        .replace("ssh://git@github.com/", "https://github.com/")
    )


def remote_url(path, name):
    # Keep the configured URL; get-url would replace it through Git mirror rules.
    url = git(path, "config", "--get", f"remote.{name}.url")
    # Relative filesystem remotes must still resolve after staging moves to scratch.
    if ":" not in url and not Path(url).is_absolute():
        url = str((path / url).resolve())
    return url


def validate_content(path, repo):
    if path.is_symlink() or not (path / ".git").is_dir():
        raise CheckError("~/dotfiles is missing or is not the selected clone")
    urls = [remote_url(path, name) for name in git(path, "remote").splitlines()]
    if str(path) != repo and repo_id(repo) not in {repo_id(url) for url in urls}:
        raise CheckError("~/dotfiles belongs to another repository; nothing was overwritten")


def edited_paths(path):
    """List tracked paths with uncommitted edits. Untracked files never appear."""
    records = git(path, "status", "--porcelain=v1", "-z", "--untracked-files=no", raw=True).split(
        "\0"
    )
    names, index = [], 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        names.append(record[3:])
        if record[0] in "RC":
            names.append(records[index])  # A rename also records its original path.
            index += 1
    return names


def managed_edits(path):
    """Return uncommitted edits to content that Fleet installs, including the manifest.

    The foundation is the tracked content that Fleet installs. Unmanaged files, such as
    untracked notes or an edited README, never block setup. Native chezmoi content
    treats every tracked file as managed.
    """
    from convert import detect, selected_paths

    edited = edited_paths(path)
    if not edited:
        return []
    try:
        kind, manifest = detect(path)
    except (OSError, ValueError):
        return edited
    if kind == "chezmoi":
        return edited
    sources = {name for name, _ in selected_paths(path, manifest, {}).values()}
    return [name for name in edited if name in sources or name in ("fleet.toml", "dotfleet.toml")]


def require_clean(path, content=False):
    if path.is_symlink() or not (path / ".git").exists():
        raise CheckError("checkout is missing; use the one-line installer to recover it")
    if content:
        edits = managed_edits(path)
        if edits:
            raise CheckError(
                "content checkout has uncommitted edits to managed files: "
                + ", ".join(sorted(edits)[:5])
                + "; commit or move them before updating"
            )
    elif git(path, "status", "--porcelain"):
        raise CheckError("checkout has local edits; commit or move them before updating")


def upstream(path, local_only=False):
    branch_ref = git(path, "symbolic-ref", "--quiet", "HEAD", optional=True)
    if not branch_ref:
        raise CheckError("checkout has no current branch; use the one-line installer to recover it")
    branch = branch_ref.removeprefix("refs/heads/")
    remote = git(path, "config", "--get", f"branch.{branch}.remote", optional=True)
    reference = git(path, "config", "--get", f"branch.{branch}.merge", optional=True)
    if local_only and not git(path, "remote"):
        return {"branch": branch, "remote": "", "reference": "", "local_only": True}
    if not remote or remote == "." or not reference.startswith("refs/heads/"):
        raise CheckError("no remote upstream is configured; set a Git upstream before updating")
    return {
        "branch": branch,
        "remote": remote,
        "reference": reference,
        "local_only": False,
        "url": remote_url(path, remote),
    }


def compare(path, local_only=False, content=False):
    require_clean(path, content)
    source = upstream(path, local_only)
    current = git(path, "rev-parse", "HEAD")
    remote_commit = current
    if not source["local_only"]:
        git(
            path, "fetch", "--quiet", "--no-tags", source["remote"], source["reference"], fetch=True
        )
        remote_commit = git(path, "rev-parse", "FETCH_HEAD")
    ahead, behind = map(
        int,
        git(path, "rev-list", "--left-right", "--count", f"{current}...{remote_commit}").split(),
    )
    if ahead and behind:
        raise CheckError("checkout and upstream have diverged; reconcile them before updating")
    if content and behind:
        require_no_collision(path, current, remote_commit)
    return dict(
        source,
        path=str(path),
        current=current,
        upstream_commit=remote_commit,
        target=remote_commit if behind else current,
        ahead=ahead,
        behind=behind,
    )


def copy_tracking(source, destination):
    """Keep remote names and the selected upstream when cloning into a new location."""
    remotes = git(source, "remote").splitlines()
    if remotes:
        existing = git(destination, "remote").splitlines()
        for name in set(existing) - set(remotes):
            git(destination, "remote", "remove", name)
        for name in remotes:
            action = "set-url" if name in existing else "add"
            git(destination, "remote", action, name, remote_url(source, name))
    # Initial installs may come from a detached CI checkout. Keep their clone behavior.
    branch_ref = git(source, "symbolic-ref", "--quiet", "HEAD", optional=True)
    if branch_ref:
        branch = branch_ref.removeprefix("refs/heads/")
        remote = git(source, "config", "--get", f"branch.{branch}.remote", optional=True)
        reference = git(source, "config", "--get", f"branch.{branch}.merge", optional=True)
        if remote and reference:
            git(destination, "config", f"branch.{branch}.remote", remote)
            git(destination, "config", f"branch.{branch}.merge", reference)


def stage(path, destination, local_only=False, content=False):
    require_clean(path, content)
    upstream(path, local_only)  # Reject unsupported tracking before creating a staged copy.
    git(path, "clone", "--quiet", "--no-hardlinks", str(path), str(destination))
    copy_tracking(path, destination)
    if local_only and not git(path, "remote"):
        git(destination, "remote", "remove", "origin")
    result = compare(destination, local_only)
    if result["behind"]:
        if content:
            # The staged clone holds the target commit; the checkout holds the local files.
            require_no_collision(path, result["current"], result["target"], destination)
        git(destination, "merge", "--ff-only", "--no-edit", result["target"])
    return destination


def require_no_collision(path, current, target, history=None):
    """Stop before backup when an update would change a local file that Fleet does not manage."""
    history = history or path
    changed = set(git(history, "diff", "--name-only", "-z", current, target, raw=True).split("\0"))
    local = set(edited_paths(path))
    local |= set(git(path, "ls-files", "--others", "-z", raw=True).split("\0"))
    collisions = sorted((changed & local) - {""})
    if collisions:
        raise CheckError(
            "the update changes files with local edits in ~/dotfiles: "
            + ", ".join(collisions[:5])
            + "; commit or move them before updating"
        )


def check_marketplaces(home, display):
    """Check marketplaces after the manager and content. Return 1 when an unpinned one is behind."""
    import marketplaces

    if not marketplaces.load_records(home)["marketplaces"]:
        return 0
    try:
        results = marketplaces.check(home)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Warning: Could not check marketplaces: {error}.", file=sys.stderr)
        return 2
    for name, result in results.items():
        count = result["behind"]
        unit = "commit" if count == 1 else "commits"
        state = f"{count} {unit} behind" if count else "up to date"
        if result["pin"] and count:
            state += f"; pinned at {result['pin']}, so the pin blocks the update"
        if display:
            print(f"\nMarketplace {name}: {state} @ {result['current'][:8]}")
    return int(any(result["behind"] and not result["pin"] for result in results.values()))


def check(home, profile, display=True, marketplaces=False):
    name = "manager"
    try:
        sources = []
        for name, path in [("manager", home / MANAGER), ("content", home / "dotfiles")]:
            if name == "content":
                validate_content(path, profile["content_repo"])
            local_only = name == "content" and profile["content_repo"] == str(path)
            sources.append(dict(compare(path, local_only, name == "content"), name=name))
        record = {
            "schema_version": 1,
            "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "sources": sources,
        }
        cache = home / CACHE
        if cache.is_symlink() or not cache.parent.resolve().is_relative_to(home.resolve()):
            raise CheckError("the check cache must stay inside the manager store")
        atomic(cache, json.dumps(record, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        import run_index

        run_index.failure(f"Warning: Could not check {name}: {error}.")
        print(f"Warning: Could not check {name}: {error}.", file=sys.stderr)
        return 2
    if display:
        for index, source in enumerate(sources):
            if index:
                print()
            count = source["behind"]
            unit = "commit" if count == 1 else "commits"
            state = f"{count} {unit} behind" if count else "up to date"
            if source["local_only"]:
                state += " (local content; no remote)"
            print(f"{source['name'].capitalize()}: {state} @ {source['current'][:8]}")
    result = int(any(source["behind"] for source in sources))
    if marketplaces:
        # The update gate leaves marketplaces out; marketplaces update advances them.
        result = max(result, check_marketplaces(home, display))
    return result


def summary(home, profile=None):
    from command_display import command_name, current_command

    display = current_command(home) if profile is None else command_name(profile)
    missing = f"Updates: not checked yet — run: {display} check"
    try:
        cache = home / CACHE
        if cache.is_symlink() or not cache.parent.resolve().is_relative_to(home.resolve()):
            return missing
        record = json.loads(cache.read_text())
        if record["schema_version"] != 1:
            return missing
        checked = (
            datetime.datetime.fromisoformat(record["checked_at"]).astimezone().strftime("%H:%M")
        )
        sources = record["sources"]
        if [source["name"] for source in sources] != ["manager", "content"]:
            return missing
        if any(type(source["behind"]) is not int or source["behind"] < 0 for source in sources):
            return missing
        changes = ", ".join(
            f"{source['name']} +{source['behind']}" for source in sources if source["behind"]
        )
        if changes:
            return f"Updates: {changes} (checked {checked}) — run: {display} update"
        return f"Updates: none (checked {checked})"
    except (OSError, ValueError, KeyError, TypeError):
        return missing


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
