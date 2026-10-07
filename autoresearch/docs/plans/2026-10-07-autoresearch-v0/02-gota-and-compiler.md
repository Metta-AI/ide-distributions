# Plan 02 — GotA Extension and Compiler Port

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `00-overview.md` first.

**Goal:** A working GotA game extension (contract, toolkit, seed workspace, game skills) and a `build` binding that turns a candidate `STRATEGY.md` into a verified GotA BASIC policy — via the ported Paintbot PW compiler with the Goal (G) layer, or, if the tripwire fires, via a direct BASIC module build with the same interface.

**Architecture:** Everything lives in `games/gota/` and `skills/gota/`; core code reaches it only through `GAME.md` bindings (System Design §9). The compiler keeps the PW pipeline (prepare → per-unit LLM → assemble → gates → immutable build with provenance) and swaps game specifics.

**Tech Stack:** Python 3.12, Nim 2.2.10, coworld 0.1.57 (`coworld run-episode`), Hermes v0.21.5 (`hermes -z` for the compiler's LLM step).

**Spec:** System Design §3.1 (STRATEGY entity incl. G layer), §9 (game contract), §10; Runtime §11–12; `00-overview.md`.

## Global Constraints
See `00-overview.md`. Plus: every copied file carries a provenance header (source repo, commit, path); `games/gota/PROVENANCE.md` lists them all. Starts after Plan 01 Task 1.5 for record-dependent tasks (seed validation); toolkit and compiler tasks can start immediately.

## Review Focus
1. **Baseline identity:** the baseline is the artifact actually submitted as v13, identified by hash, not the diagnostic v20 that the README's 252/256 count describes. → Tasks 0.1, 2.4, 3.1.
2. **Goal layer adds no runtime state:** `FOR G.x` and goal-labelled telemetry must not add globals. → Task 2.2.
3. **Tool drift:** copied tools must run against coworld 0.1.57 / game_version 65, not the Sept 18 docs. → Task 1.2.
4. **Fallback parity:** with the BASIC fallback, experiments still name components and record provenance. → Task 3.1.
5. **Budget gate:** G3 must use 80% only for instruction and work-unit budgets; globals and arrays use the hard limits (else the baseline fails). → Task 1.4.

---

## Milestone 0

### Task 0.1: Globals budget and baseline identification

**Files:** `docs/plans/findings/gota-globals-budget.md`, `games/gota/tools/count_globals.py`, `tests/unit/gota/test_count_globals.py`
- [ ] **Step 1: Failing test**: `count_globals` on a fixture `.bas` with 3 `DIM` scalars, 1 array, and 2 implicit globals returns `{"globals": 5, "arrays": 1}` (implicit = first assignment at top level outside `SUB`).
- [ ] **Step 2–4:** Implement; PASS.
- [ ] **Step 5:** Identify the v13 baseline **by its submitted artifact**: download the policy version behind league entry `sub_38be3038…` (GotA `policy/README.md:239-240`) with the coworld CLI as the Player, record its sha256, and find the `personal_labs_gota` commit whose `policy/build.py` output is byte-identical (try the commits around the Sept 17 v13 acceptance). If none is byte-identical, record the closest and label the baseline "gameplay-equivalent only". Count globals/arrays of the downloaded artifact (expected below the v20 diagnostic's 252).
- [ ] **Step 6:** List globals the PW runtime adds (`strategy_basic.generate_tables`, `runtime/lib.bas`, telemetry state) at the pinned Paintbot commit; estimate total for a v13-equivalent STRATEGY. Verdict FITS / FITS WITH PACKING / DOES NOT FIT.
- [ ] **Step 7:** Commit. DOES NOT FIT → skip Milestone 2; do Milestone 3 only.

---

## Milestone 1 — Game contract and toolkit

### Task 1.1: GotA GAME.md and conformance
**Files:** `games/gota/GAME.md`, `tests/unit/gota/test_contract.py`
GAME.md front matter (consumed by Plan 01 `load_game`): bindings `build`, `local_check`, `upload`, `evaluate`, `replay_inspect` (each an exact command line), `reference_types` (`replay-segment: games/gota/tools/resolve_segment.py`), `outcome_metrics: [{name: xp_per_1k_ticks, direction: up}, {name: win_rate, direction: up}, {name: team_xp_lead, direction: up}]` (the loader's exact schema; `win_rate` is the per-episode team win indicator 0/1 averaged over episodes, computed by the GotA `evaluate` binding from `results.json`), `performance_guardrails: [xp_per_1k_ticks, win_rate]`, `min_episodes_floor: 40`, `compiler: pw|basic`, `league_id: <GotA Competition league id — set here once James confirms; test asserts non-empty>`, `noise_source: league_episodes`, `layers: [P,K,S,SK,C,G,ST,A]` (no COM).
- [ ] Tests: every binding command's executable exists in the repo; `load_game` loads the real GotA GAME.md; missing league id fails with an actionable message; the `evaluate` binding on a fixture episode emits per-episode `xp_per_1k_ticks`, `win_rate` (0 or 1), and `team_xp_lead`. Commit.

### Task 1.2: Toolkit assembly with provenance
**Files:** `games/gota/tools/` (`replay_stats.py`, `compare.py`, `miner_rows.py`, `features.py`, `lasthit_eval.py`, Nim sources `gota_verify.nim`, `gota_audit.nim`, `gota_policy_replay.nim`, `expand_replay.nim`), `games/gota/tools/build_nim.sh`, `games/gota/PROVENANCE.md`, `games/gota/docs/` (game guide, mechanics, policy surface, replay format), tests `tests/unit/gota/test_tools.py`, fixture replay `tests/fixtures/gota/replay_small.*`
- [ ] **Step 1: Failing tests**: each Python tool `--help` exits 0; `replay_stats` on the fixture replay returns the recorded JSON; `build_nim.sh` compiles `expand_replay` (skip with reason if Nim missing).
- [ ] **Step 2:** Copy files from GotA lab `07dc6f94` and co-gas `6742feb` with provenance headers. **Step 3:** PASS.
- [ ] **Step 4:** Drift check: run one local episode with `coworld==0.1.57` (`coworld run-episode <manifest> <ten .bas> --variant competition`) and parse its replay with the tools; record game_version 65 compatibility in PROVENANCE.md. Commit.

### Task 1.3: Seed workspace
**Files:** `games/gota/seed/` — `plan.md` (empty priorities), `kb/hubs/` (mechanics, our-policy, league-meta hubs derived from docs, each with currency `game_version: 65`), `kb/claims/` (mechanism claims citing game source `S` tier where the source is available, else docs `A`), `strategy/STRATEGY.md` (from Milestone 2 or 3), `stats/` (empty; calibration card fills it), `lessons.md` (seeded from co-gas failed-experiments red flags, each tentative with provenance)
- [ ] Tests (after Plan 01 Task 1.5 and Plan 03 Task 1.2): `records validate --all` on the seed has no errors; `kb lint` has no errors; every claim cites at least one evidence ref. Commit.

### Task 1.4: Shared gates (used by both build paths)
**Files:** `games/gota/gates/{g1_lint.py,g2_episode.py,g3_budget.py,g4_screen.py,g5_telemetry.py,limits.py}`, tests
- `limits.py`: 256 globals, 32 arrays / 4096 cells, 64 KiB source, 64 routines, 20k instructions and 50k work units per decision, PRINT 1,024 bytes / 128 events, two events per PRINT item (GotA lab `README.md:117-132`, `policy/README.md:135`; verify PRINT accounting with one episode and record).
- G1 lint (BASIC syntax via the Polyworld BASIC checker); G2 `coworld run-episode` with all 10 seats; G3 budget: globals ≤ 256 and arrays ≤ 32 (hard), instructions ≤ 16k and work units ≤ 40k per decision (80% of 20k/50k), PRINT within limits; G4 screen: XP per 1,000 ticks over seeds 1–10 vs baseline (advisory for behaviour changes); G5 telemetry parse.
- Each gate: `run(artifact: Path, context: GateContext) -> GateResult(ok, details)`.
- [ ] Tests: each gate passes on the v13 baseline artifact (Task 0.1) and fails on a crafted violation (33rd array; a 20k+ instruction loop; malformed telemetry). Commit.

### Task 1.5: GotA skills
**Files:** `skills/gota/{replay-inspection,eval-design,diagnosis,ab,survey}/SKILL.md`
Each: front matter `name`, `description`; when to use; exact tool commands from Task 1.2; outputs as records via `"$AUTORESEARCH_CLI" records new`. Adapt from optimizer-seed `games/_template/skills/*` and co-gas `.agents/skills/coworld-replay-diagnosis`.
- [ ] Test (Plan 03 Task 4.3 harness): front matter valid; every command exists. Commit.

---

## Milestone 2 — Compiler port (skip if Task 0.1 says DOES NOT FIT)

### Task 2.1: Vendor the generic pipeline
**Files:** `games/gota/compiler/{strategy_format.py,strategy_build.py,strategy_basic.py,strategy_report.py,pw_cli.py}`, `games/gota/compiler/PROVENANCE.md`, ported tests `tests/unit/gota/compiler/`
- [ ] Pin a clean Paintbot commit (ask James to commit or stash the uncommitted edits at `6b0bffa5`, or use the last clean commit); copy; port the existing tests; all pass unchanged on Paintbot fixtures before any edit. Commit.

### Task 2.2: Goal layer (G)
**Files:** modify `strategy_format.py` (layer `G`, rule grammar `R.id [prio]: WHEN <S.x> DO <C.y> FOR <G.a>[, <G.b>]`), `strategy_basic.py` (telemetry label), tests
Rules (System Design §3.1): `G.id` with claim, priority, provenance; rules may list goals; goals are metadata and tracing labels only — **no runtime goal state, no new globals**. The existing PW `FOR` role selector is renamed `AS <role>` to free `FOR` for goals; migrate fixtures.
- [ ] **Step 1: Failing tests**: parser accepts `FOR G.survive, G.farm`; unknown goal → lint error; rule without FOR allowed; generated BASIC global count identical with and without G annotations on the same strategy; telemetry line for a fired rule includes goal ids.
- [ ] **Step 2–5.**

### Task 2.3: GotA specifics for the compiler
**Files:** `games/gota/compiler/gota_host.py`, `games/gota/compiler/runtime/{lib.bas,main.bas}`, tests (gates come from Task 1.4)
- LIMITS: 256 globals, 32 arrays / 4096 cells, 20k instructions and 50k work units per decision, 64 KiB source, 64 routines, PRINT 1,024 bytes / 128 events (GotA lab README:117-132); HOST_DATA and KEYWORDS from the GotA policy surface doc; 10 seats; drop COM.
- Gates: G1 lint; G2 compile + `coworld run-episode` all 10 seats; G3 budget: globals ≤ 256 and arrays ≤ 32 (hard), instructions ≤ 16k and work units ≤ 40k per decision (80% of 20k/50k); G4 screen: XP per 1,000 ticks over fixed seeds 1–10 vs baseline (advisory for behaviour changes); G5 telemetry parse. PRINT accounting: two events per item (GotA `policy/README.md:135`) — verify with one episode and encode.
- [ ] Tests: the compiler pipeline calls the Task 1.4 gates in order G1, G2, G3, G4 (advisory), G5; HOST_DATA and KEYWORDS match the policy surface doc; COM layer rejected by lint. Commit.

### Task 2.4: Hermes LLM backend and M1 baseline
**Files:** `games/gota/compiler/agents.py` (adds `hermes`), `games/gota/seed/strategy/STRATEGY.md`, `games/gota/tools/equivalence.py`, tests
- Hermes backend: `hermes -z --query-file <prompt> -t file --max-turns 40 --usage-file <path>` in the scratch repo; the existing write-scope check is unchanged; model from `OPENROUTER_MODEL`.
- M1: write the GotA STRATEGY.md describing the v13 baseline artifact (Task 0.1) as components with Status `specified`; compile; **equivalence oracle**: `equivalence.py` runs the downloaded v13 artifact and the compiled policy on seeds 1–20 with fixed seats and fixed opponents and compares per-tick action streams for our 5 heroes, excluding telemetry PRINT lines; pass = identical streams.
- **Tripwire:** M1 must pass by the compiler tripwire **in force** in `00-overview.md` ("Dates in force"). As of this plan that is end of day Wed Oct 7, so Milestone 3 (BASIC fallback) is the v0 path unless James moves the date; Milestone 2 continues as non-blocking work.
- [ ] Tests: fake `hermes` binary for the backend; equivalence tool unit-tested on identical and differing synthetic streams; the real M1 run recorded in a findings file. Commit.

### Task 2.5: Build binding
**Files:** `games/gota/tools/build.py` (the `build` binding), tests
`build.py <candidate-dir> --out <build-dir> --json` → runs compile + gates, writes `build-dir/version.json` (source hashes, compiler agent/model/version, gates, policy sha256) and returns the JSON envelope `{ok, build_id, gates, artifact}`; exit 0/1/2/3 as in PW.
- [ ] Tests on the fake fixture and the seed STRATEGY. Commit.

---

## Milestone 3 — BASIC fallback (tripwire path)

### Task 3.1: Direct BASIC build with component naming
**Files:** `games/gota/fallback/` (v13 modules copied with provenance), `games/gota/tools/build.py` (mode `basic`), `games/gota/seed/strategy/STRATEGY.md` (fallback form), tests
- The fallback STRATEGY.md lists the v13 **modules as components** (`C.scan`, `C.lasthit`, `C.ksteal`, `C.punish`, `C.survive`, `C.shop`, `ST.main`) with one module file each; experiments name these component IDs in `change`; candidates live in `strategy/candidates/E-…/` as a copy of the module directory; `build.py --mode basic` concatenates modules in name order (GotA `policy/build.py`), runs the Task 1.4 gates G1–G3 and G5, and writes the same `version.json`.
- The G layer exists as annotations in STRATEGY.md only. Goal-labelled decision traces are **not** produced on this path: this is the approved-deviation item in `00-overview.md` and needs James's approval before the fallback ships.
- `GAME.md` `build` binding switches by a single key `compiler: pw|basic`; skills read the mode from `load_game` and use the matching procedure text.
- [ ] Tests: building the seed reproduces the downloaded v13 artifact byte for byte (or the recorded gameplay-equivalent baseline if Task 0.1 found none); an experiment that edits `C.lasthit` changes only that module; provenance recorded. Commit.
