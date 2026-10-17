---
name: candidate
description: Use when preparing a candidate or starter-policy handoff.
---

# Prepare the candidate for the orchestrator

Implements the researcher's side of System Design §5.5 and design v4.4 §9. The researcher
prepares evidence and uploads; the orchestrator owns submission and champion changes.
Doctrine upheld: D-01, D-03, D-06, D-09, D-20, D-25.

## Reviewed candidate

1. Read the experiment, its Result and uploaded version:
   `"$RESEARCH_CLI" records show <E-id>`
   `"$RESEARCH_CLI" records show <R-id>`
   `"$RESEARCH_CLI" records show <V-id>`
2. Use the recorded `reviewed` experiment with `decision.outcome: promote`, a valid Result
   and the stored eligible `stats/outcomes/<E-id>.json` from `stats evaluate`. Do not invent
   a decision, edit the statistical output or relax the frozen rule to make it eligible.
3. Keep the uploaded candidate binding and `strategy/candidates/<E-id>/STRATEGY.md` intact.
   They must describe the candidate actually evaluated. Read
   `"$RESEARCH_CLI" softmax leaderboard`; if the baseline changed, report that fact and
   propose re-evaluation. Do not rewrite a frozen decision or promote against a different baseline.
4. Prepare the handoff fields for `card`. Set `experiment` to `{"id":"<E-id>","status":"reviewed","decision":"promote"}`,
   list the changed records, and put the candidate version and Result refs in `next`.
   The orchestrator receives the integrated decision, stats, binding and candidate strategy.
5. A `highlight:` reports the supported finding, not a submission that has not happened.
   Leave `strategy/STRATEGY.md`, promotion records and platform submissions to the orchestrator.
6. After the review and comments are finished, follow `card` once: journal, integrate as the
   last workspace step, and complete with the prepared metadata.

## Bootstrap card: upload the starter policy

When explicitly assigned a bootstrap card for a fresh installation with no champion:

1. Read `"$RESEARCH_CLI" softmax leaderboard`. If a champion now exists, report that the
   bootstrap is obsolete and finish without another upload. If the field conflicts with
   canonical state, send a `help_request:` to the orchestrator and block.
2. Read the game's `## Starter policy` in `GAME.md` (`card` gives its location). Build that
   starter policy in this worktree through the installed game binding, into
   `.scratch/build/champion`. Do not substitute an experimental candidate. Run the separate
   `local_check` binding and retain the reports. Required game checks must pass; these local
   checks are not competitive evidence (D-04).
3. Upload only:
   `"$RESEARCH_CLI" softmax upload-bootstrap --name opening-rush --build-dir .scratch/build/champion`
   Choose your own descriptive name in place of `opening-rush` (at most 23 lowercase letters, digits, or hyphens). Reuse it on retries; the CLI appends your full player ID.
   Keep `data.version_id` and `data.platform_policy_version_id`. The upload is not a champion.
4. Journal the starter-policy build and upload. Integrate, then complete using `card` with
   `experiment: null`, `awaiting: null`, both version IDs as metadata fields, and a `next`
   recommending that the orchestrator submit this starter version before commissioning an
   experiment against it. The first submission is intentionally unevaluated; claim no improvement.

## When the handoff is not ready

- Missing/invalid evaluation or candidate binding: report what is missing and recommend the
  appropriate review/build work. Do not fabricate the promote decision.
- Identity refusal: report to the orchestrator and block; never change Player.
- Platform upload failure: a retry of the same build can reuse its artifact binding. If it
  remains unavailable, preserve what happened and complete `partial` with the needed follow-up.
- A game check fails: retain the evidence and report the policy/build failure. An `errored`
  check or tooling traceback instead follows D-26 in `card`; do not retry the faulty tool.
- An integration failure follows `card`'s integration block path. Never claim the candidate
  is delivered to the orchestrator until integration succeeds.
