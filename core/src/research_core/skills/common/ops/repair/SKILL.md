---
name: repair
description: Use when a capability, tool, command or install is broken.
---

# Repair

A failing tool, command, install or call is work, not a wall. Capabilities are what the checks test: the mixin commands (`build`, `check`, `upload`, `evaluate`, game tools), the platform logins (`login:player`, `login:user`), GitHub access and the build routes. A failure marks the capability broken, opens or joins one `high` maintenance Task for it, and Tasks whose type needs it wait on that Task. After two failed repair Tasks the user gets a notice.

## Diagnose

1. Read everything. `workspace.capabilities` lists each capability with its status, last check, command and output tail; the repair Task's description carries the same. Tool refusals carry the log path (`documents/logs/<name>-<timestamp>.log`) and a 2,000-character tail: read the whole log. `workspace.doctor` adds commits, the watcher heartbeat, CLI staleness and store checks.
2. Reproduce in your worktree with the exact command from `MIXIN.md` `commands.<name>`, in the workspace environment (`environment` skill for the paths). A result you cannot reproduce is not understood yet.
3. Name the layer:
   - **Environment**: a Python mismatch (the mixin needs a toolchain under `tools/<kind>-<version>`; `MIXIN.md` `toolchains` declares it and setup installs it); a missing package (install it into that toolchain, never into Hermes's interpreter); an absent tool on `PATH`; a dependency the game's docs name.
   - **The command**: a wrong path or flag in `commands.<name>`, a script the repository moved; fix the mixin, which is yours to edit.
   - **The platform**: a route, status and request id in the error. 401 marks `login:<identity>` broken; a 4xx on a body means the shape changed (`softmax-api-routes`); 5xx and timeouts are retried and then become a `platform` wait. A refusal (a permit, credits, an access error, the user's no) is not a break: do not route around it with another identity, credential or command.
   - **The machinery**: the profile source checkout (plugin, store, tools, prompts) is read-only, and `research` CLI versions can lag the lock (`workspace.doctor` reports it). Report a bug there with the exact error in your result; do not patch it.

## Fix

- Make the change in your Task worktree and commit it: a mixin command, a toolchain declaration, a script, a dependency pin. Keep the same inputs and engine version.
- Run the fixed command yourself until it passes; that is the evidence. Then `workspace.capabilities {check: true}`: the watcher reruns the checks within a minute and marks the capability `ok`. Read `workspace.capabilities` once afterwards to confirm; one read is not polling. Still broken: read the new output and continue.
- `task.complete {task, outcome: succeeded, commit, summary, capability: <name>}` names the capability so dependent Tasks resume. A maintenance Task never makes a Version.

## Keep working around a block

When a repair cannot finish in this Task, reach the result another reasonable way: another documented option or command, a copy of the tool in your worktree, the step done by hand with the CLI (`research softmax exec -- coworld …`). Prefer the way that keeps the same inputs and engine version; never edit the read-only checkout. Report what failed (command, full output, exit code), what you did instead and how it could change the result, so the capability still gets fixed.

## Giving up well

`task.complete {outcome: failed, failure}` with `what_failed`, `error`, `exit_code`, `tried` and a `next_step` someone can act on (the `task` skill has the good and bad example). A reported blocker beats an invented result: never present plausible output you did not get. A recurring platform error goes in the result for an upstream report.

## Common cases

- `evaluate` fails on downloaded results: the runs are marked invalid and the capability broken; the Experiment waits for the repair, not for a hand-computed verdict.
- `build` works for you but `version.create` refuses: the tree is dirty, or the artifact path in `MIXIN.md` is wrong.
- A CLI command works in your shell but the tool fails: the tool runs in the workspace environment with the Player's credential; compare the two before blaming the platform.
