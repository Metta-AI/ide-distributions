---
name: candidate
description: Use when preparing builds or handing off candidates.
---

# Prepare the candidate for the orchestrator

Implements the researcher's side of System Design §5.5 and design v4.4 §9. The researcher
prepares committed evidence; the coordinator owns acceptance, league submission and champion changes.
Doctrine upheld: D-01, D-03, D-06, D-09, D-20, D-25.

## Reviewed candidate

1. Read the experiment, its Result and uploaded version:
   `"$RESEARCH_CLI" records show <E-id>`
   `"$RESEARCH_CLI" records show <R-id>`
   `"$RESEARCH_CLI" records show <V-id>`
2. Use the recorded `reviewed` experiment with `decision.outcome: submit`, a valid Result
   and the stored eligible `stats/outcomes/<E-id>.json` from `stats evaluate`. Do not invent
   a decision, edit the statistical output or relax the frozen rule to make it eligible.
3. Keep the uploaded candidate binding and `strategy/candidates/<E-id>/STRATEGY.md` intact.
   They must describe the candidate actually evaluated. Read
   `"$RESEARCH_CLI" softmax leaderboard`; if the baseline changed, report that fact and
   propose re-evaluation. Do not rewrite a frozen decision or promote against a different baseline.
4. Prepare the handoff fields for `card`. Set `experiment` to `{"id":"<E-id>","status":"reviewed","decision":"submit"}`,
   list the changed records, and put the candidate version and Result refs in `next`.
   The orchestrator receives the staged decision, stats, binding and candidate strategy.
5. A `highlight:` reports the supported finding, not a submission that has not happened.
   Leave `strategy/STRATEGY.md`, promotion records and platform submissions to the orchestrator.
6. After the review and comments are finished, follow `card` once: stage artifacts as the
   last workspace step, and complete with the prepared metadata.

## Explicit build/upload-only request

When the coach requests a source change and upload without evaluation or league submission:

1. Follow `build-upload`'s explicit assignment procedure. Run the required build checks; evaluation is required only when assigned.
2. Upload the successful artifact with the Coworld CLI using the assigned policy name and existing credentials.
3. Follow `card` to stage source and build evidence, then return the registered version ID with `experiment: null`.
   Do not invent an experiment or submit to a league.

## Bootstrap card: prepare the starter policy

When explicitly assigned a bootstrap card for a fresh installation with no champion:

1. Read `"$RESEARCH_CLI" softmax leaderboard`. If a champion now exists, report that the
   bootstrap is obsolete and finish without another upload. If the field conflicts with
   canonical state, send a `help_request:` to the orchestrator and block.
2. Read the game's `## Starter policy` in `GAME.md` (`card` gives its location). Build that
   starter policy in this worktree through the installed game binding, into
   `.scratch/build/champion`. Do not substitute an experimental candidate. Follow the game's
   build validation contract. Review the successful build evidence and artifact hash. When the
   game separates build checks from evaluation, preserve evaluation `not_run`; do not start
   full `local_check` just to upload.
   Missing, failed or errored required build evidence is not uploadable. Full evaluation,
   when separately requested, retains its own complete evidence and is not competitive proof.

3. Copy the complete passing build into tracked `build-evidence/<build-id>`.
4. Stage, then complete using `card` with `experiment: null`, `awaiting: null`, the build path,
   artifact hash and a suggested upload name. Recommend coordinator upload and starter submission
   before commissioning an experiment. The starter remains unevaluated; claim no improvement.

## When the handoff is not ready

- Missing/invalid evaluation or candidate binding: report what is missing and recommend the
  appropriate review/build work. Do not fabricate the promote decision.
- Identity refusal: report to the orchestrator and block; never change Player.
- A game check fails: retain the evidence and report the policy/build failure. An `errored`
  check or tooling traceback instead follows D-26 in `card`; do not retry the faulty tool.
- A staging failure must be reported with the retained artifacts. Never claim the candidate
  is delivered to the orchestrator until the task branch is staged.
