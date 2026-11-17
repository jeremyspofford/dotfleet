# Layer 1 decisions

Fleet is Layer 1: the manager for machine enrollment and later changes.
Dotfiles is Layer 0: independent configuration content. Content uses real home filenames and does not depend on Fleet.
Git transfers repositories. chezmoi applies managed files. Mise manages tool versions.

These five decisions are settled. The term v1 names the first product scope, not permission to release version 1.0.0.

## 1. Use a wizard and flags

The wizard guides interactive enrollment. Flags supply the same choices in scripted runs.
A profile stores the selected content, domain, identity, exact tool versions, groups, and agents.
A domain selects optional content. Groups select related files or command shortcuts; agents are coding assistants.
Missing choices must remain visible. Without a terminal, incomplete required choices stop the run instead of accepting silent defaults.
Review planned changes before interactive apply. Read decisions from the terminal, never piped script input.

## 2. Keep the registry in the manager store

The registry is `~/.local/share/fleet/registry.toml`. A registry holds saved machine records; TOML is its structured file format.
Schema 1 uses a `machines` table keyed by saved machine name. Its legacy `hostname` field equals that key.
Each record stores hostname, operating system, domain, content repository and commit, manager version, last run, and last backup.
A commit identifies a saved Git version. The registry retains full commit identifiers and absolute backup paths.
The `schema_version` field is 1. Each machine table contains these fields:

- Strings: `hostname`, `home`, `os`, `domain`, `content_repo`, `content_commit`, `manager_version`, `last_run`, `last_backup`, `last_run_record`, and `result`.
- Booleans: `domain_chosen` and `enrolled`. These record explicit domain choice and whether the machine retains an installed profile.

`ssh_target` is an optional string: an SSH configuration host name or `user@host`. Missing or empty means `no connection`.
Registration defaults the name to its SSH target. Either --name or --target supplies both, unless both flags differ.
The machines set-ssh command saves under the same registry lock and backup rules as removal. It then tests SSH once.
Failure keeps the saved link and returns exit 2 with guidance.
New connection records may have no machine facts yet. Record refreshes preserve the saved connection.

`last_run` uses UTC time in ISO format. UTC is the common time reference; ISO format stores an unambiguous date and time.
Paths remain absolute. `home` records the machine's home path so reports can print its saved paths with `~`.
The registry does not store identity, pins, or credentials.
Use unique hostnames when combining records manually.

Example record; the values below describe an illustrative machine:

```toml
schema_version = 1

[machines."example-hub"]
hostname = "example-hub"
home = "/home/example"
os = "Linux"
domain = "personal"
domain_chosen = true
content_repo = "https://github.com/jeremyspofford/dotfiles"
content_commit = "0123456789abcdef0123456789abcdef01234567"
manager_version = "0.2.0"
last_run = "2026-11-16T12:00:00+00:00"
last_backup = "/home/example/fleet-backups/20261116-070000"
last_run_record = "/home/example/fleet-backups/20261116-070000/RUN.json"
result = "SUCCESS"
enrolled = true
```

Backups include the registry for manual recovery. Automatic rollback never replaces it, and uninstall never removes it.
These operations preserve other machines' records. Each operation refreshes this machine's record; uninstall records `enrolled = false`.
Inspect current records before any manual recovery. An older registry can contain stale facts about other machines.

Backed-up apply, update, restore, and backup runs refresh this machine's record, including failures.
Read-only commands and cancellations do not update timestamps. Cancelling before confirmation leaves managed files unchanged. Earlier store migration moves remain.
`dotfleet machines` prints saved records already present in the file. It does not contact other machines or prove that their records are current.
Keep profile data and the registry out of the content repository. Machine synchronization and a live fleet view remain deferred.

## 3. Keep status local in v1

Without a machine name, `dotfleet status` reports this machine only. It shows profile choices, checkout state, drift, and recent runs and backups.
A checkout is a local Git copy. Drift means managed files differ from the selected content.
The behind count compares content with the last fetched remote state. Plain status never fetches.
It adds one line from the saved update check. `dotfleet status --check` runs a check first.
`dotfleet doctor` checks local readiness and names fixes. Missing domain provenance counts as unchosen until a user selects a domain.
Provenance records how a choice was made. Passing a domain explicitly records a human choice.

### Share update selection

`dotfleet check` and update share upstream selection and comparison. An upstream is the remote branch tracked by a checkout's current branch.
They compare both manager and content. Update preserves these upstream settings when replacing its manager checkout.
Both refuse uncommitted edits and diverged histories. Diverged histories contain new local and remote commits that cannot combine by moving forward.
Local commits stay intact when the remote adds nothing. Adopted content without a remote stays local.

Check quietly fetches each source with a ten-second limit. Update fetches into temporary checkouts before review and backup.
Neither substitutes the remote's default branch for the selected upstream. New commits after a check can change the next update's result.

The manager owns `~/.local/share/fleet/check.json`. Content cannot manage this file.
Schema 1 stores `checked_at` and `sources`. Each source records its name, path, branch, remote, current and upstream commits, target commit, and ahead/behind counts.
The time includes its time zone. The cache keeps full paths and commit identifiers; command output shortens commits.
Check replaces the private cache atomically only after both sources succeed. A failed check leaves the previous cache unchanged.
It creates no backup or run record and writes no profile or managed files.
Exit codes are 0 for current, 1 for updates, and 2 for an unavailable check. `status --check` returns the same codes.
Plain status reads only the cache. Without a valid cache, it says “not checked.”

