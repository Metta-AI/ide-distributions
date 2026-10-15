# Assessment of the untracked GotA compiler port

Provenance: **personal_labs_gota working tree, untracked, 2026-10-07**.
Source root: `gods_of_the_arena_lab/`. Inspection only; no source checkout files changed.
This is the assessment requested by VERDICT-A item 2 and VERDICT-A2, not Phase C implementation.

## Recommendation

Keep `personal_labs_paintbot_pw@6b0bffa5` as Task 2.1's reproducible pipeline source.
Do not replace Tasks 2.1–2.3 wholesale with this working tree. Use individual GotA
adaptations as references after checking them against the selected live engine and
our contracts. Any later copied code needs this working-tree provenance and a file hash.

The port has real parser, assembly, runtime, telemetry and driver code. It is more
than a scaffold. The Task 1.2 live readback identifies GotA 2026.10.6.1 as Coworld
`cow_162aa34f-f447-4c5d-8db3-480ec83e98f4`, source
`5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`, matching this port's Polyworld target.
Its baseline, resource policy and missing integration pieces still prevent direct reuse
as the autoresearch compiler. Source verification distinguishes two modes: unmarked policies use `bots.nim:114-138`
limits of 256 globals, 32 arrays and 4096 cells. Policies whose first line is
`' @gota-structures` use `structures.nim:161-175` limits of 512 globals, 128 arrays
and 262144 cells; `bots.nim:1074-1081` selects that mode. The port targets this
structured interface, so its larger limits are not inherently incorrect.
Both modes use 100000 instructions/250000 work per decision. VERDICT-B explicitly
selects the smaller storage ceilings and 80000/200000 dynamic thresholds for our gates.
The earlier blanket claim that the port's static limits were false was incomplete.
See `gota-toolkit-drift.md`.

## What is present

- `tools/gota_strategy.py`: lint, prepare, compile, assemble, verify and inspect CLI;
  a three-round generation/gates repair loop.
- `tools/gota_cli.py`: structured CLI results and exit codes, adapted from PW.
- `tools/strategy_format.py`: components P/K/S/SK/C/ST/A, ten-seat roles, no COM.
- `tools/strategy_basic.py`: unit validation, generated tables, assembly, local-parameter
  ABI, telemetry accounting, resource attribution and allocation helpers.
- `tools/strategy_build.py`: committed-input checks, incremental preparation,
  isolated compiler execution, write-scope checks, immutable build finalization.
- `tools/strategy_gates.py` and `strategy_telemetry.py`: intended engine checks,
  local screening and SD/SP/SE/SB parsing/coverage.
- Compiler instructions, lessons, engine context, host-surface JSON, runtime library/main,
  four test modules and a trivial strategy fixture.

## Contract gaps and dependencies

| Area | Observed source | Effect on our tasks |
| --- | --- | --- |
| Engine | Context README and lessons pin Polyworld `5287c437`, Bassy `a02e6dd5` | Polyworld target matches Task 1.2 live readback; verify Bassy pin and individual host/dialect facts |
| Storage and execution | AGENT.md specifies 512 globals, 128 arrays, 262144 cells, 100000 instructions/250000 work per decision | Larger static values apply to opt-in structured policies (structures.nim:161-175); unmarked v13 uses256/32/4096. VERDICT-B chooses smaller ceilings for our gates |
| Resource margin | `strategy_basic.py` uses `CEILING=0.90`, `ALLOWANCE=0.85`; gates use 80% dynamic budgets | Not our hard globals/arrays gate; cannot substitute these policies |
| Goals | `strategy_format.py` SECTIONS/LAYERS/ID_RE omit G; RULE_RE still uses FOR for roles | Task 2.2 remains required, including AS role migration and zero runtime goal state |
| Baseline | `strategy_build.py` M1_REFERENCE is `reference/base-5287c437.bas` | Official starter is not the authoritative submitted v13 artifact |
| Backend | `strategy_build.py` accepts only claude/codex | Hermes backend still absent |
| Gate API | `strategy_gates.py` exposes `verify(build_id)` with dictionaries | Does not supply our `run(artifact, context) -> GateResult` contract |
| Screen | Seeds 1–14, balanced-screen CI policy | Different from our seeds 1–10 glory/win-rate advisory screen |
| Missing local integration | `gota_local.py`, `gota_match.nim`, `build_gota_match.sh`, `engine.env` absent | Full driver/gate import or execution is not available as found |
| Missing build source/report | Root `strategy/STRATEGY.md` and `tools/strategy_report.py` absent | No complete v13 strategy or five-level semantic audit established |

The runtime's packed tables and entry SUB local parameters are useful reference
patterns. They do not prove that the Phase A v13 packing layout or no-goal-state
contract is implemented. No submitted-v13 action-stream equivalence was found or run.
The code's committed-input guard rejects untracked compiler inputs by design.

## Isolated validation

Copied only the inspected compiler code, context and tests to a temporary directory;
used the autoresearch project's locked Python 3.12 environment. No source imports or
caches were written into the lab. No model, API or engine run was started.

Commands inside the copied tree (`<venv-python>` is the project interpreter):

```sh
<venv-python> -m pytest -q -p no:cacheprovider gods_of_the_arena_lab/tools/tests
```

```text
ModuleNotFoundError: No module named 'gota_local'
1 error in 0.11s
exit 2
```

The missing module is local engine integration, not an installable dependency to add.
The focused parser/generator/telemetry check was then run without the blocked driver suite:

```sh
<venv-python> -m pytest -q -rs -p no:cacheprovider \
  gods_of_the_arena_lab/tools/tests/test_strategy_format.py \
  gods_of_the_arena_lab/tools/tests/test_strategy_basic.py \
  gods_of_the_arena_lab/tools/tests/test_strategy_telemetry.py
```

```text
89 passed, 4 skipped in 0.09s
exit 0
```

The four skips are existing tests requiring the absent/built engine harness.
These results establish useful unit-level implementation, not complete integration.

## Inspected file identities

Working-tree files can change independently of Git. These SHA-256 hashes identify
this assessment's snapshot; recheck before reuse.

| File | SHA-256 |
| --- | --- |
| `tools/gota_strategy.py` | `173afb9f966971c50947201e748206515fba2cbac6ac17fafdef1876ca8ed285` |
| `tools/gota_cli.py` | `9de932334984aa0a8c80bb3048f11bce3ecd1fb45ca6beca08b37ef5121ec6e1` |
| `tools/strategy_basic.py` | `235150df3d74319fdf021a2dc129c546ebcc6652092bd3d45624b22696b5c413` |
| `tools/strategy_build.py` | `2cabf09d62013221b32eaf2304bc73e1e5a6e7e4a14356f974b79bb99431cb9f` |
| `tools/strategy_format.py` | `f2b210d30740aa3020aff0863561fac0d16093433dbb935c9252a3384c2be6ca` |
| `tools/strategy_gates.py` | `088235875c61d8b2740ff0874cf8457ca97f113a93be6640a7d275b4ce28cc88` |
| `tools/strategy_telemetry.py` | `07c4f114caa1be3e87c5530ed294e9259b331f4ce2c6c872c1c83224808a0e4e` |
| `strategy/compiler/runtime/lib.bas` | `2ade9a20be685c841b50f82c5d7e54e673d831c9c38c2871b7c722287c5309f1` |
| `strategy/compiler/runtime/main.bas` | `ac5459adfa0cd4a8665d99b616efa658b2c3d3048dcac29f1b81c78c3fa30747` |
