# Dotfleet

Dotfleet manages home configuration across machines. A profile saves each machine's repository, domain, Git identity, tool pins, and content groups.
Git records history. chezmoi applies files. Mise selects developer tool versions. A pin selects a version; a group selects optional content.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/dotfleet/main/install.sh | bash
```

- Setup backs up existing files and keeps your editable content at `~/dotfiles`.
- Re-run the same command to update. Saved selections remain unchanged.
- Backups live in `~/dotfleet-backups/<timestamp>/`. Review `MANIFEST.json` before restoring `home/` files and external targets.

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
8. Select detected agents, which are coding assistants. Choose names or `none` to skip configuration.
9. Review all choices, the backup location, and the number of changed files. Type `confirm`, `quit`, or a step name to return.
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
It excludes credential and shell-history filenames, detected secret patterns, binary files, and files above 2 MiB. Excluded paths appear in review and summary.
The scan cannot prove that remaining files contain no secrets. Review your local repository before pushing it.
Adopted files keep their exact contents, including existing Mise files. Profile choices do not rewrite adopted files.
Only accepted files enter the first local Git commit. Nothing is uploaded.

1. Review `~/dotfiles` and its Git history.
2. Create an empty repository on your chosen Git host.
3. Run `cd ~/dotfiles && git remote add origin URL`, replacing URL with that repository's address.
4. Run `git push -u origin main` when ready.

An unrelated `~/dotfiles` stops setup. Preserve it before choosing another repository or adopting files.
Native chezmoi templates and hooks can execute code. Use trusted content only.
Quitting review leaves home unchanged. Preparation uses temporary files outside home; missing tools move into `~/.local/bin` only after confirmation.

### Flags and updates

```sh
curl -fsSL https://raw.githubusercontent.com/jeremyspofford/dotfleet/main/install.sh | bash -s -- --configure
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

## How it works

[dotfiles](https://github.com/jeremyspofford/dotfiles) provides plain content with real home names. It works without Dotfleet.
The [content contract](docs/CONTENT-CONTRACT.md) defines files, formats, groups, secrets, and ownership.
Dotfleet converts plain files into a private chezmoi source. Native chezmoi repositories use their checkout directly.
Stow layouts under `packages/<package>/` also work; their package prefixes do not reach the home directory.

Your profile lives at `~/.config/dotfleet/profile.toml`. It saves the domain, Git identity, exact tool versions, and group choices between runs.
Edit pins there, then rerun the installer. Mise records which versions are installed; Dotfleet reads that list during setup.
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

`domain_chosen` records a human choice in this wizard or through `--domain`. Carried and defaulted domains remain unchosen until confirmed.
`unpinned_tools` records skipped tools, so content defaults cannot select them later. Remove a tool there to allow its defaults again.
Content defaults fill gaps only when the requested versions are installed. The summary lists other defaults as available, without installing them.
The manager creates Git identity, domain, Mise settings, and a private secrets file. Keep secret references in the profile, never values.
A reference such as `op://Vault/Item/field` names a secret in 1Password. References are disabled unless explicitly enabled.

Old Layer 0 enrollment migrates automatically. Setup reads old chezmoi data, rendered Mise pins, and saved installer data before applying files.
Migration also saves existing Mise settings in the profile. Existing Dotfleet profiles remain authoritative. Backups preserve replaced files and prior staged sources.
Conflict menus explain letter keys and require Enter. Skipping changes can leave differences; the summary reports that as incomplete enrollment.

1. Edit files in `~/dotfiles` or edit your profile.
2. Commit content changes before updating. Setup refuses a checkout with uncommitted edits.
3. Rerun the one-liner to convert and apply.
4. Run `chezmoi status` to check for differences.

To roll back, restore saved `home/` files and external targets from the backup. Remove only new paths marked absent.
Restore the saved profile too. Keep the backup and rerun setup when you want to regenerate files.

## Agents, add-ons, and releases

An agent is a coding assistant. The optional [agent generator](docs/AGENTS.md) writes settings and links content skills; it installs no agents.
The [add-on contract](docs/ADDON-INTERFACE.md) remains a proposal. The enrollment wizard uses terminal prompts. No full-screen interface, add-on runtime, custom sync engine, or daemon exists here.
`VERSION` starts at 0.1.0. Release automation stays in 0.x; only the owner can approve a manual 1.0.0 release.
Read [release rules](docs/RELEASES.md) and [writing rules](docs/WRITING.md).

## Checks

CI means automated change checks. It runs shell checks, formatting, release-policy tests, and container enrollment tests.
Container tests cover plain content, the private Stow archive, and a native chezmoi fixture.
They check adoption, secret exclusions, review cancellation, repeat runs, shell startup, group changes, saved profiles, and clean status.
