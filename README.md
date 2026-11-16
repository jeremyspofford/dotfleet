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
First setup asks for a content repository, domain, author name/email, and content groups. Prompts read the terminal, never piped input.
Domains: core means shared settings; personal adds personal settings; work adds employer settings; client adds client settings.
Groups show their purpose and an on/off choice. On includes content; off excludes it. Press Enter to keep the displayed answer.
Later runs ask only about new groups. Use `--configure` to review all answers, or `--defaults` to accept saved/default answers without prompts.

```sh
bash ~/.local/share/dotfleet/manager/install.sh --configure
bash ~/.local/share/dotfleet/manager/install.sh --repo https://github.com/you/dotfiles
```

`--repo` chooses content. An unrelated `~/dotfiles` stops setup; preserve it before choosing another repository.
Read `--help` for all options and exit codes. Native chezmoi templates and hooks can execute code; use trusted content only.

## How it works

[dotfiles](https://github.com/jeremyspofford/dotfiles) provides plain content with real home names. It works without Dotfleet.
The [content contract](docs/CONTENT-CONTRACT.md) defines files, formats, groups, secrets, and ownership.
Dotfleet converts plain files into a private chezmoi source. Native chezmoi repositories use their checkout directly.
Stow layouts under `packages/<package>/` also work; their package prefixes do not reach the home directory.

Your profile lives at `~/.config/dotfleet/profile.toml`. Edit pins there, then rerun the installer.
Content defaults fill gaps only when the requested versions are installed. The summary lists other defaults as available, without installing them.
The manager creates Git identity, domain, Mise settings, and a private secrets file. Keep secret references in the profile, never values.
A reference such as `op://Vault/Item/field` names a secret in 1Password. References are disabled unless explicitly enabled.

Old Layer 0 enrollment migrates automatically. Setup reads old chezmoi data, rendered Mise pins, and saved installer data before applying files.
Existing Dotfleet profiles remain authoritative. Backups preserve replaced files and prior staged sources.
Conflict menus explain letter keys and require Enter. Skipping changes can leave differences; the summary reports that as incomplete enrollment.

1. Edit files in `~/dotfiles` or edit your profile.
2. Commit content changes before updating. Setup refuses a checkout with uncommitted edits.
3. Rerun the one-liner to convert and apply.
4. Run `chezmoi status` to check for differences.

To roll back, restore saved `home/` files and external targets from the backup. Remove only new paths marked absent.
Restore the saved profile too. Keep the backup and rerun setup when you want to regenerate files.

## Agents, add-ons, and releases

An agent is a coding assistant. The optional [agent generator](docs/AGENTS.md) writes settings and links content skills; it installs no agents.
The [add-on contract](docs/ADDON-INTERFACE.md) remains a proposal. No terminal interface, add-on runtime, custom sync engine, or daemon exists here.
`VERSION` starts at 0.1.0. Release automation stays in 0.x; only the owner can approve a manual 1.0.0 release.
Read [release rules](docs/RELEASES.md) and [writing rules](docs/WRITING.md).

## Checks

CI means automated change checks. It runs shell checks, formatting, release-policy tests, and container enrollment tests.
Container tests cover plain content, the private Stow archive, and a native chezmoi fixture.
They check repeat runs, shell startup, group changes, saved profiles, and clean status.
