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
  the game contract (`game show`), the current game version (`softmax game-version`), and
  the noise record `stats/metric-noise.yaml` (through `stats design`).
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
   Use `current_champion` (a platform policy version id: the policy the league shows
   competing for us, A9) as the baseline. When it is null (`note` says "no champion"), this
   is a fresh install. Make the first champion in this card
     with the `submit` skill, section "Bootstrap card" (A14: the game's starter policy is
     promoted as champion, with no experiment). Then request the experiment again so it
     designs against that champion:
     `"$AUTORESEARCH_CLI" queue request create --title "Experiment for <H-id>" --mode experimentation --why "first champion bootstrapped; design against it" --ref ar://hypothesis/<H-id>`
     and finish as that section says. Do not design this experiment in the bootstrap card.
4. Attack the design before you spend on it: does the metric measure the mechanism, or only
   a correlate? Can the evaluation shape show the effect? What else could produce the
   predicted signal? Is N affordable? Redesign when an answer is bad.
5. Choose one conceptual change (D-08): the STRATEGY components to edit and a description.
   Take the default metrics and guardrails from the hypothesis's Topics (D-08). Give each
   behavioural primary metric at least one anti-gaming guardrail; a rate metric needs its
   volume counterparts (D-09).
6. Find the current game version and the game contract:
   `"$AUTORESEARCH_CLI" softmax game-version`
   `"$AUTORESEARCH_CLI" game show`
   Use `data.game_version`, the `outcome_metrics` (names and directions) and the
   `performance_guardrails`. A performance guardrail is checked automatically; do not list it
   again as a primary metric or an agent guardrail.
7. Write the decision rule in the experiment body before any result exists (D-09). Use this
   default unless the Topic needs another:
   - `promote`: `stats evaluate` reports `promote_eligible: true` and the Result is valid.
   - `refute`: N is met and the bootstrap CI of every primary metric lies at or below 0.
   - `invalid`: the Result is not valid (wrong versions, failed episodes, the change did not
     activate).
   - `hold`: every other case.
   Also write the prediction if the hypothesis is true and if it is false. They must differ.
8. Create the experiment, still without its evaluation design:
   `"$AUTORESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>`
   with `{"hypothesis": "<H-id>", "mode": "experimentation", "lever": …, "baseline": "<champion>", "change": {"components": […], "description": …}, "environment": {"game_id": "<$AUTORESEARCH_GAME>", "game_version": "<data.game_version of step 6>"}, "status": "designed", "xp_requests": [], "results": [], "lifecycle": {"launch_attempts": 0}}`.
9. Freeze the evaluation design from the noise record (D-09). Write the request to
   `.scratch/design-request.json`:
   `{"baseline_version": "<champion of step 3>", "seats": [<our seat slots>], "opponent_count": <opponents per episode>, "seeds": [<unique integers, one candidate/baseline pair each>], "primary_metrics": [{"name", "direction", "min_effect"}], "guardrails": [{"name", "direction", "tolerance"}]}`
   (optional `"k"`, default 0.5; optional `"opponent_episodes"`, default 100). Take `seats`
   and `opponent_count` from the `## Seats and opponents` section of `GAME.md` (seat count
   and team rules). The opponents are sampled from the current league (A12): each seed gets
   its own line-up, weighted toward the policies we actually meet, and both arms of the
   pair face it. Only when the question is about specific opponents (for example "does this
   beat X specifically"), give `"opponents": ["<policy ref>", …]` and
   `"opponent_rationale": "<why>"` instead of `opponent_count`. Then:
   `"$AUTORESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json`
   It reads recent league episodes and the standings (allow a minute), computes `n_min` (at
   least the game's floor) and `delta` from the noise record of the current game version,
   records the record's revision as `noise_rev`, and writes `evaluation_design` into the
   experiment, with the sampled `lineups` and the `league_snapshot` they came from. Read `data.evaluation_design.n_min`: you need at
   least that many seeds. The design is frozen once the status leaves `designed`. Never edit
   the rule after (D-09).
10. Make the candidate: copy `strategy/STRATEGY.md` to
   `strategy/candidates/<E-id>/STRATEGY.md` and edit only the named components there. The
   champion `strategy/STRATEGY.md` does not change until a promotion (D-25).
11. Move the hypothesis to under-test and link the experiment (the card names it, D-23):
    `"$AUTORESEARCH_CLI" records status <H-id> under-test --reason "experiment registered" --ref ar://experiment/<E-id>`
    `"$AUTORESEARCH_CLI" records link <H-id> experiments <E-id>`
12. Validate: `"$AUTORESEARCH_CLI" records validate <E-id>`. Then go on with `build-upload`.

## Failure handling

- `records new experiment` exit 1: read `error.problems` (schema problems), fix the meta,
  and run it again.
- `stats design` exit 1, by `error.reason`:
  - `needs_calibration`: no noise record for the current game version. Do not compute a
    design by hand. Store the request on the experiment so a later card can freeze it:
    `"$AUTORESEARCH_CLI" records update <E-id> --patch-file .scratch/design-patch.json`
    with `{"evaluation_design_request": <the request>}`. Still do steps 10 and 11 (the
    candidate STRATEGY and the hypothesis under-test), then request the build card; its
    `build-upload` step 0 freezes the design, and admission holds it until the calibration
    card has written the noise record (C10):
    `"$AUTORESEARCH_CLI" queue request create --title "Build <E-id>" --mode experimentation --why "design frozen after calibration; build and upload" --ref ar://experiment/<E-id> --ref ar://hypothesis/<H-id>`
    Then finish `partial`.
  - `bad_request`: the message names the problem (an empty primary list, a metric that is
    also a performance guardrail, fewer seeds than `n_min`). Fix the request and rerun.
  - `version_mismatch`: the experiment's `environment.game_version` is not the current
    version. Update it with `records update` (still `designed`) and rerun.
  - `not_designed`: the design is already frozen. Never change it.
- The hypothesis is not in the card refs: do not change it. Request a card that names it.
- If you cannot finish the build in this card, request a "Build <E-id>" card as in the
  `needs_calibration` case and finish `partial`. The runtime makes a build card by itself
  only when it rebuilds a lost board.
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: do not retry it. Follow the D-26 sequence (see `complete-card`, "Tool fault").
