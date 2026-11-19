#!/usr/bin/env bash
# Purpose: Show the planned agent outputs for this machine without writing.
# An agent is a coding assistant. The content's agents table declares its configuration.
# Usage: bash scripts/generate-agent-configs.sh [OPTIONS]
# Parameters: --home PATH selects a full home path. --dry-run is accepted; the script never writes.
# -h and --help print full help.
# Examples: bash scripts/generate-agent-configs.sh
# Exit codes: 0 success/help; 1 invalid content or conflict; 2 invalid arguments.
# Author: Jeremy Spofford / Aria Labs; Version: read from ../VERSION at runtime.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/logger.sh
source "$SCRIPT_DIR/logger.sh"
usage() {
	log_section "Agent configuration help"
	cat <<HELP
generate-agent-configs.sh — show planned agent outputs ($DOTFILES_VERSION)
Usage: bash scripts/generate-agent-configs.sh [OPTIONS]

An agent is a coding assistant. The content's agents table declares its configuration.
Read the saved profile and ~/dotfiles. Print each planned output, entry, removal, and conflict.
Write nothing. Run fleet update to review and apply the plan with a backup.
Requires Python 3.11+.

Options:
  --dry-run     Accepted for earlier scripts. The script never writes.
  -h, --help    Print full help.
  --home PATH   Full target home path; default: the current user's home.

Examples:
  bash scripts/generate-agent-configs.sh
  bash scripts/generate-agent-configs.sh --home /tmp/demo

Outputs are recorded in ~/.fleet/agents.json after an update applies them.
Author: Jeremy Spofford / Aria Labs; Version: $DOTFILES_VERSION (VERSION).
HELP
}
args=()
while (($#)); do
	case $1 in
	-h | --help)
		usage
		exit 0
		;;
	--dry-run)
		shift
		;;
	--home)
		if [[ $# -lt 2 || -z $2 || $2 == --* ]]; then
			log_error "Missing value for $1"
			usage >&2
			exit 2
		fi
		args+=("$1" "$2")
		shift 2
		;;
	*)
		log_error "Unknown argument: $1"
		usage >&2
		exit 2
		;;
	esac
done
if ! command -v python3 >/dev/null; then
	log_error "Python 3.11+ required."
	exit 1
fi
python3 "$SCRIPT_DIR/agent_deploy.py" "${args[@]}"
