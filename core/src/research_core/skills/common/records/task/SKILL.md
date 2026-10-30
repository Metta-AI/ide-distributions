---
name: task
description: Use when working a Task from claim to completion.
---

# Working a Task

One Task per Session, in the Task's worktree, ending in `task.complete`. The Goal and Task records are the plan; there is no plan file.

## Start

1. `task.claim {task: T-n}` is your first call: it creates the worktree (`worktrees/task-<n>`, branch `task/<n>` from `main`) and returns the Task, the type's instructions and the messages waiting for you.
2. Read the briefing in your system prompt, `goal.read {goal}`, `directive.list` (the workspace and `scope: G-n`), the Task's `description` and `reasoning`, and `task.read` for messages. Search claims and Experiments before you repeat work.
3. Load the skills the type names and the mixin's `<coworld>-<binding>` skills for anything game-specific.

## Work

- Commit in your worktree; never on `main`. Logs of long commands land under `documents/logs/`.
- Record findings as claims as you go (`writing-claims`); the events log makes your work visible without a report.
- A message from the Orchestrator outranks your plan: apply it, or say in the result why not.

## Ask or decide

`task.ask {task, question}` only for what changes the deliverable and only the Orchestrator can answer: the scope, a conflict between directives, spending beyond the Experiment's setting. It records the question, puts the Task on a `question` wait, and you end your run. Everything else you decide and write down in a claim or the result.

## Wait, then end the run

`task.wait {task, kind, detail, ref}` records what you wait for; then stop. Say in one line what you wait for and end the run. The watcher resumes this Session, with its history, when the wait resolves. Kinds: `xp` (ref: the request ids), `process` (a pid from a backgrounded command), `credits`, `permit`, `submission`, `platform` (set by the tools after retries), `user`, `dependency` (`T-n`), `question` (through `task.ask`). Never poll, sleep in a loop or relaunch a command because it has not finished.

## Finish

`task.complete {task, outcome: succeeded, ...}` with the type's deliverables; the tool refuses and names each absent one. Fix what is missing; never fake it.

- research: `claims: ["C-3"]` or `claims: "none: <reason>"`.
- improvement: `claims`, `experiment`, `versions`, `submission_candidate: yes|no`, `submission_reason`.
- ideation: `proposed_tasks` (two to five, from `task.propose`).
- maintenance: `commit`, `summary`; a repair also `capability` (its check rerun). Never a Version.

Also `commit` (the sha to merge, on the Task branch), `merge: yes` or `merge: no` with `merge_reason`, and `result`: findings first, then what is open, pointing at records by id. A review wake reads it. `task.release {task, reason}` gives the Task back unfinished.

## Failing

`task.complete {task, outcome: failed, failure}` skips the checklist. `failure` needs `what_failed` and `next_step`; add `error`, `exit_code` and `tried` when they exist. Write what the next person needs to pick the Task up.

Good:

```json
{"what_failed": "mixin build fails on the candidate: tools/build.sh cannot find nim after the toolchain path changed",
 "error": "sh: nim: command not found", "exit_code": 127,
 "tried": ["PATH with tools/nim-2.0.8/bin: same error", "workspace.capabilities check: build broken, repair T-31 open"],
 "next_step": "after T-31 fixes commands.build in MIXIN.md, rebuild from 3f2a9c1 on task/12 and rerun X-4"}
```

Bad:

```json
{"what_failed": "build didn't work", "next_step": "try again"}
```

## Propose

- Follow-up work: `task.propose {goal, type, title, description, reasoning, for_human}`; the Orchestrator approves or rejects. An ideation Task proposes two to five.
- A Version worth submitting: `task.propose_submission {task, version, reason}`; only the Orchestrator submits (`submission` skill).

## For the Orchestrator

The same fields come back at review: `task.read` shows the result, `failure` and messages; `task.abandon {task, failure}` uses the same `failure` shape (a user's "drop T-5" needs only `what_failed` and `next_step`).
