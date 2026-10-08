---
name: submit
description: Use when a promotion card submits a promoted candidate.
---

# Submit (promotion)

This skill implements the promotion card of System Design §5.5. It adapts optimizer-seed
`submit`, with the pre-registered decision rule in place of a human gate. It also covers the
promotion-recovery card.

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
| `champion_unknown` | Ledger and leaderboard disagree. | Send a `help_request`. Finish `blocked`. |
| `champion_mismatch` | The champion changed since the evaluation. | Request "Re-evaluate E-…" (mode experimentation, refs the experiment and version). Finish `done`. |
| `promotion_refused` | The lock is held, the champion is not the baseline, or a push failed. If `details.request_id` is set, the runtime already requested the re-evaluation card. | Finish `partial`. |
| `busy` | Another card is running `xp create`, `upload` or `submit` for this experiment now. | Do not submit again. Finish `partial`. |
| `identity` | The credential is not our Player (D-03). | Send a `help_request`. Finish `blocked`. |
| `platform` | The platform failed. | Finish `partial`. The promotion state stays recoverable. |

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
4. Carry over the other changes the holder card made on its branch (for example the
   version's `submitted` event): compare that branch with `main` (`git diff main <branch>`)
   and redo them in your worktree. Do not copy the branch's journal.
5. Finish with `complete-card`, outcome `done`. Its merge moves the promotion to `recorded`
   and then `published`.

If `adopt` or `stage-champion` exits 1 (refused), record the message in the journal, send a
`help_request` that names the promotion id and refs the experiment, and finish `blocked`.
