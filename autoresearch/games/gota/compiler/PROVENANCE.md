# Pinned Paintbot compiler pipeline

Pinned committed source: `personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84`. No source working-tree bytes were used. Implementation retains the lab-relative `tools/`, `strategy/compiler/`, `docs/`, and `reference/` layout. Build reports remain in `tools/strategy_build.py:finalize`; no `strategy_report.py` alias exists.

Task 2.1 originally vendored Paintbot code; the Task 2.3 section below records the current GotA backend adaptation. The semantic audit modules are staged for later adaptation; Paintbot historical builds, uploads, replay evidence, and native binaries are not copied. No semantic audit or engine qualification is claimed. `tools/pw.py` retains the original dispatcher catalog; unrelated catalog commands are not included in this focused closure.

Approved C1 mechanical corrections: destination test imports point to the vendored tools; the committed-input test uses `base-bassy-28030de6.bas`; the M1 screen assertion accepts `high >= .5`, as implemented and documented by the current pinned command reference. Gate implementation is unchanged. Task 2.4 action-stream equivalence remains required. Python/BASIC/Markdown/environment files carry comment provenance; JSON config has an inert `_provenance` field. Original source SHA-256 values below identify bytes before headers.

| Destination | Original source under paintbot_pw_lab | Original SHA-256 |
| --- | --- | --- |
| `tools/strategy_gates.py` | `tools/strategy_gates.py` | `c212518f9a5fe5f59c63f9c60d89459fa7c81de189d13eb8ad079f90c4681cb4` |
| `tools/strategy_audit_runtime.py` | `tools/strategy_audit_runtime.py` | `e430c38955632541dbccc854f88bcb34c9e8341e25dd65fb6faacf1c7f60f065` |
| `tools/strategy_audit_comms.py` | `tools/strategy_audit_comms.py` | `758cdf899577c8d225bf719dc382e3b67842029273214360c6119e1c8d08fc59` |
| `tools/strategy_comms.py` | `tools/strategy_comms.py` | `71fad5fa243465eb71b8a80f6a070ad1554c65452c59220ceef39740cb60f8a7` |
| `tools/strategy_audit.py` | `tools/strategy_audit.py` | `6b276ed3b4924b222839c1d077034b5f7dc32182552079666959ed9a128655f6` |
| `tools/strategy_format.py` | `tools/strategy_format.py` | `46ff44e22b0bb8478520cd3de81b10afeaf2c97dad3900e68738275731e43f53` |
| `tools/strategy_build.py` | `tools/strategy_build.py` | `8948f9bce170a91987c3a88936eee85c802169ac2ef993b0e74d8e629a985727` |
| `tools/strategy_audit_baseline.py` | `tools/strategy_audit_baseline.py` | `70ae26dd9bd0a23ced231ec7dfa7e531bdfe794dd1d1df767c2e1cb505a6aa9a` |
| `tools/strategy_basic.py` | `tools/strategy_basic.py` | `b3818ae285d2da96e3d6b6ca0d8a2bcd6abae012f348801b1c1af6e65f712780` |
| `tools/pw_cli.py` | `tools/pw_cli.py` | `3d988c9504269e7bf6944aec739633df3bddd5e0775e26303f0cd532585d5797` |
| `tools/pw_release.py` | `tools/pw_release.py` | `68283656ad029b75f119eb8ef2f9d1d04004979c00a9a70b433409e713b444dd` |
| `tools/pw_strategy.py` | `tools/pw_strategy.py` | `e56e3ff1e98d04520569a63a3e5e8005061d624c7f4b287dbe053930ae1f6405` |
| `tools/pw.py` | `tools/pw.py` | `954672c1b9584cd9d091d7db3b79dccd1f21d8b67bd51bd760b4fa41560784c6` |
| `tools/pw_local.py` | `tools/pw_local.py` | `fb8a1d27ac71adf7a087ec1090d4900edb50de56b2e2f4ab9b0e23e350402947` |
| `tools/pw_terrain.py` | `tools/pw_terrain.py` | `4ddc544c60f9eed2e9bb737e4534f955bb504f6f178452e375287f438b73eade` |
| `tools/pw_intent.py` | `tools/pw_intent.py` | `f8ad7ee2ff30bc437637b17d5692fe7879a12627fcbb8d303c94d12c773581fe` |
| `tools/pw_episodes.py` | `tools/pw_episodes.py` | `0406334457926cbb57a44aaf678e0f83de24d37a738bdd74dc5098bd83259ed6` |
| `tools/release.env` | `tools/release.env` | `0561b309c7c9842b7dc474ea0bfcda1d38f8013ab3485ee3097bbf4168adc09c` |
| `strategy/compiler/AGENT.md` | `strategy/compiler/AGENT.md` | `fdf8939ceda5ab4641570cd71874c7f8a788d51c9e126c2451e2c3182fc6b863` |
| `strategy/compiler/config.json` | `strategy/compiler/config.json` | `f0240c328e26289f6ddf41857df3b9eddaafce89404228988303df332c3d2c93` |
| `strategy/compiler/LESSONS.md` | `strategy/compiler/LESSONS.md` | `8734a91f73563a780e2b9342e7938ed2cf9343f2d04fbdbc7e25342745a9a0b5` |
| `strategy/compiler/runtime/lib.bas` | `strategy/compiler/runtime/lib.bas` | `ef0a6b940d95f537ac9623f53d25b6aa26b9e474d311ccb6bc64d89bfa47dee7` |
| `strategy/compiler/runtime/comms.bas` | `strategy/compiler/runtime/comms.bas` | `05639c3995e9d527f0e4b124a3d37b57d76636266102a8da7d21f5c4603d1e4e` |
| `strategy/compiler/runtime/main.bas` | `strategy/compiler/runtime/main.bas` | `735c59e4033a60229b3d8ae3d3128a26d8aac9563873999e0f5db71111a30527` |
| `docs/policy-surface.md` | `docs/policy-surface.md` | `b5fce6e48e24edecd5d29a7913c6652e598340df8bb61f5a42ad3d8bd3a1361d` |
| `reference/base-bassy-28030de6.bas` | `reference/base-bassy-28030de6.bas` | `8c81b016a92a4bde2d8420e793633b79ef4f1f8e834e7bf3cd2baf6cc9e4775b` |
| `autoresearch/tests/unit/gota/compiler/test_strategy_basic.py` | `tools/tests/test_strategy_basic.py` | `b331d2b17e903a5cceed31aa07d3aa93476464bd016169cd9234676125868c7b` |
| `autoresearch/tests/unit/gota/compiler/test_strategy_format.py` | `tools/tests/test_strategy_format.py` | `41754f8fc124232171aeb8f2050895934684abb61a48dcb236a70d4c49a487f8` |
| `autoresearch/tests/unit/gota/compiler/test_strategy_build.py` | `tools/tests/test_strategy_build.py` | `2e36b71b370e3cdf473bc47c88138af1130aaea3ec8dff34900c937ea074031a` |
| `autoresearch/tests/unit/gota/compiler/fixtures/strategy_trivial/STRATEGY.md` | `tools/tests/fixtures/strategy_trivial/STRATEGY.md` | `a8ebe8ec216f62ac7e07f14f3d69ae1c32ec2bc0df19df6a1d462e75958185cf` |
| `tools/pw_scout.py` | `tools/pw_scout.py` | `b62c9ba289d3e38e1939c60c743b5ae99918272a236ff83d9bf78707bab94318` |
| `tools/pw_fights.py` | `tools/pw_fights.py` | `c63e8448ded5f2c5b9a16edf06a1618a77773ab68f708b546325acc060731973` |
| `tools/pw_metrics.py` | `tools/pw_metrics.py` | `0f33530405b5997ec303b991e56e77de0a36a3ee8d91f7fc0978779d01940ac8` |
| `tools/pw_public.py` | `tools/pw_public.py` | `7443140caa7e6ad1948865993acd362feadef7b0a717893a68ecf6ba9b2d8888` |

