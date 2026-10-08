---
name: league-watch
description: Use when a scouting card snapshots the league standings.
---

# League watch

This skill implements the first step of System Design §5.3 (scouting opponents and the
league): snapshot the league standings and store the snapshot as evidence.

Doctrine upheld: D-03, D-04, D-07.

## Reads and writes

- Reads: the leaderboard.
- Writes: league snapshots (evidence of kind `leaderboard-snapshot`) and the journal.

## Procedure

1. Read our standing and the current champion:
   `"$AUTORESEARCH_CLI" softmax leaderboard`
   The data has `league_id`, `player_id`, `leaderboard_champion` (our policy on the
   leaderboard), `current_champion` (that policy, confirmed against the promotion ledger),
   and `note`.
2. Save the output as a file, for example
   `evidence/league/<UTC timestamp>.json`, in the worktree. Then record it:
   `"$AUTORESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json`
   with `{"sha256": "<sha256 of the file>", "kind": "leaderboard-snapshot", "path": "evidence/league/<file>", "fetched_at": "<ISO time>", "source_ids": {"league": "<league_id>"}}`.
3. Compare with the last snapshot
   (`"$AUTORESEARCH_CLI" records find evidence --where kind=leaderboard-snapshot`):
   a changed champion, or a `note` that says the ledger and the leaderboard disagree.
4. When `current_champion` is null and `note` says the ledger and the leaderboard disagree,
   send a `help_request` (see `communicate`). Submissions refuse until they agree.
5. Hand the snapshot to `opponent-profile` and `meta-recon` in the same card, or finish with
   `complete-card`.

## Failure handling

- Reason `identity`: the credential is not our Player (D-03). Stop, send a `help_request`,
  and finish with `--outcome blocked`.
- Reason `platform`: the platform failed. Note it in the journal and finish `partial`.
- The CLI gives only our own leaderboard entry. Full standings (other players and their
  policy versions) are not available from the CLI yet. Record what you have; do not guess
  other players' ranks.
