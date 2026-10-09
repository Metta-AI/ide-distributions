---
name: submit
description: Use when a promotion card submits a promoted candidate.
---

# Submit (promotion)

This skill implements the promotion card of System Design §5.5. It adapts optimizer-seed
`submit`, with the pre-registered decision rule in place of a human gate. It also covers the
bootstrap card (the first champion on a fresh install, A14) and the promotion-recovery card.

Doctrine upheld: D-01, D-03, D-06, D-20, D-25.

## Reads and writes

- Reads: the experiment decision, its Results, the stored outcome
  `stats/outcomes/<E-id>.json`, versions, the promotion state.
- Writes: the submission (through the tool), the version's `submitted` event, the champion
  `strategy/STRATEGY.md` (staged by the tool, D-25), and a highlight.

## Promotion card ("Promote E-…")

1. Check the current promotion:
   `"$AUTORESEARCH_CLI" promotion status`
   If another promotion is in `acquired`, `intent`, `submitted`, or `recorded`, do not
   submit. Record it in the journal and finish `partial`.
2. Submit. The tool takes the promotion lock, checks that the decision is `promote`, that
   the stored outcome is eligible and current (guardrails and N, D-06), the identity, and
   that the champion is still the experiment's baseline. Then it submits, records the
   submission, and stages the candidate STRATEGY at the champion path in your worktree:
   `"$AUTORESEARCH_CLI" softmax submit <E-id>`
   Use `--candidate-path <path>` only when the candidate is not at
   `strategy/candidates/<E-id>/STRATEGY.md`.
3. On success, `data` has `promotion_id`, `submission_id`, and `version_id`. Do not edit
   `strategy/STRATEGY.md` yourself; the tool staged it (D-25). Send one `highlight` for the
   promotion (D-20) with refs `ar://experiment/<E-id>` and `ar://version/<V-id>`.
4. Finish with `complete-card`, outcome `done`. The runtime records and publishes the
   promotion when your branch merges.

## Refusals: record and finish

Read `error.reason`, record it in the journal Decisions section (D-01), and finish:

| Reason | Meaning | Do |
| --- | --- | --- |
| `decision_not_promote`, `no_binding` | The experiment is not a promote. | Finish `failed`. |
| `no_outcome`, `not_eligible`, `stale_outcome` | The stored outcome is missing, not eligible, or out of date. | Request a review card for the experiment. Finish `failed`. |
| `champion_unknown` | The league shows no champion for us, though this experiment was evaluated against one. | Send a `help_request`. Finish `blocked`. |
| `champion_mismatch` | The league's champion changed since the evaluation. | Request "Re-evaluate E-…" (mode experimentation, refs the experiment and version). Finish `done`. |
| `promotion_refused` | The lock is held, the champion is not the baseline, or a push failed. If `details.request_id` is set, the runtime already requested the re-evaluation card. | Finish `partial`. |
| `busy` | A process is running `xp create`, `upload` or `submit` for this experiment now (`details.holder`). | `details.own_run: true`: your own earlier submit still runs; wait for it (native `process_manage`, action `wait`, its `session_id`), then read `promotion status`. Otherwise do not submit again; finish `partial`. |
| `identity` | The credential is not our Player (D-03). | Send a `help_request`. Finish `blocked`. |
| `platform` | The platform failed. | Finish `partial`. The promotion state stays recoverable. |

## Bootstrap card (first champion, A14)

When the league shows no policy competing for our player (a fresh install), there is no
champion to compare against, so the game's starter policy becomes the first champion without
an experiment: it is built, uploaded, and promoted through
the normal promotion. Its first league submission is unevaluated, on purpose. Run this when
`experiment` step 3 (or the card) says so.

1. Check that there is no champion and no promotion in flight:
   `"$AUTORESEARCH_CLI" softmax leaderboard`
   `"$AUTORESEARCH_CLI" promotion status`
   Go on only if `current_champion` is null (the league shows no policy competing for us;
   `note` says "no champion"), and no
   promotion is in `intent`, `submitted` or `recorded`. Otherwise record it and finish
   (`done` when a champion exists; `partial` when a promotion is in flight).
