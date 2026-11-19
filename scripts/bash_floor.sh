# shellcheck shell=sh
# Purpose: Find or install the modern bash that Fleet's scripts need.
# Usage: source this file, then call fleet_ensure_bash yes|no; it prints the bash path.
# This is the bootstrap layer. It runs before any upgrade, so it stays POSIX sh and bash 3.2 safe.
# No arrays, no mapfile, and no [[ ]]. See docs/STYLE.md.
# Exit codes: fleet_ensure_bash returns 0 with a path, or 1 after a one-line reason.
# Author: Jeremy Spofford / Aria Labs; Version: read from VERSION.

# Homebrew installs into one of these prefixes. Tests name their own prefixes instead.
fleet_brew_prefixes=${FLEET_BREW_PREFIXES:-/opt/homebrew /usr/local /home/linuxbrew/.linuxbrew}
# shellcheck disable=SC2016 # The user runs this text; it must stay unexpanded here.
fleet_homebrew_command='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

# Print MAJOR.MINOR for the bash at $1. Print nothing when $1 is not a bash.
fleet_bash_version() {
	# shellcheck disable=SC2016 # The candidate bash expands its own version variables.
	"$1" -c 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"' 2>/dev/null </dev/null
}

# Succeed when the bash at $1 is 4.4 or newer.
fleet_bash_modern() {
	[ -n "$1" ] && [ -x "$1" ] || return 1
	fleet_version=$(fleet_bash_version "$1") || return 1
	fleet_major=${fleet_version%%.*}
	fleet_minor=${fleet_version#*.}
	case $fleet_major.$fleet_minor in
	*[!0-9.]* | .* | *.) return 1 ;;
	esac
	[ "$fleet_major" -gt 4 ] || { [ "$fleet_major" -eq 4 ] && [ "$fleet_minor" -ge 4 ]; }
}

# Print the Homebrew command by path. Over SSH, brew is often missing from PATH.
fleet_find_brew() {
	fleet_found=$(command -v brew 2>/dev/null) || fleet_found=''
	if [ -n "$fleet_found" ] && [ -x "$fleet_found" ]; then
		echo "$fleet_found"
		return 0
	fi
	for fleet_prefix in $fleet_brew_prefixes; do
		if [ -x "$fleet_prefix/bin/brew" ]; then
			echo "$fleet_prefix/bin/brew"
			return 0
		fi
	done
	return 1
}

# Print the first bash 4.4 or newer that Fleet can name by path.
fleet_find_bash() {
	fleet_path_bash=$(command -v bash 2>/dev/null) || fleet_path_bash=''
	for fleet_candidate in "${FLEET_BASH:-}" "$fleet_path_bash"; do
		if fleet_bash_modern "$fleet_candidate"; then
			echo "$fleet_candidate"
			return 0
		fi
	done
	for fleet_prefix in $fleet_brew_prefixes; do
		if fleet_bash_modern "$fleet_prefix/bin/bash"; then
			echo "$fleet_prefix/bin/bash"
			return 0
		fi
	done
	# A Homebrew in another prefix keeps its bash beside its own bin folder.
	if fleet_brew=$(fleet_find_brew); then
		fleet_candidate="$("$fleet_brew" --prefix 2>/dev/null </dev/null)/bin/bash"
		if fleet_bash_modern "$fleet_candidate"; then
			echo "$fleet_candidate"
			return 0
		fi
	fi
	return 1
}

# Ask on the terminal. Succeed only when the user types yes.
fleet_ask_bash() {
	while :; do
		printf '  yes   Install bash with Homebrew now, then continue.\n' >&2
		printf '  stop  Stop now. Nothing is installed.\n' >&2
		printf 'Type yes or stop, then press Enter. Enter alone chooses stop.\n' >&2
		printf 'Install bash [stop]: ' >&2
		IFS= read -r fleet_answer </dev/tty || return 1
		case $fleet_answer in
		yes) return 0 ;;
		stop | '') return 1 ;;
		esac
		printf 'Type yes or stop.\n' >&2
	done
}

