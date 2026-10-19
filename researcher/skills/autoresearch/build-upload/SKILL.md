---
name: build-upload
description: Use when building and uploading an experiment candidate.
---

# Build and upload

This skill implements the build, local check, and upload steps of System Design §5.4. It
adapts optimizer-seed `build-upload`. The build is game-specific, so it runs the game's
`build` binding.

Doctrine upheld: D-03, D-04, D-08, D-09, D-25.

## Reads and writes

- Reads: the candidate `strategy/candidates/<E-id>/STRATEGY.md`, the experiment (its
  change).
- Writes: the build output (in `.scratch/`), the version record and its `uploaded` event,
  the experiment's `candidate_binding` and status (`built`, then `uploaded`).

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
5. Upload. The tool checks the identity, uploads, creates the version record, and binds it
   to the experiment (status `uploaded`):
   `"$RESEARCH_CLI" softmax upload <E-id> --name opening-rush --build-dir .scratch/build/<E-id>`
   Keep `data.version_id` and `data.platform_policy_version_id` for the journal. This
   uploads the candidate only. For a starter policy on a bootstrap card, use
   `candidate` and the upload-only bootstrap path instead; the orchestrator submits it.
6. Go on with `xp-request`.

## Rejected build inputs

A build with `status: rejected` and `error.reason: invalid_input` is a correctable input refusal, not a tool fault. Read `error.details.path` and `error.details.action`, correct and commit truthful inputs within the card scope, then retry with a new output directory. Preserve the rejected report; do not overwrite it, invent provenance, or bypass a check. If correction needs unavailable facts or authority, request help and block. This explicit result takes precedence over generic CLI-failure handling.

## Failure handling

- A gate fails: do not upload. Fix the candidate within the experiment's one change, or
  finish `failed` with the gate output in the journal. Never edit the design.
- Reason `identity` (D-03): the credential is not our Player. This is not a tool fault.
  Stop. Send a `help_request` (see `report`) and finish `blocked`.
- Reason `platform`: the platform refused the upload. This is not a tool fault, and a retry is safe (the tool reuses the version when
  the artifact hash is the same). After a second failure, finish `partial`.
- Reason `busy`: a process is running `xp create` or `upload` for this experiment
  now; `error.details.holder` names it. This is not a tool fault. If `details.own_run` is
  `true`, it is your own earlier command: wait for it to finish and use its result, do not
  upload again and do not finish `partial`. To wait, call the native `process_manage` tool
  with action `wait`, that command's `session_id` and `timeout` 1800 ("Long commands" in
  the `card` skill), then read the binding with
  `"$RESEARCH_CLI" records show <E-id>`. Otherwise another card holds it: do not upload a
  second time, record it in the journal, and finish `partial`.
- Exit 3 (`tool_missing`): the pinned platform tool is missing. This is a tool fault: follow
  the D-26 sequence (see `card`, "Tool fault").
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: do not retry it. Follow the D-26 sequence (see `card`, "Tool fault").

Choose a descriptive lowercase `--name` (letters, digits, hyphens; at most 23 characters). Both upload commands append the full player ID. Reuse the name on retry. A new name creates a new immutable version record for an unbound build, including recovery from an old conflicting name. Artifact hashes remain build provenance.
