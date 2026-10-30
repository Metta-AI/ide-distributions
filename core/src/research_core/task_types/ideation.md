```json
{
  "required": [],
  "checklist": [
    {
      "key": "proposed_tasks",
      "text": "two to five proposed Tasks for the Goal (T- ids)",
      "pattern": "^T-[1-9][0-9]*$",
      "min_items": 2,
      "max_items": 5
    }
  ]
}
```

# Ideation Task

Deliver two to five proposed Tasks for the Goal, each with reasoning grounded in the Goal's
claims and in what was tried. No code: an ideation Task reads and proposes.

Start: read the Task, its Goal (`goal.read`: the end state and "where it stands"), the
directives, the briefing and these instructions; load `proposing`. Read what is known:
`claim.search` with the Goal's tags and `include_retired: true`, and `experiment.search` for
every Experiment under the Goal: confirmed, refuted, inconclusive and abandoned. A refuted claim
or a failed Experiment is a lead: what changed since, what was not controlled. Read the league
now: `platform.standings`, `platform.active_policies {exclude_player: true}`, the champion.
Game-specific levers and failure vocabulary: see the mixin's `<coworld>-diagnosis` and
`<coworld>-ab` skills or docs.

Work: for each idea, `task.propose {goal, type, title, description, reasoning, for_human,
end_condition, end_detail, priority, ideation_round}` with `ideation_round` from your Task.
`reasoning` cites claims (C-n) and Experiments (X-n) by id; `description` is concrete enough
that a Researcher starts cold. Types are `improvement`, `research` or `maintenance`, never
another `ideation`. Rank them: the first is what you would run. Two strong proposals beat five
weak ones; five is the ceiling, not the target.

Ask (`task.ask`) only when the Goal's end state cannot be read; the choice of ideas is yours.

End: `task.complete {task, proposed_tasks: ["T-7", "T-8"], result, merge: "no", merge_reason:
"ideation changes no code"}`. `result` says in two sentences what the Goal should try first and
why.

When it fails, `task.complete {outcome: "failed", failure}` with `what_failed` and `next_step`,
plus `error`, `exit_code` and `tried` when they exist. Good: what_failed "the build failed on
nim 2.2: undefined symbol X", next_step "pin nim 2.0 in MIXIN.md toolchains or patch
tools/build.sh line 12". Bad: what_failed "it broke", next_step "try again".
