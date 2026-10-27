---
name: build-upload
description: Use when preparing file policies for coordinator upload.
---

# Build and hand off

Use this procedure when the selected publication tool accepts verified file artifacts.
For other artifact types, discover the appropriate tool and applicable procedure before acting.

Build and verify through the game’s `build` binding. The coordinator owns upload after
the native task completes; researchers never publish artifacts directly.

Doctrine upheld: D-03, D-04, D-08, D-09, D-25.

## Reads and writes

- Reads: the candidate `strategy/candidates/<E-id>/STRATEGY.md`, the experiment (its
  change).
- Writes: committed complete build evidence and experiment status `built`.
- The coordinator records the uploaded version and candidate binding.

## Procedure

0. If the experiment has no `evaluation_design` yet (an earlier card stopped on
   `needs_calibration`), freeze it first from the request it stored. Write
   `data.meta.evaluation_design_request` of `"$RESEARCH_CLI" records show <E-id>` to
   `.scratch/design-request.json`, then:
   `"$RESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json`
   On exit 1, follow the `experiment` skill's failure handling for `stats design`. The
   experiment cannot leave `designed` without a frozen design.
1. Check that the candidate differs from the champion only in the components the experiment
   names (D-08): `git diff --no-index strategy/STRATEGY.md strategy/candidates/<E-id>/STRATEGY.md`.
2. Build through the game's `build` binding. Read `GAME.md` (see `card` for its path and
   `RESEARCH_HOME`) for the binding's arguments. Run it from `"$RESEARCH_HOME"` with absolute paths to the
   candidate and to the output directory `<worktree>/.scratch/build/<E-id>`. The binding
   writes `version.json` in the output directory. Every non-advisory gate must pass. A build
   can run for an hour: start it in the background and wait on it as the `card` skill says
   ("Long commands"). Never poll it.
3. Follow the game's required build checks in `GAME.md`. Review the successful build
   evidence and artifact hash. When the game separates build checks from evaluation,
   preserve evaluation `not_run` and do not start full `local_check` just to upload.
   Run full evaluation only when separately requested, retaining its own evidence. Missing, failed or
   errored required build evidence is not uploadable. Local checks are not decision evidence (D-04).
4. Mark the experiment built:
   `"$RESEARCH_CLI" records status <E-id> built`
5. Copy the complete passing build directory into `build-evidence/<build-id>` inside this
   task's worktree. Preserve all files, hashes and provenance. Do not hand off ignored scratch paths:
   native completion may remove a clean checkout.
6. Follow `card`: stage the complete build and finish this assignment. Report its task-relative path,
   experiment ID, suggested upload name, build hash and verified source behavior.
   The coordinator calls `upload_build` against this completed native run and integrates its receipt.
   Evaluation, when authorized, can continue in a subsequent assignment after the binding exists.

## Rejected build inputs

A build with `status: rejected` and `error.reason: invalid_input` is a correctable input refusal, not a tool fault. Read `error.details.path` and `error.details.action`, correct and commit truthful inputs within the card scope, then retry with a new output directory. Preserve the rejected report; do not overwrite it, invent provenance, or bypass a check. If correction needs unavailable facts or authority, request help and block. This explicit result takes precedence over generic CLI-failure handling.

## Failure handling

- A gate fails: do not upload. Fix the candidate within the experiment's one change, or
  finish `failed` with the gate output in the journal. Never edit the design.
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: do not retry it. Follow the D-26 sequence (see `card`, "Tool fault").

