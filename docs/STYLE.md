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
Use --name and --target for SSH registration. One flag supplies both values unless the other flag gives a different value.
Help and examples show flags. Parse supported old forms without documenting them as the main interface.

The command audit uses these forms:

| Command | Parameters |
| --- | --- |
| machines set-ssh | --name, --target; no flags opens the terminal picker |
| machines remove | --name |
| machines export | --output |
| domain | --name; no flag lists choices |
| groups | --name, --state; no flags lists groups |
| pins set | --tool, --version |
| pins remove | --tool |
| rollback | --backup; no flag lists backups |
| status, check, diff, doctor, update | One optional machine subject; status also has --check and diff has --stat |
| topology | --json selects structured output |
| tui, profile, backups, secrets, content, addons, configure, backup, version, uninstall | No value parameters |
| Installer and agent generator | Existing named flags |
| Converter | --checkout, --output, --profile, --format |

Internal bridges also label optional values, such as --backup and --machine. Function arguments are not command-line parameters.

## Preserve behavior

Run the same tests after a style change. Keep output, exit codes, and golden files unchanged.
Golden files store expected output. Add missing help without changing other behavior.
Record any file that needs a behavior change before it can meet these rules.

Imported helper modules may provide help only. Keep their existing behavior when called without help.
Sourced shell configuration is not a command. Do not add argument parsing that changes the caller’s shell.
