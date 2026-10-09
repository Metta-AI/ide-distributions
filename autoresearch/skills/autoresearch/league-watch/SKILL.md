---
name: league-watch
description: Use when a scouting card snapshots the league standings.
---

# League watch

This skill implements the first step of System Design §5.3 (scouting opponents and the
league): snapshot the league standings and store the snapshot as evidence.

Doctrine upheld: D-03, D-04, D-07.

## Reads and writes

- Reads: the league standings and our leaderboard entry (both read-only).
- Writes: league snapshots (evidence of kind `leaderboard-snapshot`) and the journal.

## Procedure

1. Read our standing and the current champion:
   `"$AUTORESEARCH_CLI" softmax leaderboard`
   The data has `league_id`, `player_id`, `leaderboard_champion` and `current_champion`
   (both the policy the league shows competing for us: the champion, A9), `note` (when there
   is none), `conflict` (null, or what the loop expected and what the league shows), and
   `submission` (null, or where the latest promotion's submission stands when the league
   does not show it: `pending`, `competing`, `rejected`, `unknown`).
2. Read the full league standings (every division, every player, with rank, score and
   rounds played):
   `"$AUTORESEARCH_CLI" softmax standings`
3. Save both outputs in one file, for example `evidence/league/<UTC timestamp>.json`
   (`{"leaderboard": <data of step 1>, "standings": <data of step 2>}`), in the worktree.
   Then record it:
   `"$AUTORESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json`
   with `{"sha256": "<sha256 of the file>", "kind": "leaderboard-snapshot", "path": "evidence/league/<file>", "fetched_at": "<ISO time>", "source_ids": {"league": "<league_id>"}}`.
4. Compare with the last snapshot
   (`"$AUTORESEARCH_CLI" records find evidence --where kind=leaderboard-snapshot`):
   a changed champion, our rank, new entrants near the top, a player whose score moved by
   more than its usual round-to-round swing, or a `conflict`. A score without its rank and
   rounds played is not a finding.
5. When `conflict` is set, record it in the journal. Do not send your own `help_request`:
   queue-keeper already paused the agent and told the user (A9).
6. Hand the snapshot to `opponent-profile` and `meta-recon` in the same card, or finish with
   `complete-card`.

## Failure handling

- Reason `identity`: the credential is not our Player (D-03). Stop, send a `help_request`,
  and finish with `--outcome blocked`.
- Reason `platform`: the platform failed. Note it in the journal and finish `partial`.
- Standings list players, not their policy versions. To learn which policy a player runs,
  use `opponent-profile` (replays and episodes), not guesses.
