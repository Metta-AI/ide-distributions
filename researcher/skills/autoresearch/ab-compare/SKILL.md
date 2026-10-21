---
name: ab-compare
description: Use when computing paired A/B statistics for a review.
---

# A/B compare

This skill implements the statistics of System Design §5.5 (review and decision): matched
A/B statistics with paired bootstrap intervals, under the frozen design. It adapts
optimizer-seed `ab-compare`. `review-xp` calls it.

Doctrine upheld: D-06, D-09.

## Reads and writes

- Reads: the episode rows of the baseline and candidate arms, the frozen evaluation design.
- Writes: `stats/rows/<E-id>.jsonl` and `stats/outcomes/<E-id>.json` (the stored outcome
  that the orchestrator's promotion workflow checks), and the metrics of the Result.

## Procedure

1. Collect the rows (see `fetch-artifacts` step 4):
   `cat evidence/xp/<E-id>/*/rows.jsonl > .scratch/<E-id>-rows.jsonl`
2. Ingest them against the frozen design. It drops duplicates, failed episodes, and rows
   with the wrong game version, seats, or seed:
   `"$RESEARCH_CLI" stats ingest --experiment <E-id> --rows .scratch/<E-id>-rows.jsonl`
   Read `data.report`: `kept`, `duplicates`, `failed`, `wrong_version`, `wrong_seats`,
   `wrong_seed`, `missing_metrics`. Many dropped rows can make the Result invalid.
3. Apply the decision rule. The champion is the policy the league's leaderboard shows
   competing for us; do not pass `--champion` inside a card:
   `"$RESEARCH_CLI" stats evaluate <E-id>`
4. Read `data`: `promote_eligible`, `failures` (for example `n_short:…`,
   `primary_not_significant:<m>`, `guardrail_breach:<m>`, `performance_slip:<m>`,
   `baseline_not_champion`), and `per_metric` (`mean_signed_diff`, `ci`, `threshold`,
   `passed` per metric and role (`per_metric[name][role]`); larger is always better after the sign). Copy `per_metric` and the
   pair count into the Result metrics, with units.
5. Report the pairing of the design (`seed_and_opponents` or `seed_only`) in the Result
   analysis.

## Failure handling

- Do not peek and stop. N was committed with the rule (D-09). Evaluate only when every
  request is terminal.
- `failures` contains `baseline_not_champion` (the league's champion changed since the
  design), or `stats evaluate` exits 2 "champion unknown" (the league shows no champion for
  us): record it; the review decision is `hold` and a re-evaluation against the current
  champion is the follow-up card.
- Never edit `stats/rows/` or `stats/outcomes/` by hand. The promotion workflow checks their hashes.
