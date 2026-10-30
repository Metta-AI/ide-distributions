---
name: proposing
description: Use when proposing Tasks or a Version for submission.
---

# Proposing Tasks and submissions

A Researcher never creates accepted work and never submits: it proposes, and the Orchestrator
decides in a management wake. A good proposal is one the Orchestrator can approve without asking
you anything.

## Proposed Tasks

`task.propose {goal, type, title, description, reasoning, for_human, end_condition, end_detail,
priority, depends_on, experiment, ideation_round}`.

- `description`: what to do and what to deliver, concrete enough that another Researcher starts
  cold: the lever, the metric, the design, the episodes.
- `reasoning`: why this, now, with ids: the claims it rests on (C-n), the Experiment that
  motivated it (X-n), the refuted claim it would revisit and what changed since. A proposal
  without a cited claim is a hunch; say so when it is.
- `for_human`: one plain sentence clients show.
- `end_condition`: `deliverable`, `steps` or `effort`, with `end_detail` saying when it is done.
- `type`: `improvement` for one change tested by one Experiment; `research` to learn something
  first; `maintenance` for a fix; never `ideation` from an ideation Task.
- `depends_on` when one must finish first.

An ideation Task delivers two to five proposals: `task.complete {proposed_tasks: ["T-7", "T-8"]}`
with the ids `task.propose` returned. Ground every proposal in the Goal's claims and in what was
tried: `claim.search` with the Goal's tags and `include_retired: true`, `experiment.search` for
the refuted and inconclusive Experiments too, then `platform.standings` and
`platform.active_policies {exclude_player: true}` for what the league looks like now.
Game-specific levers: see the mixin's `<coworld>-diagnosis` and `<coworld>-ab` skills or docs.
Rank them: the first proposal is the one you would run.

Outside an ideation Task, propose when the work shows a follow-up worth a Task of its own (a
second lever, a surprising opponent behaviour): one or two at most, named in your `result`.

## Proposed submissions

`task.propose_submission {task, version, reason}` after an Experiment confirmed its decision rule
for that Version: `experiment.close` returned `confirmed`, the candidate beat the baseline by the
pre-registered effect at the mixin's minimum episodes, and the guardrail metrics held. `reason`
gives the numbers: the effect, the episodes, the baseline (the champion, by id) and the
Experiment (X-n).

Do not propose a Version whose Experiment was inconclusive, whose arms differed in anything but
the change, or that is not committed on your branch. In an improvement Task also answer
`submission_candidate` and `submission_reason` in `task.complete`; the proposal and the answer
must agree.

The Orchestrator applies the user's settings and the quality bar (a merged Version and a
confirmed Experiment against the champion) and may ask the user; the outcome shows on the Task.
A refusal is not a verdict on your work.
