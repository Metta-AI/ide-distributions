---
name: review-xp
description: Use when a review card judges an experiment's XP.
---

# Review XP and decide

This skill implements System Design §5.5 (review and decision), up to the promotion card.
It adapts co-gas `coworld-xp-review`. The runtime makes the review card ("Review E-…")
when every XP request of the experiment is terminal.

Doctrine upheld: D-01, D-06, D-09, D-14, D-15, D-17, D-20, D-22, D-23.

## Reads and writes

- Reads: the experiment (its pre-registered rule), XP status, evidence, the game contract.
- Writes: evidence, a Result, then the experiment decision; the hypothesis state, claims,
  hubs, component status, and lessons; a "promote E-…" card request; a highlight.

## Procedure

1. Read the experiment and its rule:
   `"$AUTORESEARCH_CLI" records show <E-id>`
2. Fetch and inspect the artifacts with `fetch-artifacts`. Check the exact candidate and
   baseline versions, the seeds, and that the change activated in the candidate's episodes
   (a change that never activated decides nothing).
3. Compute the statistics with `ab-compare` (`stats ingest`, then `stats evaluate`).
4. Write the Result before any decision (D-06):
   `"$AUTORESEARCH_CLI" records new result --meta-file .scratch/result.json --body-file .scratch/result.md`
   with `{"experiment": "<E-id>", "xp_requests": ["<request id>", …], "evidence": ["EV-…", …], "policy_versions": {"baseline": "<id>", "candidate": "<id>"}, "game_version": …, "episodes_completed": <n>, "metrics": {<per_metric from stats evaluate>}, "validity": {"status": "valid|invalid", "reason": …}}`.
   The body is the analysis: what moved, by how much, with uncertainty, and the
   observation gaps. A Result never changes after creation. Link it:
   `"$AUTORESEARCH_CLI" records link <E-id> results <R-id>`
5. Apply the pre-registered rule in the experiment body exactly (D-09). No new thresholds
   after seeing data. Choose `promote`, `hold`, `refute`, or `invalid`. `promote` needs
   `promote_eligible: true` from `stats evaluate` and a valid Result.
6. Record the decision with its reason and the Result ref (set once, D-01), then close the
   experiment:
   `"$AUTORESEARCH_CLI" records update <E-id> --patch '{"decision": {"outcome": "<o>", "reason": "<why>", "decided_at": "<ISO time>", "refs": ["ar://result/<R-id>"]}}'`
   `"$AUTORESEARCH_CLI" records status <E-id> reviewed`
7. Update the hypothesis, claims, hubs, and component status. The review card's refs name
   the experiment, its version, its hypothesis, and that hypothesis's Topics, so change
   the hypothesis and the Topics here; no follow-up card is needed for them. Claims and hubs
   change only when the refs name them (D-23); for others, request one maintenance card
   whose refs name them, and give it the decision:
   - `promote` → the hypothesis becomes `supported`; `refute` → `falsified`; `hold` and
     `invalid` do not move it:
     `"$AUTORESEARCH_CLI" records status <H-id> supported --reason "<decision>" --ref ar://result/<R-id>`
   - Add the Result as B-tier evidence to the claims it bears on; supersede claims it
     contradicts (D-15). Hub prose describes the present (D-14).
   - After a refute, when no live hypothesis still targets the lever, write a refuted
     mechanism claim (kind `mechanism`, status `refuted`) whose `revise_if` says what
     reopens the lever (D-17).
   - Count lesson recurrences (see `lessons`).
8. If the decision is `promote`, request the promotion card (D-22):
   `"$AUTORESEARCH_CLI" queue request create --title "Promote <E-id>" --mode experimentation --why "decision promote, ar://result/<R-id>" --ref ar://experiment/<E-id> --ref ar://version/<V-id> --priority P1`
9. Send one `highlight` per event (D-20): a supported or falsified hypothesis, a surprising
   result. See `communicate`.
10. Finish with `complete-card`.

## Failure handling

- Some requests failed or rows were dropped so N is short: the decision is `invalid` or
  `hold` per the rule. To finish the evaluation, request a "Launch E-…" card only if the
  experiment can still take requests; otherwise request a new experiment.
- `stats evaluate` refuses (champion unknown or changed): decide `hold`, and request a card
  to re-evaluate the candidate against the current champion.
- `records update` exit 1 on `decision`: the decision is set once and cannot change. Do not
  try to rewrite it.