2. Read the `## Starter policy` section of `GAME.md` (see `queue` for its path and
   `AR_HOME`). If there is no such section, do not invent a build: send a `help_request`
   and finish `blocked`. Build the starter policy with the game's `build` binding as it
   says, from `"$AR_HOME"`, into `<worktree>/.scratch/build/champion`. Every non-advisory
   gate must pass.
3. Run the game's `local_check` binding on it the same way (D-04: never evidence).
4. Promote it. The tool checks the build, the identity, that no promotion is in flight and
   that no champion exists, uploads it (the same build is never uploaded twice), takes the
   promotion lock, submits, and records the submission:
   `"$AUTORESEARCH_CLI" softmax bootstrap-champion --build-dir .scratch/build/champion`
5. On success, `data` has `version_id`, `platform_policy_version_id`, `promotion_id` and
   `submission_id`. Do not edit `strategy/STRATEGY.md`: the shipped strategy already is the
   champion STRATEGY. Write the ids in the journal's Decisions ("bootstrapped the first
   champion") and send one `highlight` with ref `ar://version/<V-id>` (D-20).
6. Finish with `complete-card`, outcome `done`. The runtime records and publishes the
   promotion when your branch merges.

Refusals of `softmax bootstrap-champion`, by `error.reason`:

| Reason | Meaning | Do |
| --- | --- | --- |
| `champion_exists` | The league shows a policy competing for us: that is the champion. | Nothing to bootstrap. Finish `done`. |
| `promotion_in_flight`, `promotion_refused` | Another card is promoting (or took the lock first), or a push failed. | Run `"$AUTORESEARCH_CLI" promotion status`. If this card is the holder at `submitted` (an earlier run of this command succeeded), the bootstrap is done: journal the ids from `promotion status` and finish `done`. Otherwise finish `partial`. |
| `not_shipped_strategy` | `strategy/STRATEGY.md` in your worktree or on your branch differs from the shipped one, or the build compiled another STRATEGY.md. The bootstrap promotes only the unchanged shipped strategy. | Restore it (`git checkout main -- strategy/STRATEGY.md`), commit, rebuild from it as `GAME.md` says, and run it again. |
| `gates_failed`, `bad_build`, `artifact_mismatch` | The starter policy does not build. | Do not substitute a candidate. Journal the gate output, send a `help_request`, finish `blocked`. |
| `identity` | The credential is not our Player (D-03). | Send a `help_request`. Finish `blocked`. |
| `platform` | The platform failed. | Run it once more (the same build reuses its upload). After a second failure, finish `partial`. |

## Promotion-recovery card

The runtime makes this card ("Recover promotion P-…", mode `maintenance`, P0) when a
promotion is `submitted` and its holder card ended without a usable handoff. Its reason
names the promotion id, the candidate commit, and the holder's branch. Never submit again.

1. `"$AUTORESEARCH_CLI" promotion status` and check that the id and stage `submitted`
   match the card.
2. Adopt the promotion:
   `"$AUTORESEARCH_CLI" promotion adopt <P-id>`
3. Stage the champion from the candidate commit:
   `"$AUTORESEARCH_CLI" promotion stage-champion <P-id>`
4. Confirm with the league that the submitted policy is now competing for us:
   `"$AUTORESEARCH_CLI" softmax leaderboard`
   `current_champion` must be the promotion's `platform_policy_version_id` (from
   `promotion status`). If it is not, the league did not take the submission: do not
   finish the recovery. Record both policies in the journal, send a `help_request` that
   names the promotion id and both policies, and finish `blocked`. (queue-keeper also pauses
   the agent when the league disagrees after the grace window, A9.)
5. Carry over the other changes the holder card made on its branch (for example the
   version's `submitted` event): compare that branch with `main` (`git diff main <branch>`)
   and redo them in your worktree. Do not copy the branch's journal. For a bootstrap
   promotion (no experiment), `stage-champion` (step 3) already restored the champion's
   version record from the candidate commit; if the holder was an experiment card, request
   that experiment again (the card's reason says so).
6. Finish with `complete-card`, outcome `done`. Its merge moves the promotion to `recorded`
   and then `published`.

If `adopt` or `stage-champion` exits 1 (refused), record the message in the journal, send a
`help_request` that names the promotion id and refs the experiment (or, for a bootstrap
promotion, the version), and finish `blocked`.
