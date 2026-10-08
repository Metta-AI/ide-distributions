# Task 2.4 local M1 readiness

This is the historical pre-C3 readiness record. Real local M1 qualification completed
on 2026-10-08; see [the execution finding](compiler-task-2.4-m1.md) for the generated
artifact, twenty-seed oracle, bound audits and spending limits.

C2 accepted the recorded containment incident and authorized local-only resumption.
NOTES-2 made committed `personal_labs_gota@09d3ed28` the primary GotA reference and
refreshed the engine to live `801749e5` / 2026.10.7.2. See the
[live pin finding](gota-live-refresh-2026-10-07.md) and the preserved
[incident history](compiler-task-2.4-containment.md).

## Design ruling

The generated seed has two units: K.config owns all 46 config scalars; C.farm owns
the coherent shared state and ordered helper/main operations. This is the smallest
faithful decomposition under the existing unit ABI. Its always rule labels three
goals without distinguishing executed branches or proving claims. State does not
reset in C.start/init. Runtime death handling resets selection on a respawn rising
edge; a native lifecycle test exercises alive/dead/dead/alive/dead transitions.

Structured mode provides sufficient verified headroom, so production pvId packing
and bindings are removed; packing.json fails explicitly. Historical packing tests
remain historical. Adopted host/context/harness mechanics and exact source hashes
are inventoried in [compiler provenance](../../../games/gota/compiler/PROVENANCE.md).
The existing PW ABI, G wire and shared gates remain. The lab's incompatible phase
ABI and stale semantic-audit engine contract were not adopted.

## Local proof

- The mandatory session guard blocks real agent executable paths, symlink escapes,
  shell command strings and env wrappers. Dispatch tests use only fake Hermes plus
  allowlisted local Git/Python; unknown backends fail closed.
- Fake Hermes writes the full test-only units through the actual generator; the
  compiler checks scope, immutable context, Git refs/index and regular outputs.
  A temporary committed repository exercises prepare through finalized build and
  the supported build-bound goal audit. Forged map metadata with a refreshed map
  hash is still rejected against committed source.
- Native strict full-fixture budget: 384 globals, 102 arrays, 157,993 cells,
  54 routines, 5,056 instructions, 54,452 policy bytes / 58,108 injected bytes.
  These fit structured 512/128/262144/64/20000/65535 policy ceilings.
- Full seed 1 compares all 31,200 ticks / 31,202 records, five candidate heroes,
  actual engine decision ordering and ordered attempted commands. It matches the
  unchanged v13 reference. Each hero has 28,820 decisions. Adding attackTarget(0)
  produces a detected rejected-action mismatch at draft tick 241.
- Receipts capture loaded source SHA1, frozen policy SHA256, actual engine/Bassy,
  binary fingerprint, all opponent hashes and evidence hashes. The primary
  twenty-seed model-produced oracle remains future work, not proven by this test.
- Goal audit binds checked immutable build/map/committed source to native receipts
  before complete coverage can be reported. Raw map exploration cannot claim
  verified coverage. Every five-level predicate and goal claim stays unmeasurable.

The backend guide documents CLI defaults, explicit manifest, offline native builds,
Hermes turn limit/usage and future commands. Inputs cover the full helper/context
closure. Pandas is locked; no dependency overlay is needed for tests.

The full owned suite and exact local evidence paths are recorded in
`/tmp/ide-mvp-codex-task-2.4-report.md`. New/adopted Python passes Ruff; the remaining
116 diagnostics exactly match the pre-resumption baseline by file/rule/message.
They are inherited vendor debt, not hidden by new ignore rules.

## Boundary

No real agent/version probe, model, broker, credential or remote action occurred
by the implementer after C2. The parent separately performed the NOTES-2-authorized
isolated live verification/download, with no model/broker/spend action. The earlier incident must not be described as a clean local-only session.
Real M1, twenty-seed generated-policy proof, competitive evidence and Task 2.5 have
not run. Local readiness is subject to independent review and OpenRouter credit.


## Independent review fix round 1

The first review rejected local readiness on four evidence/containment gaps.
The fixes reject env wrapper escapes, reject symmetric inconsistent oracle traces,
bind audit/oracle executables to immutable expected native provenance, and replace
mtime/three-file cache checks with one enumerated source/config/dependency/output
receipt used by match and gate builds. Verification stores the identities actually
used, and finalization retains them separately. New binary receipts replace the
old fingerprint sidecar as the authority. See the backend guide and compiler
provenance for the exact contract; focused results are appended to the task report.

Disk space fell below the future M1 4-GiB preflight during this fix. No unrelated
cleanup occurred. Local code readiness is subject to re-review, and execution
readiness additionally requires disk space and the still-blocked model credit.

## Final whole-phase review corrections

The default backend is Hermes, with explicit Claude/Codex choices retained. A
guarded fake-only prepare regression verifies the default and `OPENROUTER_MODEL`
precedence. All adopted context/host files now carry full per-file provenance;
removing only that metadata restores their recorded upstream hashes. The adapted
lessons and policy-surface headers retain their full original source identity.
Baseline oracle bytes and executable runtime lines are unchanged.

Usage receipts cover successfully validated generation attempts, including when
later assembly/gates fail. Failed generation totals require provider reconciliation;
retained receipts alone are not complete spend accounting. Native receipts bind
the enumerated game/dependency/helper/Nim executable/config/options and output
set, excluding external C compiler, Nim library/runtime and system/SDK attestation.
They do not establish reproducible-build completeness. No native rerun was needed
for this documentation/default correction; focused checks are in the task report.

The parent subsequently recovered disk space, but the final-fix readback again
fell below 4 GiB. Operators must repeat the preflight immediately before M1.
Independent final review and fresh credit
authorization remain required. This is not real M1 or Phase C completion.
