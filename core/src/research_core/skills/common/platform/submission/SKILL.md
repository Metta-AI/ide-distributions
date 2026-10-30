---
name: submission
description: Use when a Version is uploaded or submitted to a League.
---

# Submission

A policy reaches a League in three steps, each its own tool: a Version (`version.create` or `version.adopt`), `upload {version}`, `submit {version, league}`. Upload takes any Version; submit takes any uploaded Version. Only the Orchestrator submits.

## The status ladder

`version.read` shows one computed status: `built` → `uploaded` → `submitted` → `qualifying` → `competing` → `champion`. The last four come from the platform's submission states (`pending`, `processing`, `placed`, `rejected`) and membership states. The Champion is the policy version that represents the Player in the League, never the League's winner; `platform.champion {league}` reads it, and the watcher syncs it every ten minutes.

## Upload

`upload {version: V-7}` sends the exact built bytes and checks the platform's hash against the artifact digest. It records an intent first, so a crash after the platform accepted the bytes never uploads twice; a second call is a no-op. An adopted Version is already uploaded.

## A user asked

Do it in the same turn: `version.create` on the code they mean (`code/` for what is on `main`), `upload`, then `submit {version, league, user_requested: true, cites: <their message>, reason}`. No setting, quality bar, pause or Task applies. Warn about what is missing (no Experiment, not merged) and submit anyway. `cites` is a record, never a gate: without it the submission still runs and the result asks you to record the reference.

## Your own initiative

`submit` without `user_requested` applies the settings and refuses with every shortfall named, each ending "a user request overrides this":

- `submissions.mode`: `ask` means `ui.ask` the user first; `autonomous` submits.
- `submissions.until` (an open window) and `submissions.max_count`; `paused` stops all of it.
- `submissions.quality_bar`: the Version is merged into `main` and a confirmed Experiment compares it with the League's champion at a primary effect of at least `submissions.min_margin_over_champion`. With no champion, a merged Version whose mixin `check` passes is enough.

The bar is on your initiative, not on the user: they can turn it off (`settings.update {key: submissions.quality_bar, value: false, cites}`) or lower the margin. Meet a shortfall rather than argue with it: get the Task reviewed and merged, run the Experiment against the champion (`experiment`).

## Researchers

A Researcher never submits. `task.propose_submission {task, version, reason}` records the proposal; a management wake applies the rule above, and in mode `ask` the Orchestrator asks the user. `task.complete` carries `submission_candidate: yes|no` with `submission_reason`. Waiting for a placed submission is `task.wait {kind: submission}`.

## Options and after

- `auto_champion`: `always` (default: the platform makes it the Player's champion once placed), `never`, or `lineage`.
- `reason` is kept on the champion record; every autonomous submission sends a notice.
- After `submit`, say where it stands in platform terms (`submitted`, `qualifying`, `competing`, `champion`) and nothing else. A `rejected` submission is a finding: read the platform's notes, record a claim, fix the Version.
- A policy the user uploaded elsewhere becomes a Version with `version.adopt {policy_version | label, summary}`, so it can be submitted and compared.
