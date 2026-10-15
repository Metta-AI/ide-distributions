# Plan 02 — GotA Extension and Compiler Port

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `00-overview.md` first.

**Goal:** A working GotA game extension (contract, toolkit, seed workspace, game skills) and a `build` binding that turns a candidate `STRATEGY.md` into a verified GotA BASIC policy through the ported Paintbot PW semantic↔policy compiler, including the Goal (G) layer and goal-labelled decision tracing. This is the only build path (James, 2026-10-07: no BASIC fallback).

**Architecture:** Everything lives in `games/gota/` and `skills/gota/`; core code reaches it only through `GAME.md` bindings (System Design §9). The compiler keeps the PW pipeline (prepare → per-unit LLM → assemble → gates → immutable build with provenance) and swaps game specifics.

**Tech Stack:** Python 3.12, Nim 2.2.10, coworld 0.1.57 (`coworld run-episode`), Hermes v0.21.5 (`hermes -z` for the compiler's LLM step).

**Spec:** System Design §3.1 (STRATEGY entity incl. G layer), §9 (game contract), §10; Runtime §11–12; `00-overview.md`.

## Global Constraints
See `00-overview.md`. Plus: every copied file carries a provenance header (source repo, commit, path); `games/gota/PROVENANCE.md` lists them all. Starts after Plan 01 Task 1.5 for record-dependent tasks (seed validation); toolkit and compiler tasks can start immediately.

## Review Focus
1. **Baseline identity:** the baseline is the artifact actually submitted as v13, identified by hash, not the diagnostic v20 that the README's 252/256 count describes. → Tasks 0.1, 2.4.
2. **Goal layer adds no runtime state:** `FOR G.x` and goal-labelled telemetry must not add globals. → Task 2.2.
3. **Tool drift:** copied tools must run against coworld 0.1.57 and the live GotA version 2026.10.6.1, not the Sept 18 docs. → Task 1.2.
4. **Goal tracing end to end:** every fired rule's telemetry line carries its goal ids, and the five-level audit can attribute behaviour to goals. → Tasks 2.2, 2.4.
5. **Budget gate:** G3 must use 80% only for instruction and work-unit budgets; globals and arrays use the hard limits (else the baseline fails). → Task 1.4.

---

## Milestone 0

### Task 0.1: Globals budget and baseline identification

**Files:** `docs/plans/findings/gota-globals-budget.md`, `games/gota/tools/count_globals.py`, `tests/unit/gota/test_count_globals.py`
- [ ] **Step 1: Failing test**: `count_globals` on a fixture `.bas` with 3 `DIM` scalars, 1 array, and 2 implicit globals returns `{"globals": 5, "arrays": 1}` (implicit = first assignment at top level outside `SUB`).
- [ ] **Step 2–4:** Implement; PASS.
- [ ] **Step 5:** Identify the v13 baseline **by its submitted artifact**: download the policy version behind league entry `sub_38be3038…` (GotA `policy/README.md:239-240`) with the coworld CLI as the Player, record its sha256, and find the `personal_labs_gota` commit whose `policy/build.py` output is byte-identical (try the commits around the Sept 17 v13 acceptance). If none is byte-identical, record the closest and label the baseline "gameplay-equivalent only". Count globals/arrays of the downloaded artifact (expected below the v20 diagnostic's 252).
- [ ] **Step 6:** List globals the PW runtime adds (`strategy_basic.generate_tables`, `runtime/lib.bas`, telemetry state) at the pinned Paintbot commit; estimate total for a v13-equivalent STRATEGY. Verdict FITS / FITS WITH PACKING / DOES NOT FIT.
- [ ] **Step 7:** Commit. FITS WITH PACKING → Task 2.3 must implement the packing (scalars into arrays) and its test. DOES NOT FIT → stop and bring James the numbers and a leaner decomposition proposal; there is no fallback path.

---

## Milestone 1 — Game contract and toolkit

### Task 1.1: GotA GAME.md and conformance
**Files:** `games/gota/GAME.md`, `tests/unit/gota/test_contract.py`
GAME.md front matter (consumed by Plan 01 `load_game`): bindings `build`, `local_check`, `upload`, `evaluate`, `replay_inspect` (each an exact command line), `reference_types` (`replay-segment: games/gota/tools/resolve_segment.py`), `outcome_metrics: [{name: glory, direction: up}, {name: win_rate, direction: up}, {name: xp_per_1k_ticks, direction: up}, {name: team_xp_lead, direction: up}]` (the loader's exact schema; `glory` is the league score "Emmett's Glory": winning heroes' lifetime XP per simulated minute, 0 on loss, draw, or timeout; `win_rate` is the per-episode team win indicator 0/1; both read by the GotA `evaluate` binding from `results.json`), `performance_guardrails: [glory, win_rate]`, `min_episodes_floor: 40`, `compiler: pw`, `league_id: league_3c60897b-25cf-4b37-9d1a-8554c1198f28`, `noise_source: league_episodes`, `layers: [P,K,S,SK,C,G,ST,A]` (no COM).
- [ ] Tests: every binding command's executable exists in the repo; `load_game` loads the real GotA GAME.md; missing league id fails with an actionable message; the `evaluate` binding on the probe episode's `results.json` (Plan 01 Task 0.1 artifacts) emits per-episode `glory`, `win_rate` (0 or 1), `xp_per_1k_ticks`, and `team_xp_lead`. Commit.

### Task 1.2: Toolkit assembly with provenance
**Files:** `games/gota/tools/` (`replay_stats.py`, `compare.py`, `miner_rows.py`, `features.py`, `lasthit_eval.py`, Nim sources `gota_verify.nim`, `gota_audit.nim`, `gota_policy_replay.nim`, `expand_replay.nim`), `games/gota/tools/build_nim.sh`, `games/gota/PROVENANCE.md`, `games/gota/docs/` (game guide, mechanics, policy surface, replay format), tests `tests/unit/gota/test_tools.py`, fixture replay `tests/fixtures/gota/replay_small.*`
- [ ] **Step 1: Failing tests**: each Python tool `--help` exits 0; `replay_stats` on the fixture replay returns the recorded JSON; `build_nim.sh` compiles `expand_replay` (skip with reason if Nim missing).
- [ ] **Step 2:** Copy files from GotA lab `07dc6f94` and co-gas `6742feb` with provenance headers. **Step 3:** PASS.
- [ ] **Step 4:** Drift check: run one local episode with `coworld==0.1.57` against the live GotA coworld version 2026.10.6.1 (`coworld run-episode <manifest> <ten .bas> --variant competition`) and parse its replay with the tools; also parse the probe replay from Plan 01 Task 0.1; record compatibility in PROVENANCE.md. Commit.

### Task 1.3: Seed workspace
**Files:** `games/gota/seed/` — `plan.md` (empty priorities), `kb/hubs/` (mechanics, our-policy, league-meta hubs derived from docs, each with currency `game_version: 65`), `kb/claims/` (mechanism claims citing game source `S` tier where the source is available, else docs `A`), `strategy/STRATEGY.md` (from Milestone 2), `stats/` (empty; calibration card fills it), `lessons.md` (seeded from co-gas failed-experiments red flags, each tentative with provenance)
- [ ] Tests (after Plan 01 Task 1.5 and Plan 03 Task 1.2): `records validate --all` on the seed has no errors; `kb lint` has no errors; every claim cites at least one evidence ref. Commit.

### Task 1.4: Shared gates (used by both build paths)
**Files:** `games/gota/gates/{g1_lint.py,g2_episode.py,g3_budget.py,g4_screen.py,g5_telemetry.py,limits.py}`, tests
- `limits.py`: 256 globals, 32 arrays / 4096 cells, 64 KiB source, 64 routines, 20k instructions and 50k work units per decision, PRINT 1,024 bytes / 128 events, two events per PRINT item (GotA lab `README.md:117-132`, `policy/README.md:135`; verify PRINT accounting with one episode and record).
- G1 lint (BASIC syntax via the Polyworld BASIC checker); G2 `coworld run-episode` with all 10 seats; G3 budget: globals ≤ 256 and arrays ≤ 32 (hard), instructions ≤ 16k and work units ≤ 40k per decision (80% of 20k/50k), PRINT within limits; G4 screen: glory and win rate over seeds 1–10 vs baseline (advisory for behaviour changes); G5 telemetry parse.
- Each gate: `run(artifact: Path, context: GateContext) -> GateResult(ok, details)`.
- [ ] Tests: each gate passes on the v13 baseline artifact (Task 0.1) and fails on a crafted violation (33rd array; a 20k+ instruction loop; malformed telemetry). Commit.

### Task 1.5: GotA skills
**Files:** `skills/gota/{replay-inspection,eval-design,diagnosis,ab,survey}/SKILL.md`
Each: front matter `name`, `description`; when to use; exact tool commands from Task 1.2; outputs as records via `"$AUTORESEARCH_CLI" records new`. Adapt from optimizer-seed `games/_template/skills/*` and co-gas `.agents/skills/coworld-replay-diagnosis`.
- [ ] Test (Plan 03 Task 4.3 harness): front matter valid; every command exists. Commit.

---

## Milestone 2 — Compiler port

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
- **Target:** M1 passing by end of day Wed Oct 7 (`00-overview.md`). There is no alternative build path; if it slips, work continues here.
- [ ] Tests: fake `hermes` binary for the backend; equivalence tool unit-tested on identical and differing synthetic streams; the real M1 run recorded in a findings file. Commit.

### Task 2.5: Build binding
**Files:** `games/gota/tools/build.py` (the `build` binding), tests
`build.py <candidate-dir> --out <build-dir> --json` → runs compile + gates, writes `build-dir/version.json` (source hashes, compiler agent/model/version, gates, policy sha256) and returns the JSON envelope `{ok, build_id, gates, artifact}`; exit 0/1/2/3 as in PW.
- [ ] Tests on the fake fixture and the seed STRATEGY. Commit.

---
