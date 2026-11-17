# Marketplaces

A marketplace is a Git repository of agent packages. An agent is an installed coding assistant.
A package bundles any mix of skills, rule sets, MCP servers, connectors, plugins, and hooks. MCP means Model Context Protocol.
Fleet consumes every marketplace through the same mechanism, including one that you publish yourself.

## Catalog format

The repository root holds `fleet-marketplace.toml`. TOML is a configuration file format.

```toml
schema = 1
description = "Example agent packages."

[packages.review-kit]
description = "Review commands and checklists."
version = "1.2.0"
split = true

[packages.review-kit.skills.pr-review]
description = "Review a pull request."
path = "review-kit/skills/pr-review"

[packages.review-kit.mcp.review-docs]
description = "Search review guides."
transport = "stdio"
command = "npx"
args = ["-y", "@example/review-docs"]
```

`schema` is the integer 1. `packages` declares at least one package. Unknown keys fail validation.
Each package needs `description`. `version` is a label for people. `split = true` lets a user take single features.
A package's `rules`, `mcp`, `connectors`, `plugins`, and `hooks` tables use the keys of the content's [agents table](CONTENT-CONTRACT.md#agents-table).
Paths are relative to the marketplace root, and Git must track each file.
A skill entry names a folder with `path`. That folder holds `SKILL.md`.
Package items cannot limit domains. The user's content defines domains.
An item name must be unique across the content and every installed package. A collision stops the run before any write.

## Commands

1. Run `fleet marketplaces add --source URL` to install every package. Use `--packages A,B` for named packages.
2. Use `--features PACKAGE/KIND.NAME` to take single features. Only packages with `split = true` allow that.
3. Use `--pin REF` to keep a tag or commit. The pin blocks updates, not information.
4. Run `fleet marketplaces list` to see each source, version, pin, installed packages, and saved update result.
5. Run `fleet marketplaces update` to move every unpinned marketplace to its newest commit.
6. Run `fleet marketplaces delete --name NAME` to remove a marketplace, its library copy, and its installed outputs.

`marketplaces update --name NAME` changes one marketplace. With `--name`, these flags also apply:

- `--pin REF` moves the pin to another tag or commit and checks it out.
- `--unpin` clears the pin and moves to the newest commit.
- `--all`, `--packages`, or `--features` replaces the selection.

A pinned marketplace stays at its pin. The update prints one line that names the pin. When nothing changes, the update applies nothing.

## Update results

`fleet check` fetches each marketplace into its library, with a ten-second limit, after the manager and content.
It prints how many commits each marketplace is behind. A pinned marketplace also says that the pin blocks the update.
Check exits 1 when the manager, the content, or an unpinned marketplace is behind. A pinned marketplace alone does not change the exit code.
A failed marketplace fetch prints a warning and exits 2. The earlier marketplace result stays saved.
`fleet status` and `fleet marketplaces list` read the saved result, `~/.local/share/fleet/marketplaces-check.json`. They never fetch.
Status describes a pinned marketplace that is behind as held at its pin.
`fleet update` does not move marketplaces. Run `fleet marketplaces update` for that.

`--name` saves the marketplace under another name. The default is the repository name.
Each change runs the shared review and apply. The backup runs first. Unattended runs never prompt.

## Where packages live

Pulled-in packages live in an untracked library, `~/.local/share/fleet/library/NAME`. No file enters your tracked content.
The record of each installation lives in the manager store, `~/.local/share/fleet/marketplaces.json`.
Fleet renders pulled-in items into the same agent tree as content items. See [agent configuration](AGENTS.md).
A pulled-in skill is copied into the tree and linked as `~/.agents/skills/NAME`.

A refresh replaces a pulled-in component. When you edited the copy, the run backup keeps your edited file.
The run account prints one line for each replaced edited copy.
The per-machine exception list applies to pulled-in items by name, as it applies to content items.
