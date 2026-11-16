#!/usr/bin/env bash
# Purpose: Install Dotfleet and enroll or update a content checkout.
# Usage: bash install.sh [OPTIONS]
# Parameters: --repo chooses content; --configure asks again; --defaults skips prompts for complete profiles.
# --domain, --git-name, and --git-email supply profile choices. Repeat --pin TOOL=VERSION for full tool versions.
# --format selects plain (real names) or chezmoi (native sources); -h/--help prints help.
# Example: bash install.sh --repo https://github.com/jeremyspofford/dotfiles
# Exit codes: 0 success/help; 1 setup failure; 2 invalid arguments.
# Author: Jeremy Spofford / Aria Labs; Version: read from VERSION.
set -euo pipefail
raw=https://raw.githubusercontent.com/jeremyspofford/dotfleet/main
manager_repo=https://github.com/jeremyspofford/dotfleet.git
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
usage() {
	log_heredoc '' <<EOF
Dotfleet $DOTFILES_VERSION manages home configuration with a saved machine profile.
Usage: bash install.sh [OPTIONS]
       curl -fsSL $raw/install.sh | bash
Options:
  --repo URL       Content Git repository; default: https://github.com/jeremyspofford/dotfiles.
                   Plain repositories use real names. Chezmoi repositories use native source names.
  --configure      Ask profile questions again. Enter keeps each displayed answer.
  --defaults       Skip prompts only with a complete profile. New groups default to on.
                   This flag cannot bypass missing identity, domain confirmation, or floating pins.
  --domain VALUE   Record a domain choice: core, personal, work, or client (defined below).
  --git-name NAME  Record the Git author name.
  --git-email EMAIL Record the Git author email.
  --pin TOOL=VERSION Record a full tool version, such as node=24.14.1. Repeat for more tools.
  --format FORMAT  plain=real names; chezmoi=native sources. A manifest takes priority.
  -h, --help       Print full help and exit 0.
A profile saves the repository, domain, Git author identity, tool pins, and content groups.
Domains: core=shared; personal=personal+shared; work=employer+shared; client=client+shared.
Groups select optional files or alias blocks. Each prompt defines on and off.
Plain reruns ask about missing identity, an unconfirmed domain, and floating pins such as latest.
Pins use full versions. Enter accepts the prompt's stated action; skip leaves a choice incomplete.
Complete profiles need no profile prompts. The summary lists skipped gaps under Warnings.
Without a terminal, missing identity, an unconfirmed domain, or floating pins stop setup with exit 1.
Supply the listed flags to complete those values. New group defaults are printed before applying files.
Prompts read /dev/tty, the terminal device, never piped input.
Requires git, curl, tar, and Python 3.11+. Supports Linux, macOS, and WSL (Windows Subsystem for Linux).
Missing chezmoi and Mise go in ~/.local/bin. Setup never uses sudo or installs content-default tools.
Content lives in ~/dotfiles. Backups live in ~/dotfleet-backups/<timestamp>/.
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
		usage
		exit 0
		;;
	--configure | --defaults) shift ;;
	--repo | --format | --domain | --git-name | --git-email | --pin)
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
case $(uname -s) in
Linux)
	host=Linux
	if [[ -n ${WSL_DISTRO_NAME:-} ]] || { [[ -r /proc/version ]] && grep -qi microsoft /proc/version; }; then host=WSL; fi
	;;
Darwin) host=macOS ;;
*) error_exit 'Supported systems: Linux, macOS, or WSL.' ;;
esac
log_info "Host: $host / $(uname -m)"
export PATH="$HOME/.local/bin:$PATH"
umask 077
mkdir -p "$HOME/.local/bin"
if ! command -v chezmoi >/dev/null; then
	log_info 'Installing chezmoi into ~/.local/bin'
	curl -fsSL https://get.chezmoi.io -o "$work_dir/chezmoi-install"
	sh "$work_dir/chezmoi-install" -b "$HOME/.local/bin" </dev/null
fi
if ! command -v mise >/dev/null; then
	log_info 'Installing Mise into ~/.local/bin'
	curl -fsSL https://mise.run -o "$work_dir/mise-install"
	MISE_INSTALL_PATH="$HOME/.local/bin/mise" sh "$work_dir/mise-install" </dev/null
fi
if [[ -z $script_dir ]]; then
	script_dir="$HOME/.local/share/dotfleet/manager"
	if [[ -e $script_dir ]]; then
		[[ -d $script_dir/.git && ! -L $script_dir ]] || error_exit 'Manager path is occupied by another directory.'
		remote=$(git -C "$script_dir" config --get remote.origin.url)
		[[ $remote == "$manager_repo" ]] || error_exit 'Manager checkout belongs to another repository.'
		[[ -z $(git -C "$script_dir" status --porcelain) ]] || error_exit 'Preserve manager checkout edits before updating.'
		git -C "$script_dir" pull --ff-only </dev/null
	else
		mkdir -p "$(dirname -- "$script_dir")"
		git clone "$manager_repo" "$script_dir" </dev/null
	fi
fi
python3 "$script_dir/scripts/manager.py" "${arguments[@]}" </dev/null 2>&1 | while IFS= read -r line; do log_info "$line"; done
