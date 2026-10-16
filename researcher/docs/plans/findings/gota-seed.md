# GotA seed workspace (Task 1.3)

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

Bootstrap applies the seed only to new workspaces. It has an empty priority list,
three hubs, six claims, six tentative lessons, two indexed evidence snapshots,
and an empty stats directory (Git placeholder only). C10 must obtain a
current-version noise record before experimentation.

Mechanism claims cite Polyworld commit
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`, verified against immutable Git objects.
`seed/evidence/source-citations.json` preserves exact excerpts, line ranges,
repository names, full commits, excerpt hashes and full-file hashes. This allows
citation checks without a network fetch. Excerpts are not experimental results;
historical lab docs are not promoted to current game truth.

Lessons cite `Metta-AI/co-gas` commit
`6742feb08f8b119591d09261cadc581065fa698b`, `docs/failed-experiments.md`, inspected
with read-only `git show`. These methodological transfers remain tentative with
zero GotA recurrences. Co-gas submission rules do not override runtime doctrine.

Fresh Games Bond readback using project-locked coworld 0.1.58 / softmax-cli
0.26.38 returned canonical Coworld `cow_3e536e3c-3f1c-4029-b006-b3d3ef3393b3`,
version `2026.10.8.1`, full Polyworld pin above. Champion-only memberships returned
empty `entries`. This is dated evidence; refresh before baseline selection or
promotion. The snapshot preserves timestamp and command. Only user-token data
was copied to a temporary HOME; player selection affected that copy, which was
removed. Original credentials were hash checked unchanged. No upload or XP ran.

Existing `strategy/STRATEGY.md` remains byte-identical, SHA-256
`857a1e84af3b116678052b63a0a607b6699b50a3a154bbea2816b93352696dd8`.
Its evidence wording predates M1. The policy hub and snapshot separately record
completed build `00a0bdcc-2`, source `00a0bdcc1414cc9b162d3a5e30a8097f11db6a17`,
qualified on engine `801749e509e888b423ba44ee2188946c22e3ba85` / game
`2026.10.7.2`. The preserved summary reports twenty-seed ordered attempted-action
equivalence over 624040 records. Goal predicates remain unmeasurable. This task
claims neither current-engine equivalence nor competitive strength. Snapshot
claims have tier D / belief status; source-backed mechanisms have tier S /
verified status.

Run from `autoresearch/`:

```sh
uv run --locked --python 3.12 python -m autoresearch records validate --all --root games/gota/seed
uv run --locked --python 3.12 python -m autoresearch kb lint --root games/gota/seed
uv run --locked --python 3.12 pytest tests/unit/gota/test_seed.py -q
uv run --locked --python 3.12 ruff check tests/unit/gota/test_seed.py
```

Seed tests are pure Python and do not require Nim or native source preparation.
Tests that execute native helpers explicitly request their preparation fixtures
and require Nim 2.2.10. This host selects the installed version with
`PATH=/Users/jamesboggs/.nimby/nim-2.2.10/bin:$PATH`; no guard is disabled.

## One-off existing-workspace maintenance task text

TASKTEXT:

Import GotA seed knowledge from the installed profile's `games/gota/seed/` in this
maintenance card's worker worktree. Read GAME, current KB, and card refs first.
Preserve existing records, champion/promotion state, strategy, priorities and
calibration. Do not copy `plan.md`, `strategy/`, or `stats/`.

Copy only missing claims, hubs, lessons, evidence index records and their
referenced snapshots/source excerpts, preserving IDs and immutable bytes.
Compare by ID before copying. Preserve existing IDs; report differing content
and request reconciliation naming those entities. Do not overwrite them. To
merge knowledge into an existing hub, request a follow-up maintenance card
naming that hub and claims; do not edit existing entities outside current refs.
Merge excerpt maps by citation key, preserving existing entries and requesting
reconciliation for differing content under the same key.

Refresh read-only standings with `"$AUTORESEARCH_CLI" softmax standings` and the
`"$AUTORESEARCH_CLI" softmax game-version`. Treat seed snapshots as dated evidence and historical
M1 as qualified only on its recorded engine. If current game version differs,
request re-verification and preserve the recorded old-version currency; the
runtime handles stale marking.
Do not replace champion or noise records; do not start evaluations.

Run `"$AUTORESEARCH_CLI" records validate --all` and
`"$AUTORESEARCH_CLI" kb lint`; inspect that findings are empty (lint exit zero
alone is insufficient). Journal imported IDs, collisions, version mismatches and
follow-up refs. Follow `complete-card`: one journal, required acknowledgements,
outcome, `"$AUTORESEARCH_CLI" card finish --outcome done` (or partial with
follow-ups), then native `kanban_complete` when available. The runtime merges
and publishes; do not write canonical directly or manually push remote state.
This text is supplied for scheduling and was not executed in this task.
