# Plan 03 — Research Completion and Hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `00-overview.md` first.

**Goal:** Complete everything the research agent needs beyond the Plan 01 skeleton: the knowledge base with its deterministic maintenance, statistics and noise calibration, the full softmax tool, doctrine and skills, operational configuration, and a real GotA research cycle with an acceptance checklist.

**Architecture:** Builds on Plan 01's records, queue, and runtime; uses Plan 02's GotA bindings for the real cycle.

**Tech Stack:** As `00-overview.md`. Statistics use numpy (added to `pyproject.toml` in Task 2.2); no other new dependencies.

**Spec:** System Design §3.5 (KB), §3.3 (transitions), §4 (doctrine), §5 (procedures), §6 (skills and tools), §8 (signals), §10 (parameters); Runtime §6–§7 (config, memory), §10 (identity), §13–§14 (tests, schedule).

## Global Constraints
See `00-overview.md`.

## Review Focus
1. **Verification from the wrong evidence:** a behaviour claim with only game-source evidence must never become `verified`. → Task 1.1.
2. **Game update storm:** 40% of claims go stale at once; compaction archives none of them. → Task 1.3.
3. **Noise record missing or zero-variance:** experiment admission blocked; δ/N computation returns an explicit error, not a divide-by-zero. → Task 2.2.
4. **XP denied for credits mid-evaluation:** experiment goes `awaiting-credits`, nothing lost, relaunch later without duplicate requests. → Task 3.2.
5. **Skill names a command that does not exist:** caught by the skill conformance test before any agent runs it. → Task 4.3.

---

## Milestone 1 — Knowledge base

### Task 1.1: Evidence grading and verification
**Files:** `src/autoresearch/kb/grading.py`, `tests/unit/kb/test_grading.py`
**Interfaces — Produces:**
```python
Tier = Literal["S","A","B","C","D","E"]
@dataclass class Ev: ref: str; tier: Tier; direct: bool; stance: Literal["supports","contradicts"]; source_group: str  # independence: same source_group = not independent
def effective_tier(e: Ev) -> Tier            # indirect S counts as A; indirect others drop one tier (A→B, B→C, C→D, D→E)
def grade(evs: list[Ev]) -> Literal["high","moderate","low","very_low"]
#   high: any supporting effective S, or ≥2 independent supporting A/B; moderate: 1 supporting A/B, or ≥2 independent C; low: 1 C or ≥1 D; very_low: only E or none; any unresolved contradicts of effective tier ≥ the best supporting tier lowers one level
def verification_eligible(kind: str, evs: list[Ev]) -> bool
#   mechanism: one direct supporting S, or ≥2 independent supporting from {A,B}; and no unresolved contradicts
#   behaviour|strategy|opponent: ≥1 supporting B, or a supporting C plus another independent supporting play source (B or C); S/A alone never; and no unresolved contradicts
#   platform: ≥1 supporting A or S; never from D/E alone
```
A contradiction is resolved when a later evidence entry with `stance: "resolves"` and `target: <ref>` exists (append-only, Plan 01 Task 1.5), or the contradicting evidence's own claim is `superseded`/`refuted`.
- [ ] **Step 1: Failing tests** (one function each): `direct_S_verifies_mechanism`; `S_alone_never_verifies_behaviour`; `two_independent_C_verify_behaviour`; `two_C_same_source_group_do_not`; `single_D_never_verifies`; `unresolved_contradiction_blocks`; `resolved_contradiction_allows`; `indirect_S_counts_as_A`; grade table cases for each level.
- [ ] **Step 2–5.**

### Task 1.2: Linter with an apply pass
**Files:** `src/autoresearch/kb/{lint,apply}.py`, fixture `tests/fixtures/workspaces/kb_small/`, tests
**Interfaces — Produces:**
```python
def analyze(layout) -> LintReport   # pure; Finding(code, entity, message); codes LINK_UNRESOLVED, ORPHAN_CLAIM, HUB_SENTENCE_UNCITED, HUB_CITES_MISSING_CLAIM, UNRESOLVED_CONTRADICTION, STATUS_GRADE_MISMATCH, VERIFIED_NOT_ELIGIBLE, ARCHIVED_STILL_CITED
def apply(layout, report, now) -> list[Path]
#   writes evidence_grade into each claim; demotes `verified` claims no longer eligible to `belief` with status_reason; writes one kb/log/<ULID>.md entry per change with provenance (finding code, before/after); never edits claim text
```
Runtime: `queue-keeper` calls `analyze` + `apply` after each merge (Plan 01 Task 3.9 hook point), under the canonical lock, before generating the index. `apply` is a trusted runtime write; its changes are committed with the merge publication.
- [ ] Tests per finding code against the fixture; apply writes grades and log entries; running apply twice writes nothing the second time. Commit.