## Relocation, validation, and remaining work

`tools/pw_release.py` changes only `REPO = LAB.parent` to `REPO = LAB.parents[3]`, the enclosing Git checkout. LAB and all lab-relative paths remain inside this vendor directory. This correction lets relocated fixtures and future candidate sources share the same checkout identity. `reference/base-bassy-28030de6.bas` is preserved byte-identically, with adjacent provenance sidecar, under the approved identity-oracle exception. Runtime comment headers change runtime hashes; historical semantic-audit hash qualification does not transfer.

Original unchanged baseline: 62 passed, 2 failed, 5 skipped with isolated pandas 3.0.6 overlay. After C1-approved destination corrections and four new portability tests: `uv run --locked --with pandas pytest tests/unit/gota/compiler -q` gives **68 passed, 5 skipped**. Missing native handoff binary causes five existing skips. Imports and source inventory resolve inside this checkout. This is not engine, hosted, semantic, or GotA policy qualification. pandas is not yet in the shared project lock; the overlay is provisional dependency proof only.

Ruff on the four-file test suite and copied Python modules finds **116 inherited diagnostics** under the destination configuration, exactly matching the same source-file set at the pin, with identical per-rule counts. Source executable modes are preserved; 14 EXE001 diagnostics also exist in pinned files with mode 100644. The new `test_vendor_layout.py` passes Ruff. No suppression or lint configuration changes were made. Source/destination diagnostic JSON was retained in `/tmp/ide-mvp-codex-task-2.1-ruff-{source,destination}.json`. The gate body is hash-verified unchanged.

