# Fleet

Renamed from Dotfleet, November 2026.

The installed command is `dotfleet`. Users can type the `fleet` alias.
Rancher's Fleet already provides a `fleet` command. We keep `dotfleet` to avoid PATH wars.
PATH lists the folders your shell searches for commands. The alias stays undefined when a real `fleet` command already exists.

Fleet manages home configuration across machines. A profile saves each machine's repository, domain, Git identity, tool pins, and content groups.
Git records history. chezmoi applies files. Mise selects developer tool versions. A pin selects a version; a group selects optional content.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/fleet/main/install.sh | bash
```

- Setup backs up existing files and keeps your editable content at `~/dotfiles`.
- Re-run the same command to update. Saved selections remain unchanged.
- Backups live in `~/fleet-backups/<timestamp>/`. Review `MANIFEST.json` before restoring `home/` files and external targets.

Requires Bash, git, curl, tar, and Python 3.11+. Use Linux, macOS, or WSL (Windows Subsystem for Linux).
Missing chezmoi and Mise install into `~/.local/bin`. Setup never uses sudo.
The wizard runs in a terminal for a new profile, incomplete choices, an unchosen domain, or `--configure`.
Prompts read the terminal, never piped input. Complete profiles update without enrollment questions.

1. Read the welcome screen. Backups come before replacement; review comes before apply.
2. Review the scan: operating system, installed Mise tools, home dotfiles, and existing enrollment.
3. Choose a source. **Adopt** keeps this machine's files; **defaults** uses our examples; **repo** accepts your Git URL.
4. Choose a domain. The prompt shows manifest descriptions and commands found in each overlay file.
5. Confirm Git name and email. Enter keeps the displayed value; type `skip` explicitly to leave it empty.
6. Compare installed tools, content defaults, and saved pins. Accept them or enter exact versions.
7. Choose groups. On includes their content; off excludes it. New groups say NEW.
8. Select detected agents, which are coding assistants. Set each detected agent on or off. Press Enter to keep its current choice.
9. Review all choices and the full planned change account. Type `confirm`, `quit`, or a step name to return.
10. Read the apply result and summary warnings.

**Start with `core` unless you want a domain's examples.** It includes shared shell, Git, aliases, and prompt settings.
The bundled `personal`, `work`, and `client` overlays each add two harmless example commands: show your directory and list its entries.
Replace those examples with your own settings. Empty overlays explicitly say they add nothing yet.
Choosing a domain installs no software and creates no accounts. Identity and tool pins are separate choices.
A new profile shows `core`; later prompts show your recorded domain. Confirm it by pressing Enter or entering another domain name.

Prompts use headings, spacing, and the shared logger's colors. Text labels keep choices clear without color.
Set `NO_COLOR` to disable colors, including an empty value. Redirected output stays plain.

### Adopt existing files

Adoption is recommended when the scan finds home dotfiles. It scans root dotfiles and `.config` files, excluding manager state and cache directories.
It excludes credential filenames, detected secrets, binary files, and files above 2 MiB. Logs, histories, caches, telemetry, session files, and receipts are not candidates.
The scan shows counts and up to 15 relevant file lines. RUN.json keeps the full scan and private exclusion list.
The scan cannot prove that remaining files contain no secrets. Review your local repository before pushing it.
Adopted files keep their exact contents, including existing Mise files. Profile choices do not rewrite adopted files.
Only accepted files enter the first local Git commit. Nothing is uploaded.

1. Review `~/dotfiles` and its Git history.
2. Create an empty repository on your chosen Git host.
3. Run `cd ~/dotfiles && git remote add origin URL`, replacing URL with that repository's address.
4. Run `git push -u origin main` when ready.

An unrelated `~/dotfiles` stops setup. Preserve it before choosing another repository or adopting files.
Native chezmoi templates and hooks can execute code. Use trusted content only.
Quitting review cancels planned file changes. Earlier [store moves](README.md#store-migration) stay in place.
Preparation uses temporary files outside home; missing tools move into `~/.local/bin` only after confirmation.

### Flags and updates

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/fleet/main/install.sh | bash -s -- --configure
```

