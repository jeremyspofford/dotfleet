# Layer 2 agent module

Status: draft for owner review. This document proposes a design. It changes no code, tests, or existing documents.
A pull request (PR) is a proposed repository change. Do not merge this PR before the owner rules on the open questions.

Fleet is Layer 1: the manager for machine enrollment and later changes. Dotfiles is Layer 0: independent configuration content.
Layer 2 adds shared modules that the manager applies. The agent module is the first Layer 2 build.
This draft builds the module into the manager, as an extension of today's generator. Open question 1 asks the owner to confirm.

## Terms

- An agent is an installed coding assistant, such as Claude Code or Codex.
- A skill is a folder of instructions for an agent. Its main file is `SKILL.md`.
- MCP means Model Context Protocol, a standard that lets an agent call external tools.
- An MCP server is a program or web service that offers those tools. In this document, a server means an MCP server.
- A plugin is a package that an agent installs to add skills, commands, hooks, or servers.
- An extension is Gemini CLI's plugin type.
- A connector is a hosted service that an agent reaches after the user signs in.
- A hook is a command that an agent runs when a named event occurs.
- The manifest is `fleet.toml` in the content repository. TOML is a configuration file format.
- The profile is `~/.config/fleet/profile.toml`. It saves this machine's choices.
- The catalog is `agents/manifest.toml` in the manager. It lists each agent's detection commands, formats, and paths.
- The inventory is `~/.local/state/dotfleet/agents.json`. It records every output that Fleet rendered.
- A fingerprint records a file's content hash and permissions, or a link's target.
- Drift means a rendered output differs from its record or is missing.
- A conflict is a planned write that would replace a value that Fleet did not write.
- A domain selects optional content: core, personal, work, or client. Each machine has one domain.
- A group selects optional content that one machine can turn on or off.
- chezmoi is the tool that applies managed files. Mise is the tool that selects tool versions.
- The wizard is the guided enrollment that runs in a terminal.
- The run account is the report that a change-making run prints and saves.
- The update gate is the check that `fleet update` runs before any backup.
- The builder is whoever implements this design.
- The test machine is the Linux machine that the author used for the tests in Appendix B.

## Purpose

Each agent keeps its settings in its own files and formats.
A user who changes agents must rebuild skills, servers, plugins, and hooks by hand.
This module keeps one store of agent configuration in a neutral format. A neutral format names no agent's file layout.
Fleet renders that store. To render means to write an agent's native files from the neutral declarations.
A user can then switch agents and keep the same setup.

The module does not manage models, permissions, credentials, project settings, or agent installation.

## Rules this design honors

The owner settled three rules for this module:

1. Dependencies point one way. Content declares configuration. The profile records machine-local selections. Profile data never enters the content repository.
2. Generated files are listed and regenerable. Fleet leaves no orphan: a generated file that no record lists.
3. Fleet never depends on Nova, the owner's separate agent project. Nova can consume the same files later.

The module also keeps these existing manager rules:

- Review planned changes before interactive apply. Back up every target before replacement.
- Never choose overwrite for an unattended conflict.
- Declare secret references and names, never credentials.
- Install and start no agent. Agent login remains separate. Open question 8 asks whether to keep this rule.
- Add no sync engine and no background service.

## Current state

The manager already ships a small agent generator. See [the agent guide](AGENTS.md).

- The catalog names seven agents and their home paths. It marks Ollama as unsupported.
- Wizard step 8 turns each detected agent on or off. The profile saves those choices under `agents`.
- The generator writes one settings file per agent and one link per skill.
- Each settings file holds minimal valid content: `{}` or one comment line.
- The inventory records each output's hash or link target.
- Unknown or modified outputs stop generation before any write.
- Outputs for a deselected agent stay in place. The generator prints one progress line for each.

Five limits block a shared store:

1. Settings carry no servers, plugins, connectors, or hooks.
2. The generator owns whole files. An existing hand-written settings file stops setup.
3. The catalog fixes the skills folder name. Content cannot declare it.
4. Status, doctor, and the update gate read chezmoi status only. They do not see agent drift.
5. Uninstall deletes every path that the inventory lists. Rollback rewrites each saved file whole.

Limit 2 matters most. On the test machine, four of five installed agents already had that settings file.
The agent or the user wrote each one. Fleet cannot own those files whole.
Limit 5 makes whole-file records dangerous for those files. Section 4 sets the rules for uninstall and rollback.

## 1. Contract addition

The content repository declares agent configuration in its manifest, under one new table: `agents`.
This draft places the table in `fleet.toml`. Open question 2 asks the owner to confirm.
The table is optional. Without it, Fleet links skills from `agents/skills`, as it does today, and renders nothing else.
Today's manager ignores an unknown table. Content can therefore add `agents` before every machine updates.
Authored files stay in the repository: skill folders and hook scripts.

### Table layout

| Table or key | Holds |
| --- | --- |
| `agents.schema` | The integer 1. Fleet rejects a number that it does not know. |
| `agents.skills_folder` | The canonical skills folder, relative to the repository root. |
| `agents.skills.NAME` | Optional domain limits for one skill. |
| `agents.mcp.NAME` | One MCP server. |
| `agents.connectors.NAME` | One connector. |
| `agents.plugins.NAME` | One plugin, with one source per agent. |
| `agents.hooks.NAME` | One hook. |

Names use lowercase letters, digits, and single hyphens. A name is unique within its table.
Unknown keys fail validation. A new key needs a new schema number.

### Shared keys

Servers, connectors, plugins, and hooks accept these keys:

| Key | Required | Meaning |
| --- | --- | --- |
| `description` | Yes | One plain sentence. The wizard and reports show it. |
| `agents` | No | Agent names that receive the item. Default: every selected agent that supports it. |
| `domains` | No | Machine domains that receive the item. Default: every domain. |

A machine receives an item when the list holds the machine's own domain.
`core` has no special meaning in this list. An item without `domains` reaches every machine.

### Skills

`skills_folder` names one folder. Each direct child folder that holds `SKILL.md` is one skill.
The folder name is the skill name. Skills follow the [Agent Skills specification](https://agentskills.io/specification).
That specification requires a `name` and a `description` at the top of `SKILL.md`. The name must match the folder name.
Fleet checks the folder name and the file's presence. Each agent validates the rest.
Fleet links skill folders. It never rewrites a skill.
A skill needs no manifest entry. Add `agents.skills.NAME` only to limit its domains.
A skill entry accepts `domains` only. A skill reaches every agent that reads its folder, so `agents` cannot limit it.
This draft keeps today's layout: a repository folder that Fleet links. Open question 3 offers two other layouts.

### MCP servers

| Key | Applies to | Meaning |
| --- | --- | --- |
| `transport` | All | `stdio` starts a local program. `http` calls a web address. |
| `command` | `stdio` | The program to start. |
| `args` | `stdio` | A list of arguments. |
| `env` | `stdio` | Variable names with plain, non-secret values. |
| `pass_env` | `stdio` | Names of variables that the server reads from the user's environment. |
| `url` | `http` | The server address. |
| `headers` | `http` | Header names with plain, non-secret values. |
| `header_env` | `http` | Header names mapped to variable names. |
| `bearer_token_env` | `http` | The name of the variable that holds a bearer token. |

A bearer token is a secret string that proves access. The manifest holds variable names, never secret values.
`pass_env`, `header_env`, and `bearer_token_env` name variables in the user's environment.
The existing `secrets` table can supply those variables through `~/.secrets`.
Fleet renders each name as the agent's own variable reference. Fleet never writes the value.
Fleet scans `env` and `headers` values with the secret patterns from the wizard's home scan. A match fails validation.
Schema 1 omits the older Server-Sent Events transport. Pi rejects that transport.

### Connectors

A connector entry names a hosted service and its optional `url`.
With a `url`, Fleet renders the address as an HTTP server without credentials. The user signs in through the agent.
Without a `url`, Fleet cannot render the connector. Fleet reports it as a manual step.
Each agent keeps the sign-in session in its own store. Fleet never reads that store.

### Plugins

No shared plugin format exists. Each agent installs its own package type.
A plugin entry is a cross-reference: one name, one description, and one source per agent.
`sources` maps an agent name to an `id` and an optional `marketplace`.
A marketplace is a catalog that an agent installs plugins from.
Fleet treats both values as opaque text in that agent's own notation.
This draft only reports plugins. Open question 7 asks how far Fleet should go.

### Hooks

| Key | Meaning |
| --- | --- |
| `event` | When the hook runs. Schema 1 defines six events. |
| `tools` | Optional tool classes for tool events: `shell`, `edit`, `read`, or `mcp`. Default: all tools. |
| `command` | An executable file in the repository, relative to its root. |
| `args` | An optional list of arguments. |
| `timeout` | An optional time limit in seconds. |

The six events are `session-start`, `session-end`, `prompt-submit`, `before-tool`, `after-tool`, and `stop`.
The catalog maps each event and tool class to an agent's native name.
Each agent sends its own event data and reads its own exit codes. Fleet translates neither.
Fleet sets three variables for the command: `FLEET_AGENT`, `FLEET_HOOK`, and `FLEET_HOOK_EVENT`.
They hold the agent name, the item name, and the neutral event. One script can then serve several agents.
The builder chooses how to set them: a command prefix or a small generated wrapper file.
An agent that lacks an event skips that hook. Fleet reports the skip.
Hooks are the least portable part. Start with hooks that only read event data.
Hooks and `stdio` servers run commands from content. Use only trusted repositories.
The content contract gives the same warning for templates.

### Complete example

```toml
[agents]
schema = 1
skills_folder = "agents/skills"

[agents.skills.release-notes]
domains = ["work", "client"]

[agents.mcp.docs-search]
description = "Search the team documentation."
transport = "stdio"
command = "npx"
args = ["-y", "@example/docs-mcp"]
env = { DOCS_REGION = "us" }
pass_env = ["DOCS_API_KEY"]

[agents.mcp.issues]
description = "Read and update work issues."
transport = "http"
url = "https://mcp.example.com/issues"
bearer_token_env = "ISSUES_TOKEN"
domains = ["work", "client"]

[agents.connectors.calendar]
description = "Read the calendar after sign-in."
url = "https://mcp.example.com/calendar"
agents = ["claude", "codex"]

[agents.plugins.review-kit]
description = "Review commands and checklists."
domains = ["personal"]

[agents.plugins.review-kit.sources]
claude = { id = "review-kit@example-market", marketplace = "example/agent-plugins" }
codex = { id = "review-kit@example-market", marketplace = "example/agent-plugins" }
gemini = { id = "https://github.com/example/review-kit" }

[agents.hooks.guard-shell]
description = "Stop destructive shell commands."
event = "before-tool"
tools = ["shell"]
command = "agents/hooks/guard-shell.sh"
timeout = 10

[agents.hooks.session-notes]
description = "Print project reminders at session start."
event = "session-start"
command = "agents/hooks/session-notes.sh"
agents = ["claude", "gemini"]
```

The example names two variables: `DOCS_API_KEY` and `ISSUES_TOKEN`. The existing `secrets` table can declare both:

```toml
[secrets.DOCS_API_KEY]
reference = "op://Example/Docs/api-key"
enabled = false

[secrets.ISSUES_TOKEN]
reference = "op://Example/Issues/token"
enabled = false
```

### Validation

Fleet validates the table before it plans any write. One failure stops the run.

1. Reject unknown keys, wrong types, and bad names.
2. Reject absolute paths and parent traversal. Require Git to track every named file.
3. Reject an entry that lacks a key its transport requires.
4. Reject a secret match in `env` or `headers`.
5. Reject a domain other than `core`, `personal`, `work`, or `client`.
6. Warn about an unknown agent name, then skip it. Content can name an agent that an older manager lacks.

### Machine choices

The manifest never names a machine, a home path, or a native file. Those belong to the profile and the catalog.
The profile keeps the machine's choices: agents, domain, and groups.
The wizard needs no new step. Step 8 still selects agents. Its review step lists the items that each agent receives.
Open question 9 asks how one machine turns one item off.

## 2. Render targets

### Output kinds

Fleet prefers the first kind that an agent supports:

1. An owned path is a file, link, or folder that Fleet creates and owns whole.
2. An owned entry is one named value inside a file that the agent or user also writes.
3. A reported step is an action that only the user or the agent can complete. Fleet prints the exact command and writes nothing.

Owned paths need no merge and remove cleanly. Owned entries need the shared-file rules in section 4.
This preference is a proposal. Open question 4 asks the owner to confirm it.
Fleet renders at user level only. It never writes a project folder.
Paths below are the Linux, macOS, and WSL paths. WSL means Windows Subsystem for Linux.
Native Windows paths stay descriptive, as they are today.
The run account lists owned outputs under Files and reported steps under Next steps.
Fleet prints a reported step in the run that first renders or changes its item.

### Evidence labels

Every claim below carries one label:

- Tested: the author ran the named version on 2026-11-17 and observed the behavior. Each run used a throwaway home folder.
- Documented: vendor documentation or vendor source states it. No test confirmed it.
- Unverified: neither a test nor a vendor statement confirms it. The first build must test it.

Tested and Documented claims are known. Unverified claims are not.
Some Tested claims describe the test machine's real settings files. The author read their key names, never their values.
Agents change quickly. Each label describes one version on one date.

### One copy per agent

Several agents read each other's folders. One skill in two folders can reach one agent twice.
Gemini CLI then prints a conflict warning at every start (Tested).
Today's catalog gives Codex `~/.agents/skills` and Gemini CLI `~/.gemini/skills`. With both on, Gemini CLI finds each skill twice.

| Agent | Own skills folder | Reads `~/.agents/skills` | Reads `~/.claude/skills` | Same name twice |
| --- | --- | --- | --- | --- |
| Claude Code | `~/.claude/skills` | Not documented | Own folder | Personal wins over project (Documented) |
| Codex | `~/.codex/skills`, deprecated | Yes (Documented) | Not documented | Both copies appear (Documented) |
| Gemini CLI | `~/.gemini/skills` | Yes; it overrides the own folder (Tested) | No (Tested) | Warning at every start (Tested) |
| Antigravity | `~/.gemini/config/skills` | Not documented | Not documented | Not documented |
| OpenCode | `~/.config/opencode/skills` | Yes (Tested) | Yes (Tested) | Loads one copy (Tested); the winner is not fixed (Documented) |
| Pi | `~/.pi/agent/skills` | Yes (Documented) | No (Documented) | First found wins, with a warning (Documented) |
| Grok | `~/.grok/skills` | Yes (Documented) | Yes, unless switched off (Documented) | Loads one copy per name (Documented) |

The catalog assigns one skills folder to each agent. Fleet links each skill once per folder.
Claude Code gets `~/.claude/skills`. Antigravity gets `~/.gemini/config/skills`. Every other agent gets the shared folder, `~/.agents/skills`.
OpenCode and Grok also read Claude Code's folder. They then find a skill twice and load one copy.
The shared folder also reaches an installed agent that is off. Fleet writes nothing into that agent's own folders.
Fleet never relies on one agent reading another agent's private folder. That reading can change or be switched off.

### Variable references

Each agent has its own way to name an environment variable. Fleet renders `pass_env`, `header_env`, and `bearer_token_env` with it:

| Agent | Reference form | Evidence |
| --- | --- | --- |
| Claude Code | `${NAME}` in `command`, `args`, `env`, `url`, and `headers` | Tested in a plugin folder for `args` |
| Codex | Names in `env_vars`, `bearer_token_env_var`, and `env_http_headers` | Tested |
| Gemini CLI | `$NAME` or `${NAME}` | Tested for `args` |
| Antigravity | None confirmed | Unverified |
| OpenCode | `{env:NAME}` | Tested |
| Pi | `${NAME}` in `env` and `headers` values | Documented; Tested for a bearer header |
| Grok | `${NAME}` in `url`, `command`, `args`, `env`, and `headers` | Documented |

When an agent offers no reference form, Fleet skips that server for that agent and reports why.

### Claude Code

Tested version: 2.1.326.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.claude/skills/NAME/SKILL.md`. An entry can be a link. | One link per skill. | Documented |
| MCP servers | `mcpServers` in `~/.claude.json`, or `.mcp.json` inside a plugin folder. | `.mcp.json` inside one generated plugin folder. | Tested |
| Hooks | `hooks` in `~/.claude/settings.json`, or `hooks/hooks.json` inside a plugin folder. | `hooks/hooks.json` inside the same folder. | Tested |
| Connectors | Account-level. No local file declares them. | With a `url`: an HTTP entry in `.mcp.json`, plus a reported sign-in. Otherwise a reported step. | Documented |
| Plugins | `enabledPlugins` and `extraKnownMarketplaces` in `~/.claude/settings.json`. | A reported step: `claude plugin install ID`. | Tested for the keys |

The generated folder is `~/.claude/skills/fleet`. It holds `.claude-plugin/plugin.json`, `.mcp.json`, and `hooks/hooks.json`.
Claude Code loads it as `fleet@skills-dir` at the next session (Tested). Fleet edits no shared file.
The folder also loads through a link (Tested). Claude Code skips its trust check for this location (Documented).
Servers appear as `plugin:fleet:NAME` (Tested). Tools appear as `mcp__plugin_fleet_NAME__TOOL` (Documented).
A permission rule that names a tool must use that longer name.
An administrator policy can block folders of this kind (Documented). Fleet then reports the block.

The alternative writes owned entries into `~/.claude.json` and `~/.claude/settings.json`.
Claude Code writes its own state into `~/.claude.json` (Tested). Community reports say it reverts outside edits (Unverified).
Open question 4 asks the owner to choose.
Claude Code downloads an enabled plugin by itself after start (Documented). Open question 7 covers that effect.

### Codex

Tested version: 0.169.0.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.agents/skills`. The older `~/.codex/skills` still loads. | One link per skill in `~/.agents/skills`. | Documented |
| MCP servers | `mcp_servers` tables in `~/.codex/config.toml`. | Owned entries in that file. | Tested |
| Hooks | `~/.codex/hooks.json`, or a `hooks` table in `config.toml`. | Owned entries in `~/.codex/hooks.json`, plus a reported trust step. | Documented |
| Connectors | Apps are account-level. Local settings only switch them on or off. | With a `url`: an owned HTTP entry, plus `codex mcp login NAME`. Otherwise a reported step. | Documented |
| Plugins | `plugins` tables in `config.toml`, plus an installed copy under `~/.codex/plugins/cache`. | A reported step: `codex plugin add ID`. | Tested for the keys and the command |

Codex offers no include file and no extra file that always loads (Documented).
A named profile file needs a flag on every run (Documented), so it cannot carry Fleet's entries.
Codex writes `config.toml` itself. The test machine's file held plugin switches and hook trust records (Tested).
Choosing "always allow" for a tool adds a key inside the server's table (Documented in source).
Codex skips a new or changed hook until the user trusts it in `/hooks` (Documented). Fleet reports that step and never bypasses it.
A trust record includes the hook's position in its file (Documented in source). Fleet appends its hooks and keeps their order.
Codex warns at start when both hook locations hold hooks (Documented). Fleet reports that case before it writes.
Codex follows a linked skill folder but ignores a linked `SKILL.md` file (Documented). Fleet links folders only.
A plugin entry without an installed copy is an error (Documented in source). Settings alone cannot enable a plugin.

### Gemini CLI

Tested version: 0.50.0. The newest stable version is 0.68.0 (Documented).

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.gemini/skills` and `~/.agents/skills`. | One link per skill in `~/.agents/skills`. | Tested |
| MCP servers | `mcpServers` in `~/.gemini/settings.json`, or in an extension folder. | `gemini-extension.json` inside one generated extension folder. | Tested |
| Hooks | `hooks` in `settings.json`, or `hooks/hooks.json` in an extension folder. | `hooks/hooks.json` inside the same folder. | Documented |
| Connectors | An HTTP server entry, then a sign-in inside Gemini CLI. | An HTTP entry in the extension file, plus a reported sign-in. | Unverified for the sign-in |
| Plugins | Extensions: `gemini extensions install SOURCE`, which asks for consent. | A reported step. | Tested for the command |

An extension is Gemini CLI's plugin type. The generated folder is `~/.gemini/extensions/fleet`.
Gemini CLI loads a hand-placed folder without install records (Tested). A link to the folder also loads (Tested).
Gemini CLI lists each server as "from fleet" (Tested).
An allow-list setting, `security.allowedExtensions`, blocks a folder without install records (Documented in source). Fleet then reports the block.
`$NAME` and `${NAME}` resolve in settings and extension files (Tested for `args`; Documented for `env` and `headers`).
Gemini CLI disables user-level and extension servers in a folder that the user has not trusted (Tested).
Gemini CLI writes `settings.json` itself. The test machine's file held sign-in and editor keys (Tested).
A newer version replaces a linked `settings.json` with a regular file (Documented in source).
Hook timeouts count milliseconds (Documented). Claude Code and Codex count seconds, so the catalog must convert.
Gemini CLI 0.50.0's own migrate command copies seconds as milliseconds (Tested). Version 0.68.0 converts them (Documented in source).

### Antigravity

Google replaced Gemini CLI with Antigravity for unpaid and Google One users on 2026-06-18 (Documented).
Gemini CLI still works with paid API keys (Documented). The catalog has no Antigravity entry.
No test ran. Antigravity is not installed on the test machine.
The table shows what Fleet would write after a tested install. Open question 12 covers that condition.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.gemini/config/skills`. The terminal command also reads `~/.gemini/skills`. | One link per skill in `~/.gemini/config/skills`. | Documented |
| MCP servers | `~/.gemini/config/mcp_config.json`. | Owned entries in that file. | Documented |
| Hooks | `~/.gemini/config/hooks.json`. | Owned entries in that file. | Documented |
| Connectors | A remote server entry. Antigravity then handles the sign-in. | An owned entry, plus a reported sign-in. | Documented |
| Plugins | A plugin folder in `~/.gemini/config/plugins`. | A reported step. | Documented |

The terminal command is `agy` (Documented). A skill bundled in Gemini CLI 0.50.0 names the same command (Tested).
Antigravity's documentation never names `~/.agents/skills`. Antigravity therefore gets its own skills folder.
A remote server uses the key `serverUrl`; `url` also works (Documented).
The documentation names no variable reference form for `mcp_config.json`. One community report says references fail.
Until a test passes, Fleet skips an Antigravity server that needs a secret.
`agy plugin import gemini` converts Gemini CLI extensions (Documented).
Whether a generated plugin folder can carry Fleet's servers and hooks is Unverified.

### OpenCode

Tested version: 1.17.8. A second release line, version 2, renames several keys (Documented).

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.config/opencode/skills`, `~/.agents/skills`, and `~/.claude/skills`. | One link per skill in `~/.agents/skills`. | Tested |
| MCP servers | `mcp` entries in the global settings files. | Owned entries in `~/.config/opencode/opencode.json`. | Tested |
| Hooks | None as commands. Plugins are JavaScript or TypeScript code. | Not rendered. Reported as unsupported. | Documented |
| Connectors | A remote server entry, then `opencode mcp auth NAME`. | An owned entry, plus a reported sign-in. | Tested for the command |
| Plugins | Names in the `plugin` list, or code files in `~/.config/opencode/plugins`. | A reported step: `opencode plugin MODULE --global`. | Tested for the command |

OpenCode merges three global files: `config.json`, then `opencode.json`, then `opencode.jsonc`. The last file wins (Tested).
The test machine keeps hand-written settings in `opencode.jsonc`, with comments. The catalog names only `opencode.json`.
Fleet writes `opencode.json` and never edits `opencode.jsonc`. A same-name entry in `opencode.jsonc` wins inside OpenCode.
Fleet then skips the item for OpenCode and reports the shadowing entry. The run continues.
OpenCode's own commands also write `opencode.json` first (Documented in source). The file therefore holds owned entries.
OpenCode rewrites a settings file that lacks `$schema` (Documented in source). Fleet must write that key.
OpenCode refuses to start on an invalid entry (Tested). Fleet must validate each entry before it writes.
Version 2 drops `config.json` and still reads the version 1 shape (Documented). Fleet writes the version 1 shape.
A local entry holds `type`, a `command` list, and `environment`. A remote entry holds `type`, `url`, and `headers` (Tested).
`{env:NAME}` resolves in both (Tested). An unset variable becomes an empty string (Documented).

### Pi

Tested version: 1.3.0. Mise labels this install 0.80.6; the binary reports 1.3.0.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.pi/agent/skills` and `~/.agents/skills`. | One link per skill in `~/.agents/skills`. | Documented |
| MCP servers | `mcpServers` in `~/.pi/agent/mcp.json`. | Owned entries in that file. | Tested |
| Hooks | None as commands. Extensions are TypeScript files in `~/.pi/agent/extensions`. | Not rendered. Reported as unsupported. | Documented |
| Connectors | An HTTP server entry, then `pi mcp login NAME`. | An owned entry, plus a reported sign-in. | Tested for the command |
| Plugins | Pi packages: `packages` in `~/.pi/agent/settings.json`. | A reported step: `pi install SOURCE`. | Documented |

Pi writes to `mcp.json` itself. It saves enable and exposure changes into the entry (Documented).
Pi accepts a hand-written entry with a variable reference and a disabled flag (Tested).
Pi's newer documentation says it installs a missing package at the next start (Documented). Open question 7 covers that effect.

### Grok

No test ran. Grok Build is not installed on the test machine. Every claim below is Documented at best.
The table shows what Fleet would write after a tested install. Open question 12 covers that condition.

| Part | Native form | Fleet output | Evidence |
| --- | --- | --- | --- |
| Skills | `~/.grok/skills`, `~/.agents/skills`, and `~/.claude/skills`. | One link per skill in `~/.agents/skills`. | Documented |
| MCP servers | `mcp_servers` tables in `~/.grok/config.toml`. | Owned entries in that file. | Documented |
| Hooks | One JSON file per hook set in `~/.grok/hooks`. JSON is a structured file format. | One owned file: `~/.grok/hooks/fleet.json`. | Documented |
| Connectors | The connectors command is an alias of the MCP command. | An owned HTTP entry, plus a reported sign-in. | Documented |
| Plugins | `plugins` and `marketplace.sources` tables in `config.toml`. | A reported step: `grok plugin install ID`. | Documented |

Grok also loads Claude Code's servers, hooks, skills, and plugins (Documented).
Fleet keeps Claude Code's servers and hooks inside the generated plugin folder. Whether Grok loads that folder is Unverified.
A community tool also installs a `grok` command and uses `~/.grok` (Documented in its source).
That tool keeps `user-settings.json`, not `config.toml`. Detection must tell the two apart.

### Hook events

| Neutral event | Claude Code | Codex | Gemini CLI | Antigravity | Grok |
| --- | --- | --- | --- | --- | --- |
| `session-start` | `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` | `SessionStart` |
| `session-end` | `SessionEnd` | `SessionEnd` | `SessionEnd` | None | `SessionEnd` |
| `prompt-submit` | `UserPromptSubmit` | `UserPromptSubmit` | `BeforeAgent` | Unverified | `UserPromptSubmit` |
| `before-tool` | `PreToolUse` | `PreToolUse` | `BeforeTool` | `PreToolUse` | `PreToolUse` |
| `after-tool` | `PostToolUse` | `PostToolUse` | `AfterTool` | `PostToolUse` | `PostToolUse` |
| `stop` | `Stop` | `Stop` | `AfterAgent` | `Stop` | `Stop` |
| Evidence | Documented | Documented | Tested | Documented | Documented |

These five agents share one entry shape: an event, an optional `matcher`, and a list of commands.
Gemini CLI's column comes from its own migrate command (Tested).
Antigravity also documents `PreInvocation` and `PostInvocation`. Their match for `prompt-submit` is Unverified.
Timeout units differ. Gemini CLI counts milliseconds. Claude Code, Codex, and Antigravity count seconds (Documented).
Grok's timeout unit is Unverified.
Tool names differ per agent. Gemini's migrate command maps `Bash` to `run_shell_command` (Tested).
Other tool class names are Unverified.
Whether each agent runs a hook command through a shell is Unverified.
OpenCode and Pi run hooks only through code (Documented). Open question 10 covers them.

### Findings for the catalog

The tests found six gaps in today's catalog. They are facts for the builder. Open question 12 covers the Antigravity entry.

1. Pi's package moved to `@earendil-works/pi-coding-agent` at version 0.74.0 (Documented). The catalog's Mise names lack it.
2. OpenCode users can keep settings in `opencode.jsonc` (Tested). The catalog names only `opencode.json`.
3. With Codex and Gemini CLI both on, the catalog's skill folders make Gemini CLI warn at every start (Tested).
4. A community tool shares Grok's command name and its `~/.grok` folder (Documented). Detection cannot tell them apart today.
5. Antigravity has no entry, although Google now directs most Gemini CLI users to it (Documented).
6. The Codex and Pi documentation links now redirect to new addresses (Tested for Codex; Documented for Pi).

## 3. Where the store lives

Three placements are possible:

| Placement | Meaning | Result |
| --- | --- | --- |
| Content repository only | Declarations and machine choices both live in content. | Breaks rule 1. Machine choices would enter content. |
| Manager store only | Fleet keeps declarations under `~/.local/share/fleet`. | Breaks rule 1. Content no longer declares. Machines need a new transfer path. |
| Split | Each kind of data lives with its owner. | Meets all three rules. |

Recommendation: split the store by owner.

| Data | Lives in | Reason |
| --- | --- | --- |
| Declarations, skills, and hook scripts | Content repository | Content declares. Git carries history and transfer. Nova can read the same files. |
| Agent catalog | Manager repository | Native paths and formats change with each agent. The catalog and renderer release together. |
| Machine choices | Profile | Rule 1. Choices differ per machine. |
| Inventory | Manager state: `~/.local/state/dotfleet/agents.json` | Rule 2. Fleet lists every output. |
| Rendered outputs | Each agent's native location | Fleet can rebuild them from content, catalog, and profile. |
| Agent sign-in sessions and tokens | Each agent's own store | Fleet never reads or writes them. |
| Secret values that servers need | `~/.secrets`, as today | The manager already writes this file from secret references. This module adds only variable names. |

The reasoning applies each settled rule:

1. Rule 1 puts declarations in content and choices in the profile. The split follows that line exactly.
2. Rule 2 needs one list of outputs. The inventory already exists, so it stays in manager state.
3. Rule 3 needs files that Nova can read without Fleet code. Plain TOML and skill folders in Git meet that need.
4. Git already transfers content between machines. A manager-side store would need a sync engine, which the manager forbids.
5. The catalog stays out of content. A content repository then never names a native path, so it stays neutral.

Rendered outputs are never a source of truth. Delete any output, run `fleet update`, and Fleet rebuilds it.
The inventory record for each output holds its path, kind, agent, source item, and fingerprint.
An owned entry adds the entry's name and the rendered keys and values. Rendered values hold no secrets.
A generated folder lists each file inside it.
The inventory keeps owned entries apart from owned paths. A shared file is never an owned path.

## 4. Precedence, drift, and repair

### What Fleet owns

Fleet owns only the outputs that the inventory lists. Everything else in an agent's folders belongs to the user or the agent.
For an owned entry, Fleet owns the keys that it wrote. Other keys in the same file stay untouched.
In a table of named entries, the name identifies Fleet's entry. In a list of hooks, the rendered command identifies it.

### Migration from today's generator

The new renderer replaces today's generator. One code path remains.

1. Stop writing minimal settings files. Each recorded settings file becomes a shared file.
2. Drop the whole-file record for each settings file. Keep the file when it changed or when an entry needs it.
3. Remove a settings file only when it still matches its record and no entry needs it.
4. List each kept file once in the run account as released to the user.
5. Treat a recorded skill link in a folder that the catalog no longer assigns as a stale output.

### First contact with existing settings

Fleet compares each planned output with the home folder before any write:

| Finding | Action |
| --- | --- |
| Nothing exists at the target | Create the output and record it. |
| An unrecorded value exists and equals the rendered value | Record it as managed, without a write. The run account lists it. |
| An unrecorded value exists and differs | Conflict. The hand-written value stays until a person decides. |
| A recorded output still matches its record | Update it when the declaration changed. Otherwise leave it. |
| A recorded output differs from its record | Drift. The local edit stays until a person decides. |
| A recorded output is missing | Create it again. A missing output is drift that needs no decision. |

The hand-written value wins by default. Fleet never replaces it without a terminal decision.
Fleet checks every file that the agent merges. A same-name entry in a file that Fleet cannot edit shadows the item.
Fleet then skips the item for that agent and reports the shadow. A shadow does not stop a run.

### Conflicts and drift in a terminal

Fleet asks about each conflict during apply, after the backup. File conflicts already ask at that point.
The prompt reads the terminal, never piped input. It uses the same letters as the file prompt:

```text
Agent conflict
--------------
codex: MCP server issues in ~/.codex/config.toml differs from the rendered value.
Type one letter, then press Enter.
Uppercase S differs from lowercase s.

  d  Show changes for this item, then ask again.
  o  Replace this item with the rendered version.
  a  Replace this item and all remaining conflicts without asking again.
  s  Keep this item unchanged; continue with other items.
  S  Keep all conflicting items unchanged; apply other changes.
  q  Stop applying. Changes already made remain in place.

Choose [d/o/a/s/S/q]:
```

A skipped item stays in conflict. The run then ends as incomplete with exit code 1, as a skipped file conflict does today.
The inventory records each unresolved item. Status and doctor report it until a later run resolves it.
Invalid input repeats the prompt. Tests must drive this prompt through a pseudo-terminal, which simulates a user's terminal.

### Unattended runs

Without a terminal, a conflict or a changed output stops the run before the first write.
Fleet lists each item, its file, and the fix. This covers scripted, terminal-interface, and remote updates.
Fleet never chooses overwrite for the user. A missing output does not stop the run; Fleet recreates it.

### Detection between runs

Agent drift joins the existing drift signal:

- `fleet status` counts agent drift in its Drift line.
- `fleet diff` prints one patch per changed item and exits 1.
- `fleet doctor` fails its managed-files check and names the fix.
- The update gate treats agent drift as a `drift repair` trigger. A missing output counts.

Status and doctor read local files only. Diff and the update gate fetch first, as they do today.
No command starts an agent.
A patch can show secret values that a user wrote by hand. The existing diff warning applies.

### Repair choices

1. Take the rendered value: run `fleet update` in a terminal and choose `o`.
2. Keep the local value on this machine: turn the item off for this machine. Open question 9 covers how.
3. Change the declaration: edit the manifest, commit, push, and run `fleet update`.
4. Stop managing the item everywhere: remove its declaration.

### Removal

An output becomes stale when its declaration disappears or no longer selects this machine.
Today the generator keeps every stale output. It prints one progress line, and the run account omits it.
This draft proposes two changes. First, list every stale output in the run account.
Second, remove a stale output that still matches its record. The backup keeps a copy.
An edited stale output stops the run. The owner then keeps it unmanaged or removes it by hand.
Fleet removes only listed files. It never deletes an agent folder recursively.
Turning an agent off keeps its outputs for review, as it does today.
Open question 6 asks the owner to confirm these rules.

### Uninstall and rollback

Today uninstall deletes every path that the inventory lists. Rollback rewrites each saved file whole.
Neither action may treat a shared file as an owned path:

1. On uninstall, remove each owned path and each owned entry. Never delete a shared file.
2. Remove the whole entry, including keys that the agent added. The backup taken first keeps a copy.
3. On rollback, restore owned paths as today.
4. On rollback, restore only Fleet's entries in a shared file, from the backup copy.
5. Keep every other value that the agent or user wrote after that backup.

### Shared files

An owned entry lives in a file that Fleet does not own. Six rules protect that file:

1. Parse the file first. Stop when it is invalid, holds comments that a rewrite would lose, or is a link that leaves home.
2. Change only the owned entry. Keep key order.
3. Parse the result. Stop unless every other value equals its earlier value.
4. Write atomically and keep the file's permissions. An atomic write replaces the file in one step.
5. Compare the file's fingerprint between plan and apply. A change in between stops the run, as it does today.
6. Refuse a file that content also manages. Two sources cannot own one output.

An agent can add keys inside an owned entry, such as a disabled flag or a tool approval. Those keys belong to the user.
Fleet compares only the keys that it wrote, and it keeps the others (open question 5).
A backup of a shared file copies every value in it. Backups stay private, as they do today.

### Precedence inside an agent

Each agent merges its own layers. A project setting can override a user-level setting.
Fleet writes user-level settings only. A project override is the agent's normal behavior, not drift.

## 5. Nova consumption

Nova would need six things from this format:

| Need | Does the design give it? |
| --- | --- |
| Files that it can read without Fleet code | Yes. The store is plain TOML plus skill folders in Git. |
| A stable name for every item | Yes. Each item has a unique name in its table. |
| A scope label for every item | Yes. `domains` carries it. Nova must map domains to its own scopes. |
| No secret values | Yes. The store holds variable names and references only. |
| A version to cite | Yes. `agents.schema` and the content commit identify what Nova read. |
| A render root other than home | Partly. The format never names home. Nova must supply its own renderer or catalog. |

Nova needs neither the profile nor the inventory. Both describe one machine's Fleet state.
The partial answer is deliberate. The catalog lists paths under home, and Nova renders elsewhere.
Nova can read the catalog as a reference. Fleet gains no dependency either way.

## 6. Open questions for the owner

Each question carries a recommendation. The owner rules; this document does not decide them.

1. Is the agent module manager code, or the first add-on under the add-on contract?
   Recommendation: manager code. The generator, wizard step, profile field, and inventory already live in the manager.
   No add-on host exists yet. The module can still keep the contract's removal protections as rules.
2. Where does the `agents` table live: inside `fleet.toml`, or in a separate file that the manifest names?
   Recommendation: inside `fleet.toml`. One manifest keeps one validator and one schema. A later split stays possible.
3. Where should skills and hook scripts live, and where should links point?
   Today, skills live in `agents/skills` and links point into the content checkout.
   The manifest's `paths` key also installs an unused copy at `~/agents/skills`.
   Option A keeps checkout links and drops the unused copy.
   Option B stores these files under real home names, such as `.agents/skills`. chezmoi then installs them as ordinary content.
   Option C copies them into a manager-owned folder and links there.
   Recommendation: option B. Existing code then lists, backs up, and repairs these files. Five agents read the shared folder without links.
   Option B has two costs. An edit reaches agents only after commit and update. A skill's domain limit needs new converter code.
   Option A lets agents read uncommitted edits.
4. Should Fleet prefer an agent's self-contained folder over entries in shared files, where the agent offers one?
   Recommendation: yes. Use a generated plugin folder for Claude Code and a generated extension folder for Gemini CLI.
   Accept Claude Code's longer server and tool names.
5. For an owned entry, should Fleet compare only the keys that it wrote, or the whole entry?
   Recommendation: only the keys that it wrote. Codex and Pi add keys inside entries, and a whole-entry check would report false drift.
6. Should Fleet remove stale outputs that still match their record?
   Recommendation: yes for a removed declaration or a domain change. Keep today's rule when the user turns an agent off: keep the outputs.
7. How far should Fleet go with plugins: report only, write an agent's enable setting, or run an agent's installer?
   Recommendation: report only in the first build. This matches tool pins: content defaults never install software.
   Add an explicit per-machine opt-in later.
8. Today the generator starts no agent. Should this module keep that rule, even where an agent's own command could write settings?
   One such command is `codex mcp add`.
   Recommendation: keep the rule. A file edit shows in the run account before and after. A command's side effects do not.
   Even a help command wrote files into two agents' folders during the tests.
9. How does one machine turn one item off, or keep its own value?
   Option A: a group in the manifest lists agent items, and the machine turns that group off.
   Option B: the profile keeps a list of items that are off on this machine, and a command edits it.
   Recommendation: option B. It needs no content change, so it can settle a conflict at once. The conflict prompt can then offer it.
10. Should Fleet generate code so that OpenCode and Pi can run declared hooks?
    Recommendation: no in the first build. Report those hooks as unsupported. Generated code in two languages needs its own review.
11. Should Fleet offer a read-only import that prints manifest entries from an agent's existing settings?
    Recommendation: yes, after the first build. It is the shortest path from one configured agent to a shared store.
12. Should Antigravity get a catalog entry now, and should Grok stay marked as supported?
    Recommendation: add Antigravity. Detect both agents, but render for neither until one tested install confirms the documented paths.
13. Should this module also carry shared instruction files, such as one `AGENTS.md` for every agent?
    Recommendation: not in this module. Plain content can already install those files under their real names.

## Appendix A: Suggested build order

This order is a proposal for the builder. Each step is useful alone.

1. Read and validate the `agents` table. Extend the inventory and migrate today's records.
2. Link skills through the catalog's folder assignment.
3. Render MCP servers and connectors. Teach uninstall and rollback about owned entries.
4. Add agent drift to status, diff, doctor, and the update gate. Add the conflict prompt and the per-machine off switch.
5. Render hooks for Claude Code, Codex, and Gemini CLI.
6. Report plugins and other manual steps in the run account.

## Appendix B: Evidence record

The author ran every test on 2026-11-17 on one Linux machine under WSL.
Each test set the home folder to a new empty folder. No real agent settings changed.
The author also read key names, never values, in the machine's real settings files.
Each version comes from the binary's own version output.

| Agent | Version | What the tests exercised |
| --- | --- | --- |
| Claude Code | 2.1.326 | `claude mcp add`; `claude plugin init`, `list`, `details`, and `validate`; a linked plugin folder; variable expansion. |
| Codex | 0.169.0 | `codex mcp add`, `list`, and `get`; hand-written variable-name keys; comment survival. |
| Gemini CLI | 0.50.0 | `gemini mcp add` and `list`; `skills list` with links; a hand-placed extension; `hooks migrate`. |
| OpenCode | 1.17.8 | `opencode debug config` with three settings files; `debug skill` with three folders; an invalid entry. |
| Pi | 1.3.0 | `pi mcp add` and `list`; the documentation that ships inside the package. |
| Antigravity | Not installed | Nothing. Vendor documentation only. |
| Grok Build | Not installed | Nothing. Vendor documentation and source only. |

Gemini CLI and Pi wrote files into their folders during a help command (Tested). OpenCode created folders (Tested).
Agent folders change without a user action. This is why Fleet owns entries, not whole settings files.

Documented claims come from these sources, read on 2026-11-17:

- Claude Code: `code.claude.com/docs`, pages for skills, plugins, MCP, hooks, and settings.
- Codex: `learn.chatgpt.com/docs` and the `openai/codex` source at tag `rust-v0.169.0`.
- Gemini CLI: `geminicli.com/docs` and the `google-gemini/gemini-cli` source at tags `v0.50.0` and `v0.68.0`.
- Antigravity: `antigravity.google/docs`.
- OpenCode: `opencode.ai/v1/docs` and the `anomalyco/opencode` source at tag `v1.17.8`.
- Pi: `docs.pi.dev` and the documentation inside package version 1.3.0.
- Grok: `docs.x.ai/build` and the `xai-org/grok-build` source.
- Skills: `agentskills.io/specification`.