## 4. Let Fleet own releases

Fleet owns the release workflow, version file, changelog, policy script, and tests.
Its release machinery follows the former dotfiles setup: GitHub Actions and release-please.
GitHub Actions runs checks. Release-please proposes version changes from Conventional Commits, commit messages with defined type prefixes.
The content repository does not own manager releases.

Stay in the zero-major version range until the owner explicitly declares production readiness.
Automation must never propose version 1.0.0. That version requires a deliberate manual release by the owner.
The owner reviews and merges release pull requests. See [the release rules](RELEASES.md).

## 5. Keep Bash and Python

Bash provides the installer and thin command entry. Python handles profiles, conversion, run records, and validation.
Use the existing manager path for changes. One renderer prints plans and completed run records.
The renderer shows readable paths and commit identifiers; machine records retain full values.

The terminal interface uses Textual in `~/.local/share/fleet/tui-venv`, a manager-owned virtual environment with separate Python packages.
The installer and update maintain it after review and backup. Pinned requirements define its contents; Software records each change.
The plain command core uses only the standard library. Missing interface packages never prevent plain commands.

Interface actions run the existing command dispatcher in a separate process. This isolates terminal input, environment variables, and Python output streams.
A private inherited socket carries reviews and answers. A socket is a local communication channel; it has no listening network address.
Only the shared core validates confirmation words and applies files. Both views render the same run record.
Status and dashboard never fetch. Updates uses the shared check and update cores.
Machines offers the same remote command core only for records with an SSH target.
Configure closes Textual before the original wizard takes the terminal. Unattended conflicts stop without choosing overwrite.
Keep Git, chezmoi, and Mise as the proven tools. Do not add a custom sync engine or background synchronization daemon.

Add-ons follow the [add-on contract](ADDON-INTERFACE.md). None ship with this version.
The contract defines declarations and ownership before an executable add-on interface is introduced.

## On-demand remote operations

SSH runs the target's installed command. The target owns execution, profile validation, backups, rendering, and run records.
The local manager validates status, check, diff, doctor, or update before starting SSH. Both ends validate the request.
A fixed remote shell command starts the receiver. User arguments never enter that command.
A versioned JSON request travels through standard input. JSON is a structured transfer format; it is never printed to the terminal.
Requests contain one action and boolean options for status checks or diff paths. They cannot name a profile, path, executable, or another target.

SSH uses batch mode, no terminal, one connection attempt, and a ten-second connection timeout.
Keepalive checks stop a dead connection. Live updates have no artificial time limit.
The target disables terminal decisions and Git password prompts. Update passes --defaults to the existing installer.
Incomplete profiles fail before action dispatch. Unattended file conflicts fail without overwriting local edits.
Configure, rollback, and uninstall remain local-only. There is no persistent network listener, remote file editing, or cross-machine profile transfer.

The target's shared renderer supplies human output. The caller adds the machine name and result, without recording a local install run.
Check and diff preserve their meaningful exit 1 results. SSH failures return nonzero with one diagnostic line and one next step.
An interrupted connection leaves the update outcome unknown; the owner must inspect the target's last run.

## SSH discovery and recorded topology

The terminal picker and TUI read the same concrete Host names from the user’s SSH config and Include files.
Relative Include paths start at ~/.ssh, matching OpenSSH. Includes expand file patterns in sorted order; visited files prevent include cycles.
Wildcard and negated Host patterns, duplicate names, and registered names or targets are excluded.
The collector does not execute Match commands. SSH itself resolves the chosen host's connection settings.
No available names means typed input. No terminal means explicit flags are required.

Registration runs the fixed SSH command true, with a ten-second total timeout. Remote operations still use the target's own manager.
Fleet never configures networks, relays, SSH keys, or SSH settings. Missing access produces guidance, not an automatic setup attempt.

The local contact cache is ~/.local/share/fleet/contacts.json. Schema 1 records target, action, outcome, exit code, and observation time per saved name.
Writes are private, atomic, and locked. Cache failures warn without changing the remote operation's outcome.
Topology reads this cache and the local check cache. It never fetches or copies another machine's store.
A changed target cannot inherit an old target's observation. Missing observations say never contacted.
Every saved result has its time and a recorded label. The header names the viewing machine; views are not synchronized.
Only the explicit topology --json option prints structured output. The normal CLI and TUI share the same tree renderer.

## Product name and stores

The command remains `dotfleet`; the guarded `fleet` alias avoids an existing Rancher Fleet command.
The content manifest is `fleet.toml`; the older `dotfleet.toml` name remains readable. The new name wins when both exist.
The machine registry is `~/.local/share/fleet/registry.toml`.

Enrollment and update move old stores before other work. The [migration rules](../README.md#store-migration) define all three folder moves.
Profiles and backup payloads stay intact. Rollback maps their old paths during restoration and repairs chezmoi's source pointer.
Migration appears under Profile changes in the run record and review. Cancelling later apply does not undo those moves.
Internal module names, environment variables, and `.local/state/dotfleet` records keep their existing names.