`--configure` repeats the wizard with saved choices. `--defaults` uses complete recorded values without prompts; new groups default to on.
Without a terminal, incomplete profiles stop and list their gaps. No prompt or default silently fills identity, domain confirmation, or floating pins.

| Flag | Meaning |
| --- | --- |
| `--repo URL` | Use a content Git URL or local repository. |
| `--adopt` | Create a local repository from scanned home files. Cannot combine with `--repo`. |
| `--domain NAME` | Confirm core, personal, work, or client. Content defines the additions. |
| `--git-name NAME` | Set the Git commit author name. |
| `--git-email EMAIL` | Set the Git commit author email. |
| `--pin TOOL=VERSION` | Record an exact version. Repeat for more tools. |
| `--group NAME=on\|off` | Include or exclude a named group. Repeat for more groups. |
| `--agents NAMES` | Configure detected agents, separated by commas. Use none to skip. |
| `--format plain\|chezmoi` | Choose real names or native sources when detection is unclear. The manifest takes priority. |

Read `--help` for examples, limits, and exit codes.

## Run account

Every run prints six categories, including scripted runs. Empty categories collapse to one line.
A clean no-change run has about twelve content lines, plus spacing between sections.
Its Locations line points to RUN.json; the record retains every location. Review screens and changed runs show locations in full.
The wizard uses the same run record before apply. It describes planned actions; the end summary describes confirmed results.
Paths use `~` for your home directory. Commit IDs identify saved Git versions and show eight characters.
Full paths and commit IDs remain in RUN.json. Present software shares one quiet line.
File markers mean added (+), removed (−), or changed (~). Color reinforces these markers when a terminal supports it.
Set NO_COLOR to disable color. Redirected output stays plain.

File lists include generated settings and agent files. Removing a pin does not uninstall software.
Backups, converted sources, and internal state are not counted as setting changes.
Manager updates compare the running commit with the last confirmed run. Missing earlier values say “not set.”

The backup contains MANIFEST.json, which lists original files, and RUN.json, which records planned actions and confirmed results.
CHEZMOI-DIFF.patch records content and permission differences before apply. These private records can contain secret values.
Run reviews show paths. `dotfleet diff` shows patches, which can contain secret values. Failed runs report confirmed changes only.
An interrupted run directs you to its backup record when completion is unknown.

To roll back manually:

1. Read the backup's MANIFEST.json.
2. Restore saved home/ files and recorded external targets. Remove only new paths marked absent.
3. Keep later user edits and the backup. Restore the profile before running setup again.

This example is checked against the renderer's recorded test fixture:

```text
1 · Software — no changes.
  Already present: chezmoi 2.74.1, Mise 2026.8.5, Fleet 0.2.0

2 · Manager and content — no changes.

3 · Files — 1 added, 1 changed, 1 removed.
  + ~/.zshrc
  ~ ~/.aliases
  − ~/.config/docker/aliases.zsh

4 · Profile changes — 1 changed.
  Group docker  on → off

5 · Locations
  Content  ~/dotfiles
  Profile  ~/.config/fleet/profile.toml
  Source   ~/.local/share/fleet/chezmoi-source
  Backup   ~/fleet-backups/20261116-140000

6 · Next steps
  Update    fleet update
  Rollback  ~/fleet-backups/20261116-140000/MANIFEST.json
  Wizard    fleet configure

chezmoi status: clean · warnings: none
Run record: ~/fleet-backups/20261116-140000/RUN.json
```

## How it works

