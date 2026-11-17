# Code style

Use [WRITING.md](WRITING.md) for headers, help, comments, and documentation.

1. Start every script with its purpose and usage.
2. Give Bash scripts a `usage()` function. Connect it to `--help` and `-h`.
3. Use argparse for Python help. Argparse reads command arguments and prints help. Give each command a useful description.
4. Format shell scripts with shfmt and tabs. Format Python with Ruff, the repository’s Python formatter, at 100 columns.
5. Separate logical blocks with blank lines. Split long statements. Keep one action per line.
6. Explain reasons in comments. Do not repeat obvious code. Remove stale comments and commented-out code.
7. Keep functions small. Give each function one job. Put the main flow after its helpers.
8. End Python files with a main guard. This condition runs entry code only when the file is executed directly.
9. Use names that explain their purpose. Avoid abbreviations that a new reader must decode.

Use Prettier, the JavaScript formatter, for release helpers.

## Command parameters

Use flags for optional parameters and for values that a reader can confuse with each other.
Commands and action words stay positional. A positional argument is a value without a named flag.
The single trailing machine in status, check, diff, doctor, and update is the subject. Keep that machine name positional.
Tools set and remove take one tool name as their subject. Keep the version as a named flag.
Use --name and --target for SSH registration. One flag supplies both values unless the other flag gives a different value.
Help and examples show flags. Keep promised positional SSH-registration forms readable, but do not document them as the main interface.
Command groups require explicit action verbs. A bare group prints its action list.
Sort command names, action names, and flags alphabetically in help. Sort flags by their long name, ignoring leading hyphens.
Use raw-text help formatting. Preserve authored line breaks; put each exit code and example on its own line.
Top-level help ends after global options. Put command-specific details in that command’s help, and define shared terms in the README.
Support help, -h, and --help at each command and action. Support help COMMAND and both version and --version.
Status reads saved state without network requests. Check fetches updates. Keep their help and output distinct.

The command audit uses these forms:

| Command | Parameters |
| --- | --- |
| machines set-ssh | --name, --target; no flags opens the terminal picker |
| machines remove | --name |
| backups delete | Exactly one of --all, --keep, or --name |
| machines export | --output |
| domain list / set | set requires --name |
| groups list / set | set requires --name and --state |
| tools set | TOOL, --version |
| tools remove | TOOL |
| tools uninstall | TOOL, --version |
| backups restore | --backup; no flag lists backups |
| status, check, diff, doctor, update | One optional machine subject; diff also has --stat |
| topology | --json selects structured output |
| content | No value parameters |
| machines list, backups list, secrets list, tools list, addons list | No value parameters |
| backups create, tui, configure, version, uninstall | No value parameters |
| Installer and agent generator | Existing named flags |
| Converter | --checkout, --output, --profile, --format |

Every command and group action needs a reachable terminal-interface entry and a primary-output golden test.
Use the shared core for actions, reviews, records, and confirmations. List each entity in its own block; omit fields without values.
Internal bridges also label optional values, such as --backup and --machine. Function arguments are not command-line parameters.

## Preserve behavior

Run the same tests after a style change. Keep output, exit codes, and golden files unchanged.
Golden files store expected output. Add missing help without changing other behavior.
Record any file that needs a behavior change before it can meet these rules.

Imported helper modules may provide help only. Keep their existing behavior when called without help.
Sourced shell configuration is not a command. Do not add argument parsing that changes the caller’s shell.

Argument errors use the shared parser handler. Show the failing command’s usage, then name the problem and accepted arguments. Exit 2.