### Task 1.3: Staleness, code-citation renewal, compaction
**Files:** `src/autoresearch/kb/{staleness,compaction}.py`, tests
**Interfaces — Produces:**
```python
def mark_stale(layout, current_version: str, now) -> list[str]          # claims with last_checked_version < current → status stale (reason), log entries
def renew_code_citations(layout, game_repo: Path, old_sha: str, new_sha: str) -> RenewalReport   # unchanged cited line ranges → last_checked_version renewed; changed → re-verification card request (P2, refs=[claim])
def compact(layout, now, trigger=0.10, target=0.025, min_stale_days=14, pending_reverify: set[str] = frozenset()) -> list[str]
#   only when stale share ≥ trigger; archive longest-stale first to kb/archive/ until share ≤ target; skip claims stale < min_stale_days, pending re-verification, or cited by live hubs/topics/hypotheses/experiments; archived claims stay resolvable (resolver checks kb/archive/)
```
- [ ] Tests: renewal on unchanged lines; changed lines create a request; compaction no-op below 10%; above 10% archives down to 2.5%; **game update storm** (40% stale today) archives none; cited claims never archived; archived claim resolves by `ar://claim/<id>`. Commit.

### Task 1.4: Index, hub checks, KB CLI
**Files:** `src/autoresearch/kb/{index,hubs,cli}.py`, tests
- `write_generated(layout, token_budget=6000)`: `kb/index.md` (hubs, then claims grouped by Topic with status, grade, one-line summary; stale marked), `kb/_generated/backlinks.json`, `contradictions.json`, `versions.jsonl` view; byte-identical on rerun.
- `hub_flags(layout, size_limit_tokens=2000)`: `HUB_TOO_LARGE`, `HUB_DRIFT` (cited claims form ≥2 weakly connected components of ≥3 claims each), `HUB_OVERLAP` (Jaccard ≥ 0.6) → maintenance card requests.
- CLI `autoresearch kb lint|apply|index|stale|renew|compact|hubs`.
- [ ] Tests for determinism, budget truncation order (drop lowest-grade claim lines first), each flag, CLI envelopes. Commit.

---

## Milestone 2 — Statistics and calibration

### Task 2.1: Typed design, ingestion, pairing
**Files:** `src/autoresearch/stats/{design,ingest}.py`, `schemas/evaluation_design.json`, tests
**Interfaces — Produces:**
```python
@dataclass(frozen=True) class MetricSpec: name: str; direction: Literal["up","down"]; role: Literal["primary","agent_guardrail","performance"]; min_effect: float|None; tolerance: float|None
@dataclass(frozen=True) class EvaluationDesign: game_version: str; baseline_version: str; seats: tuple[int,...]; opponents: tuple[str,...]; seeds: tuple[int,...]; metrics: tuple[MetricSpec,...]; k: float; delta: dict[str,float]; n_min: int; noise_rev: str
#   frozen into experiment.evaluation_design at preregistration (C10); performance metrics come from Game.performance_guardrails (GotA: glory AND win_rate);
#   metric names and directions come from Game.outcome_metrics; the experiment adds primary metrics and agent guardrails with min_effect/tolerance.
#   The candidate is NOT in the design: it is bound once after upload via experiment.candidate_binding (Plan 01 Task 1.5) and passed to pair()/evaluate().
@dataclass class EpisodeRow: episode_id: str; game_version: str; policy_version: str; seed: int; seats: tuple[int,...]; opponents: tuple[str,...]; metrics: dict[str,float]; status: Literal["completed","failed"]
@dataclass class IngestReport: kept: int; duplicates: int; failed: int; wrong_version: int; wrong_seats: int; missing_metrics: list[str]
def ingest_rows(rows: list[EpisodeRow], game_version: str, metrics: list[str]) -> tuple[list[EpisodeRow], IngestReport]   # design-independent (used by calibration)
def ingest(rows: list[EpisodeRow], design: EvaluationDesign) -> tuple[list[EpisodeRow], IngestReport]                      # ingest_rows + seat/opponent checks
def pair(rows: list[EpisodeRow], design: EvaluationDesign, candidate_platform_id: str) -> list[tuple[EpisodeRow, EpisodeRow]]   # keys (game_version, seed, seats, opponents); baseline_version vs candidate_platform_id; unique pairs only
def signed(value: float, spec: MetricSpec) -> float   # direction normalisation: "down" metrics negated so larger is always better
```
- [ ] Tests: duplicates, failed, wrong-version, wrong-seat rows dropped and counted; missing metrics reported; pairs never mix game versions; `signed` negates "down" metrics; **lifecycle**: preregister → build → upload → bind candidate once → ingest fixture rows tagged with the real baseline and candidate platform ids → pairs found, with no change to frozen design fields. Commit.

