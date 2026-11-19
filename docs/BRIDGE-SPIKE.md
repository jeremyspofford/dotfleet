# Bridge plugin spike

Status: design spike for owner review. This document proposes a design. It builds nothing.
A pull request (PR) is a proposed repository change. The owner decides whether a later PR builds this design.

## Terms

- An agent is an installed coding assistant, such as Pi or OpenCode.
- A hook is a command that an agent runs when a named event occurs. The content's `agents.hooks` table declares hooks.
- The tree is `~/.fleet/agents`, the Fleet-owned folder that holds each machine's agent configuration.
- A bridge is one Fleet-shipped plugin per agent. It reads the shared hook declarations from the tree and registers them inside the agent.
- A pointer is the one link or entry that connects an agent to the tree.

## Problem

Claude Code, Codex, and Gemini CLI run hook commands from settings files. Fleet renders those hooks today.
Pi and OpenCode run hooks only as code that loads inside the agent. Fleet reports their hooks as unsupported (ruling 10).
A bridge would close that gap without generating code per hook. Each agent gets one fixed plugin; the tree supplies the declarations.

## Evidence

Each claim carries one label. Tested means observed on 2026-11-17. Documented means vendor documentation or vendor files state it. Unverified means neither.

| Claim | Label |
| --- | --- |
| Pi 1.3.0 loads TypeScript or JavaScript files and folders with `index.ts` from `~/.pi/agent/extensions`. | Documented in the package's `docs/extensions.md` |
| Pi compiles TypeScript extensions on load through jiti, with no separate build step. | Documented, same file |
| Pi's `pi.on()` subscribes to events, including `session_start`, `session_shutdown`, `before_agent_start`, `tool_call`, `tool_result`, and `agent_end`. | Documented, same file |
| A Pi `tool_call` handler can block a call by returning `{ block: true, reason }`. A handler failure blocks the tool. | Documented, same file |
| Pi extensions must not start processes in the factory. They start them from `session_start` or from a handler. | Documented, same file |
| OpenCode 1.17.8 recognizes plugin hooks named `tool.execute.before`, `tool.execute.after`, `chat.message`, and `shell.env`. | Tested: the names appear in the binary |
| OpenCode emits `session.created`, `session.idle`, and `session.deleted` events to plugins. | Tested: the names appear in the binary |
| OpenCode loads plugin files from `~/.config/opencode/plugins`. | Tested: the test machine's folder holds a loaded plugin file; contents were not read |
| An OpenCode plugin blocks a tool by throwing an error from `tool.execute.before`. | Unverified |
| Tool names that each agent passes to these handlers. | Unverified |

## Proposed design

### Shared declarations

Fleet would render one neutral file per agent: `~/.fleet/agents/bridges/AGENT/hooks.json`.
Each entry would hold the hook name, the neutral event, the tool classes, the timeout, and the wrapper path.
The wrapper is the same tree script that Claude Code, Codex, and Gemini CLI run today. It sets `FLEET_AGENT`, `FLEET_HOOK`, and `FLEET_HOOK_EVENT`.
Updates would change this file and the wrappers. They would never change the bridge's pointer.

### Event payload and exit codes

Each agent sends its own event data. A bridge must choose what the wrapper receives on standard input.
This design proposes one neutral JSON object: `event`, `tool`, `tool_class`, `input`, and `cwd`.
Exit code 0 allows the action. Exit code 2 blocks a `before-tool` event and returns standard error as the reason, as Claude Code does.
Any other exit code reports an error and allows the action. The owner rules on that last choice (open question 3).

### Pi bridge

The bridge is `~/.fleet/agents/bridges/pi/index.ts`. The pointer is a link: `~/.pi/agent/extensions/fleet`.

| Neutral event | Pi event | Action |
| --- | --- | --- |
| `session-start` | `session_start` | Run the hook. |
| `session-end` | `session_shutdown` | Run the hook. |
| `prompt-submit` | `before_agent_start` | Run the hook. The match is Unverified. |
| `before-tool` | `tool_call` | Run the hook. Exit 2 returns `{ block: true, reason }`. |
| `after-tool` | `tool_result` | Run the hook. Never change the result. |
| `stop` | `agent_end` | Run the hook. |

The bridge reads `hooks.json` in `session_start` and on each event. It starts each wrapper as a child process, never in the factory.

### OpenCode bridge

The bridge is `~/.fleet/agents/bridges/opencode/fleet.js`. The pointer is a link: `~/.config/opencode/plugins/fleet.js`.

| Neutral event | OpenCode hook or event | Action |
| --- | --- | --- |
| `session-start` | `session.created` event | Run the hook. |
| `session-end` | `session.deleted` event | Run the hook. |
| `prompt-submit` | `chat.message` hook | Run the hook. |
| `before-tool` | `tool.execute.before` hook | Run the hook. Exit 2 throws an error to block. Unverified. |
| `after-tool` | `tool.execute.after` hook | Run the hook. |
| `stop` | `session.idle` event | Run the hook. The match is Unverified. |

### Delivery

| Option | Meaning | Result |
| --- | --- | --- |
| A. Render into the tree | The manager ships each bridge's source. Fleet copies it into the tree and links the pointer. | No network access. The bridge and renderer release together. |
| B. Publish a package | Fleet publishes an npm package. The ruling 7 plugin installer installs it per agent. | Agents update it on their own schedule. Fleet then depends on a package registry. |
| C. Install from Git | The plugin installer installs from the manager repository's Git address. | No registry. The installed copy can lag the manager version. |

Recommendation: option A. It matches the deployment model: one payload in the tree, one pointer per agent, set once.
It adds no network dependency and keeps the bridge version tied to the declaration format.

## Risks

1. A bridge runs code inside the agent's process. A defect can slow or stop the agent. Bridges must catch every error.
2. Agent plugin interfaces change often. Each manager release must state the agent versions that it tested.
3. A blocking `before-tool` hook that fails open lets a destructive call through. A hook that fails closed can stop work.
4. Tool names differ per agent. Tool class matching needs a tested table for each agent before it ships.

## Test plan for a later build

1. Load each bridge in a throwaway home with the installed agent, as the deployment tests did for Claude Code and Gemini CLI.
2. Drive one session per event. Record the wrapper's environment variables and its input.
3. Prove that exit code 2 blocks a shell tool call in both agents.
4. Prove that a missing or invalid `hooks.json` leaves the agent running and reports one error.
5. Prove that removing a declaration stops the hook at the next session without changing the pointer.

## Open questions for the owner

1. Which delivery option should the build use? Recommendation: A, render into the tree.
2. Should bridges pass one neutral JSON payload, or each agent's native event data? Recommendation: one neutral payload.
3. Should a hook error other than exit 2 allow the action or block it? Recommendation: allow it and report the error.
4. Should the first build cover both agents, or Pi first? Recommendation: Pi first; its documentation states its blocking contract.