# Print a bash 4.4 or newer by path. Install one with Homebrew after consent.
# $1 is yes when --update-bash already gave that consent.
fleet_ensure_bash() {
	if fleet_find_bash; then return 0; fi
	fleet_current=${BASH:-$(command -v bash 2>/dev/null)}
	fleet_old=$(fleet_bash_version "${fleet_current:-bash}") || fleet_old=''
	printf "Found bash %s at %s. Fleet's scripts need bash 4.4 or newer.\n" \
		"${fleet_old:-none}" "${fleet_current:-no path}" >&2
	if ! fleet_brew=$(fleet_find_brew); then
		printf 'Fleet installs bash with Homebrew, and Homebrew is missing. Nothing was installed.\n' >&2
		printf 'Install Homebrew with this command, then run Fleet again:\n  %s\n' "$fleet_homebrew_command" >&2
		printf 'Fleet does not install Homebrew: its installer needs admin rights, and Fleet uses none.\n' >&2
		return 1
	fi
	printf 'Fleet will install bash with Homebrew: %s install bash.\n' "$fleet_brew" >&2
	if [ "${1:-no}" != yes ]; then
		# A subshell keeps a failed /dev/tty open from ending a POSIX sh caller.
		if [ "${FLEET_REMOTE_RUN:-}" = 1 ] || ! (: </dev/tty) 2>/dev/null; then
			printf 'No terminal is attached, so Fleet cannot ask first. Nothing was installed.\n' >&2
			printf 'Run again in a terminal, or add --update-bash to accept the bash install.\n' >&2
			return 1
		fi
		if ! fleet_ask_bash; then
			printf 'Stopped. Nothing was installed.\n' >&2
			return 1
		fi
	fi
	if ! "$fleet_brew" install bash </dev/null >&2; then
		printf 'Homebrew could not install bash. Fix the error above, then run Fleet again.\n' >&2
		return 1
	fi
	if fleet_find_bash; then return 0; fi
	printf 'Homebrew finished, but Fleet found no bash 4.4 or newer.\n' >&2
	return 1
}

# Help runs only when this file is executed directly, never when sourced or sent to a probe.
case ${0##*/} in
bash_floor.sh)
	fleet_usage() {
		cat <<'USAGE'
bash_floor.sh — find or install the modern bash that Fleet's scripts need
Usage: . scripts/bash_floor.sh; fleet_ensure_bash yes|no
       bash scripts/bash_floor.sh --help

Fleet's scripts need bash 4.4 or newer. macOS ships bash 3.2.
This file is the bootstrap layer: it runs in POSIX sh and in bash 3.2.
fleet_find_bash prints the first bash 4.4 or newer that Fleet can name by path.
It checks FLEET_BASH, the bash on PATH, and each Homebrew prefix.
fleet_ensure_bash asks on the terminal before it installs bash with Homebrew.
Pass yes to accept without asking; installer and deploy runs pass yes for --update-bash.
Without a terminal and without yes, it stops and installs nothing.
Without Homebrew, it stops and names the Homebrew install command.
Fleet does not install Homebrew: its installer needs admin rights, and Fleet uses none.

Parameters:
  -h, --help  Print full help.
  No other command-line arguments are accepted.

Environment:
  FLEET_BASH           A bash path to try first.
  FLEET_BREW_PREFIXES  Homebrew prefixes to search, separated by spaces.

Example:
  . scripts/bash_floor.sh && modern=$(fleet_ensure_bash no) && exec "$modern" install.sh
USAGE
	}
	if [ $# = 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
		fleet_usage
		exit 0
	fi
	printf 'Source bash_floor.sh for its functions, or run it with --help.\n' >&2
	fleet_usage >&2
	exit 2
	;;
esac
