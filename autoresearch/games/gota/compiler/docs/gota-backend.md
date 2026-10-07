# GotA build adapter and bounded packing

Task 2.3 adapts the pinned Paintbot pipeline to the live unstructured GotA host.
It does not provide the v13 strategy decomposition, Hermes invocation, or a
v13 action-equivalence verdict; those belong to Task 2.4.

## Entry points

From `autoresearch/`, the existing driver is:

```sh
uv run --locked --with pandas python games/gota/compiler/tools/pw_strategy.py lint PATH/STRATEGY.md
uv run --locked --with pandas python games/gota/compiler/tools/pw_strategy.py prepare --source PATH/STRATEGY.md
uv run --locked --with pandas python games/gota/compiler/tools/pw_strategy.py assemble BUILD-ID
uv run --locked --with pandas python games/gota/compiler/tools/pw_strategy.py verify BUILD-ID
```

The retained `pw_strategy.py` filename is a vendor-layout compatibility detail.
Every driver parse (lint, prepare, generation context, incremental comparison,
assembly) selects `backend='gota'`. Finalized version/map files record the backend;
audit source reconstruction reads that value. The low-level parser defaults to
`paintbot` only to retain reproducible historical fixtures. Call
`parse_strategy(path, backend='gota')` in GotA integrations. Unknown backends fail.
The legacy `strategy_gates.py` remains historical Paintbot reference; the driver
imports `gota_gates.verify`, which calls the shared `games.gota.gates` modules.

Prepare requires committed inputs and an installed configured compiler CLI. It
reads `--version`; it does not run a model. The full `compile` command runs the
configured model and is not authorized by Task 2.3. No model was run in this task.
The pandas overlay is still provisional until the shared dependency owner updates
its lock. No project dependency file changed.

## Explicit baseline bindings

A strategy can supply a sibling `packing.json`:

```json
{"baseline_bindings": {"sk_baseline__pvId": "pvId", "sk_baseline__pvN": "pvN"}}
```

This example illustrates names, not a ready baseline strategy. The mapping keys
must identify actual emitted component globals or arrays. Targets must be exact
v13-live storage names (case-insensitive), with matching scalar/array kind and
original inclusive bounds. Targets are unique, cannot collide with already
emitted storage, and cannot bind runtime/generated ST state. Unknown/malformed
bindings fail. Missing bindings never cause prefix guessing or baseline copying;
new scalar names consume reserve cells, while an unbound new array fails.

The exact reference is `tests/fixtures/gota/v13-live.bas`, SHA-256
`1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
The packer checks this identity before using its lexical storage inventory.
It never copies any executable baseline source into generated output.

`source_hashes` includes packing.json when present and rejects its addition after
prepare. It also includes the actual baseline, every compiler Python helper, the
compiler config/instructions/runtime, shared gate code, and GotA Python/Nim/build
helpers. Map `packing` records canonical bindings, physical slot mapping and the
baseline hash. Original unit texts are retained separately from packed policy.bas.

## Allocation and bounds

The Phase A map is implemented literally:

| Region | pvId indices |
| --- | --- |
| Original baseline | 0–63 |
| Runtime arrays | 64–483 |
| Sorted 49 fixed scalars | 484–532 |
| New metadata and scratch, sorted by name | 533–660 |

The emitted DIM is `pvId(660)`, exactly 661 cells. Rule/situation capacities remain
63/124; the seven adaptation arrays each have five cells (unused zero plus four
adaptations). Lint rejects larger component counts; allocation rejects more than
128 new scalar names or any unsupported extra arrays. Goals occupy only literal
PRINT text and map metadata. Exact offsets are exported as ARRAYS/FIXED in
`tools/gota_packing.py` and included in each build map.

The rewrite tokenizes identifiers with the existing scanner, respects SUB-local
parameters, preserves PRINT literals and inline comments, and removes indentation,
blank lines and comment-only lines to reduce source size. Nested indices retain
parentheses. DIM/SUB compound statements and multiple DIM declarations on one
line are rejected explicitly. Literal signed out-of-range runtime/pvId indices
are rejected before flattening. Array accesses cannot silently truncate.

This is **not arbitrary dynamic-index isolation**. Generated runtime loops are
bounded by lint-checked rule, situation and adaptation counts. Baseline pvId
reads/writes are bounded by pvN/efN at 64 as recorded in Phase A. Task 2.4's source
and bindings must preserve those invariants; an arbitrary generated unit with a
computed out-of-range index has not been proven safe by this lexical pass.

The final packed source is counted and rejected above the actual globals, arrays,
cells or source-size ceilings. G1 compiles it with the real engine, and G3 also
checks routine/static-instruction limits and measured decisions. Storage fit
alone never establishes a full G3 pass or action equivalence.

## Shared gates and offline execution

`gota_gates.verify(build_id, context=GateContext(...))` supports explicit local
fixtures. The CLI uses `strategy/compiler/config.json`'s `gates.manifest`; null
means G2 fails for missing evidence. Supply an already downloaded live manifest
and available image for real G2; the adapter does not invent a manifest or skip
this gate. Default baseline is exact v13-live. The order is G1, G2, G3, G4, G5;
G1/G2 failures mark subsequent gates unrun/failed. G4 regressions remain advisory,
but missing or failed measurements fail. The shared screen owns seeds 1–10 and
Glory/win-rate scoring.

The adapter uses only the cached `tools/bin/gate_probe-5287c437`. It rejects missing
or stale binaries against gate_probe.nim, replay_outcome.nim and build_nim.sh;
it never fetches or builds implicitly. If stale, establish the pinned offline
Nim/dependency toolchain before rebuilding. Do not invoke build_nim.sh's network
branch under Phase C's offline authorization.

Shared G5 retains LH parsing for legacy policies. A compiler map switches it to
the existing strict PWD v2/v3 and PWP/PWE/PWB/PWC reader for all ten episode logs,
after G2 artifact/completion checks. Missing decisions, malformed labels, backwards
ticks or BASIC failures fail. This proves wire parsing only:
`compiler_trace_coverage` remains null, and no goal truth, complete temporal
coverage or semantic equivalence is inferred.

## Local evidence

The authored trivial strategy fixture emits 7,596 bytes, zero scalar globals,
one 661-cell array, 27 routines and 1,452 static instructions. A cached native
3,000-tick seed-1 simulation measured 620 decisions on each of ten seats, no VM
errors, 2,010 peak instructions, 3,600 work, 33 PRINT events and 110 PRINT bytes.
It is a runtime fixture, not the v13 decomposition or a competitive screen.

Separately, packing the exact v13-live fixture without adding runtime execution
compiles with 251 globals, 32 arrays and 4,061 cells. A token comparison proves
only the pvId DIM bound changed (comments/whitespace omitted). No gameplay or
container episode was run for that packed reference. Real G2, full G3/G4 and the
action-stream oracle remain for the Task 2.4 compiled artifact.
