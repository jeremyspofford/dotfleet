#!/usr/bin/env bash
# Purpose: Run Dotfleet commands after enrollment.
# Usage: dotfleet COMMAND [ARGS]
# Parameters: COMMAND selects an action. Run dotfleet COMMAND --help for its arguments.
# Examples: dotfleet status; dotfleet update; dotfleet domain personal
# Exit codes: 0 success/help/cancel; 1 failed check or operation; 2 invalid arguments.
# Author: Jeremy Spofford / Aria Labs; Version: read from the manager VERSION file.
set -euo pipefail
manager="$HOME/.local/share/dotfleet/manager"
local_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -f $local_root/scripts/command.py ]]; then manager=$local_root; fi
# shellcheck source=scripts/logger.sh
source "$manager/scripts/logger.sh"
usage() { python3 "$manager/scripts/command.py" --help; }
case ${1:---help} in
-h | --help) usage ;;
*) python3 "$manager/scripts/command.py" "$@" ;;
esac
