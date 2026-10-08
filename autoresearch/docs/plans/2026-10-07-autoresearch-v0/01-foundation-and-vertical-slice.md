# Plan 01 — Foundation and Protocol Slice

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `00-overview.md` first: contracts C1–C12 are binding.

**Goal:** A fresh install of the autoresearch profile on local Hermes v0.21.5 admits cards, runs two concurrent card workers through the real Kanban dispatcher, and processes their results with durable finish, entity-aware merge, ordered outbox publication, promotion state, pause, and rebuild — proven with scripted card bodies and recovery tests.

**Architecture:** Deterministic Python package (`src/autoresearch/`); stdlib-only plugin and launchers; Hermes Kanban as the queue; `queue-keeper` as the single canonical writer. See `00-overview.md`.

**Tech Stack:** Python 3.12, uv, pytest, jsonschema, python-frontmatter, PyYAML, python-ulid, git, Hermes v0.21.5.

**Spec:** System Design §3, §3.6–3.7, §5.1, §5.8, §7; Runtime §3–§9; `00-overview.md` C1–C12.

## Global Constraints
See `00-overview.md`. Stdlib-only: `src/autoresearch/{launch,runctx}.py`, `src/autoresearch/plugin/{__init__,adapters,policy}.py`, and `plugins/autoresearch/__init__.py`. Every other module may use the locked dependencies. All JSON CLIs print one envelope `{"ok": bool, "data"|"error": …}` with exit codes 0 ok, 1 validation/business failure, 2 bad arguments, 3 missing tool.

## Review Focus
1. **Fresh install executes** from a copy outside the source tree with no ambient package: cron launcher syncs the venv, worker shell finds `$AUTORESEARCH_CLI`, plugin loads. → Task 1.3.
2. **Identity failure before or during work**: wrong credential on the first tick admits nothing and pauses; a running worker's upload is refused. → Tasks 3.1, 3.9, 5.2.
3. **Concurrent finish, merge, and publication with crashes**: reverse-order completion, crash at every publication step, retry of the same task, removed worktree. → Tasks 3.3–3.5.
4. **Promotion state**: completed holder waiting for publication is never taken over; submit succeeded before the record. → Task 3.6.
5. **Restore from the remote only**: clone into an empty home after admitted requests, a credit refusal, and a submit-before-publication. → Task 3.8.

## File Structure (this plan)
```
autoresearch/
  pyproject.toml uv.lock distribution.yaml config.yaml SOUL.md .gitignore README.md
  cron/jobs.json
  scripts/queue_keeper.py scripts/xp_poll.py scripts/health.py
  plugins/autoresearch/plugin.yaml plugins/autoresearch/__init__.py
  schemas/entity/*.json schemas/{card_request,handoff,message,inbound,receipt}.json
  games/fakegame/                                   # test-only game extension
  src/autoresearch/
    __init__.py __main__.py cli.py launch.py ids.py paths.py runctx.py
    io/docs.py io/locks.py io/gitops.py
    games/loader.py
    records/registry.py records/mutability.py records/store.py records/cli.py
    queue/card.py queue/base.py queue/file_queue.py queue/kanban_queue.py queue/requests.py queue/cli.py
    comms/envelope.py comms/stage.py comms/publisher.py comms/reader.py comms/inbound.py comms/resolver.py comms/cli.py
    runtime/{bootstrap,preflight,pause,briefing,admission,card_finish,handoff,merge,promotion,routing,signals,planning,rebuild,queue_keeper,xp_poll,health}.py
    softmax_tool/protocol.py softmax_tool/fake.py
    plugin/__init__.py plugin/adapters.py plugin/policy.py
  tests/unit/ tests/protocol/ tests/contract/ tests/integration/ (marker hermes)
```

---

## Milestone 0 — De-risking checks

### Task 0.1: Player-only XP probe — DONE (PASS)
Completed 2026-10-07; see `docs/plans/findings/player-xp-probe.md`. A Player-only credential (Games Bond, no `--elevated`) can create, get, list, read episodes of, and download artifacts from XP requests on the GotA league. The single-credential design (C6) stands. Note: XP requests record `requester_user_id` = the user account.

### Task 0.2: Hermes v0.21.5 facts
**Files:** `docs/plans/findings/hermes-facts.md`
- [ ] **Step 1:** `uv tool install git+https://github.com/NousResearch/hermes-agent@v2026.9.24`; `hermes --version` prints `0.21.5`.
- [ ] **Step 2:** In `HERMES_HOME=/tmp/hh`, record PASS/FAIL with evidence:
  1. Profile install without `distribution_owned` copies `scripts/`, `plugins/`, `src/`, `uv.lock`, `bin/` is absent until bootstrap; shipped cron jobs arrive `enabled: false, state: "paused"`.
  2. With `plugins.enabled: [autoresearch]`, the plugin loads in Kanban worker sessions and the gateway; `pre_tool_call` returning `{"action":"block","message":…}` blocks; `pre_llm_call` (`is_first_turn=True`) injects text.
  3. `hermes kanban --board B create T --body J --branch card/X --workspace worktree:<repo>/.worktrees/X --priority 40 --idempotency-key K --json` returns a task; repeating the key returns the same task; the worktree branch is `card/X`.
  4. An **unassigned** ready task is never claimed for 3 dispatcher ticks; `assign <id> <profile>` makes it claimable; `assign <id> none` on a ready task un-assigns it (C7).
  5. Highest `priority` is claimed first; `max_in_progress: 2` caps workers; `auto_decompose: false`, `review_dispatch: false` hold; no `default_assignee` fill-in.
  6. `kanban show <id> --json` exposes runs with run id and terminal outcome.
  7. `terminal.env_passthrough` (exact names) delivers `SOFTMAX_PLAYER_CREDENTIAL` and `AUTORESEARCH_CLI` to a cron script and a worker terminal.
  8. `hermes config set` writes nested keys.
  9. Copy one real `cron/jobs.json` record produced by `hermes cron create` for a no-agent script job created paused.
  10. A worker's cwd is the worktree; `AGENTS.md` committed in the canonical repo appears there and loads.
  11. Tool calling works in Hermes through OpenRouter with `moonshotai/kimi-k3`, `z-ai/glm-5.3`, and `z-ai/glm-5.3-flash`: each completes a card that runs one terminal command, writes one file, and calls `kanban_complete`. Record failures per model; a failing primary is a decision for James.
