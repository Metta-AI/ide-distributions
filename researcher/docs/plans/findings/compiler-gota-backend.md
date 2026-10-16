# Task 2.3: GotA backend and packing

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

The pinned Paintbot compiler remains the pipeline source. The inspected untracked
GotA partial port was useful for its whitespace/comment compaction pattern, but
its structured-host storage limits, telemetry, missing local driver and starter
baseline do not satisfy this task. No partial-port implementation was copied.
The exact Phase A allocation is implemented in `gota_packing.py`; the live host
is pinned to Polyworld 5287c437 and Bassy a02e6dd5. No new dependency is needed:
the existing BASIC scanner and shared G1–G5 gates supply the appropriate seams.

C1 mechanical rulings, agreed with the parent:

- Retain the real vendor layout (`compiler/tools`, `strategy/compiler/runtime`)
  and historical low-level Paintbot fixture mode. All actual GotA driver paths
  explicitly select GotA; no generic backend framework was added.
- Live role comparisons use hero IDs 100–109 for strategy seats 0–9. Host names
  include current draft/status/cooldown inputs and constants; keywords include
  `is` and `as`. Tests compare both inventories against the cached live source.
- Bind relocated baseline variables through a hash-bound sibling packing.json.
  This preserves namespaced unit ownership without counting baseline globals as
  251 new reserve cells. It adds no STRATEGY or GAME.md schema change.
- Preserve original unit bytes for provenance, compact/rewrite the final artifact,
  and record literal physical offsets in its map. No baseline execution fallback.
- Wire the existing driver to shared gates, and extend shared G5 to recognize
  compiler wire via the existing strict reader. Legacy LH behavior is retained.
- Replace active generation instructions, lessons and host context with GotA
  facts/reference; retain original Paintbot hashes in PROVENANCE.md. Expand source
  hashing to cover backend and transitive helper/gate implementations.

Safety boundaries: compound DIM/SUB declarations fail explicitly; fixed literal
index overflow fails; metadata/array/capacity overflow fails without truncation.
Arbitrary dynamic-index isolation is not established. Task 2.4 must preserve and
verify the v13 pvN/efN invariants and run the action-stream oracle. No behavior,
full-episode or competition equivalence is claimed here.

The authored generated fixture passed actual cached G1 and short native G3:
0 globals, 1 array/661 cells, 7,596 bytes, 27 routines, 1,452 static instructions;
620 decisions per seat across all ten, peak 2,010 instructions/3,600 work,
33 PRINT events/110 bytes. Packed v13-live passed actual compile with
251 globals/32 arrays/4,061 cells and token-equivalent original accesses.
Native details: `/tmp/ide-mvp-codex-task-2.3-native.json`.
Binary SHA-256: `422703171ddebcf001a78fbe60c878f8969a3e00a8b5a9c1ce2ac2992d40b8b3`.

No model, credentials, remote operation, upload or container episode was run.
Real G2 and real compiled-v13 qualification remain for Task 2.4 after its gate.
The audit-documentation skill checked the generation context, policy surface,
backend guide, gate guide, runtime comments and provenance against code/tests.


## Independent review round 1 corrections

P1: relocation previously tested source names for local scope but did not protect
physical target names. Assembly now rejects SUB parameters colliding with any
bound baseline target (scalar or array) or synthesized pvId. The error identifies
the SUB and parameter and asks for a rename. Source-symbol parameter shadowing
remains valid and is tested through actual assembly.

P2: every verify call now creates a retained attempt directory and a fresh copy
of GateContext, clearing old episode, measurements and compiler map. This applies
to both default and reused caller contexts; the caller is unchanged. Per-attempt
G2 output and G5 parsing stay aligned with the current artifact. Both partial G2
failure and a later G3 failure can be followed by a successful repaired attempt;
prior episode artifacts and gate reports are retained.

P3: the active AGENT instruction no longer calls the exact v13-live reference an
upstream gameplay-changing Bassy example. It requires preserving specified
behavior without copying the full baseline or using it as a fallback.

These are faithful corrections under C1, not new gameplay or schema choices.
The eight regressions initially had seven failures and one pass. They exercise
actual assembly, real adapter/G2/G5 behavior and synthetic local episode output;
external execution is stubbed, so no model, container, remote or credential action
is implied. The audit-documentation skill checked the active prompt and backend
operational/storage docs against these changes before the fix commit.