### Task 2.2: Noise record, δ, N, bootstrap
**Files:** `src/autoresearch/stats/{noise,power,bootstrap}.py`, `schemas/metric-noise.json`, tests; add `numpy` to dependencies
```python
def compute_noise(rows, metrics: list[str], game_version, evidence_refs) -> dict   # {metric: {mean, marginal_sd, paired_diff_sd|None, n, n_pairs}, game_version, computed_at, evidence}
def write_noise(layout, record) -> str      # stats/metric-noise.yaml; previous copy kept at stats/history/<ts>.yaml; returns the record revision (git blob sha after commit)
def load_noise(layout, game_version) -> dict   # NoiseError if missing or for another game version
def sd_for(record, metric) -> float         # paired_diff_sd if n_pairs ≥ 10 else marginal_sd*sqrt(2); NoiseError if missing or zero
def delta(sd, n_planned, k=0.5, k_cap=1.0) -> float          # min(k, k_cap) * sd / sqrt(n_planned)
def min_episodes(sd, min_effect, power=0.8, alpha=0.05, floor=40) -> int   # max(floor, ceil(((z_{1-α/2}+z_{power})*sd/min_effect)**2))
def paired_bootstrap_ci(diffs, alpha=0.05, iters=10000, seed=0) -> tuple[float,float]
def freeze_design(experiment_meta, game: Game, noise: dict, planned_pairs: int) -> EvaluationDesign   # computes delta per performance metric and n_min; records noise_rev
```
- [ ] Tests: δ (sd=10, n=100, k=0.5) = 0.5; `min_episodes(10, 2)` = 197; floor 40 applied; zero or missing sd → `NoiseError`; `load_noise` for another game version → `NoiseError` (so admission requests recalibration, Plan 01 Task 3.2); seeded bootstrap reproducible; a frozen design is unchanged after a later `write_noise`. Commit.

### Task 2.3: Decision-rule evaluator and stats CLI
**Files:** `src/autoresearch/stats/{decision_rule,cli}.py`, tests
```python
def evaluate(design: EvaluationDesign, pairs, candidate_platform_id: str, current_champion: str) -> RuleOutcome   # promote_eligible: bool, failures: list[str], per_metric: dict
#   unique pairs ≥ design.n_min; every primary metric: mean signed diff ≥ min_effect and bootstrap CI excludes 0;
#   every agent guardrail: signed diff ≥ -tolerance; every performance metric (all of them; GotA: glory AND win rate): signed diff ≥ -delta[metric],
#   and a negative performance diff is allowed only when at least one primary metric is significant; design.baseline_version == current_champion
```
CLI: `"$AUTORESEARCH_CLI" stats ingest --league <id>` (uses `ingest_rows`) `| --experiment <E>` (uses `ingest` with the frozen design and the candidate binding), `stats noise`, `stats evaluate <E>` (reads the champion from Plan 01 `current_champion`). The calibration card's procedure runs `stats ingest --league` then `stats noise`.
- [ ] Tests: on fixture rows carrying both `glory` and `win_rate` from the GotA contract: glory up but win rate down beyond δ → blocked; guardrail breach (shots fired down) → blocked; slip within δ with a significant primary → eligible; slip without significance → blocked; lower-is-better primary handled; N short → blocked; baseline ≠ champion → blocked. Commit.

## Milestone 3 — softmax tool

