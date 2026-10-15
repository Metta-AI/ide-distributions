---
name: fetch-artifacts
description: Use when a review needs an experiment's XP artifacts.
---

# Fetch artifacts

This skill implements the "fetch and inspect the artifacts" step of System Design §5.5
(review and decision). It adapts optimizer-seed `fetch-artifacts`.

Doctrine upheld: D-04, D-05, D-06.

## Reads and writes

- Reads: the experiment's XP handles (`xp_requests`).
- Writes: evidence files and evidence index records for artifacts you inspect beyond the
  ones the XP poller stored; scratch copies in `.scratch/`.

## Procedure

1. Check that every request is terminal:
   `"$AUTORESEARCH_CLI" softmax xp status <E-id>`
   Each row has `state`, `episodes_completed`, `episodes_total`, and the handle (batch,
   attempt, seed, arm). Only the latest attempt of each (batch, seed, arm) counts.
2. Check the stored evidence. The XP poller already stored each completed request at
   `evidence/xp/<E-id>/<request id>/` (`results.json`, `rows.jsonl`, `meta.json`) with an
   index record of kind `xp-result` (`ar://xreq/<request id>` resolves to it):
   `"$AUTORESEARCH_CLI" records find evidence --where kind=xp-result`
   `"$AUTORESEARCH_CLI" comms resolve ar://xreq/<request id>`
   Every completed request must have its `meta.json`. If one is missing, the poller has not
   finished it: finish the card `partial` and say so. Do not fetch and judge it yourself.
3. For replays, logs, and other artifacts, download into scratch:
   `"$AUTORESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>`
   Add `--batch <n>` and `--attempt <n>` to limit it. Files land in
   `.scratch/xp/<E-id>/<request id>/`.
4. Inspect what you will cite with the game's `replay_inspect` binding and the
   replay-analysis gate (`replay-diagnosis` step 3, D-05). A file nobody inspected is a
   number, not a finding (D-06).
5. Store each artifact you cite as evidence (`replay-diagnosis` step 4): copy it out of
   `.scratch/` into `evidence/`, then `"$AUTORESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json`.
6. Collect the episode rows for `ab-compare`:
   `cat evidence/xp/<E-id>/*/rows.jsonl > .scratch/<E-id>-rows.jsonl`

## Failure handling

- Reason `artifacts_incomplete`: a request is not complete. Do not judge a batch that is
  still running (D-06). Finish `partial`.
- Failed episodes are worker or platform faults, not policy behaviour. Count them apart from
  completed episodes.