- [ ] **Step 3:** For each FAIL, write the contract amendment into `00-overview.md` and get James's OK before Milestone 3. Commit.

(The GotA globals-budget check is `02-gota-and-compiler.md` Task 0.1.)

---

## Milestone 1 — Package, install, records, game loader

### Task 1.1: Package, launchers, CLI entry
**Files:** `pyproject.toml`, `src/autoresearch/{__init__,__main__,cli,launch}.py`, `scripts/{queue_keeper,xp_poll,health}.py`, `.gitignore`, `tests/unit/test_launch.py`
**Interfaces — Produces:**
```python
# launch.py (stdlib)
class LaunchError(RuntimeError): ...
def lock_sha(hermes_home: Path) -> str                           # sha256 of uv.lock
def ensure_venv(hermes_home: Path, run=subprocess.run) -> bool   # under file lock local/venv.lock; runs `uv sync --frozen --no-dev --project <home>` with UV_PROJECT_ENVIRONMENT=<home>/local/venv only if local/venv/.lock-sha != lock_sha; writes the stamp; returns True if it synced
def main(job: str, argv: list[str] | None = None) -> int          # ensure_venv then os.execv(<venv>/bin/python, [py, "-m", f"autoresearch.runtime.{job}", *argv])
```
Each `scripts/<job>.py`: insert `$HERMES_HOME/src` (or the repo root's `src`) into `sys.path`, `from autoresearch.launch import main`, `raise SystemExit(main("<job>"))`.
- [ ] **Step 1: Failing tests**
```python
def test_sync_runs_once_per_lock(tmp_path):
    (tmp_path/"uv.lock").write_text("a"); calls=[]
    run=lambda argv, env, check: calls.append(argv)
    assert launch.ensure_venv(tmp_path, run=run) is True
    (tmp_path/"local"/"venv").mkdir(parents=True, exist_ok=True)
    assert launch.ensure_venv(tmp_path, run=run) is False and len(calls)==1
    (tmp_path/"uv.lock").write_text("b"); assert launch.ensure_venv(tmp_path, run=run) is True
def test_missing_uv_is_actionable(tmp_path):
    (tmp_path/"uv.lock").write_text("a")
    def run(*a, **k): raise FileNotFoundError("uv")
    with pytest.raises(launch.LaunchError, match="install uv"): launch.ensure_venv(tmp_path, run=run)
def test_concurrent_launchers_sync_once(tmp_path):
    import threading, time
    (tmp_path/"uv.lock").write_text("a"); calls=[]
    def run(argv, env, check):
        time.sleep(0.3); calls.append(argv); (tmp_path/"local"/"venv").mkdir(parents=True, exist_ok=True)
    ts=[threading.Thread(target=launch.ensure_venv, args=(tmp_path,), kwargs={"run": run}) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(calls)==1
```
- [ ] **Step 2:** FAIL. **Step 3:** Implement; `pyproject.toml` per overview stack with `[project.scripts] autoresearch="autoresearch.cli:main"`, pytest markers `hermes`, `addopts="-m 'not hermes'"`. `cli.py` registers groups added by later tasks. **Step 4:** `uv lock && uv run pytest -v` PASS, `ruff check` clean. **Step 5:** Commit.

### Task 1.2: Distribution files
**Files:** `distribution.yaml`, `config.yaml`, `SOUL.md`, `cron/jobs.json`, `plugins/autoresearch/{plugin.yaml,__init__.py}`, `src/autoresearch/plugin/{__init__,adapters,policy}.py` (stubs), `tests/unit/test_distribution.py`
- [ ] **Step 1: Failing tests**
```python
def test_manifest():
    m = yaml.safe_load((ROOT/"distribution.yaml").read_text())
    assert m["name"]=="autoresearch" and m["hermes_requires"]==">=0.21.5" and "distribution_owned" not in m
    env={e["name"]:e for e in m["env_requires"]}
    for k in ["OPENROUTER_API_KEY","SOFTMAX_PLAYER_CREDENTIAL","AUTORESEARCH_PLAYER_ID","AUTORESEARCH_WORKSPACE_REMOTE"]: assert env[k]["required"] is True
    assert env["OPENROUTER_MODEL"]["default"]=="moonshotai/kimi-k3"
    assert env["AUTORESEARCH_GAME"]["default"]=="gota"
def test_config():
    c=yaml.safe_load((ROOT/"config.yaml").read_text())
    assert "autoresearch" in c["plugins"]["enabled"]
    assert c["model"]["default"]=="moonshotai/kimi-k3" and "z-ai/glm-5.3" in json.dumps(c["fallback_providers"])
    k=c["kanban"]; assert (k["max_in_progress"],k["auto_decompose"],k["review_dispatch"],k["auto_subscribe_on_create"])==(2,False,False,False) and "default_assignee" not in k
    assert c["memory"]=={"memory_enabled":True,"user_profile_enabled":False,"write_approval":False}
    assert set(c["terminal"]["env_passthrough"])=={"SOFTMAX_PLAYER_CREDENTIAL","AUTORESEARCH_PLAYER_ID","AUTORESEARCH_WORKSPACE","AUTORESEARCH_WORKSPACE_REMOTE","AUTORESEARCH_GAME","AUTORESEARCH_SOFTMAX","AUTORESEARCH_CLI"}
def test_cron_jobs():
    jobs=json.loads((ROOT/"cron/jobs.json").read_text())["jobs"]; by={j["id"]:j for j in jobs}
    assert set(by)=={"queue-keeper","xp-poll","health"}
    for j in jobs: assert j["no_agent"] is True and j["enabled"] is False and j["state"]=="paused" and isinstance(j["schedule"],dict)
def test_plugin_import_graph_is_stdlib_only():
    code=f"import runpy,sys; sys.argv=['x']; runpy.run_path({str(ROOT/'plugins/autoresearch/__init__.py')!r})"
    r=subprocess.run([sys.executable,"-S","-I","-c",code],capture_output=True,text=True,env={"HERMES_HOME":str(ROOT)})
    assert r.returncode==0, r.stderr
```
- [ ] **Step 2:** FAIL. **Step 3:** Write files; `cron/jobs.json` copies the record shape from Task 0.2 item 9. `plugins/autoresearch/__init__.py` inserts `$HERMES_HOME/src` into `sys.path` and does `from autoresearch.plugin.adapters import register`. **Step 4:** PASS. **Step 5:** Commit.

### Task 1.3: Fresh-install integration (marker `hermes`)
**Files:** `tests/integration/conftest.py`, `tests/integration/test_install.py`
- [ ] **Step 1:** Fixture: `git archive HEAD autoresearch | tar -x -C tmp/src-copy`; `HERMES_HOME=tmp/hh`; empty `PYTHONPATH`; `hermes profile install tmp/src-copy/autoresearch --name ar-it -y`; write `.env` with fake values and `AUTORESEARCH_SOFTMAX=fake`; a local bare repo as `AUTORESEARCH_WORKSPACE_REMOTE`.
- [ ] **Step 2: Tests**
  - `test_payload_present`: profile has `scripts/queue_keeper.py`, `src/autoresearch/launch.py`, `uv.lock`, `plugins/autoresearch/plugin.yaml`.
  - `test_launcher_bootstraps`: run `python <profile>/scripts/queue_keeper.py --bootstrap-only` (flag handled by `runtime.queue_keeper`, Task 3.9 adds it; until then a minimal `queue_keeper.main` that runs `bootstrap` only); assert `local/venv/bin/python`, `bin/autoresearch`, canonical `workspace/.git`, `.supervisor/ledger/`, pushed `main` on the bare remote.
  - `test_worker_shell_finds_cli`: create one card body "run `"$AUTORESEARCH_CLI" --version` and finish"; let one worker run with `z-ai/glm-5.3-flash` (skip if no OpenRouter key); assert the run log contains the version string.
  - `test_plugin_loads_in_worker`: same run; assert `runtime/tool_events.jsonl` exists (post_tool_call stub appends).
  - `test_update_preserves`: write `workspace/x`, `memories/MEMORY.md`, set `kanban.max_in_progress: 3`; `hermes profile update ar-it` from a modified copy; assert both files survive, cap stays 3, changed script arrived.
- [ ] **Step 3:** Iterate to PASS. **Step 4:** Commit.

### Task 1.4: IDs, run context, layout, IO, locks, git
**Files:** `src/autoresearch/{ids,paths,runctx}.py`, `src/autoresearch/io/{docs,locks,gitops}.py`, tests
**Interfaces — Produces:**
```python
# ids.py (uses python-ulid)
ENTITY_PREFIXES={"topic":"T","hypothesis":"H","experiment":"E","result":"R","version":"V","claim":"C","hub":"HUB","lesson":"L","journal":"J","message":"M","inbound":"I","card_request":"Q","evidence":"EV","version_event":"VE"}
def new_id(entity)->str; def parse_id(s)->tuple[str,str]; def short(s)->str
# runctx.py (stdlib)
@dataclass(frozen=True) class RunContext: hermes_home: Path; canonical: Path; worker: Path|None; card_id: str|None; run_id: str|None
def from_env(env=os.environ) -> RunContext     # serial fallback: if HERMES_KANBAN_TASK unset and canonical/.supervisor/runtime/serial_current.json exists, read card_id/run_id/worker from it
def runtime_dir(ctx)->Path; def ledger_dir(ctx)->Path
# paths.py
@dataclass(frozen=True) class Layout: root: Path   # kb, claims, hubs, archive, kb_log, kb_generated, topics, hypotheses, experiments, results, versions, evidence_index, journal, comms_outbox, comms_staged, comms_inbox, card_requests, strategy, candidates, stats, plan, agents_md, ledger
# io/docs.py: read_doc, write_doc (atomic), read_json, write_json (atomic)
# io/locks.py: file_lock(path, timeout_s=60) context manager (fcntl)
# io/gitops.py: git, head, commit_all(allow_empty), diff_paths(a,b), diff_status(a,b)->list[(status,path)], show(sha,path)->bytes|None, merge_no_ff(sha,msg)->(ok, conflict_paths), contains(sha), push(remote, refspecs), merge_base(a,b)
```
- [ ] **Step 1: Failing tests:** `new_id` prefix/length/order; `parse_id` rejects unknown prefix; `short("C-01K6Z3V8QXABCDEFGHJKMNPQRS")=="C-MNPQRS"`; `from_env` worker vs canonical vs override; serial fallback file read; atomic write leaves no temp file; second lock holder times out; `merge_no_ff` on a conflict returns `(False,["f.txt"])` and leaves a clean tree; `diff_status` reports deletions as `D`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit.

### Task 1.5: Records — registry, mutability, store, CLI
**Files:** `schemas/entity/{topic,hypothesis,experiment,result,version,version_event,claim,hub,lesson,journal,evidence}.json`, `src/autoresearch/records/{registry,mutability,store,cli}.py`, `tests/unit/records/{fixtures.py,test_store.py,test_mutability.py,test_cli.py}`
**Interfaces — Produces:**
```python
# registry.py
REGISTRY: dict[str, EntityType]   # EntityType(name, prefix, subdir, fmt, schema)
def path_for(layout, id_, slug=None)->Path; def entity_of_path(layout, rel_path: str)->str|None
# mutability.py
def check_change(entity: str, before: Doc|None, after: Doc|None) -> list[str]   # Doc=(meta, body); after None = deletion
# store.py
class RecordStore:
    def create(self, entity, meta, body="", slug=None)->str
    def load(self, id_)->tuple[dict,str]
    def update(self, id_, patch: dict, body: str|None=None)->None
    def add_event(self, version_id: str, kind: Literal["uploaded","submitted"], data: dict)->str   # versions/<V>/events/<ULID>.json
    def find(self, entity, **eq)->list[str]
    def validate_change(self, rel_path: str, before: Doc|None, after: Doc|None)->list[str]          # used by handoff validation at SHAs
```
Mutability (System Design §3):
- Immutable (no body or meta change, no deletion): `result`, `evidence`, `version`, `version_event`, `journal` once `outcome` set. Corrections are new records with `supersedes`.
- `experiment`: design fields `hypothesis, baseline, change, evaluation_design, environment, parents` frozen once `status != designed`; `candidate_binding {version_id, platform_policy_version_id}` is set exactly once, in status `uploaded`, before any XP request; `results`, `xp_requests` append-only (each request has `batch`, `attempt`); status graph `designed→built→uploaded→evaluating→reviewed`, plus `uploaded|evaluating → awaiting-credits → evaluating` (partial batches resume under the same preregistration); `decision` set once; `lifecycle.launch_attempts` monotonically increases.
- `hypothesis`: transitions per System Design §3.3, each with `state_reason` and a ref; `experiments` append-only.
- `claim`: `evidence` append-only; contradiction resolution is a new evidence entry `{stance: "resolves", target: <evidence ref>, ref}`; status changes need `status_reason`; `superseded_by` set once; body edits allowed (prose describes the present).
- Deletion of any record except a staged message → problem.
- No `lever` entity: closed levers are refuted mechanism claims.
- [ ] **Step 1: Failing tests** (`fixtures.py` defines fully populated `TOPIC`, `HYP`, `EXP_DESIGNED`, `RESULT`, `CLAIM`, `VERSION` dicts, every required schema field set):
```python
def test_create_validate(store, layout):
    cid=store.create("claim", CLAIM, "Damage is 10.", slug="damage"); assert path_for(layout,cid,"damage").exists()
def test_result_body_change_rejected(store):
    rid=store.create("result", RESULT, "analysis A"); with pytest.raises(ValueError, match="immutable"): store.update(rid, {}, body="analysis B")
def test_result_deletion_rejected(store, layout):
    rid=store.create("result", RESULT, "a"); before=store.load(rid)
    assert any("deleted" in e for e in store.validate_change(str(path_for(layout,rid).relative_to(layout.root)), before, None))
def test_design_frozen_after_build(store):
    eid=store.create("experiment", EXP_DESIGNED); store.update(eid,{"status":"built"})
    with pytest.raises(ValueError, match="evaluation_design"): store.update(eid,{"evaluation_design":{**EXP_DESIGNED["evaluation_design"],"n_min":5}})
def test_candidate_binding_set_once(store):
    eid=store.create("experiment", {**EXP_DESIGNED,"status":"uploaded"}); store.update(eid,{"candidate_binding":{"version_id":"V-1","platform_policy_version_id":"pv_1"}})
    with pytest.raises(ValueError, match="candidate_binding"): store.update(eid,{"candidate_binding":{"version_id":"V-2","platform_policy_version_id":"pv_2"}})
def test_evaluating_to_awaiting_credits_allowed(store):
    eid=store.create("experiment", {**EXP_DESIGNED,"status":"evaluating"}); store.update(eid,{"status":"awaiting-credits"})
def test_results_append_only(store):
    eid=store.create("experiment", EXP_DESIGNED); store.update(eid,{"results":["R-1"]})
    with pytest.raises(ValueError, match="append-only"): store.update(eid,{"results":["R-2"]})
def test_contradiction_resolution_is_appended(store):
    cid=store.create("claim", {**CLAIM,"evidence":[{"ref":"EV-1","tier":"B","direct":True,"stance":"contradicts","source_group":"g1"}]}, "x")
    store.update(cid,{"evidence":[*store.load(cid)[0]["evidence"],{"ref":"R-9","stance":"resolves","target":"EV-1","tier":"B","direct":True,"source_group":"g2"}]})
def test_version_events(store):
    vid=store.create("version", VERSION); eid=store.add_event(vid,"uploaded",{"policy_version_id":"pv_1"})
    assert eid.startswith("VE-") and store.find("version_event", version=vid)==[eid]
def test_journal_needs_decisions(store):
    with pytest.raises(ValueError, match="Decisions"): store.create("journal", {"iteration":1,"mode":"planning","card":"t_1","doctrine_version":"1","outcome":"done"}, "none")
```
CLI tests (`python -m autoresearch records new|show|update|status|link|find|validate`): `status <id> <new-state> --reason R --ref X` applies a transition; `link <id> <field> <target>` appends to an append-only list; envelopes and exit codes.
- [ ] **Step 2–5.** Commit `feat: records with mutability rules and CLI`.

### Task 1.6: Game loader and the fake game
**Files:** `src/autoresearch/games/loader.py`, `games/_template/GAME.md`, `games/fakegame/{GAME.md,seed/,tools/fake_build.py,tools/fake_eval.py}`, tests
**Interfaces — Produces:** `load_game(repo_root, name) -> Game` with `bindings: dict[str,str]` (`build, local_check, upload, evaluate, replay_inspect`), `reference_types: dict[str,str]`, `outcome_metrics: list[GameMetric]` (`GameMetric(name, direction: "up"|"down")`; Plan 03 maps these into its statistical `MetricSpec` with roles), `performance_guardrails: list[str]`, `min_episodes_floor: int`, `league_id: str`, `noise_source: str`, `layers: list[str]`, `compiler: Literal["pw","basic","none"]`; `GameError` names any missing field.
- [ ] Tests: template has every field; fakegame loads; missing binding → `GameError` naming it; fake build deterministic hash; fake eval deterministic per seed. Commit.

---

## Milestone 2 — Queue

### Task 2.1: Card model, protocol, requests, file queue
**Files:** `schemas/card_request.json`, `schemas/receipt.json`, `src/autoresearch/queue/{card,base,requests,file_queue}.py`, tests
**Interfaces — Produces:**
```python
Mode=Literal["investigation","scouting","experimentation","maintenance","planning"]; Priority=Literal["P0","P1","P2","P3","P4","P5"]
@dataclass class CardSpec: request_id: str; title: str; mode: Mode; why: str; refs: list[str]; created_by: str; priority: Priority; order: int = 0
@dataclass class Card: task_id: str; spec: CardSpec; state: Literal["pending","claimed","completed","blocked"]; assignee: str|None; runs: list[RunInfo]
@dataclass class RunInfo: run_id: str; terminal: bool; outcome: str|None
class Queue(Protocol):
    def create_unassigned(self, spec: CardSpec, branch: str, worktree: Path) -> str         # idempotent on req:<request_id>; returns task id
    def assign(self, task_id: str, assignee: str|None) -> None                              # None = unassign
    def get(self, task_id: str) -> Card; def list(self, state: str|None=None) -> list[Card]
    def reprioritize(self, task_id: str, priority: Priority, order: int=0) -> None          # pending only
    def drop(self, task_id: str, reason: str) -> None                                       # pending → completed, outcome dropped
    def requeue(self, task_id: str, reason: str) -> None
def kanban_priority(p: Priority, order: int=0) -> int     # 10*(5-int(p[1]))+order, order 0..9
# requests.py
def write_request(layout, op: Literal["create","reprioritize","drop"], spec: CardSpec|None=None, target_request: str|None=None, priority: Priority|None=None, reason: str|None=None) -> str   # cards/requests/<Q-id>.json
def read_requests(repo: Path, sha: str) -> list[dict]     # read from a commit, not a directory
```
`FileQueue` (tests and serial fallback) adds `claim_next(worker) -> Card|None` (assigned + pending, highest priority then oldest) and `finish(task_id, run_id, outcome)`.
- [ ] Tests: idempotent create; `kanban_priority` values (P0→50, P5→0, ("P2",3)→33); unassigned cards never returned by `claim_next`; drop/reprioritize only on pending; concurrent `claim_next` never double-claims; `read_requests` reads from a commit SHA. Commit.

### Task 2.2: Kanban queue
**Files:** `src/autoresearch/queue/kanban_queue.py`, `tests/unit/queue/test_kanban_queue.py` (fake `hermes` on PATH), `tests/integration/test_kanban_queue.py` (marker `hermes`)
`KanbanQueue(board, profile, hermes_bin="hermes")`; every command starts `hermes kanban --board <board>`:
- create_unassigned: `create <title> --body <json> --branch <branch> --workspace worktree:<worktree> --priority <int> --idempotency-key req:<request_id> --json`
- assign: `assign <id> <profile|none>`; get/list: `show <id> --json`, `list --json`; reprioritize: `edit <id> --priority <int>`; drop: `complete <id> --summary "dropped: <reason>" --metadata '{"outcome":"dropped"}'` (pending tasks; add `--force` only if Task 0.2 shows it is required); requeue: the command Task 0.2 identifies for blocked tasks.
- [ ] Unit tests assert exact argv and parse canned JSON; integration test runs each operation on real Hermes. Commit.

### Task 2.3: Queue CLI
`"$AUTORESEARCH_CLI" queue list|show|request` — `request` writes a card request into the current worker's worktree (create/reprioritize/drop); `list|show` read the board. Tests for each subcommand's envelope; commit.

---

## Milestone 3 — Runtime protocols

### Task 3.1: Softmax protocol, fake backend, preflight
**Files:** `src/autoresearch/softmax_tool/{protocol,fake}.py`, `src/autoresearch/runtime/preflight.py`, tests
**Interfaces — Produces:**
```python
class Softmax(Protocol):
    def whoami(self) -> Identity                         # Identity(subject_type, player_id, expires_at)
    def credits(self) -> int
    def xp_create(self, experiment: str, batch: int, attempt: int, spec: dict) -> XpHandle | CreditDenied
    def xp_status(self, handle: XpHandle) -> XpStatus
    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]
    def xp_recover(self, experiment: str) -> list[XpHandle]
    def upload(self, artifact: Path, name: str) -> str   # policy_version_id
    def submit(self, policy_version_id: str, league_id: str) -> str   # submission id
    def submissions(self, player_id: str) -> list[dict]
    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None
class FakeSoftmax(Softmax):   # persistent JSON state at <canonical>/.supervisor/runtime/fake_softmax.json under a file lock; fault injection via env AUTORESEARCH_FAKE_FAULTS="credits=0;fail_after=submit;identity=user"
def from_env(env, canonical) -> Softmax                  # AUTORESEARCH_SOFTMAX=fake → FakeSoftmax; else real client (Plan 03 Task 3.1)
def preflight(env, sm: Softmax, canonical: Path, now) -> PreflightResult   # ok, reasons, warnings, credits
```
- [ ] Tests: FakeSoftmax state persists across two processes (`subprocess` writes, parent reads); faults injectable; preflight: user token → not ok; wrong player → reason names both ids; expired → not ok; expiring in 1 h → warning; disk < 1 GB → not ok. Commit.

### Task 3.2: Pause, briefing, admission
**Files:** `src/autoresearch/runtime/{pause,briefing,admission}.py`, tests
**Interfaces — Produces:**
```python
def pause(queue, ledger: Path, reason: str) -> list[str]     # unassign pending tasks, record prior assignees in ledger/pause.json; idempotent
def resume(queue, ledger: Path) -> list[str]                  # reassign exactly the recorded tasks; clears pause.json
def admit(canonical: Path, spec: CardSpec, queue, game: Game) -> list[str]
#   refs resolve at canonical HEAD (resolver, Task 3.5); experimentation needs a noise record for the current game version (C10);
#   planning: rejected if a planning card is pending/claimed/finished-unpublished (C11) — caller moves it to the backlog
def build_briefing(canonical: Path, spec: CardSpec, task_id: str, inputs: BriefingInputs) -> str
def admit_and_create(ctx, queue, spec: CardSpec, profile: str, inputs: BriefingInputs) -> AdmissionResult   # the C3 transaction with receipts
```
- [ ] Tests: pause unassigns only pending tasks and resume restores the same assignees; repeated pause does not overwrite the recorded assignees; admission rejects unresolved refs, experimentation without a current-version noise record, a second planning card; `admit_and_create` crash after create (simulated exception before assign) → rerun finds the same task via idempotency key and finishes assign; briefing content rules (non-planning omits plan.md and proposals; includes only Topics of refs; urgent inbound marked "handled by <task>" when not responsible). Commit.

### Task 3.3: Card finish and validation at SHAs
**Files:** `src/autoresearch/runtime/{card_finish,handoff}.py`, `schemas/handoff.json`, `src/autoresearch/records/cli.py` (adds `card finish`), tests `tests/protocol/test_card_finish.py`
**Interfaces — Produces:**
```python
def validate_at(repo: Path, base_sha: str, result_sha: str, spec: CardSpec, responsible_inbound: list[str], task_id: str) -> HandoffReport
#   from git only: exactly one new journal for task_id with ## Decisions and doctrine_version; each responsible inbound of kind reply|steer acknowledged by a staged note (in_reply_to); inbound `task` is exempt (the runtime acknowledges it at publication, C11);
#   every changed record passes store.validate_change(before at base, after at result, deletions included); existing entities changed ⊆ spec.refs (else D-23);
#   no generated or ledger files changed; staged messages and card requests valid
def finish(ctx: RunContext, outcome: str) -> FinishResult
#   commit worktree on its branch (allow empty) → result_sha; base_sha = merge_base(result_sha, canonical HEAD); validate_at; if invalid and outcome != "failed": print problems, exit 1 (worker fixes, retries);
#   else, in one canonical commit under canonical.lock: write ledger/handoffs/<task>/<run>.json state=committed AND move the request receipt to handed_off{task, run} (trusted path); then mark runtime/cards/<task>.json finished=true
```
- [ ] **Step 1: Failing tests** (real git; canonical + worktree via `git worktree add`):
  - valid finish → handoff file at `ledger/handoffs/<task>/<run>.json`, committed in canonical, `finished=true`;
  - no changes → empty commit recorded;
  - missing journal → exit 1, no record;
  - editing an existing hypothesis not in refs → problem, `violations["D-23"]==1`;
  - editing `kb/index.md` or a ledger file → problem;
  - responsible inbound not acknowledged → problem names it; a non-responsible inbound need not be acknowledged;
  - retry of the same task with a new run id → a second record file, the first unchanged;
  - after finish, the worktree directory is deleted → validation of the record still works from SHAs.
- [ ] **Step 2–5.**

### Task 3.4: Handoff processing and merge
**Files:** `src/autoresearch/runtime/{merge}.py`, tests `tests/protocol/test_merge.py`
**Interfaces — Produces:**
```python
def process_handoffs(ctx, queue, store) -> list[HandoffOutcome]
#   for each ledger/handoffs/*/*.json with state in {committed, validated, merged}: skip unless the run is terminal (queue.get(task).runs) or the task is from an earlier board epoch (C3 adoption);
#   reconciles the request receipt (sets handed_off if a crash left it behind) and moves it to published/superseded at the end
#   committed: if contains(result_sha) → merged; else validate_at → validated (report stored) or repair
#   validated: overlap W∩M → reconcile; merge_no_ff → merged(merge_sha) or reconcile (conflict)
#   repair/reconcile: write a card request (P0; refs = original refs + ar://handoff/<task>/<run>; why = problems or both SHAs)
#   superseded: set when the repair/reconcile card's own handoff publishes
def find_orphan_done(queue, ledger) -> list[str]    # done tasks whose last run has no handoff → repair request
```
- [ ] **Step 1: Failing tests** (simulated workers with real git):
  - disjoint new entities, B finishes first, then A → both merge;
  - same hypothesis, non-overlapping lines (git would merge) → second → reconcile with both SHAs;
  - textual conflict elsewhere → reconcile;
  - handoff present but run not terminal → not processed;
  - crash after merge before state write → rerun → `contains` → merged, no second merge commit and no revalidation;
  - invalid content → repair request referencing `ar://handoff/...`; when the repair card's handoff publishes, the original becomes `superseded` and is never processed again;
  - `done` task with no handoff → repair request;
  - board-epoch adoption: a committed handoff whose task is not on a new-epoch board is processed without a run lookup and its request receipt reaches `published`.
- [ ] **Step 2–5.**

### Task 3.5: Resolver, staging, publication, reader, comms CLI, fake Preston
**Files:** `schemas/message.json`, `src/autoresearch/comms/{envelope,resolver,stage,publisher,reader,cli}.py`, tests `tests/protocol/test_publication.py`, `tests/contract/comms/{fake_preston.py,test_contract.py}`
**Interfaces — Produces:**
```python
# resolver.py
CORE_TYPES = {"wiki","hub","claim","topic","hypothesis","experiment","result","version","lesson","journal","plan","evidence","xreq","episode","file","handoff","skill-proposal","doctrine-proposal","opponent"}
def resolve(canonical: Path, uri: str, rev: str|None=None) -> Resolved   # Resolved(path, content: bytes, rev); anchors #dN (journal decision), #L-… (lesson entry); archived claims under kb/archive/; game types via Game.reference_types
# stage.py (worker): stage(layout, kind, title, body, refs: list[Ref], **fields) -> str   # Ref(uri, rev="@result", role, note)
# publisher.py (runtime, under canonical.lock): recover(canonical) -> None; publish_staged(canonical, merge_sha) -> list[int]; publish_runtime(canonical, kind, title, body, refs, **fields) -> int
# reader.py: read_since(canonical, consumer, after=None) -> list[dict]; advance(canonical, consumer, seq)
# cli.py: "$AUTORESEARCH_CLI" comms stage|list|get|resolve|status|inbound
```
- [ ] **Step 1: Failing tests:**
  - resolver: every core type resolves at the current rev and at an older rev after the entity changed; journal decision anchor; lesson anchor; archived claim; unknown type → error; game-declared type via fakegame;
  - publication: two branches publish in reverse order → seqs strictly increasing, no gaps, a reader between publications sees the later message next; `@result` replaced and the referenced entity created in that branch resolves at the published rev;
  - crash injection at each step (after file write, after receipt, after staged removal, before commit, before push) → `recover` + rerun yields exactly one outbox file and receipt per message, contiguous seqs;
  - push failure → committed locally, pushed on next run;
  - contract suite (fake Preston over the CLI): two consumers with independent cursors; fake Preston reads, resolves every ref at its rev, answers a question, sends `seen`; acknowledgement note appears with `in_reply_to`; an inbound `task` is acknowledged when its card publishes; an unknown additive field in a message is ignored by the reader.
- [ ] **Step 2–5.**

### Task 3.6: Promotion state machine
**Files:** `src/autoresearch/runtime/promotion.py`, tests `tests/protocol/test_promotion.py`
**Interfaces — Produces:**
```python
def acquire(ctx, queue, experiment: str, version: str, candidate_ref: str) -> bool          # C8 rules
def record_intent(ctx, promotion) -> None; def record_submitted(ctx, promotion, submission_id) -> None   # trusted; commit under canonical.lock
def on_merge(ctx, promotion, merge_sha) -> None            # stage recorded (champion copied in the merged branch)
def on_published(ctx, promotion) -> None                    # stage published
def reconcile(ctx, queue, sm: Softmax) -> list[Action]      # C8: intent+terminal → submitted|aborted; submitted+terminal with no committed handoff for the holder's run → promotion-recovery card
def adopt(ctx, promotion_id: str) -> Promotion              # used only by the promotion-recovery card: re-binds holder_task/run_id to itself
def current_champion(ctx, sm, league_id) -> str|None
```
- [ ] **Step 1: Failing tests:** acquire refused while another promotion is in `intent|submitted|recorded`, even if the holder run completed and 20 min passed; acquire allowed after `published`, `aborted`, or abandonment in `acquired`; `record_intent` refuses when pushing the candidate branch or the ledger fails; **submit succeeded, worker died before finish (branch exists)** → reconcile → `submitted` → promotion-recovery card admitted → its finish and merge → `recorded` → `published`, exactly one submission in FakeSoftmax, and the next promotion then acquires; champion ≠ baseline → refusal + re-evaluation request; submit succeeded but `record_submitted` crashed → `reconcile` finds the submission in FakeSoftmax → `submitted` and no second submit; an old promotion branch arriving after another promotion published → blocked as reconcile (champion mismatch); `on_published` frees the lock. **Step 2–5.**

### Task 3.7: Inbound routing
**Files:** `schemas/inbound.json`, `src/autoresearch/comms/inbound.py`, `src/autoresearch/runtime/routing.py`, tests
**Interfaces — Produces:** `submit_inbound(canonical, kind, payload, author) -> str`; `route_all(ctx, queue) -> list[Action]` per C11 (exactly one responsible card; delivery state in `ledger/inbound/<I-id>.json`); `derived_status(canonical, message_id) -> StatusInfo(state, late)`.
- [ ] Tests: valid `task` → card request `task:<I>`; invalid `task` → published `note` with errors; `steer` now → P0 card responsible; same urgent message visible read-only in a second briefing; `proposed_topic` and two simultaneous planning sources → one planning card + backlog; orphan reply → backlog → next planning briefing; `seen` per consumer; late reply → `StatusInfo("answered", late=True)`; rerun after crash → no duplicates. Commit.

### Task 3.8: Signals, planning cadence, rebuild, restore
**Files:** `src/autoresearch/runtime/{signals,planning,rebuild}.py`, tests
**Interfaces — Produces:** `compute_signals(ctx, queue, now, cfg) -> list[Signal]` (System Design §8 with §10 defaults; escalation T=3 publishes a runtime `help_request` once); `planning_request(ctx, queue, now, every=2h, growth=False) -> CardSpec|None` (C11 single owner); `needs_rebuild(ctx, queue) -> bool` and `rebuild(ctx, queue, sm) -> list[CardSpec]` (C12).
- [ ] Tests: each signal on its fixture; planning not requested while a planning card is pending, claimed, or finished-unpublished; **restore test**: canonical with (a) a published request, (b) an admitted request with no handoff, (c) a request whose handoff is committed but unpublished, (d) an `awaiting-credits` experiment, (e) a promotion in `submitted` whose holder never finished, and (f) open Topics → `git clone` the bare remote into an empty home with an empty board → run queue-keeper until quiescent → (a) not re-admitted; (b) re-admitted once with an epoch-suffixed key; (c) adopted and published with no new card; (d) launch calls `xp_recover` first, no duplicate XP; (e) one promotion-recovery card, champion published, no resubmit; a second queue-keeper run adds nothing. Commit.

### Task 3.9: queue-keeper, xp-poll, health
**Files:** `src/autoresearch/runtime/{bootstrap,queue_keeper,xp_poll,health}.py`, tests `tests/protocol/test_queue_keeper.py`
`queue_keeper.main()` order (canonical writes under the lock): bootstrap (C1, no cards) → preflight → on failure: pause, publish help_request, return 0 → promotion reconcile → rebuild if needed → process handoffs (3.4) → publish (3.5) → KB apply and generated files (hook point for Plan 03 Task 1.2; until then index only) → push `main` and `refs/heads/card/*` → sweep skill staging into `skills-proposed/` → route inbound (3.7) → admit requests from merged commits with receipts (3.2) → planning request (3.8) → signals → orphan/blocked detection → daily proposal re-raise → resume if paused. `--bootstrap-only` stops after bootstrap. xp-poll: for `evaluating` experiments fetch status/artifacts via `Softmax`, write evidence records under the lock, request review cards (`review:<E>`); for `awaiting-credits`, request launch (`launch:<E>:<attempt>`) when credits > 0. health: blocked tasks → requeue once then repair request + help_request; disk; token expiry.
- [ ] Tests (FileQueue + FakeSoftmax, scripted timeline): identity failure on the first tick → no admission, pause recorded, help_request published; a full timeline (two finishes → merges → publications → new requests admitted → planning due → XP completes → review card) produces the expected canonical log, outbox, and ledger. Commit.

---

## Milestone 4 — Plugin

### Task 4.1: Policy and adapters
**Files:** `src/autoresearch/plugin/{policy,adapters}.py`, tests
**Interfaces — Produces:**
```python
# policy.py (stdlib)
def tool_policy(tool_name: str, args: dict, ctx: RunContext, card: dict|None) -> tuple[bool, str]
#   block: player switching (`coworld player use`, `softmax login`, writes under ~/.softmax), raw `coworld submit|upload-policy|xp-request create`, model file writes outside ctx.worker,
#   any model write after card["finished"], `memory` tool writes when card["mode"] != "planning"; allow `"$AUTORESEARCH_CLI" …`
# adapters.py (stdlib) — Hermes signatures (website/docs/user-guide/features/hooks.md:546-573, 676-710)
def pre_tool_call(tool_name=None, args=None, task_id=None, **kw) -> dict|None   # {"action":"block","message":reason} or None
def post_tool_call(tool_name=None, args=None, status=None, error_type=None, error_message=None, duration_ms=None, **kw) -> None
def on_session_end(session_id=None, completed=None, failed=None, interrupted=None, turn_exit_reason=None, **kw) -> None
def pre_llm_call(is_first_turn=False, **kw) -> str|None                         # briefing from runtime/briefings/<task>.md on the first turn of a card worker
def kanban_task_completed(task_id=None, run_id=None, **kw) -> None              # observer: append runtime/kanban_events.jsonl
def register(ctx) -> None
```
Card metadata comes from `runtime/cards/<task-id>.json` written at admission (no subprocess per tool call).
- [ ] Unit tests: policy matrix; directive dict shape; finished card blocks writes; memory allowed only on planning cards; adapters accept unknown kwargs; `register` registers five hooks on a fake ctx. Integration (marker `hermes`): a worker told to run `coworld player use X` gets the block message; the first model turn contains the briefing marker. Commit.

---

## Milestone 5 — Protocol slice on real Hermes, and the serial fallback

### Task 5.1: Protocol slice (marker `hermes`)
**Files:** `tests/integration/test_protocol_slice.py`, `tests/integration/slice_cards/*.md`
- [ ] **Step 1:** Install (Task 1.3 fixture) with `AUTORESEARCH_GAME=fakegame`, `AUTORESEARCH_SOFTMAX=fake`, a bare remote, `OPENROUTER_MODEL=z-ai/glm-5.3-flash` (the test **fails**, not skips, when the OpenRouter key is missing during the go/no-go run).
- [ ] **Step 2:** Write three card requests into canonical (committed) and run queue-keeper: A and B (investigation) both reference hypothesis H-x and are admitted together so both branch from the same base; their bodies instruct the worker exactly (edit H-x's body line N, create one claim, stage one highlight, write one card request, run `"$AUTORESEARCH_CLI" card finish --outcome done`, call `kanban_complete`). C is a promotion card body that runs `"$AUTORESEARCH_CLI" softmax submit E-fake` (Plan 03 Task 3.4; with `AUTORESEARCH_SOFTMAX=fake` the eligibility check reads a pre-seeded `reviewed` experiment whose stored RuleOutcome is eligible) against that experiment.
- [ ] **Step 3:** Resume cron jobs; run the gateway; wait up to 20 min for all handoffs to reach a final state.
- [ ] **Step 4: Assert:** one of A/B merges, the other produces a reconciliation request (same entity); outbox seqs contiguous; requested cards admitted with receipts; remote has `main` and `card/*`; plugin events exist; C reaches promotion `published` and FakeSoftmax shows one submission.
- [ ] **Step 5: Fault injection:** `AUTORESEARCH_FAKE_FAULTS=crash_after=merge` makes queue-keeper exit between merge and publish once; next run publishes exactly once. Set a wrong player id in `.env` → next queue-keeper run pauses (pending tasks unassigned) and the running worker's submit is refused by the softmax tool.
- [ ] **Step 6:** Record wall time and token cost in `docs/plans/findings/protocol-slice.md`; commit. **This test is the Kanban go/no-go evidence.**

### Task 5.2: Serial fallback (build only if the go/no-go selects it)
**Files:** `src/autoresearch/runtime/serial.py`, `scripts/serial_pre.py`, `cron/jobs.serial.json`, tests
- Serial pre-run script (agent cron job `research-serial`, every 5 min): if no current card, claim the top assigned pending card from `FileQueue`, create its worktree at `<canonical>/.worktrees/serial` on branch `card/<Q-id>`, write `runtime/serial_current.json` `{task_id, run_id, worker, started_at}` (consumed by `RunContext.from_env`), print the briefing as the job context; else print `{"wakeAgent": false}`. The job's `workdir` is set once by bootstrap to `<canonical>/.worktrees/serial` (verify the agent's cwd in the test).
- **Slot release** is one idempotent function `release_slot(ctx)` with recorded steps in `serial_current.json` (`steps_done: [...]`): (1) push the card branch — on failure stop and keep everything; (2) `git worktree remove --force .worktrees/serial` (the branch is retained, already pushed); (3) delete `serial_current.json`. `card finish` in serial mode writes the handoff, calls `FileQueue.finish`, then calls `release_slot`.
- **Recovery needs no liveness check.** Hermes never overlaps runs of one cron job and runs the pre-run script inside the new run (`cron/scheduler.py:2293-2308, 4337-4364`), so whenever the serial pre-run script finds `serial_current.json`, the worker that wrote it has ended. The pre-run script, before claiming:
  - marker + handoff exists for its run → `FileQueue.finish(task_id, run_id, handoff.outcome)` (idempotent: an already-finished run is accepted unchanged), then `release_slot` (resumes at the first unfinished step; creates no new result and no repair);
  - marker + no handoff → commit whatever is in the worktree to its branch (allow empty), write a repair request (`ar://handoff/<task>/<run>` with `state: missing`), mark the FileQueue card `failed`, then `release_slot`;
  - if `release_slot` stops on a failed push → print `{"wakeAgent": false}`, keep the worktree and marker, and publish a runtime `help_request` after 3 consecutive failures; never remove unpushed work.
- `queue-keeper` uses `FileQueue` (selected by `AUTORESEARCH_QUEUE=file`) and is otherwise unchanged.
- [ ] Tests: Milestone 3 protocol tests run against `FileQueue`; on the real `research-serial` cron job: **two successive** fake-game cards complete; fault (a) kill the agent before `card finish` → next tick produces a repair request and the next card runs; fault (b1) interrupt right after the canonical handoff/receipt commit, before `FileQueue.finish` → next tick makes the run terminal, the original handoff publishes exactly once, and the next card runs; fault (b2) interrupt after `FileQueue.finish` but before marker deletion → next tick finishes the release with no repair and no second result, and the next card runs; fault (c) remote unreachable during release → worktree and marker kept, no agent wake, then remote restored → release completes and the next card runs. Commit.
