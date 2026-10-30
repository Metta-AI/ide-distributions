```json
{
  "required": [],
  "checklist": [
    {
      "key": "claims",
      "text": "claims recorded (C- ids)",
      "pattern": "^C-[1-9][0-9]*$",
      "min_items": 1,
      "none_ok": false
    },
    {
      "key": "experiment",
      "text": "the Experiment run (X- id)",
      "pattern": "^X-[1-9][0-9]*$"
    },
    {
      "key": "versions",
      "text": "the Versions tested (V- ids)",
      "pattern": "^V-[1-9][0-9]*$",
      "min_items": 1
    },
    {
      "key": "submission_candidate",
      "text": "whether a tested Version is a submission candidate (yes or no)",
      "pattern": "^(yes|no)$"
    },
    {
      "key": "submission_reason",
      "text": "why it is or is not a submission candidate",
      "pattern": "\\S"
    }
  ]
}
```

# Improvement Task

Deliver exactly one Experiment from hypothesis to verdict, the Version(s) it tested, the claims
it produced, and a submission answer. One conceptual change per Version.

Start: read the Task, its Goal, the directives, the briefing and these instructions; load
`candidate`, `experiment` and `writing-claims`; `claim.search` and `experiment.search` for what
was tried. Game-specific metrics, floors and rosters: see the mixin's `<coworld>-ab` and
`<coworld>-eval-design` skills or docs.

Work, in order:
1. `experiment.create {summary, task, hypothesis, methods}`: the decision rule, metric, effect
   size, seeds and episodes are pre-registered. Then `experiment.start`; the design locks.
2. Make the change in your worktree, commit, `version.create {summary, task, experiment}`, then
   `upload {version}`.
3. `xp.request {experiment, candidate, baseline, seeds, episodes}`, then `task.wait {kind:
   "xp", ref: [...]}` and END YOUR RUN. On resume `xp.status {experiment}` stores the rows.
4. `experiment.close {experiment}` applies the rule: confirmed, refuted or inconclusive. Record
   the verdict as claims with `experiment` evidence; a refuted hypothesis is a finding.
5. `task.propose_submission` only for a confirmed Version (see `proposing`).

Ask (`task.ask`) when the lever or the baseline is ambiguous, or the Experiment would cost far
more credits than the Task implied; decide seeds, episodes above the mixin's floor and
implementation details yourself.

End: `task.complete {task, claims, experiment, versions, submission_candidate: "yes" or "no",
submission_reason, commit, merge: "yes" or "no", merge_reason, result}`. Merge yes only for a
confirmed Version; `submission_candidate` and its reason must agree with the verdict.

When it fails, `task.complete {outcome: "failed", failure}` with `what_failed` and `next_step`,
plus `error`, `exit_code` and `tried` when they exist. Good: what_failed "the build failed on
nim 2.2: undefined symbol X", next_step "pin nim 2.0 in MIXIN.md toolchains or patch
tools/build.sh line 12". Bad: what_failed "it broke", next_step "try again".
