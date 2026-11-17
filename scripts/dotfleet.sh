#!/usr/bin/env bash
# Purpose: Run Fleet commands after enrollment.
# Usage: dotfleet COMMAND [ARGS]
# Parameters: COMMAND selects an action. Run dotfleet COMMAND --help for its arguments.
# Examples: dotfleet status; dotfleet update; dotfleet domain personal
# Exit codes: 0 success/help/cancel; 1 failed operation or updates available; 2 invalid arguments/check failure.
# Author: Jeremy Spofford / Aria Labs; Version: read from the manager VERSION file.
set -euo pipefail
manager="$HOME/.local/share/fleet/manager"
if [[ ! -d $manager && -d $HOME/.local/share/dotfleet/manager ]]; then
	manager="$HOME/.local/share/dotfleet/manager"
fi
local_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -f $local_root/scripts/command.py ]]; then manager=$local_root; fi
# shellcheck source=scripts/logger.sh
source "$manager/scripts/logger.sh"
usage() { python3 -B "$manager/scripts/command.py" --help; }
case ${1:---help} in
-h | --help) usage ;;
*)
	for argument in "$@"; do
		case $argument in
		-h | --help | --version)
			python3 -B "$manager/scripts/command.py" "$@"
			exit 0
			;;
		esac
	done
	work_dir=$(mktemp -d)
	trap 'rm -rf -- "$work_dir"' EXIT
	python3 -B "$manager/scripts/store_migration.py" --record "$work_dir/store-moves.json"
	export DOTFLEET_STORE_MOVES_FILE="$work_dir/store-moves.json"
	if [[ $manager == "$HOME/.local/share/dotfleet/"* ]]; then
		manager="$HOME/.local/share/fleet/${manager#"$HOME/.local/share/dotfleet/"}"
	fi
	python3 -B "$manager/scripts/command.py" "$@"
	;;
esac