At Task 2.1, the copied policy surface, AGENT, LESSONS, config, runtime, semantic evaluators and reference were Paintbot inputs. Task 2.3 below records the active GotA context; semantic gameplay evaluators remain historical. The original dispatcher catalog includes unsupported unrelated commands; only the focused strategy pipeline closure is vendored. No agent invocation, credential, remote action, or native execution was performed in Task 2.1.

## Task 2.2 destination extension: descriptive goals

Base `03bba60`; original source pin and original SHA-256 inventory above remain
unchanged. Destination changes add `## Goals` components, rule `FOR G.id,...`, and
rename rule roles to `AS set=role,...`; adaptation Effect FOR duration is unchanged.
Claim, Priority (integer 0..1000), Provenance, and Status are required structured
goal fields. Evidence, Rationale, and Checks are optional metadata. Goals compile
no units, constants, globals, or arrays and never arbitrate gameplay.

Approved wire choice: when rules carry goal annotations, PWD uses v3 with ordered
literal `g=G.id,...` before t; unannotated rules and idle use `g=-`. No annotated
rules means unchanged PWD v2. Rule-specific branches merge labels into the initial
PRINT item and account for their actual item/event count and worst-case bytes.
Longest labels participate in the existing telemetry lint bound. No new PRINT
events are added. Other wire formats, runtime BASIC, and selection are unchanged.
The map stores goal metadata, rule goal_refs, and an explicit decision_version;
the strict reader validates labels against that map. Audit loading checks the map
against committed source; reconstruction and check evidence retain attribution.
Goal claims and G Checks have no evaluator and remain explicitly unmeasurable.

C1 mechanical ruling: the pinned reader and audit closure, rather than only the
plan's two parser/generator filenames, require destination edits to carry labels
through real PRINT, reading, and evidence attribution. Source lab/archive inputs
were not modified. Detailed grammar, wire schema, invariants, and limits are in
[`docs/goal-layer.md`](docs/goal-layer.md); rulings are in
[`compiler-goal-layer.md`](../../../docs/plans/findings/compiler-goal-layer.md).

TDD: initial focused run had 23 failures and 3 passes before implementation.
Final focused goal suite: **31 passed**. Full GotA regression before the last
additional independent PRINT-accounting test: **182 passed, 5 skipped**;
the five existing native-engine tests skip because the handoff binary is absent.
Commands: `uv run --locked --with pandas pytest tests/unit/gota -q` and
`uv run --locked --with pandas pytest tests/unit/gota/compiler/test_goal_layer.py -q`.
The pandas overlay remains provisional; no shared dependency files were edited.

Independent local comparison against `03bba60` preserves generated full policy
bytes and all budgets for both unannotated trivial and role-bearing RICH fixtures
(old FOR spelling at base, AS spelling in destination). Only RICH's ST.rules map
hash changes with source spelling. Evidence is
`/tmp/ide-mvp-codex-task-2.2-pin-v2.json`. Ruff retains the exact **116 inherited
diagnostic identities**, with zero additions/removals or suppressions; new goal
tests pass Ruff. Diagnostic evidence is
`/tmp/ide-mvp-codex-task-2.2-ruff.json`. Pure tests inspect full-policy generated
PRINT branches, bounds, maps, readers, and synthetic audit events. No engine,
model, semantic gameplay, hosted, or policy-equivalence qualification is claimed;
Task 2.4 remains required. Task 2.3 waits for independent review.

## Task 2.3 destination extension: GotA backend

The destination now supports the live unstructured GotA compiler backend. Original
Paintbot hashes above remain original-byte provenance, not hashes of adapted files.
All actual driver paths select GotA. Historical standalone parser/generator tests
retain explicit/default Paintbot helper behavior. The original historical gate
module is retained; the driver now calls shared GotA G1–G5 through gota_gates.

