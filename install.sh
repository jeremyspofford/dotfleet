#!/usr/bin/env bash
# Purpose: Install Fleet and enroll or update a content checkout.
# Usage: bash install.sh [OPTIONS]
# Parameters: --repo chooses content; --configure asks again; --defaults skips prompts for complete profiles.
# --domain, --git-name, and --git-email supply profile choices. Repeat --pin TOOL=VERSION for full tool versions.
# --adopt keeps scanned home files. --group NAME=on|off selects groups. --agents chooses coding assistants.
# --format selects plain (real names) or chezmoi (native sources); -h/--help prints help.
# Example: bash install.sh --repo https://github.com/jeremyspofford/dotfiles
# Exit codes: 0 success/help; 1 setup failure; 2 invalid arguments.
# Author: Jeremy Spofford / Aria Labs; Version: read from VERSION.
set -euo pipefail
raw=https://raw.githubusercontent.com/jeremyspofford/fleet/main
manager_repo=https://github.com/jeremyspofford/fleet.git
script_dir=''
if [[ -n ${BASH_SOURCE[0]:-} && -f ${BASH_SOURCE[0]} ]]; then
	script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fi
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
if [[ -n $script_dir && -r $script_dir/scripts/logger.sh ]]; then
	logger=$script_dir/scripts/logger.sh
else
	command -v curl >/dev/null || {
		printf 'Missing required command: curl\n' >&2
		exit 1
	}
	mkdir "$work_dir/scripts"
	curl -fsSL "$raw/scripts/logger.sh" -o "$work_dir/scripts/logger.sh"
	curl -fsSL "$raw/VERSION" -o "$work_dir/VERSION"
	logger=$work_dir/scripts/logger.sh
fi
# shellcheck source=scripts/logger.sh
source "$logger"
export DOTFLEET_SUMMARY_MARKER="$work_dir/summary-reported"
export DOTFLEET_APPLY_MARKER="$work_dir/apply-started"
summary_expected=1
finish_run() {
	local result=$?
	trap - EXIT
	if [[ $summary_expected == 1 && ! -e $DOTFLEET_SUMMARY_MARKER ]]; then
		# No record exists yet. Do not invent an account of unknown operations.
		log_error "Setup stopped (exit $result) before the run renderer completed."
		if [[ -e $DOTFLEET_APPLY_MARKER ]]; then
			log_warn 'Inspect the latest ~/fleet-backups/ folder for RUN.json before retrying.'
		else
			log_info 'Run record: not created; setup stopped before apply.'
		fi
	fi
	rm -rf -- "$work_dir"
	exit "$result"
}
trap finish_run EXIT
usage() {
	log_section "Fleet help"
	log_heredoc '' <<EOF
Fleet $DOTFILES_VERSION manages home configuration with a saved machine profile.
Usage: bash install.sh [OPTIONS]
       curl -fsSL $raw/install.sh | bash
Options:
  --repo URL       Content Git repository; default: https://github.com/jeremyspofford/dotfiles.
                   Plain repositories use real names. Chezmoi repositories use native source names.
  --adopt          Create a local repository from existing home files. Exclude secret matches before committing.
  --configure      Run the enrollment wizard again. Review every choice before applying.
  --group NAME=on|off Include or exclude a content group. Repeat for more groups.
  --agents NAMES   Configure detected coding assistants, separated by commas; none skips this step.
  --defaults       Skip prompts only with a complete profile. New groups default to on.
                   This flag cannot bypass missing identity, domain confirmation, or floating pins.
  --domain VALUE   Record a domain choice: core, personal, work, or client (defined below).
  --git-name NAME  Record the Git author name.
  --git-email EMAIL Record the Git author email.
  --pin TOOL=VERSION Record a full tool version, such as node=24.14.1. Repeat for more tools.
  --format FORMAT  plain=real names; chezmoi=native sources. A manifest takes priority.
  -h, --help       Print full help and exit 0.
A profile saves the repository, domain, Git author identity, tool pins, and content groups.
Domains choose optional additions from your content repository. Identity and tools are chosen separately.
  core      Base configuration only.
  personal  Your own settings; bundled examples show your directory and list its entries.
  work      Employer settings; bundled examples show your directory and list its entries.
  client    Customer settings; bundled examples show your directory and list its entries.
Domain descriptions and command names come from your content. Empty overlays say they add nothing yet.
The domain prompt inspects the selected checkout and lists its domain command additions.
Custom repositories can define other behavior; check their documentation when additions are not described.
A new profile defaults to core. Later prompts show the domain saved in ~/.config/fleet/profile.toml.
Press Enter to confirm that domain, or type another domain name and press Enter.
The profile also saves Git identity, exact tool versions, and group choices. Mise tracks installed versions.
Groups select optional files or alias blocks. Each prompt defines on and off.
The wizard runs for new or incomplete profiles, unchosen domains, and --configure.
Steps: welcome, scan, source, domain, identity, pins, groups, agents, review, apply.
Source choices: adopt keeps existing files; defaults uses our examples; repo uses your Git URL.
Adoption scans root dotfiles and .config files. It excludes credential and shell-history names, secret patterns, binary files, and files above 2 MiB.
The scan cannot prove a file contains no secrets. Review the local repository before pushing it.
Review lists planned software, checkout, file, and profile changes, plus locations and next steps. Type confirm to apply, quit to cancel, or a step name to go back.
Old stores move to the Fleet paths before setup. Profiles and backup folders stay intact.
The review lists those moves. Cancelling apply keeps them. Other home changes wait for confirmation.
Temporary tool downloads stay outside your home.
Adopted file contents stay unchanged, including existing Mise files. Profile choices do not rewrite adopted files.
Pins use full versions. Enter accepts the prompt's stated action; skip leaves a choice incomplete.
Complete profiles need no profile prompts. Every summary prints all six categories, including scripted runs.
Empty categories say no changes. Paths start with ~ for your home. RUN.json keeps full paths and commit IDs.
Skipped gaps appear under Warnings. The last lines show status and the RUN.json location.
Without a terminal, missing identity, an unconfirmed domain, or floating pins stop setup with exit 1.
Supply the listed flags to complete those values. New group defaults are printed before applying files.
Prompts read /dev/tty, the terminal device, never piped input.
Headings, input, and warnings use the shared logger's colors. Choices also have text labels.
Set NO_COLOR to disable colors, even with an empty value. Redirected output and TERM=dumb remain plain.
Requires git, curl, tar, and Python 3.11+. Supports Linux, macOS, and WSL (Windows Subsystem for Linux).
Missing chezmoi and Mise go in ~/.local/bin. Setup never uses sudo or installs content-default tools.
Content lives in ~/dotfiles. Backups live in ~/fleet-backups/<timestamp>/.
The installer adds ~/.local/bin/dotfleet. Run dotfleet --help for updates, checks, rollback, and saved choices.
The manager checkout lives in ~/.local/share/fleet/manager. Software results include the installed command.
Review MANIFEST.json before restoring backup home/ files and recorded external targets.
Native chezmoi templates and hooks can execute code. Use only trusted content repositories.
Examples:
  bash install.sh --domain personal --git-name "Example Person" --git-email person@example.invalid --pin node=24.14.1
  bash install.sh --repo git@github.com:you/dotfiles.git --configure
Exit codes: 0 success/help; 1 setup failure; 2 invalid arguments.
Author: Jeremy Spofford / Aria Labs; Version: $DOTFILES_VERSION (VERSION).
EOF
}
arguments=("$@")
while [[ $# -gt 0 ]]; do
	case $1 in
	-h | --help)
		summary_expected=0
		usage
		exit 0
		;;
	--configure | --defaults | --adopt) shift ;;
	--repo | --format | --domain | --git-name | --git-email | --pin | --group | --agents)
		[[ $# -ge 2 && -n $2 && $2 != --* ]] || {
			usage >&2
			exit 2
		}
		shift 2
		;;
	*)
		usage >&2
		exit 2
		;;
	esac
