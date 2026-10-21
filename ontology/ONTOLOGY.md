# Research ontology

The shared vocabulary of the `orchestrator` and every `researcher-<i>`. Both profiles load
this file: `tools/sync_shared.py` embeds it at the end of each distribution's `SOUL.md`. The
machine-readable form is the record schemas in `ide_research_core/schemas/` (validated by
`research records`);
the research method that uses these terms is in the researcher's `doctrine/` and the
[System Design](../researcher/docs/designs/autoresearch-system-design-2026-10-06.html) §3.

## Places

- **Canonical repository**: one bare git repository per game on the host
  (`RESEARCH_CANONICAL`, `<root>/canonical-<game>.git`). Its `main` is the research record.
- **Clone**: each profile's own clone of it (`RESEARCH_WORKSPACE`). Its `main` is never
  edited by hand.
- **Card worktree**: a git worktree of a researcher's clone on the branch `card/<id>` (or
  `repair/<id>`), made for one card. A card's work happens only here.
- **Integrate**: `research workspace integrate`, the only way a card's work reaches `main`
  (validate the records, refuse an overlap, merge under the host-wide lock, push).
  **Merged**: a card branch's commit is an ancestor of canonical `main`.

## Records and their states

Ids are a type prefix and a ULID (`E-01K...`). A reference is `ar://<type>/<id>[#anchor]`,
read at a commit (`research records resolve`); `ar://plan` is `plan.md`, `ar://wiki` the KB
index, `ar://file/<path>` any committed file, `ar://xreq/<platform id>` the evidence of an XP
request.

| Record | Prefix | What it is | States |
|---|---|---|---|
| Topic | `T-` | A research goal: objective, metrics with directions, guardrails, origin (`agent` or `human`) | `open`, `active`, `achieved`, `closed` |
| Hypothesis | `H-` | A mechanism, a prediction and the lever it pulls | `proposed`, `under-test`, `supported`, `falsified`, `abandoned` |
| Experiment | `E-` | A pre-registered test of one change against the champion | `designed`, `built`, `uploaded`, `evaluating`, `awaiting-credits`, `awaiting-permit`, `stopped`, `reviewed` |
| Result | `R-` | The measured outcome of an experiment, with confidence intervals | validity `valid` or `invalid` |
| Version | `V-` | One build with its provenance; events `uploaded` and `submitted` | |
| Claim | `C-` | One checkable assertion; kind `mechanism`, `behaviour`, `strategy`, `opponent` or `platform` | `hypothesis`, `belief`, `verified`, `stale`, `superseded`, `refuted` |
| Hub | `HUB-` | A present-tense synthesis page whose every sentence cites claims | |
| Evidence | `EV-` | An immutable artifact keyed by sha256: `replay`, `xp-result`, `log`, `artifact`, `leaderboard-snapshot`, `game-source` | tier `S` (game source) to `E` (forum post or another agent) |
| Journal | `J-` | The record of one card run, with a required `## Decisions` section | outcome `done`, `partial`, `dropped`, `failed`, `blocked` |
| Lesson | `L-` | A research-method lesson | `tentative`, `adopted` |

An experiment's **decision** is set once: `promote`, `hold`, `refute` or `invalid`, by the
rule frozen in its **evaluation design** (primary metrics, agent guardrails, a performance
guardrail, the minimum detectable effect, sample size, the noise record revision, seeded
line-ups). A **noise record** (`stats/metric-noise.yaml`) gives each metric's spread for a game
version; no experiment is designed without one. A refuted mechanism claim closes its lever
until evidence meets its `revise_if`.

`plan.md` holds priorities and open threads; the orchestrator writes it.
`strategy/STRATEGY.md` is the **champion strategy**; a **candidate** strategy sits at
`strategy/candidates/<E>/STRATEGY.md`.

## The league

- **Champion**: the policy the league shows competing for our player. It is the only
  baseline an experiment may use.
- **Candidate**: an experiment's uploaded build (its candidate binding).
- **Promotion** `P-`: submitting a candidate, owned by the orchestrator. Stages `intent`,
  `submitted`, `recorded`, `published`; or `aborted`, `rejected`. One at a time. Kinds
  `experiment`, `requested` (an explicit user submission without an improvement claim),
  `bootstrap` (the game's unevaluated starter policy on a fresh install) and
  `accepted` (a human confirmed the league's current champion as the loop's).
- **Champion conflict**: the league shows a champion the loop did not record. Research that
  depends on the baseline pauses until a human decides.
- **XP request**: one hosted evaluation run (a seed and an arm, `baseline` or `candidate`),
  paid for in **credits**. A **permit ask** is the coach's approval the platform requires
  before some acts; the answer arrives later.

## Tasks

A task is a bounded native Hermes assignment to one researcher. Its ID and execution status
come from Hermes. Task meaning is its objective and instructions, not a kind/entity/attempt key.
Researchers can run concurrently in separate worktrees. The coordinator assigns ready work
and keeps pending intentions in its plan without constructing dependency graphs.

Native completion ends the assignment; it does not prove deliverable acceptance or fulfillment
of the user's request. Git integration describes commits only. A blocked worker can resume
and produce new commits after an earlier checkpoint was integrated.

Researchers stage their branch and return factual summaries with artifact references. Research
journals and experiment records are evidence, not prerequisites for blocking or completion.
Comments carry questions and steering. The coordinator verifies deliverables, integrates
accepted work sequentially, and owns externally visible submission.

## Who decides what

- The **orchestrator** owns the plan, the cards, the answers to researchers, promotion, the
  pause decision (`control/pause.json`) and everything the user sees.
- A **researcher** owns the research inside its card: evidence, records, experiments,
  results and the decision rule's application. It never talks to the user and never submits.
