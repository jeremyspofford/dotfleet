# Layer 1 decisions

Dotfleet is Layer 1: the manager for machine enrollment and later changes.
Dotfiles is Layer 0: independent configuration content. Content uses real home filenames and does not depend on Dotfleet.
Git transfers repositories. chezmoi applies managed files. Mise manages tool versions.

These five decisions are settled. The term v1 names the first product scope, not permission to release version 1.0.0.

## 1. Use a wizard and flags

The wizard guides interactive enrollment. Flags supply the same choices in scripted runs.
A profile stores the selected content, domain, identity, exact tool versions, groups, and agents.
A domain selects optional content. Groups select related files or command shortcuts; agents are coding assistants.
Missing choices must remain visible. Without a terminal, incomplete required choices stop the run instead of accepting silent defaults.
Review planned changes before interactive apply. Read decisions from the terminal, never piped script input.

## 2. Keep the registry in the manager store

The registry is `~/.local/share/dotfleet/fleet.toml`. A registry holds saved machine records; TOML is its structured file format.
Schema 1 uses a `machines` table keyed by hostname, the machine's name.
Each record stores hostname, operating system, domain, content repository and commit, manager version, last run, and last backup.
A commit identifies a saved Git version. The registry retains full commit identifiers and absolute backup paths.
The `schema_version` field is 1. Each machine table contains these fields:

- Strings: `hostname`, `os`, `domain`, `content_repo`, `content_commit`, `manager_version`, `last_run`, `last_backup`, `last_run_record`, and `result`.
- Booleans: `domain_chosen` and `enrolled`. These record explicit domain choice and whether the machine retains an installed profile.

`last_run` uses UTC time in ISO format. UTC is the common time reference; ISO format stores an unambiguous date and time.
Paths remain absolute. The registry does not store identity, pins, or credentials.
Use unique hostnames when combining records manually.

Backed-up apply, update, restore, and backup runs refresh this machine's record, including failures.
Read-only commands and cancellations do not update timestamps. Cancelling before confirmation leaves home unchanged.
`dotfleet machines` prints saved records already present in the file. It does not contact other machines or prove that their records are current.
Keep profile data and the registry out of the content repository. Machine synchronization and a live fleet view remain deferred.

## 3. Keep status local in v1

`dotfleet status` reports this machine only. It shows profile choices, checkout state, drift, and recent runs and backups.
A checkout is a local Git copy. Drift means managed files differ from the selected content.
The behind count compares content with the last fetched remote state. Status does not fetch another machine's information.
`dotfleet doctor` checks local readiness and names fixes. Missing domain provenance counts as unchosen until a user selects a domain.
Provenance records how a choice was made. Passing a domain explicitly records a human choice.

## 4. Let Dotfleet own releases

Dotfleet owns the release workflow, version file, changelog, policy script, and tests.
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
Keep Git, chezmoi, and Mise as the proven tools. Do not add a custom sync engine or background synchronization daemon.

Add-ons follow the [add-on contract](ADDON-INTERFACE.md). None ship with this version.
The contract defines declarations and ownership before an executable add-on interface is introduced.