done
require_commands git curl tar python3
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || error_exit 'Python 3.11 or newer is required.'
if [[ -z $script_dir ]]; then
	script_dir="$work_dir/manager"
	git clone "$manager_repo" "$script_dir" </dev/null
fi
python3 -B "$script_dir/scripts/store_migration.py" --record "$work_dir/store-moves.json"
export DOTFLEET_STORE_MOVES_FILE="$work_dir/store-moves.json"
if [[ $script_dir == "$HOME/.local/share/dotfleet/"* ]]; then
	script_dir="$HOME/.local/share/fleet/${script_dir#"$HOME/.local/share/dotfleet/"}"
fi
case $(uname -s) in
Linux)
	host=Linux
	if [[ -n ${WSL_DISTRO_NAME:-} ]] || { [[ -r /proc/version ]] && grep -qi microsoft /proc/version; }; then host=WSL; fi
	;;
Darwin) host=macOS ;;
*) error_exit 'Supported systems: Linux, macOS, or WSL.' ;;
esac
log_section "Fleet setup"
log_info "Host: $host / $(uname -m)"
export PATH="$HOME/.local/bin:$PATH"
umask 077
mkdir -p "$work_dir/bin"
if ! command -v chezmoi >/dev/null; then
	log_info 'Preparing chezmoi; it moves to ~/.local/bin only after confirmation'
	curl -fsSL https://get.chezmoi.io -o "$work_dir/chezmoi-install"
	sh "$work_dir/chezmoi-install" -b "$work_dir/bin" </dev/null
	export DOTFLEET_STAGED_CHEZMOI="$work_dir/bin/chezmoi"
fi
if ! command -v mise >/dev/null; then
	log_info 'Preparing Mise; it moves to ~/.local/bin only after confirmation'
	curl -fsSL https://mise.run -o "$work_dir/mise-install"
	HOME="$work_dir" MISE_INSTALL_PATH="$work_dir/bin/mise" sh "$work_dir/mise-install" </dev/null
	export DOTFLEET_STAGED_MISE="$work_dir/bin/mise"
fi
export PATH="$work_dir/bin:$PATH"
# Python uses the same logger style for output and terminal prompts.
python3 -u "$script_dir/scripts/manager.py" "${arguments[@]}" </dev/null
