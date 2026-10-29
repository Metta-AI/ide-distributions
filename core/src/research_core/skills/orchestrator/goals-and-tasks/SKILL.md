---
name: goals-and-tasks
description: Use when turning a request into Goals, Tasks or settings.
---

# Goals, Tasks, directives and settings

Goals and Tasks are the plan; there is no plan file. Write them so a Researcher can start
without you and the user can judge the result.

## Goals

`goal.create {title, end_state, reason, description, judgement, tags}`. The end state is
something that can be judged: "a Version beats the champion by 2 points of mean score over 200
episodes with the mixin's guardrails met", not "improve the policy". `reason` is the user's
words or your reading of them; `judgement` says how the end state will be checked (which metric,
which Experiment design). Keep `where_it_stands` current with `goal.update` after each review:
one line the user reads. `goal.set_status` with a reason to park (waiting on the user or on
credits), complete (the end state was met; cite the Experiment) or abandon.

A read-only question about the platform needs no Goal. A request that takes more than a turn
does.

## Task types

- `research`: learn something; delivers claims or `none: <reason>`; no policy code.
- `improvement`: one change, one Experiment from hypothesis to verdict; delivers claims, the
  Experiment, the Versions and a submission answer.
- `ideation`: two to five proposed Tasks grounded in the Goal's claims; at most one open per Goal.
- `maintenance`: a committed change (a repair, a mixin fix, a toolchain pin) and a summary; never
  a Version. Repairs are `priority: high` and the only Tasks that may have no Goal.

`task.create {goal, type, title, description, reasoning, for_human, end_condition, end_detail,
priority, depends_on, experiment}`. `description` says what to do and deliver; `reasoning` why,
with claim and Experiment ids; `for_human` is one plain sentence clients show. Order Tasks with
`depends_on` or `task.link`. A Task can be edited only before it is claimed, and an edit clears
its approval.

## Proposals

Researchers propose with `task.propose`; a management wake brings each one to you. `task.read`
it, then `task.approve {task}` or `task.reject {task, next_step}` with what to do instead. When
`approval_required.task_types` names the type, only the user can accept: ask them, and pass
their message as `cites` in `task.approve` or `task.create`; without `cites` the Task stays
proposed and the result says so. A Researcher's question arrives the same way: `task.answer
{task, text}`; a note to a running Researcher is `task.message`.

## Ideation and parking

A Goal with no open Task and nothing waiting on the user gets an ideation Task from a management
wake. Each round raises `ideation_round`; when rounds without progress reach
`ideation.park_after_rounds` (default 3), park the Goal with `goal.set_status` and tell the user
what was tried and what would unpark it. Progress is a confirmed Experiment or a merged change,
not activity.

## Directives and settings

A standing instruction ("never touch the opening book", "always run 200 episodes") is a
directive: `directive.add {text, scope, source}` word for word, `scope` the workspace or a Goal
id, `source` the message. The briefing shows every active directive, so a summary cannot lose
it. `directive.retire` when the user withdraws it.

A preference about how the agents work is a setting: `settings.update {key, value, cites}`, and
`cites` is the user's message, always. `settings.read` lists the keys with their scopes:
`paused`, `max_researchers`, `approval_required.task_types`, the `submissions` keys (`mode`,
`until`, `max_count`, `quality_bar`, `min_margin_over_champion`), the `platform_writes` keys
(`forum`, `wiki`), the `xp` ask-above threshold, `ideation.park_after_rounds`,
`recovery.max_attempts` and `anti_stall.hour`. Settings bound only the agents' own initiative;
nothing the user asks for in a turn is refused by one.

`paused: true` stops everything in the background: no Researcher starts, no merge, no recovery,
no autonomous submission; running Researcher Sessions are drained and resume on `paused: false`.
You still answer the user, and what they ask for in a turn runs. Say what pausing stops when
they ask for it.

## Abandoning

`task.abandon {task, failure: {what_failed, next_step}}` for a Task that should not continue; a
held run is stopped by the watcher. Both fields are read by people: "rejected by the user: the
Goal changed" and "fold the opening analysis into T-12", not "dropped".
