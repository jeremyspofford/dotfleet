# Agent configuration

An agent is an installed coding assistant, such as Claude Code or Codex.
The content repository declares agent configuration in the `agents` table of `fleet.toml`. See [the content contract](CONTENT-CONTRACT.md#agents-table).
The catalog, `agents/manifest.toml`, lists each agent's detection commands and native paths. The profile saves which agents are on.
Fleet installs and starts no agent. Agent sign-in remains separate.

## Choose agents

`PATH` lists directories searched for commands. Detection checks PATH, installed Mise packages, and known configuration folders. Any match detects the agent.
Turn each detected agent on or off during enrollment. Press Enter to keep its current choice.
For scripted runs, use `--agents claude,codex` for detected agents. Use `--agents none` to skip.
Turning an agent off writes nothing new for it. Its earlier outputs stay for review.

## Deployment tree

Fleet deploys one payload per machine: the agent configuration in a Fleet-owned tree, `~/.local/share/fleet/agents`.
Updates change the tree. Each agent gets one pointer to the tree, set once:

| Agent | Pointer | Contents |
| --- | --- | --- |
| claude | `~/.claude/skills/fleet` links to the tree's plugin folder. `~/.claude/rules/fleet.md` links to its rule file. | Servers, hooks, and a link to `~/.agents/skills`. |
| gemini | `~/.gemini/extensions/fleet` links to the tree's extension folder. | Servers, hooks, and a `GEMINI.md` rule file. |
| codex | Skills: Codex reads `~/.agents/skills` itself. | Owned entries in `~/.codex/config.toml` and `~/.codex/hooks.json`. |
| opencode | Skills: OpenCode reads `~/.agents/skills` itself. Rules: one `instructions` entry names the tree's rule file. | Owned server entries in `~/.config/opencode/opencode.json`. |
| pi | Skills: Pi reads `~/.agents/skills` itself. | Owned server entries in `~/.pi/agent/mcp.json`. |

After the pointer, Fleet does not write inside an agent's own files.
Codex, OpenCode, and Pi accept no pointer for servers or hooks. They receive owned entries instead.
An owned entry is one named value that Fleet writes inside a file that the agent also writes.

Claude Code loads the plugin folder as `fleet@skills-dir`. Its servers appear as `plugin:fleet:NAME`.
A permission rule that names one of those tools must use the longer plugin name.
The tree belongs to Fleet. An update replaces hand edits there after the backup copies them.

## Skills

A skill is a folder of instructions for an agent. Its main file is `SKILL.md`.
Skills are ordinary content. Put each one at `.agents/skills/NAME/` in the content repository. chezmoi installs it at `~/.agents/skills`.
Codex, Gemini CLI, OpenCode, and Pi read that folder. Claude Code reads it through the plugin folder's link.
An `agents.skills.NAME` entry limits a skill to listed domains. The exception list can also turn one skill off on one machine.
Native chezmoi content installs every skill. Use `.chezmoiignore` there to limit skills.

To move skills from an earlier layout:

1. Move each skill folder from `agents/skills/` to `.agents/skills/` in the content repository.
2. Remove `agents/skills` from the manifest's `paths` list.
3. Commit, push, and run `fleet update`.

Earlier releases linked each skill into each agent's folder. The update removes each recorded link that still matches its record.

## Servers, connectors, rules, and hooks

Fleet renders each MCP server in the agent's own format. MCP means Model Context Protocol.
Variable names render in the agent's own reference form, such as `${NAME}` or `{env:NAME}`. Fleet never writes a secret value.
A connector with a `url` renders as an HTTP server. Next steps lists the agent's sign-in command.
A connector without a `url` appears in Next steps as a manual step.

Rule sets combine into one file per agent in the tree. Codex and Pi read no include file, so Fleet reports their rule sets as unsupported.
Whole instruction files, such as `AGENTS.md`, stay ordinary content. Install them under their real names.

Each hook runs a wrapper in the tree. The wrapper sets `FLEET_AGENT`, `FLEET_HOOK`, and `FLEET_HOOK_EVENT`, then runs a copy of the hook script.
Gemini CLI counts hook timeouts in milliseconds. Fleet converts seconds for it.
A tool class without a confirmed matcher for an agent skips that hook for that agent. The run account says so.
Codex skips a new or changed hook until the user trusts it in `/hooks`. Next steps lists that step.
OpenCode and Pi run hooks only through code plugins. Fleet reports their hooks as unsupported.

OpenCode merges `opencode.jsonc` after `opencode.json`. A same-name server there overrides Fleet's entry.
Fleet then skips that server for OpenCode and reports the override. The run continues.

## Per-machine exceptions

An item is one declared skill, rule set, server, connector, plugin, or hook. Its name is `KIND.NAME`, such as `mcp.docs`.
The profile keeps this machine's exception list under `agent_items`. Content never holds it.

1. Run `fleet agents list` to see each item, the agents and domains it reaches, and its state here.
2. Run `fleet agents set --name ITEM --state STATE`. The command saves the profile, then applies the change.

| State | Effect on this machine |
| --- | --- |
| `on` | Fleet manages the item. This removes the item's exception. |
| `off` | Fleet removes its output for the item and renders it no longer. |
| `local` | Fleet stops managing the item. The current value stays, and Fleet never reports it as drift. |

Skills accept `on` and `off` only; they are content files. Updates honor the list.

## Drift

Drift means a recorded output differs from its record or is missing.
`fleet status` counts agent drift in its Drift line. `fleet doctor` fails its managed-files check.
`fleet diff` prints one patch per changed output and lists each agent conflict. It exits 1.
The update gate treats agent drift as a drift repair trigger. A missing output returns; a changed output stops the update with its fix.
Outputs of an agent that is off never count as drift.

## Plugins

A plugin is a package that an agent installs to add skills, commands, hooks, or servers.
Fleet fully manages declared plugins. It runs each agent's own installer, driven by the inventory:

| Agent | Install | Remove |
| --- | --- | --- |
| claude | `claude plugin marketplace add MARKETPLACE`, then `claude plugin install ID --scope user` | `claude plugin uninstall ID --scope user` |
| codex | `codex plugin marketplace add MARKETPLACE`, then `codex plugin add ID` | `codex plugin remove ID` |
| gemini | `gemini extensions install ID --consent` | `gemini extensions uninstall NAME`, where NAME is the last part of ID |
| opencode | `opencode plugin ID --global` | Fleet removes ID from the `plugin` list in `opencode.json`; OpenCode has no uninstall command. |
| pi | `pi install ID` | `pi remove ID` |

Fleet's review is the consent step. Gemini CLI's own confirmation is skipped with `--consent`.
Claude Code still refuses a marketplace-declared command without a person's approval. That install then fails and names the command.
The run account lists plugin installs and removals under Software. Backups copy each agent's settings file first.
Fleet runs no other agent command to write settings.

An install runs when a plugin is declared for an agent that is on. Changing a plugin's `id` or `marketplace` removes the old install first.
A removed declaration or a domain change uninstalls the plugin. Turning an agent off keeps its plugins.
The `local` exception releases a plugin: it stays installed, and Fleet stops managing it.
A failed installer stops the run. The inventory records only completed installs and removals.

## Ownership, drift, and removal

`~/.local/state/dotfleet/agents.json` is the inventory. It lists each owned path, each owned entry, and the keys Fleet wrote.
Fleet compares only the keys that it wrote. Keys that an agent adds inside an entry never count as drift.

| Finding | Action |
| --- | --- |
| Nothing exists at the target | Create the output and record it. |
| An unrecorded value equals the rendered value | Record it without a write. |
| An unrecorded value differs | Conflict. Nothing is written. |
| A recorded output changed | Conflict. Nothing is written. |
| A recorded output is missing | Create it again. |

A conflict stops the run before backup. Fleet lists each item, its file, and the fix.
Keep the local value with `fleet agents set --name ITEM --state local`, or move the existing value aside. Then run the update again.

An output becomes stale when its declaration disappears or no longer selects this machine.
Fleet removes a stale output that still matches its record. A changed stale output stays, with one report line.
Fleet never deletes an agent folder recursively. It never deletes a shared settings file.

Shared files follow these rules:

1. Fleet parses the file first. It stops on invalid content or a link that leaves home.
2. Fleet changes only its own entries. It rewrites Codex's TOML table at the end of the file and keeps every other byte.
3. Fleet stops when a comment sits inside its own TOML table. Move the comment outside that table.
4. Fleet parses the result. It stops unless every other value equals its earlier value.
5. Fleet writes atomically and keeps the file's permissions.
6. Fleet refuses a file that content also manages. Two sources cannot own one output.

Uninstall removes owned paths and Fleet's entries. It keeps each shared file and every other value in it.
Restore returns Fleet's entries from the backup. It keeps values that the agent or user wrote after that backup.

To preview the plan from a separate Fleet checkout, run `bash scripts/generate-agent-configs.sh`. It writes nothing.

## Paths and limits

| Agent | Status |
| --- | --- |
| [claude](https://code.claude.com/docs/en/settings) | Rendered through the plugin folder. |
| [codex](https://learn.chatgpt.com/docs) | Rendered through owned entries. |
| [gemini](https://geminicli.com/docs/cli/skills/) | Rendered through the extension folder. |
| [opencode](https://opencode.ai/docs/config/) | Rendered through owned entries. |
| [pi](https://docs.pi.dev) | Rendered through owned entries. |
| [grok](https://docs.x.ai/build/settings) | Detected only; Fleet writes nothing. |
| [antigravity](https://antigravity.google/docs) | Detected only; Fleet writes nothing. |
| [Ollama](https://docs.ollama.com/faq) | Unsupported. |

Antigravity and Grok are detected only. Fleet renders nothing for either until a tested install confirms their paths.
Antigravity's command is `agy`. A community tool also installs a `grok` command; its `~/.grok/user-settings.json` file rules out Grok detection.
Pi detection also matches its renamed package, `@earendil-works/pi-coding-agent`.

Ollama is unsupported: it uses process/service environment settings, not a portable configuration file.
Service setup differs across Linux's systemd, macOS's launchctl, and Windows.
Fleet reports this limit; it invents no file or skill loader.

Paths cover Linux, macOS, and WSL (Windows Subsystem for Linux). WSL configures Linux agents, not Windows executables.
