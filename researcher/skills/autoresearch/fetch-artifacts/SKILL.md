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
   Each row contains the handle, `state`, completed/total episode counts, batch, attempt,
   seed and arm. For a repeated batch/seed/arm use the latest attempt. Keep failed episodes
   distinct from completed policy evidence. Do not judge a still-running request.
2. Reuse already stored evidence when its hashes, request IDs and versions match:
   `"$RESEARCH_CLI" records find evidence --where kind=xp-result`
   `"$RESEARCH_CLI" records show <EV-id>`
   Otherwise fetch the completed experiment's artifacts:
   `"$RESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>`
   `--batch <n>` and `--attempt <n>` narrow the fetch. The response lists files by request
   ID; inspect the actual files rather than assuming a fixed platform directory layout.
3. Use the game's `evaluate` binding on the completed result files and `replay_inspect`
   for replay evidence. Follow D-05 / `replay-diagnosis`: verify hashes and recording version,
   stable player/policy IDs, subject seats and an activation witness for the proposed change.
   Empty output, missing fields or a failed decoder are gaps, not zero-valued observations.
4. Persist inspected artifacts under `evidence/xp/<E-id>/<request-id>/` (and replay files
   under their hash paths) before citing them. Create evidence index records:
   `"$RESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json`
   Include `sha256`, `kind`, durable `path`, `game_version`, `policy_versions`, `source_ids`
   (request/episode), and `fetched_at`. Use kind `xp-result` for results and `replay` for
   replays. Reuse matching records instead of generating duplicates on a retry.
5. Write the measured episode rows for each request to its durable `rows.jsonl`, joining
   the binding's measurements to verified platform identities and the recorded XP handle.
   Each row has `episode_id`, `game_version`, `policy_version`, `seed`, `seats`, `opponents`,
   `metrics`, `status` (`completed` or `failed`) and an optional failure `reason`. Aggregate
   the subject seats of one episode as the game's contract specifies; teammates are not
   independent observations. Preserve actual versions, seats, opponents and seeds so the
   shared ingest tool can reject mismatches. Do not copy expected design values over
   conflicting platform metadata. Missing provenance means the row is not usable evidence.
6. Collect those rows for `ab-compare`:
   `cat evidence/xp/<E-id>/*/rows.jsonl > .scratch/<E-id>-rows.jsonl`
   Keep failures and exclusions visible in the Result. A downloaded file nobody inspected
   is not evidence for the decision.

## Failure handling

- `artifacts_incomplete` or a request still running: complete `partial` with existing request
  IDs in `awaiting.xp`, so the orchestrator can return to review. Do not wait in the card.
- Missing rows/artifacts: report the gap and recover them within this review if the platform
  provides them. Never invent a background job or wait for one to write them.
- Failed episodes are worker/platform failures, not measurements of policy behavior. Count
  them separately; insufficient completed evidence cannot support a promote decision.
- Tool faults follow D-26 in `card`; platform failures and incomplete evidence remain explicit.
