#!/usr/bin/env bash
# Purpose: Run Fleet commands after enrollment.
# Usage: dotfleet COMMAND [ARGS]
# Parameters: COMMAND selects an action. Run dotfleet COMMAND --help for its arguments.
# Examples: dotfleet status; dotfleet update; dotfleet domain set --name personal
# Exit codes: 0 success/help/cancel; 1 failed operation or updates available; 2 invalid arguments/check failure.
# Author: Jeremy Spofford / Aria Labs; Version: read from the manager VERSION file.
set -euo pipefail
# A deploy can install Fleet's own Python when the system Python is absent or too old.
# Store migration may move that Python from an older store into ~/.fleet, so list both.
if [[ -x $HOME/.fleet/python/bin/python3 || -x $HOME/.local/share/fleet/python/bin/python3 ]]; then
	PATH="$HOME/.fleet/python/bin:$HOME/.local/share/fleet/python/bin:$PATH"
fi
manager="$HOME/.fleet/manager"
for old in .local/share/fleet .local/share/dotfleet; do
	if [[ ! -d $manager && -d $HOME/$old/manager ]]; then manager="$HOME/$old/manager"; fi
done
local_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -f $local_root/scripts/command.py ]]; then manager=$local_root; fi
# shellcheck source=scripts/logger.sh
source "$manager/scripts/logger.sh"
usage() { python3 -B "$manager/scripts/command.py" --help; }
case ${1:---help} in
-h | --help) usage ;;
help) python3 -B "$manager/scripts/command.py" "$@" ;;
--version) python3 -B "$manager/scripts/command.py" "$@" ;;
*)
	for argument in "$@"; do
		case $argument in
		-h | --help)
			python3 -B "$manager/scripts/command.py" "$@"
			exit 0
			;;
		esac
	done
	work_dir=$(mktemp -d)
	trap 'rm -rf -- "$work_dir"' EXIT
	python3 -B "$manager/scripts/store_migration.py" --record "$work_dir/store-moves.json"
	export DOTFLEET_STORE_MOVES_FILE="$work_dir/store-moves.json"
	# Commands run from the manager's new place; forget Python paths cached before the move.
	hash -r
	for old in .local/share/fleet .local/share/dotfleet; do
		if [[ $manager == "$HOME/$old/"* ]]; then manager="$HOME/.fleet/${manager#"$HOME/$old/"}"; fi
	done
	python3 -B "$manager/scripts/command.py" "$@"
	;;
esac
