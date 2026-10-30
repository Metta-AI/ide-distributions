---
name: working-a-task
description: Use at the start of every Task Session, before any work.
---

# Working a Task

One Task per Session, in the Task's worktree, speaking only to the Orchestrator through the
Task. The whole loop fits on this page.

## 1. Claim

The watcher's prompt names the Task and your first call: `task.claim {task: T-n}`. It takes the
lease, creates `worktrees/task-<n>/` on branch `task/<n>` from `main`, and returns the Task, its
Goal, the type's instructions and the Orchestrator's messages. A claim refused because another
Session holds the Task, or because your epoch is stale, means a replacement took it: end your
run without writing anything.

## 2. Read before working

- The briefing in your system prompt: the Player, the champion, the directives, the broken
  capabilities, your Task.
- `task.read {task}` for the full record, `goal.read {goal}` for the end state the work is judged
  by, `directive.list` for standing instructions on the workspace and the Goal.
- The type's instructions (returned by the claim) and the skills they name; the mixin's
  `<coworld>-<binding>` skills for every game-specific step.
- `claim.search` and `experiment.search` for what is known or was tried: a refuted claim or an
  abandoned Experiment is a lead, not a gap.

## 3. Work in your worktree

Edit only inside your worktree and commit on your branch with messages that say what and why;
never touch `main` or another worktree. `version.create {summary, task}` builds from your
committed tree and refuses a dirty one, listing the files. Record findings as you go:
`claim.write` with evidence, `experiment.create`, `experiment.add_run`. The record is the
report; there is no second write-up. Keep the Task small enough to finish; a bigger idea becomes
a proposal (see `proposing`).

## 4. Ask or decide

`task.ask {task, question}` when the decision is not yours: the Goal is ambiguous, two readings
of a directive conflict, the Experiment would cost far more credits than the Task implied, or
the result changes what the Goal should be. The question sets a wait: end your run, and the
answer resumes you. Decide yourself everything inside the Task: the method, the seeds, which
claim to write, how to fix a broken build. A question you can answer with a tool is not a
`task.ask`.

## 5. Wait, then END YOUR RUN

Anything outside your control is a wait: `task.wait {task, kind, detail, ref}` with kind `xp`
(the request ids from `xp.request`), `credits` (after a 402), `process` (the pid of a background
build or evaluate), `question`, `permit`, `submission`, `platform` (set for you when a route
keeps failing), `user` or `dependency`. Then stop: end the run with no further tool call. That
is not quitting: the watcher resumes this Session, with its history, when the wait resolves.
Never poll in a loop and never sleep. On resume, call the tool you were waiting for
(`xp.status {experiment}` for XP) and continue.

## 6. Complete

`task.complete {task, outcome: "succeeded", checklist, commit, merge, merge_reason,
submission_candidate, submission_reason, result}`. The checklist keys come from the type
(`claims`, `experiment`, `versions`, `proposed_tasks`, `commit`, `summary`, `capability`) and
may be given flat; the tool refuses with every missing item at once, so fix them all and call
again. `commit` is the commit on your branch to merge; `merge: no` needs `merge_reason`. `result`
is two or three sentences the Orchestrator reads first.

`task.complete {task, outcome: "failed", failure: {what_failed, next_step, error, exit_code,
tried}}` when the Task cannot succeed. Good: `what_failed: "the build failed on nim 2.2:
undefined symbol X"`, `next_step: "pin nim 2.0 in MIXIN.md toolchains or patch tools/build.sh
line 12"`. Bad: `what_failed: "it broke"`, `next_step: "try again"`. Fill `tried` so nobody
repeats it.

`task.release {task, reason}` gives the Task back unfinished when you must not continue (a
directive forbids the only path).

## 7. What happens next

A review wake reads your Task, Experiment and claims. It accepts, and the watcher merges your
commit after the mixin's build and check pass again, or it reopens the Task with a reason: this
Session resumes with that reason, so read it and act on it rather than defending the first
attempt. A proposed submission is decided by the Orchestrator under the user's settings; you
never submit.
