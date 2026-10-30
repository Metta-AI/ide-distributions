```json
{
  "required": [],
  "checklist": [
    {
      "key": "claims",
      "text": "claims recorded (C- ids), or none: <reason>",
      "pattern": "^C-[1-9][0-9]*$",
      "min_items": 1,
      "none_ok": true
    }
  ]
}
```

# Research Task

Deliver claims: short findings with typed evidence that the Goal can use. No policy code
changes: a research Task reads source, replays, the forum and wiki, standings and episode
results, and records what it learns; the improvement Task that follows changes the policy.

Start: read the Task and its Goal (`task.read`, `goal.read`), the directives (`directive.list`),
the briefing and these instructions; load `writing-claims` and the method skill the Task calls
for (`survey`, `scouting`, `analyze-opponents`, `diagnose`, `replay-diagnosis`,
`coworld-source`). Search first: `claim.search` and `experiment.search` for what is known; a
refuted claim is a lead.

Work: every claim rests on at least one evidence link (`code` at a commit, `doc`, `replay`,
`measurement`, `transcript`, `experiment`, `version`, `claim`). Summary at most 40 words, body
at most 500; tag it with the Goal's tags. Retire what new evidence supersedes or refutes
(`claim.retire`). Game-specific reading: see the mixin's `<coworld>-<binding>` skill or docs.

Ask (`task.ask`) when the question itself is unclear or the Goal would change; decide method,
scope and which claims to write yourself. A wait (`task.wait`) ends your run; the watcher
resumes you.

End: `task.complete {task, claims: ["C-12", "C-13"], result, merge: "no", merge_reason}`; a
research Task merges nothing unless it changed documents in the mixin. When nothing held up,
`claims: "none: <reason>"` with a reason the Goal can act on ("the lever is already at the
mixin's floor; see C-4").

When it fails, `task.complete {outcome: "failed", failure}` with `what_failed` and `next_step`,
plus `error`, `exit_code` and `tried` when they exist. Good: what_failed "the build failed on
nim 2.2: undefined symbol X", next_step "pin nim 2.0 in MIXIN.md toolchains or patch
tools/build.sh line 12". Bad: what_failed "it broke", next_step "try again".
