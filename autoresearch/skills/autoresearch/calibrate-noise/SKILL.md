---
name: calibrate-noise
description: Use when a card must recompute the metric noise record.
---

# Calibrate metric noise

This skill implements the noise step of System Design §5.6 (maintenance): recompute
`stats/metric-noise.yaml` with its deterministic command. The runtime makes a P1
calibration card ("Calibrate metric noise for <game version>") when no noise record exists
for the current game version; no experiment can be admitted until one does.

Doctrine upheld: D-04, D-09.

## Reads and writes

- Reads: league episode rows and completed XP Results (episode rows), the game contract.
- Writes: `stats/rows/<league id>.jsonl`, `stats/metric-noise.yaml` (the old record goes to
  `stats/history/`), and the journal.

## Procedure

1. Find the game version: it is in the calibration card's title. In a maintenance card,
   use the current noise record's `game_version`, or the `game_version` of the newest XP
   rows.
2. Read `GAME.md` (see `queue`): `league_id`, `outcome_metrics`, and `noise_source`.
3. Collect episode rows. Completed XP rows are at `evidence/xp/*/*/rows.jsonl`:
   `cat evidence/xp/*/*/rows.jsonl > .scratch/noise-rows.jsonl`
   Rows of other game versions are dropped in the next step. Local runs never count (D-04).
4. Ingest them as league rows:
   `"$AUTORESEARCH_CLI" stats ingest --league <league_id> --rows .scratch/noise-rows.jsonl --game-version <game version> --game "$AUTORESEARCH_GAME"`
   Read `data.report.kept`. With fewer than 10 rows per metric the record is weak; say so
   in the journal.
5. Compute and write the record for every outcome metric, citing the evidence:
   `"$AUTORESEARCH_CLI" stats noise --league <league_id> --game-version <game version> --metrics <m1,m2> --evidence <ar://evidence/EV-…>`
   Keep `data.noise_rev` for the journal. Existing experiments keep their frozen designs
   (D-09); only new experiments use the new record.
6. Finish with `complete-card`.

## Failure handling

- No rows of the current game version exist (for example on a new workspace): no CLI
  command fetches league episodes yet. Do not invent a record. Send a `help_request` that
  says calibration needs league episode rows for `<game version>`, and finish `blocked`.
- `stats noise` exit 1 "no rows for league": step 4 kept no rows. Check the game version
  and the league id.
