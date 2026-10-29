---
name: build-upload
description: Use when preparing file or container policies for upload.
---

# Build and hand off

Use this procedure for verified game-hosted files and OCI container archives.

Build and verify through the game’s `build` binding. The coordinator owns upload after
the native task completes; researchers never publish artifacts directly.

## Explicit build/upload assignment

For a build/upload request without an experiment, follow `candidate`'s explicit build/upload-only
procedure. Do not create an experiment or run statistical evaluation just to publish an artifact.
Verify the requested source change in the generated artifact; passed gates alone do not establish it.
Preserve evaluation `not_run` when the game's build contract separates it from build checks.

The researcher commits the complete passing build, including `version.json`, and completes the native task.
The coordinator calls the existing `upload_build` tool and integrates its receipt before any requested submission.
That tool supplies player authentication, records the version and its uploaded event, and reuses them on retry.
A raw Coworld upload bypasses these records; do not substitute it or return a platform ID as a local version record.

Use the installed game's build binding and declared host requirements. These instructions do not assume Docker,
a BuildKit endpoint, or commands absent from the installed CLI. If a required capability is unavailable,
report it through `help_request:` before starting the build.

## Dockerfile policies on a configured BuildKit host

Use the installed CLI binding, which writes both the archive and `version.json`:

```bash
"$RESEARCH_CLI" game build-container --context player --strategy strategy/STRATEGY.md --out build-evidence/candidate
```

The output must be a new directory outside the Dockerfile context. This command uses the installed
`coworld build-policy`, preserves native build logs, and records the archive hash, strategy hash,
source commit, required build result, and evaluation `not_run`. Failed builds have no publishable receipt.
Review the requested change in the artifact and run the game's additional required checks before completion.
Stage the complete build as usual. The coordinator's existing `upload_build` reads `artifact_type: oci-image`,
uploads with the assigned Player credential, and writes the ordinary version record and uploaded event.
No manual archive conversion, credential setup, raw upload, or additional queue is needed.

The host must provide `BUILDKIT_HOST`, `buildctl`, and a Coworld CLI with `build-policy` and
`upload-policy --image-archive`. File-policy builds on existing hosts keep their game binding and upload behavior.

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
- A traceback, an `errored` gate, or a CLI failure of our own tooling is a tool fault: follow D-26 (work around it, keep going and report it). A failed gate verdict is a result, not a fault: never skip or weaken a gate.


### Published source and Code recovery

New Git-backed builds record `source_snapshot` in their build receipt. The snapshot retains
exact input bytes in the shared workspace volume before the worker checkout can disappear.
Container builds run from those retained inputs. `workspace stage` preserves the artifact and
receipt for coordinator handoff; it does not establish platform publication success.

After upload or supported read-only recovery returns the platform policy version UUID, the
publication runtime must promote that exact snapshot under the UUID. Code discovers the promoted
source independently of any chat. Preserve the snapshot digest and build evidence; do not replace
them with a current checkout path. Old receipts that cannot locate their inputs may still upload,
but source remains explicitly unavailable until rebuilt with complete provenance.

When Code cannot find an existing publication, inspect the game's version and build receipts.
Do not retry an upload or publish a new version to discover the old publication's identity.
The currently locked CLI does not recover a policy version UUID by upload operation ID. If an upload
response was lost, report the unresolved publication and preserve its build evidence.
Missing source is a recovery failure, not permission to invent source hashes or rewrite records.

Check `research workspace policy-files` with a `sources` request for the published policy name
and confirm the intended record and files after integration. Then read a listed file. Neither
an upload success nor finding files on disk proves that Code can expose them. Recovery does not
require evaluation, training, or league submission.
