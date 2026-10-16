# C3: native setup across checkouts and archives

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

## Cause

The receipt rewrite preserved offline validation but removed source preparation.
`native_build.build` copied a helper into an ignored engine cache that did not
exist in a fresh checkout. Compiler `cached_probe` correctly refused a missing
ignored binary. Two tests also read `.cache/polyworld` directly rather than
selecting the required current or recorded engine pin. The sibling failures were
missing setup, not evidence of a compiler semantic failure.

## Change and contract

`games/gota/tools/native_build.py prepare COMMIT` explicitly fetches the exact
engine commit, then the dependency commits from that engine's lockfile. Existing
sources must be clean and match their pin; preparation does not overwrite them.
New repositories stage separately and publish after verification. Builds and
receipt validation remain offline and continue binding source contents, dependency
pins, helper/configuration/toolchain inputs, options and executable bytes.

The GotA pytest session uses this preparation API for current Polyworld
`801749e509e888b423ba44ee2188946c22e3ba85` and recorded Polyworld
`5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`, then builds the current gate probe and
match runner. Historical replay helpers build offline when requested. Tests
select source caches by pin. This is an environment test shim; it does not add
runtime hosting or credential management.

`GOTA_ENGINE_SOURCE` and `GOTA_DEPENDENCY_ROOT` allow explicit local Git mirrors
for setup. Exact pins and contents remain checked. Without overrides, preparation
uses the engine URL and each dependency URL. Native compilation requires Nim
2.2.10. See [gate setup](../../../games/gota/gates/README.md) for commands.

Native tools in an archive need no parent repository Git metadata. Compiler
`strategy_build.committed_inputs` still requires tracked, clean compiler and
strategy inputs in a Git repository; that separate production contract is unchanged.
The vendored-layout test now resolves its fixture path like the public CLI, so
macOS `/tmp` and `/private/tmp` aliases identify the same physical install. Effective compiler/config
paths remain receipt inputs: moving an install invalidates old receipts and
requires an offline rebuild, rather than accepting stale path-bound evidence.

## Compiler decision and boundary

Retain the reviewed compiler port for the fastest build-binding path. The C3
failures concern bootstrap, not compiler semantics. The parent inspected lab
`gota-strategy-compiler` at `8fe7616a`: it uses Claude as the configured agent,
exposes Claude/Codex models, fixes strategy-build paths to the lab, and expresses
native identity relative to the repository. Switching requires new Hermes,
package, gate and provenance integration. Reconsider only if authorized real M1
work reveals an expensive or risky blocker.

No model call, real agent call, credential change, hosted job, or Task 2.5 binding
is part of C3. Fixture/native test success does not establish real M1 equivalence.
C3 began with a `PROCEED M1` hold. During portability validation, James added
`PROCEED M1` with a dedicated local key, a $40 cap and spend checks before and
after each compile. The parent will apply that authority after reviewing C3; no
real M1 run was performed in this portability task. Task 2.5 follows M1.

## Validation

Preparation regressions use independent temporary Git repositories and prove
empty-cache setup, two distinct pins, idempotence, dirty-source/dependency refusal,
and that an offline build never calls preparation. Existing receipt-tamper tests
remain intact. Full-suite and cold/relocated-archive command results are recorded
in `/tmp/ide-mvp-c3-portability-report.md` for this run.
