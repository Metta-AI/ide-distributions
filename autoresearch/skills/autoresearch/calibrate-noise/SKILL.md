---
name: calibrate-noise
description: Use when a card must recompute the metric noise record.
---

# Calibrate metric noise

This skill implements the noise step of System Design §5.6 (maintenance): recompute
`stats/metric-noise.yaml` with its deterministic command, from league episodes and completed
XP Results. The runtime makes a P1 calibration card ("Calibrate metric noise for <game
version>") when no noise record exists for the current game version; no experiment can be
admitted until one does.

Doctrine upheld: D-04, D-09.

## Reads and writes

- Reads: league episodes (through the Softmax tool, read-only), completed XP Results
  (episode rows), the game contract.
- Writes: `stats/rows/<league id>.jsonl`, `stats/metric-noise.yaml` (the old record goes to
  `stats/history/`), and the journal.

## Procedure

1. Find the current game version and the league:
   `"$AUTORESEARCH_CLI" softmax game-version`
   Use `data.game_version` and `data.league_id` from here on. If the card title names
   another version, the game changed since the card was made: calibrate the current one and
   say so in the journal.
2. Read the outcome metrics from the game contract:
   `"$AUTORESEARCH_CLI" game show`
   Use every `data.outcome_metrics[].name`.
3. Fetch league episodes as episode rows (read-only; each episode goes through the game's
   evaluate binding):
   `"$AUTORESEARCH_CLI" softmax league-episodes --league <league_id> --limit 200 --out .scratch/league-rows.jsonl`
   Run it in the foreground with a terminal `timeout` of 600. 200 episodes take a few
   minutes; a progress line goes to stderr every 10 episodes. Read `data.rows` and
   `data.game_versions`. The file is written only when the read is complete.
4. Add completed XP rows of the same version, if any:
   `cat .scratch/league-rows.jsonl evidence/xp/*/*/rows.jsonl > .scratch/noise-rows.jsonl`
   (when there are no XP rows, use `.scratch/league-rows.jsonl` alone). Rows of other game
   versions are dropped in the next step. Local runs never count (D-04).
5. Ingest them as league rows:
   `"$AUTORESEARCH_CLI" stats ingest --league <league_id> --rows .scratch/noise-rows.jsonl --game-version <game_version> --game "$AUTORESEARCH_GAME"`
   Read `data.report.kept`. With fewer than 10 rows per metric the record is weak; fetch
   more episodes (`--limit 400` at most: each episode costs two platform calls) or say so
   in the journal.
6. Compute and write the record for every outcome metric, citing the evidence you used
   (the XP evidence entries, if any):
   `"$AUTORESEARCH_CLI" stats noise --league <league_id> --game-version <game_version> --metrics <m1,m2> --evidence <ar://evidence/EV-…>`
   Keep `data.noise_rev` for the journal. Existing experiments keep their frozen designs
   (D-09); only new experiments use the new record.
7. Finish with `complete-card`.

## Failure handling

- `softmax league-episodes` exit 1 (`platform`, for example a 403 on episode results):
  calibrate from the XP rows alone if they cover the current version. If there are none, do
  not invent a record: send a `help_request` that names the error and says calibration needs
  league episode rows for `<game_version>`, and finish `blocked`.
- `data.game_versions` does not contain the current version: the league has not played it
  yet. Treat it like the case above.
- `stats noise` exit 1 "no rows for league": step 5 kept no rows. Check the game version
  and the league id.
- A binding, CLI, or gate raises an exception, reports `errored`, or fails for a reason unrelated to the policy or research content (a tool fault): do not retry it. Follow D-26: journal it, send a `help_request`, and block the card with a reason that starts with `tool-fault:`.