### Task 3.1: Client and identity (real)
**Files:** `src/autoresearch/softmax_tool/{client,identity}.py`, recorded fixtures, tests
`SoftmaxClient` implements the Plan 01 `Softmax` protocol (Task 3.1 there). It writes a profile-local credentials file from `SOFTMAX_PLAYER_CREDENTIAL` (under `$HERMES_HOME/local/softmax/`) and uses coworld/softmax-cli Python APIs where public, else their CLIs with JSON output (exact commands from Plan 01 Task 0.1). **No identity cache:** every mutating method calls `whoami` with the same credential immediately before acting (C6).
- [ ] Tests with recorded responses; wrong identity → `IdentityError` before any mutating call. Commit.

### Task 3.2: XP
**Files:** `src/autoresearch/softmax_tool/xp.py`, tests
`create(experiment) -> XpHandle | CreditDenied` (tags `experiment=<E>`, `attempt=<n>`), `status`, `artifacts(handle, dest)`, `recover(experiment) -> list[XpHandle]` (lists existing requests by tag; used before any create so retries never duplicate).
- [ ] Tests: credit denial → `CreditDenied` and the caller sets `awaiting-credits` (skill + xp-poll); recover finds an existing request so a retry does not create a second; artifact download verifies hashes. Commit.

### Task 3.3: Upload and submit
**Files:** `src/autoresearch/softmax_tool/{upload,submit}.py`, tests
`upload(build_dir, experiment) -> version_id` creates the version record and an `uploaded` event (`store.add_event`) in the worker's worktree and sets `experiment.candidate_binding` once (status `uploaded`). `submit(experiment)` requires: this card holds the promotion (Plan 01 Task 3.6 `acquire`), `stats.evaluate` eligible against `current_champion`, identity re-check; then `record_intent` → Softmax submit → `record_submitted` (C8 order); the candidate STRATEGY is staged over the champion path in the worktree so the merge carries it.
- [ ] Tests for each refusal path and the ledger write order. Commit.

### Task 3.4: softmax CLI
`autoresearch softmax whoami|credits|xp create|status|artifacts|recover|upload|submit|leaderboard` with JSON envelopes; tests; commit.

---

## Milestone 4 — Doctrine and skills

### Task 4.1: Doctrine
**Files:** `doctrine/research-agent.md` (D-01..D-25 verbatim from System Design §4 with IDs and group headings), `doctrine/communication.md`, `doctrine/VERSION` (`1`), `tests/unit/test_doctrine.py`
- [ ] Tests: IDs unique and ordered; every violation code used in `runtime/handoff.py` exists; bootstrap re-seeds `AGENTS.md` when VERSION changes (Plan 01 Task 3.9 bootstrap). Commit.

### Task 4.2: Core skills
**Files:** `skills/autoresearch/<name>/SKILL.md` for `complete-card`, `queue`, `groom-queue`, `replay-diagnosis`, `diagnose`, `meta-recon`, `opponent-profile`, `league-watch`, `experiment`, `build-upload`, `xp-request`, `fetch-artifacts`, `review-xp`, `ab-compare`, `submit`, `lessons`, `communicate`, `kb-maintenance`, `calibrate-noise`
Each SKILL.md: front matter (`name`, `description` beginning "Use when"), the System Design procedure it implements (section number), exact `"$AUTORESEARCH_CLI" …` commands in order, reads/writes (System Design §6.1), doctrine IDs upheld, failure handling (for example `xp-request` on `CreditDenied` → set `awaiting-credits`, finish `partial`). `complete-card` ends with `"$AUTORESEARCH_CLI" card finish` then the native `kanban_complete` tool call with the finish JSON as metadata (serial fallback: `card finish` alone). `groom-queue` writes card requests with ops `reprioritize|drop|create` (Plan 01 Task 2.1). Sources: optimizer-seed `skills/`, co-gas `.agents/skills/`.
- [ ] Write skills in three batches (lifecycle; research; experiment/promotion), committing each.

### Task 4.3: Skill and doctrine conformance tests
**Files:** `tests/unit/test_skills.py`
- [ ] Tests: front matter valid for every skill; every `"$AUTORESEARCH_CLI" <group> <cmd>` in a skill appears in `python -m autoresearch <group> --help` output; no skill calls bare `autoresearch`; no core skill mentions `gota`/`GotA`; every doctrine ID a skill cites exists; every System Design §6.1 skill has a file. Commit.

---

## Milestone 5 — Operational configuration

