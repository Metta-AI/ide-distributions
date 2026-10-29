---
name: environment
description: Use when unsure about paths, environments, CLIs or limits.
---

# Environment

`workspace.environment_guide` is the first thing to call when unsure about the environment; it is generated from the live configuration and the last capability check, so it is always current. This skill says how to read it and the rules that do not change.

## What the guide covers

Where you are (the workspace root and the active Coworld's folder), the image and source commits, the two Python environments, writable and read-only paths, credentials and identities, the CLIs and their versions, how to reach the user, sleep and wake, resource limits, and the last capability check with its age. `workspace.doctor` adds health facts and problems: the watcher heartbeat, store checks, CLI staleness against the lock, stale skill overrides.

## Two Python environments

- **Hermes's interpreter** runs the plugin and the store-only tools (`goal`, `task`, `claim`, `directive`, `settings`, `ui`). Nothing else runs there and nothing is installed into it.
- **The workspace environment** (`tools/venv` under the workspace root; the guide prints its Python) runs the `research` CLI, the platform tools, the mixin commands, `experiment.close` and the watcher. It holds `coworld`, `softmax`, `numpy` and `PyYAML`. Run your own scripts that need those with its Python.
- **Mixin toolchains** (another Python version, Nim and similar) are declared in `MIXIN.md` `toolchains` and installed by setup under `tools/<kind>-<version>`. A command that needs one names it in its `PATH`; a missing one is a repair (`repair` skill).

## Writable and read-only

- Writable: everything in the active Coworld's folder except its bookkeeping: `code/` (commit on a branch in a worktree, never on `main` directly), `documents/` (your files; command logs under `documents/logs/`, artifacts, replays and measurements), `worktrees/`, and `source/` as a clone you read but do not edit.
- Read-only: the profile source checkout (plugin, store library and schema, tools, prompts, cron definitions). Report a bug there; do not patch it. `config.yaml` is writable, but `prepare.py` re-applies the protected keys.
- Records live in the store and change only through the record tools; direct edits are detected and reported, and every tool returns the updated record, so nothing needs reading back.

## CLIs and identities

- `research softmax exec -- coworld <args>` and `-- softmax <args>` run the official CLIs from the workspace environment as the workspace's Player. The wrapper refuses commands that would change the identity (`login`, `player use`). `coworld docs --local` lists the installed reference docs.
- The Player credential is the workspace's; the User credential is the Orchestrator's and may be absent ("User identity not provisioned"). A permission error means the Player may not do that: report it, never look for another credential.
- The `platform` tool's reads, `upload`, `xp` and `version` act as the Player and record what they did; `softmax_api` covers the rest (`softmax-api-routes`).

## Sleep, wake and limits

- Neither profile has a turn or time limit, but a tool call has about 400 seconds, and a mixin command 240 seconds before it is backgrounded with a log and a resume token (the `candidate` skill has the protocol). Results carry the log path and a 2,000-character tail.
- The watcher ticks every minute: it resumes Sessions whose wait resolved, runs due checks, merges reviewed Tasks and syncs the champion every ten minutes. A Session that ends on `task.wait` is asleep, not finished.
- Reaching the user: the Orchestrator through the conversation or `ui.notify`; a Researcher only through its Task (`task.ask`, the result). Wakes never write into a chat.
- `paused` stops background starts; the user's requests in a turn still run.
