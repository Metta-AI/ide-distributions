---
name: fetch-artifacts
description: Use when a review needs an experiment's XP artifacts.
---

# Fetch and inspect XP artifacts

Implements the evidence collection step of System Design §5.5. The review worker collects
and inspects the results; it does not assume a background collector already did this.
Doctrine upheld: D-04, D-05, D-06, D-09.

## Procedure

1. Read the experiment and check each request:
   `"$RESEARCH_CLI" records show <E-id>`
   `"$RESEARCH_CLI" softmax xp status <E-id>`
   For a repeated batch/seed/arm only the latest attempt counts. Do not judge a still-running
   request.
2. Turn every finished request into evidence and episode rows, deterministically:
   `"$RESEARCH_CLI" softmax xp evidence <E-id>`
   It downloads each completed request's results, runs the game's `evaluate` binding, keeps
   our seats from the frozen design and reduces them by the mean (one row per episode, never
   one per seat), and writes `evidence/xp/<E-id>/<request-id>/` (results, `rows.jsonl`,
   `meta.json`) plus an `xp-result` evidence index record per request into this worktree. A
   rerun skips requests that already have evidence. Its output lists `collected`, `already`,
   `pending`, `failed`, `complete` and the `rows` files. Do not compute rows by hand.
3. Inspect before you trust (D-05): open a sample of each arm's `results.json` and, for the
   change under test, a replay through `replay_inspect` and `replay-diagnosis`. Verify the
   recording version, stable player and policy ids, our seats, and an activation witness for
   the proposed change. Empty output, missing fields or a failed decoder are gaps, not
   zero-valued observations; rows with `status: failed` keep their `reason`.
4. Collect the rows for `ab-compare`:
   `cat evidence/xp/<E-id>/*/rows.jsonl > .scratch/<E-id>-rows.jsonl`
   Keep failures and exclusions visible in the Result. A downloaded file nobody inspected
   is not evidence for the decision.

## Failure handling

- `complete: false` (a request still pending), or `artifacts_incomplete`: complete `partial`
  with the pending request ids in `awaiting.xp`, so the orchestrator can return to review. Do
  not wait in the card.
- `softmax xp evidence` exits 1 (a binding error, an unknown arm, a missing binding): that is
  a tool fault (D-26 in `card`), not a measurement; never patch rows by hand.
- Failed episodes are worker/platform failures, not measurements of policy behavior. Count
  them separately; insufficient completed evidence cannot support a promote decision.
- Tool faults follow D-26 in `card`; platform failures and incomplete evidence remain explicit.