### Task 5.1: Bootstrap configuration migrations
**Files:** `src/autoresearch/runtime/config_migrations.py`, tests (fake `hermes config set` recorder) + integration (marker `hermes`)
Implements the `00-overview.md` C9 table exactly: each key with its policy (always / missing-only), applied with `hermes config set`; `HERMES_WRITE_SAFE_ROOT` = canonical (covers `.worktrees/`) in the profile `.env`; `AUTORESEARCH_CLI` in the profile `.env`; `skills.create_dir` = `$HERMES_HOME/local/skills-staging`; no `kanban.default_assignee`; exact env passthrough list; auto-update off; curator exclusion of shipped skills; the model defaults to `moonshotai/kimi-k3` with fallback `z-ai/glm-5.3` (installer may override `OPENROUTER_MODEL`). Profile name read from `HERMES_HOME` (no hard-coded assignee).
- [ ] Tests: each key applied; missing-only keys not overwritten; integration: fresh install then update keeps installer's `kanban.max_in_progress: 3`. Commit.

### Task 5.2: MEMORY.md snapshot and README operations guide
**Files:** `src/autoresearch/runtime/memory_snapshot.py`, `README.md` (install, configure, operate, restore, update), tests
- Snapshot: after each merge, `queue-keeper` copies `$HERMES_HOME/memories/MEMORY.md` to `canonical/.supervisor/hermes-memory.md` (committed). Restore: README documents copying it back on a new host.
- README covers prerequisites (uv, Nim, git, Hermes v0.21.5 install command), `hermes profile install`, `.env` keys, resuming the three cron jobs, the Kanban go/no-go switch, restore from the workspace remote, and update.
- [ ] Tests: snapshot written and unchanged files produce no commit; README commands lint (each fenced command's executable exists). Commit.

---

## Milestone 6 — Full cycles and acceptance

### Task 6.0: Full research cycle on the fake game (marker `hermes`)
**Preconditions:** Plan 01 Milestone 5 passed; Plan 03 Milestones 1–4 complete.
**Files:** `tests/integration/test_fake_cycle.py`
- [x] Install with `AUTORESEARCH_GAME=fakegame`, `AUTORESEARCH_SOFTMAX=fake` (persistent FakeSoftmax with league episodes for calibration), a bare remote, and a real cheap model. Resume cron jobs. Let the agent run from an empty plan (no scripted card bodies; real doctrine and skills).
- [x] Assert within 90 min: calibration card writes a noise record; planning creates a Topic and cards; an investigation produces a hypothesis; an experiment freezes an evaluation design, builds, uploads, launches XP; xp-poll requests the review; review writes a Result and a decision; on `promote`, the promotion card submits and the promotion reaches `published`.
- [x] Inject `credits=0` mid-cycle once: the experiment goes `awaiting-credits` and resumes when credits return, with no duplicate XP in FakeSoftmax.
- [x] Record cost and wall time in `docs/plans/findings/fake-cycle.md`. Commit.


### Task 6.1: Real cycle on local Hermes (manual gate with checklist)
**Preconditions:** Plan 01 complete; Plan 02 compiler build binding; Plan 01 Task 0.1 PASS (done); real Player credential, OpenRouter key (model defaults to `moonshotai/kimi-k3`), workspace remote.
- [ ] **Step 1:** Install fresh; fill `.env`; resume cron jobs.
- [ ] **Step 2:** Observe in order and tick each with evidence (journal ids, outbox seqs, commit SHAs): calibration card writes a noise record; planning card creates Topics and investigation cards; an investigation card produces hypotheses with evidence; an experimentation card builds, uploads, launches XP; xp-poll creates the review card; review writes a Result and a decision; if `promote`, the promotion card submits and the champion is published.
- [ ] **Step 3:** Timeouts: if no card completes within 60 min, or the same signal fires 3 times, stop and record the state; that is a finding, not a pass.
- [ ] **Step 4:** Record cost (tokens, credits), wall time, and every manual intervention in `docs/plans/findings/first-real-cycle.md`. Commit.

### Task 6.2: Acceptance and handoff checklist
**Files:** `docs/plans/findings/acceptance-checklist.md`
- [ ] Leaderboard measurement procedure for Friday (league, player, how to read rank, what counts as top half; fallback criterion "plays reasonably" defined as completing episodes without errors and glory within one SD of the league median).
- [ ] Hosting handoff for Rohit: required env keys, remote access, token refresh owner, evidence storage and refetch policy (replays not in git), restore procedure, Kanban go/no-go status, known Hermes issues.
- [ ] Local readiness vs hosted run clearly separated. Commit.