Source verification: cached Polyworld `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`
and Bassy `a02e6dd5889310b13d21725c891bde8d01539f08`. Runtime packing uses the exact
Phase A pvId(660) layout, fixed49 and reserve128; explicit immutable baseline
bindings support genuine component decomposition. The exact reference is
v13-live SHA-256 `1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
No baseline execution is substituted into generated output.

C1 rulings: real vendor paths, explicit backend seam, 100-based host hero IDs,
packing.json storage bindings, source compaction with original units retained,
shared G5 compiler-wire support, and live generation-context replacement are
faithful mechanical changes. No strategy/GAME contract, gameplay behavior,
credentials, remote action or spend is authorized by this adaptation. See
[backend guide](docs/gota-backend.md) and
[finding](../../../docs/plans/findings/compiler-gota-backend.md).

The partial GotA port was researched but not copied. Its structured host and
starter baseline are unsuitable for these ceilings and submitted-policy oracle.
New host/packing/adapter files use the existing scanner and shared gates. Source
hashes now include all compiler Python helpers, GotA Python/Nim/build helpers,
shared gate implementations, binding metadata and exact baseline reference.

TDD began with six failing backend tests. Real cached G1/G3 validate the generated
trivial fixture (7,596 bytes, zero globals, one 661-cell array, 27 routines,
1,452 static instructions; ten seats with 620 decisions each over 3,000 ticks).
Packed baseline compile confirms 251 globals, 32 arrays and 4,061 cells and unchanged
original access tokens. Neither proves full episode/action-stream equivalence.
Dynamic unit index bounds, real G2 and full artifact G3/G4 remain Task 2.4 work.
No model or container episode was executed. Full commands/results and remaining
limitations are in `/tmp/ide-mvp-codex-task-2.3-report.md`.


### Task 2.3 independent-review corrections

Review round 1 found two integration defects: physical baseline targets could
capture SUB parameters after relocation, and repeated verify calls reused G2's
output directory. The smallest faithful fixes reject parameter names colliding
with binding targets or synthesized pvId while preserving local source-symbol
shadowing, and allocate a fresh retained attempt directory/context for every
verification call (including caller-supplied contexts). G2 and G5 share only the
current attempt's episode, and measurements are cleared between attempts.

The active AGENT warning now correctly identifies context/base.bas as the exact
v13-live semantic reference, with the no-copy/no-fallback constraint. Eight new
regressions initially gave seven failures and one valid-shadowing pass; after
correction all eight pass. Retry tests invoke the real adapter, G2 inspection and
G5 reader, stubbing external episode execution and unrelated gate measurements;
no container, model, credentials or network call occurs. No Task 2.4 behavior
or contract was added. Details and exact final commands are in the task report.

Final affected validation: `uv run --locked --offline --with pandas pytest
 tests/unit/gota/compiler/test_gota_backend.py tests/unit/gota/test_gates.py -q`
(with the command on one line) gives **65 passed** in 4.07 seconds. Ruff passes on
all gota_*.py backend modules, shared G5 and test_gota_backend.py; diff whitespace
checks pass. Broader tests were not repeated for this bounded review correction.

## Task 2.4 containment only (2026-10-07)

Task 2.4 was stopped during its initial red test. The existing `agent_command`
selected Codex for every backend other than Claude, so an unsupported `hermes`
work order reached the real Codex executable. Seven CLI processes started; six
logged unsupported-model request errors and one was interrupted. No model output
or generated units were observed. Remote request attempts crossed the task's
local-only boundary; absence of spend has not been verified. See
[the incident finding](../../../docs/plans/findings/compiler-task-2.4-containment.md).

The containment change explicitly rejects unknown agent names in both prepare
and command selection. Supported backends remain exactly Claude and Codex;
Hermes is not implemented. New regression tests isolate PATH and allow only
local Git and a fake backend through their subprocess guard. Existing scope
snapshots, Git refs/index checks, and regular-output checks are unchanged.

The proposed configuration/stateful-capability decomposition was approved in
parent review but was not implemented. No seed strategy, packing bindings,
equivalence oracle, GotA goal-audit adapter, native build, or M1 run was produced.
Task 2.4 remains stopped pending a ruling on the boundary violation. This commit
is containment, not Task 2.4 completion or real-M1 readiness.
