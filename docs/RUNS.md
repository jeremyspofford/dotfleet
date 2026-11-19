# Run records

A run attempt starts when a change-making command begins. Read-only commands are queries and do not create attempt entries.

## Read attempts

1. Run `fleet logs list` for the newest attempts first. Use --limit N to cap the list.
2. Run `fleet logs show` for the latest attempt.
3. Add --name with a listed timestamp, entry identifier, or backup folder name to select one attempt.
4. Add --json to read the entry and saved RUN.json as JSON. JSON is a structured data format.

Applied attempts use the normal run review. Failures before apply show their stage and error, even without a profile or backup.
Each list line shows time, command, and result. Remote target attempts also show the target machine.
The terminal interface's Logs screen uses these same readers.

## Index storage

The index keeps one private JSON file per attempt under `~/.fleet/runs/`.
Entries contain time, command, machine, remote status, result, stage, error line, backup path, and detailed record path.
Each new entry prunes entries older than the newest 100. Entries never copy file contents or full command arguments.
Files use mode 0600; the index directory uses 0700. Only the owner can read these files.
Error lines keep at most 2,000 characters. Updates replace each entry atomically, without an append-only log.

Install, update, configure, and other change-making actions create entries. Nested installer calls share the original entry.
Target-side remote updates record attempts on the target. Read-only remote commands create no index entries.
An empty update records NO_CHANGE and creates no backup, RUN.json, or review.
Cancellation records CANCELLED without applying files. It leaves the small entry and moves this run's migrated stores back.
A RUNNING entry has no completion result yet. The process may still run or may have stopped unexpectedly.
The installer has a shell fallback when Python cannot start. If storage itself is unavailable, it reports that recording failed.

## Other retained records

| Store | Retention rule |
| --- | --- |
| `~/.fleet/backups/*/RUN.json` | Stays with its backup. Backups delete removes both. |
| Backup MANIFEST.json and CHEZMOI-DIFF.patch | Stay with the same backup. The patch can contain private values. |
| `~/.fleet/cleanup-runs/*/RUN.json` | New cleanup records prune all but the newest 100. |
| `~/.fleet/remote-runs/*/RUN.json` | New diagnostics prune all but the newest 100. Each output stream keeps at most 65,536 characters. |
| `~/.fleet/check.json` | One current cache; each successful check replaces it. |
| `~/.fleet/contacts.json` | One latest observation for each of the 100 most recently contacted machines; no contact history. |
| `~/.fleet/last-run.json` | One confirmed manager commit; each successful publication replaces it. |
| `~/.fleet/registry.toml` | One current record per saved machine; machines remove deletes stale records. |

Remote diagnostics keep raw shell failures off the terminal. They may be written for failed queries, without creating run-index entries.
Cleanup records stay outside backups, so deleting every backup still preserves the latest deletion review.
Deleting a backup never deletes its index entry immediately. Logs show reports when a linked review has gone.
The index does not duplicate backup contents. Backup retention remains an explicit owner choice through backups delete.
No other append-only or rotating application log exists.
