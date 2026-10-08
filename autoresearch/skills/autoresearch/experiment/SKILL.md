---
name: experiment
description: Use when designing and pre-registering an experiment.
---

# Experiment design

This skill implements the design steps of System Design §5.4 (experiment design and
launch): pick a hypothesis, check refuted levers, pre-register `E-….md` with a frozen
evaluation design, and make the candidate STRATEGY. It adapts optimizer-seed `experiment`.
Then use `build-upload` and `xp-request` in the same card.

Doctrine upheld: D-06, D-08, D-09, D-10, D-11, D-23, D-25.

## Reads and writes

- Reads: hypotheses, Topics, refuted mechanism claims, `strategy/STRATEGY.md`, versions,
  the game contract (`GAME.md`), and the noise record `stats/metric-noise.yaml`.
- Writes: the experiment record (`E-…`), the candidate
  `strategy/candidates/<E-id>/STRATEGY.md`, the hypothesis state, and the journal.

## Procedure

1. Pick the proposed hypothesis that the card names and read its Topics:
   `"$AUTORESEARCH_CLI" records show <H-id>`
   `"$AUTORESEARCH_CLI" records show <T-id>`
2. Check refuted mechanism claims on its lever (D-10):
   `"$AUTORESEARCH_CLI" records find claim --where status=refuted`
   Reopen a refuted lever only with evidence that meets the claim's `revise_if`.
3. Find the baseline: the current champion.
   `"$AUTORESEARCH_CLI" softmax leaderboard`
   Use `current_champion` (a platform policy version id) as the baseline. If it is null,
   stop: send a `help_request` and finish `blocked`.
4. Attack the design before you spend on it: does the metric measure the mechanism, or only
   a correlate? Can the evaluation shape show the effect? What else could produce the
   predicted signal? Is N affordable? Redesign when an answer is bad.
5. Choose one conceptual change (D-08): the STRATEGY components to edit and a description.
   Take the default metrics and guardrails from the hypothesis's Topics (D-08). Give each
   behavioural primary metric at least one anti-gaming guardrail; a rate metric needs its
   volume counterparts (D-09).
6. Freeze the evaluation design from the noise record (D-09). Its `game_version` must be the
   current game version; if there is no current noise record, the runtime runs a
   calibration card first. No CLI command freezes the design yet, so compute it like this:
   - `sd(m)`: in `stats/metric-noise.yaml`, `metrics.<m>.paired_diff_sd` when
     `metrics.<m>.n_pairs >= 10`, else `metrics.<m>.marginal_sd × 1.41421`.
   - `seeds`: unique integers, one candidate/baseline pair each. Use at least `n_min` seeds.
   - `n_min`: for each primary metric,
     `max(min_episodes_floor, ceil((2.80159 × sd(m) / min_effect)²))`; take the largest.
     `min_episodes_floor` is in `GAME.md`.
   - `delta`: for each metric in `GAME.md` `performance_guardrails`,
     `min(k, 1) × sd(m) / sqrt(number of seeds)`, with `k` = 0.5 unless you have a reason.
   - `metrics`: the primary metrics
     (`{"name", "direction", "role": "primary", "min_effect", "tolerance": null}`), the agent
     guardrails (`"role": "agent_guardrail"`, `"min_effect": null`, `"tolerance"`), and every
     performance guardrail of `GAME.md` (`"role": "performance"`, both null). Directions
     come from `GAME.md` `outcome_metrics`.
   - `seats`: our seat slots. `opponents`: fixed policy refs for true pairing, or
     `"random"` (then pairing is seed-only).
   - `noise_rev`: the output of `git hash-object stats/metric-noise.yaml`.
   - `baseline_version`: the champion from step 3. `game_version`: the noise record's.
7. Write the decision rule in the experiment body before any result exists (D-09). Use this
   default unless the Topic needs another:
   - `promote`: `stats evaluate` reports `promote_eligible: true` and the Result is valid.
   - `refute`: N is met and the bootstrap CI of every primary metric lies at or below 0.
   - `invalid`: the Result is not valid (wrong versions, failed episodes, the change did not
     activate).
   - `hold`: every other case.
   Also write the prediction if the hypothesis is true and if it is false. They must differ.
8. Create the experiment:
   `"$AUTORESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>`
   with `{"hypothesis": "<H-id>", "mode": "experimentation", "lever": …, "baseline": "<champion>", "change": {"components": […], "description": …}, "evaluation_design": {…}, "environment": {"game_id": "<$AUTORESEARCH_GAME>", "game_version": …}, "status": "designed", "xp_requests": [], "results": [], "lifecycle": {"launch_attempts": 0}}`.
   The design is frozen once the status leaves `designed`. Never edit the rule after (D-09).
9. Make the candidate: copy `strategy/STRATEGY.md` to
   `strategy/candidates/<E-id>/STRATEGY.md` and edit only the named components there. The
   champion `strategy/STRATEGY.md` does not change until a promotion (D-25).
10. Move the hypothesis to under-test and link the experiment (the card names it, D-23):
    `"$AUTORESEARCH_CLI" records status <H-id> under-test --reason "experiment registered" --ref ar://experiment/<E-id>`
    `"$AUTORESEARCH_CLI" records link <H-id> experiments <E-id>`
11. Validate: `"$AUTORESEARCH_CLI" records validate <E-id>`. Then go on with `build-upload`.

## Failure handling

- `records new experiment` exit 1: read `error.problems` (schema or design problems), fix
  the meta, and run it again.
- The hypothesis is not in the card refs: do not change it. Request a card that names it.
- If you cannot finish the build in this card, finish `partial`. The runtime's rebuild path
  makes a "Build E-…" card for a `designed` experiment.