[dotfiles](https://github.com/jeremyspofford/dotfiles) provides plain content with real home names. It works without Fleet.
The [content contract](docs/CONTENT-CONTRACT.md) defines files, formats, groups, secrets, and ownership.
Fleet converts plain files into a private chezmoi source. Native chezmoi repositories use their checkout directly.
Stow layouts under `packages/<package>/` also work; their package prefixes do not reach the home directory.

Your profile lives at `~/.config/fleet/profile.toml`. It saves the domain, Git identity, exact tool versions, and group choices between runs.
Edit pins there, then rerun the installer. Mise records which versions are installed; Fleet reads that list during setup.
A saved pin selects a version. It does not mean that version is installed.
Setup asks when identity is incomplete, the domain choice is unconfirmed, or a pin floats, such as `latest`.
Prompts show recorded values. Press Enter to use the stated action, or type `skip` to leave a choice incomplete.
For identity, Enter keeps a nonempty displayed value. Type `skip` explicitly to leave identity empty.
Floating pins resolve to matching installed versions. Without a match, enter a full version, such as `24.14.1`, or leave the tool unpinned.
Exact pins remain unchanged. Complete profiles need no profile prompts.
The summary lists skipped gaps under Warnings. Without a terminal, incomplete identity, an unconfirmed domain, or a floating pin stops setup. `--defaults` cannot bypass this check.
For scripted runs, supply missing choices explicitly:

```sh
bash install.sh --domain personal --git-name "Example Person" --git-email person@example.invalid --pin node=24.14.1
```

`--domain` records the domain choice. `--git-name` and `--git-email` record commit identity.
Repeat `--pin TOOL=VERSION` for each full version. A floating value in a flag still fails unattended validation.
Complete profiles need no flags on later runs. New group defaults are printed during scripted setup.

`domain_chosen` records a human choice in this wizard, through `--domain`, or through `dotfleet domain NAME`.
A missing field means UNCHOSEN, including profiles saved before the wizard existed. Carried and defaulted domains remain unchosen until confirmed.
Status and doctor show that gap. Run `dotfleet domain` to read the choices, then `dotfleet domain NAME` to confirm one.
`unpinned_tools` records skipped tools, so content defaults cannot select them later. Remove a tool there to allow its defaults again.
Content defaults fill gaps only when the requested versions are installed. The summary lists other defaults as available, without installing them.
The manager creates Git identity, domain, Mise settings, and a private secrets file. Keep secret references in the content manifest, never values. Imported profile references remain valid.
A reference such as `op://Vault/Item/field` names a secret in 1Password. References are disabled unless explicitly enabled.

Old Layer 0 enrollment migrates automatically. Setup reads old chezmoi data, rendered Mise pins, and saved installer data before applying files.
Migration also saves existing Mise settings in the profile. Existing Fleet profiles remain authoritative. Backups preserve replaced files and prior staged sources.
Conflict menus explain letter keys and require Enter. Skipping changes can leave differences; the summary reports that as incomplete enrollment.

1. Edit content files in `~/dotfiles`. Change profile choices through `dotfleet domain`, `pins`, `groups`, or `configure`.
2. Commit and push content changes before updating. Setup refuses a checkout with uncommitted edits.
3. Run `dotfleet update` to convert and apply. The one-liner remains available for manager recovery.
4. Run `dotfleet status` to check for differences.

To roll back, restore saved `home/` files and external targets from the backup. Remove only new paths marked absent.
Restore the saved profile too. Keep the backup and rerun setup when you want to regenerate files.

## Agents, add-ons, and releases

An agent is a coding assistant. The optional [agent generator](docs/AGENTS.md) writes settings and links content skills; it installs no agents.
The [add-on contract](docs/ADDON-INTERFACE.md) remains a proposal. The enrollment wizard uses terminal prompts. The terminal interface uses the same command cores. No add-on runtime, custom sync engine, or daemon exists here.
`VERSION` starts at 0.1.0. Release automation stays in 0.x; only the owner can approve a manual 1.0.0 release.
Read [release rules](docs/RELEASES.md) and [writing rules](docs/WRITING.md).

## Checks

CI means automated change checks. It runs shell checks, formatting, release-policy tests, and container enrollment tests.
Container tests cover plain content, the private Stow archive, and a native chezmoi fixture.
They check adoption, secret exclusions, review cancellation, repeat runs, shell startup, group changes, saved profiles, and clean status.
The plain suite runs without Textual. A separate environment runs Textual pilot tests, which simulate keys and mouse clicks.
Pilot tests check the dashboard, updates, typed restore confirmation, narrow layouts, and plain themes.

For local checks, create a test environment with `requirements-tui.txt`. Set `FLEET_TEST_TUI_ENV` to an installed manager environment for reusable enrollment fixtures.
Run `python3 -m unittest discover -s tests -v` with standard Python.
Run that environment's Python with `-m unittest discover -s tests/tui -v` for screen tests.

## After enrollment

Use `dotfleet` for daily commands. The installer places it in `~/.local/bin` on every run.
It keeps the manager checkout at `~/.local/share/fleet/manager`. The command uses that copy and its VERSION file.
Keep `~/.local/bin` on PATH, the list of folders your shell searches for commands.
The one-line installer remains the first-install and recovery path.

| Command | Action |
| --- | --- |
| `dotfleet tui` | Open the full-screen terminal interface. Missing environments show plain command alternatives and exit 0. |
| `dotfleet update` | Fetch the manager and content, then apply saved choices. Refuse dirty or diverged checkouts. |
| `dotfleet profile` | Show saved choices and the profile location. Edit choices through commands or the wizard. |
| `dotfleet backups` | List backup dates, results, and sizes without changing files. |
| `dotfleet secrets` | List declared secret names as set or unset. Values stay private. |
| `dotfleet content` | Show the content checkout, repository, commit, behind state, and short Git status. |
| `dotfleet status` | Show choices, installed pins, drift, recent runs and backups, and the last update check. |
| `dotfleet check` | Fetch both upstreams and save their update counts. An upstream is the remote branch tracked by a checkout. |
| `dotfleet status --check` | Run check first, then show status. Return the check exit code. |
| `dotfleet diff` | Show pending patches. Use `--stat` for paths only. Exit 0 means clean; exit 1 means differences. |
| `dotfleet rollback` | List backups and choose one in a terminal. Include the saved profile when restoring. |
| `dotfleet rollback BACKUP` | Review one backup folder, then type restore to confirm. |
| `dotfleet domain` | Show the current domain, its confirmation state, and each domain's description. |
| `dotfleet domain personal` | Record the personal domain as chosen and apply it. |
| `dotfleet groups` | List content groups, their states, and descriptions. |
| `dotfleet groups docker off` | Exclude Docker content. Use on to restore it. |
| `dotfleet pins` | List saved exact versions and installed versions. |
| `dotfleet pins set node@24.14.1` | Explicitly install and record that exact version. Content defaults still never install software. |
| `dotfleet pins remove node` | Remove the pin and prevent content defaults from filling it again. Keep installed software. |
| `dotfleet configure` | Reopen the enrollment wizard. |
| `dotfleet doctor` | Check profile completeness, drift, checkout edits, and backups. Print a fix for each failed check. |
| `dotfleet backup` | Snapshot managed files and the profile without applying content. |
| `dotfleet version` | Print the manager version and content commit. |
| `dotfleet uninstall` | Review removal, then type uninstall. Keep the content checkout and all backups. |

A checkout is a local Git copy. Plain status reports saved update results and never fetches.
Its content behind count compares the last fetched remote state.
Drift means that managed files differ from the selected content. Groups select optional files or alias blocks.
The profile stores machine choices. Pins select exact tool versions.
Every command supports `--help`. Exit codes are 0 for success or cancellation, 1 for failed checks or operations, and 2 for arguments.
Check and `status --check` use 0 for current, 1 for available updates, and 2 when a check fails.

Change-making commands write RUN.json and print the standard review. File lists come from the plan and backup inventory.
Rollback makes another backup first. It restores only recorded paths, including original symlinks and recorded external targets.
It refuses directory replacement and changed external targets whose original symlink is missing.
It verifies saved file fingerprints before writing. A fingerprint records the file's content hash and permissions, or its symlink target.
A content checkout remains at its current commit after rollback. Use diff to review differences before updating again.
Uninstall offers to restore the oldest backup. Otherwise, it removes known managed files and the manager's command, checkout, converted source, and terminal interface environment.
Uninstall keeps installed tools, the content checkout, and all backups. Removal includes local edits, which the new backup preserves.
Rollback and uninstall require terminal confirmation. A scripted rollback without a folder only lists backups.

## Terminal interface

Run `fleet tui` after enrollment. Use `dotfleet tui` when the guarded alias is unavailable.
A terminal user interface (TUI) fills the terminal with navigable screens.
The dashboard fits 80 columns and 24 rows. Narrow terminals keep one column; long dashboard values shorten with an ellipsis.
Status shows the full values. Scroll the other screens to read long results.

| Screen | Action |
| --- | --- |
| Dashboard | Show this machine, domain choice, content source and commit, manager version, cached updates, last backup, and drift. |
| Status | Show the same local status as the plain command. |
| Updates | Check both sources live. Offer update when the check finds new commits. |
| Diff | Show pending file patches, or one clean-state line. |
| Backups | List saved backups. Select one, review its changes, then type `restore` to confirm. |
| Machines | Show saved registry records. No remote actions exist. |
| Doctor | Run the existing local checks and show each result. |
| Configure | Close the interface and run the existing terminal wizard. |

Arrows and Tab move between choices. Enter selects. Esc goes back or cancels review. Press q to quit.
During a change, q waits for that operation to finish. Review can still be cancelled before applying.
Mouse selection also works. Set `NO_COLOR`, even empty, or `TERM=dumb` for the plain theme.

The interface calls the existing command cores. Updates and rollback show the standard review and write the same RUN.json records.
Type `confirm` for an update. Type `restore` for rollback. Empty or incorrect answers never approve changes.
Incomplete profiles stop updates; choose Configure to finish them. Local file conflicts also stop unattended apply.
Use the plain update command in a terminal to resolve those conflicts. No interface action guesses an overwrite choice.

The installer and update create `~/.local/share/fleet/tui-venv`, a private Python environment owned by the manager.
A virtual environment holds separate Python packages. [Textual](https://textual.textualize.io/) supplies the terminal widgets.
`requirements-tui.txt` pins Textual and its dependencies. The Software category records environment creation or updates.
An unchanged environment stays untouched. A replaced environment moves into that run's backup as `previous-tui-venv`.
Failed environment installation restores the previous environment. A missing environment leaves the complete plain command interface available.
Run `fleet update`, or the README installer, to restore it. No system packages or sudo are used.
The command core needs only Python's standard library. Textual loads only in the interface process.

## Store migration

The installer and installed command move old stores before enrollment or update work:

- `~/.config/dotfleet/` moves to `~/.config/fleet/`.
- `~/dotfleet-backups/` moves to `~/fleet-backups/`.
- `~/.local/share/dotfleet/` moves to `~/.local/share/fleet/`.

Folders move intact; profiles keep their bytes, and old backups come along.
The registry becomes `~/.local/share/fleet/registry.toml`. The content manifest is `fleet.toml` in the content checkout.
Older `dotfleet.toml` content manifests still work. When both names exist, `fleet.toml` wins.

Fleet updates chezmoi's source pointer during migration. Rollback translates old backup paths to the new locations.
The review lists each store move under Profile changes. Cancelling later apply does not undo these earlier moves.
If both old and new stores exist, migration stops before moving folders. Resolve that conflict before retrying; Fleet never merges them silently.

## Check for updates

1. Run `fleet check` to fetch manager and content changes without applying them.
2. Read `fleet status` for the last result and its check time.
3. Run `fleet update` to fetch and apply changes. Run check again to refresh the saved result.

The `fleet` alias runs `dotfleet`. Use `dotfleet` directly if your content does not provide that alias.
Use `dotfleet status --check` to check and display status together.
Each fetch stops after ten seconds. Network or repository failures print a warning and return exit code 2.
A failed check preserves the previous result and its time.

Check and update use each checkout's configured upstream, including a branch other than the remote's default.
Both preserve local commits when the remote has no new commits. Both refuse local edits, missing upstreams, and histories with conflicting new commits.
Locally adopted content without a remote has no updates to fetch.
Update fetches again, so commits published after a check can also arrive.

Successful checks save both results in `~/.local/share/fleet/check.json` with owner-only permissions.
This cache is saved check data. It contains the check time, full commit identifiers, and source paths.
Check creates no backup or run record and never writes the profile or managed files.
Status shows “not checked” until a valid result exists. Plain status keeps working offline.

## Recorded machines

The fleet registry records machines in `~/.local/share/fleet/registry.toml`, inside the manager store. It is not content and never enters your dotfiles checkout.
Run `dotfleet machines` to read this machine's record and any other records in that file.
These are saved facts, not live results. Cross-machine queries and automatic registry transfer are deferred.

Each completed apply, update, restore, or backup refreshes this hostname's record. Other machine records stay intact.
The record contains hostname, operating system, domain, domain confirmation, content repository and commit, manager version, last run, and last backup.
It also records the home folder, RUN.json path, result, and whether this machine remains enrolled. Full commit IDs and paths stay in the file.
The hostname identifies the record. Choose unique hostnames before combining records from different machines.
Identity, tool pins, credentials, and secret values stay out of the registry. They remain in their existing private stores.
Read-only commands and cancellation before confirmation never refresh timestamps or create registry files.
Uninstall records the completed removal without deleting other machine records. Backups include the earlier registry file for manual recovery.
Automatic rollback does not restore that file. It refreshes this machine instead, preserving other machines recorded since the backup.

The schema, meaning the file's required structure, starts at version 1. The format is defined in [the decision record](docs/DESIGN.md).
The registry is private, with file mode 0600. Invalid registry data stops setup before apply.
Content cannot manage the registry or profile file. These belong to the manager.

## Add-on records

An add-on is an optional manager extension. Run `dotfleet addons` to list validated installed records. No add-ons ship with Fleet.
The [add-on contract](docs/ADDON-INTERFACE.md) defines installation declarations, allowed data access, output ownership, and safe teardown.
A test fixture validates the contract. It is not installed or offered as a product add-on.

Use `dotfleet machines export` to view this machine’s saved record alone.
Use `dotfleet machines export --output FILE` to save a private TOML copy for transfer. FILE must not exist.
TOML is the configuration format used by the registry. Export never prints its raw contents.
Use `dotfleet machines remove HOSTNAME` to remove another machine’s stale record. Fleet backs up the registry and records the change.
The active machine’s record cannot be removed this way.

Use `dotfleet profile` to inspect saved choices. Change them through `domain`, `pins`, `groups`, or `configure`; do not edit profile.toml.
The manager’s chezmoi configuration and converted source are internal files. Inspect their locations through `status` and their health through `doctor`.

## Terminal output

Python commands share the colors and section rule from `scripts/logger.sh`. Shell entry points use that logger directly.
Colors reinforce labels and change markers. Redirected output, `NO_COLOR`, and `TERM=dumb` disable colors.
Errors use the diagnostic output stream and return a failure code. Unexpected errors never print a traceback, Python’s technical call history.
When an apply run has a record, its technical error detail stays in private RUN.json.
