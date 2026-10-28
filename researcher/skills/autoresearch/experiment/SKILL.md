---
name: experiment
description: Use when designing and pre-registering an experiment.
---

# Experiment design

This skill implements the design steps of System Design §5.4 (experiment design and
launch): pick a hypothesis, check refuted levers, pre-register `E-….md` with a frozen
evaluation design, and make the candidate STRATEGY. It adapts optimizer-seed `experiment`.
Then use `build-upload` to hand off the verified build. The coordinator uploads it before
assigning any authorized evaluation.

Doctrine upheld: D-06, D-08, D-09, D-10, D-11, D-23, D-25.

## Reads and writes

- Reads: hypotheses, Topics, refuted mechanism claims, `strategy/STRATEGY.md`, versions,
  the game contract (`game show`), the current game version (`softmax game-version`), and
  the noise record `stats/metric-noise.yaml` (through `stats design`).
- Writes: the experiment record (`E-…`), the candidate
  `strategy/candidates/<E-id>/STRATEGY.md`, the hypothesis state, and the journal.

## Procedure

1. Pick the proposed hypothesis that the card names and read its Topics:
   `"$RESEARCH_CLI" records show <H-id>`
   `"$RESEARCH_CLI" records show <T-id>`
2. Check refuted mechanism claims on its lever (D-10):
   `"$RESEARCH_CLI" records find claim --where status=refuted`
   Reopen a refuted lever only with evidence that meets the claim's `revise_if`.
3. Find the baseline: the current champion, and nothing else.
   `"$RESEARCH_CLI" softmax leaderboard`
   Use `current_champion` (a platform policy version id: the policy the league shows
   competing for us, A9) as the baseline. Never use another version record as the
   baseline, even when the card, the plan or a version record names one (for example a
   version uploaded by an earlier card, or a title that says "baseline exists"): an
   experiment against anything but the champion can never be submitted, and `stats design`
   refuses it (`baseline_not_champion`). Uploading a version does not make it the champion;
   only an orchestrator champion submission does. If `conflict` is set, report it to the orchestrator
   and block. If `current_champion` is null, report that a starter-policy bootstrap is
   needed and propose the follow-up experiment in `metadata.next`; do not design against an
   arbitrary uploaded version or bootstrap opportunistically. The orchestrator commissions
   bootstrap work with `candidate`, submits the starter, then commissions the experiment.
4. Attack the design before running it: does the metric measure the mechanism, or only
   a correlate? Can the evaluation shape show the effect? What else could produce the
   predicted signal? Does N support the claimed effect? Redesign when an answer is bad.
5. Choose one conceptual change (D-08): the STRATEGY components to edit and a description.
   Take the default metrics and guardrails from the hypothesis's Topics (D-08). Give each
   behavioural primary metric at least one anti-gaming guardrail; a rate metric needs its
   volume counterparts (D-09).
6. Find the current game version and the game contract:
   `"$RESEARCH_CLI" softmax game-version`
   `"$RESEARCH_CLI" game show`
   Use `data.game_version`, the `outcome_metrics` (names and directions) and the
   `performance_guardrails`. A performance guardrail is checked automatically; do not list it
   again as an agent guardrail. It can also be a primary metric when the change targets it.
7. Write the decision rule in the experiment body before any result exists (D-09). Use this
   default unless the Topic needs another:
   - `submit`: `stats evaluate` reports `submit_eligible: true` and the Result is valid.
   - `refute`: N is met and the bootstrap CI of every primary metric lies at or below 0.
   - `invalid`: the Result is not valid (wrong versions, failed episodes, the change did not
     activate).
   - `hold`: every other case.
   Also write the prediction if the hypothesis is true and if it is false. They must differ.
8. Create the experiment, still without its evaluation design:
   `"$RESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>`
   with `{"hypothesis": "<H-id>", "mode": "experimentation", "lever": …, "baseline": "<champion>", "change": {"components": […], "description": …}, "environment": {"game_id": "<data.name of game show>", "game_version": "<data.game_version of step 6>"}, "status": "designed", "xp_requests": [], "results": [], "lifecycle": {"launch_attempts": 0}}`.
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
   `"$RESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json`
   It reads recent league episodes and the standings: about a minute for the default 100
   episodes, so give the terminal call a timeout of 300 seconds, and do not lower
   `opponent_episodes` to save time (fewer episodes make the sample less league-like). It
   computes `n_min` (at
   least the game's floor) and `delta` from the noise record of the current game version,
   records the record's revision as `noise_rev`, and writes `evaluation_design` into the
   experiment, with the sampled `lineups` and the `league_snapshot` they came from. Read `data.evaluation_design.n_min`: you need at
   least that many seeds. The design is frozen once the status leaves `designed`. Never edit
   the rule after (D-09).
10. Make the candidate: copy `strategy/STRATEGY.md` to
   `strategy/candidates/<E-id>/STRATEGY.md` and edit only the named components there. The
   champion `strategy/STRATEGY.md` does not change until a champion submission (D-25).
11. Move the hypothesis to under-test and link the experiment (the card names it, D-23):
    `"$RESEARCH_CLI" records status <H-id> under-test --reason "experiment registered" --ref ar://experiment/<E-id>`
    `"$RESEARCH_CLI" records link <H-id> experiments <E-id>`
12. Validate: `"$RESEARCH_CLI" records validate <E-id>`. Then go on with `build-upload`.

## Failure handling

- `records new experiment` exit 1: read `error.problems` (schema problems), fix the meta,
  and run it again.
- `stats design` exit 1, by `error.reason`:
  - `needs_calibration`: no noise record for the current game version. Do not compute a
    design by hand. Store the request on the experiment so a later card can freeze it:
    `"$RESEARCH_CLI" records update <E-id> --patch-file .scratch/design-patch.json`
    with `{"evaluation_design_request": <the request>}`. Still do steps 10 and 11 (the
    candidate STRATEGY and the hypothesis under-test). Propose calibration followed by a build
    card in a `suggestion:` and `metadata.next`, with the experiment and hypothesis refs.
    Finish `partial`; `stats design` will require current calibration when work resumes.
  - `bad_request`: the message names the problem (an empty primary list, a metric that is
    also a performance guardrail, fewer seeds than `n_min`). Fix the request and rerun.
  - `version_mismatch`: the experiment's `environment.game_version` is not the current
    version. Update it with `records update` (still `designed`) and rerun.
  - `not_designed`: the design is already frozen. Never change it.
  - `baseline_not_champion`: `baseline_version` is not the current champion. Read
    `softmax leaderboard` again and use its `current_champion`. If the champion changed
    since you made the candidate, copy the new `strategy/STRATEGY.md` again and redo the
    same one change there (step 10); update the experiment's `baseline` with
    `records update` (still `designed`). Then rerun.
  - `champion_unknown`: no champion exists. Report it and recommend a bootstrap card as in
    step 3. Do not substitute another version as baseline.
  - `champion_conflict`: report the conflicting states to the orchestrator and block.
  - `identity` (D-03): the credential is not our Player. Send `help_request:` and block.
- The hypothesis is not in the card refs: do not change it. Suggest work that names it.
- If the build cannot finish in this card, complete `partial` with the experiment state and
  a bounded build recommendation in `metadata.next`.
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: follow D-26 (work around it, keep going and report it). A failed gate verdict is a result, not a fault: never skip or weaken a gate.
