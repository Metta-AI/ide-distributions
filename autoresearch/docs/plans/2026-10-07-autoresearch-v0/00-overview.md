# Autoresearch v0 — Implementation Overview

> **For agentic workers:** This file is not executed task by task. It fixes the cross-cutting contracts that the three executable plans (`01-…`, `02-…`, `03-…`) rely on. Read it before any plan. Each executable plan uses superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Build the autoresearch Hermes profile distribution so that it runs a complete research cycle (investigate → design experiment → build → XP → review → promote/submit) on a local Hermes install, ready to hand to Rohit for hosting.

**Specs:** `autoresearch/docs/designs/autoresearch-system-design-2026-10-06.html` (approved; research behaviour), `autoresearch/docs/designs/autoresearch-runtime-2026-10-06.html` (rev 7; runtime), `autoresearch/docs/designs/autoresearch-open-questions-2026-10-07.html` (open questions; this plan uses their suggested defaults as configuration).

**Plans:**
1. `01-foundation-and-vertical-slice.md` — install/bootstrap, records, queue, runtime protocols (admission, handoff, merge, publication, promotion, pause, rebuild), plugin, and a protocol slice on the real Kanban dispatcher with recovery tests.
2. `02-gota-and-compiler.md` — GotA extension, toolkit, seed, shared gates, and the semantic↔policy compiler port with the G layer and full goal tracing (the only build path).
3. `03-research-completion-and-hardening.md` — KB maintenance, statistics and calibration, the full softmax tool, doctrine and skills, operational configuration, a full fake-game research cycle, the real GotA cycle, and the acceptance checklist.

Plan 01 is the critical path. Plan 02 can start in parallel after Plan 01 Milestone 0. Plan 03 Milestones 1–4 start after Plan 01 Milestone 1; Milestone 6 needs Plans 01 and 02. See "Execution order".

## Tech stack and versions

Python 3.12 (Hermes requires ≥3.11,<3.15); uv (required on the host); pytest, ruff; jsonschema, python-frontmatter, PyYAML, python-ulid; git CLI via subprocess. Hermes Agent v0.21.5 (tag `v2026.9.24`) installed with `uv tool install git+https://github.com/NousResearch/hermes-agent@v2026.9.24` (PyPI lags at 0.19.0). coworld 0.1.57, softmax-cli 0.26.38 (latest on PyPI on 2026-10-07; the globally installed uv tool coworld is stale at 0.1.38, so always use the project-pinned version). Live GotA coworld version on 2026-10-07: 2026.10.6.1. Models (open-weight, via OpenRouter, chosen 2026-10-07): primary `moonshotai/kimi-k3`, fallback `z-ai/glm-5.3`, tests and protocol slice `z-ai/glm-5.3-flash`. Nim 2.2.10 for GotA replay tools. Source pins: optimizer-seed `7ebea87`, co-gas `6742feb`, GotA lab `personal_labs_gota@07dc6f94`, Paintbot PW `personal_labs_paintbot_pw@6b0bffa5` (re-pin to a clean commit; the checkout has uncommitted edits).

## Global constraints (every task inherits these)

- Hermes pinned to v0.21.5. Facts about Hermes cite the source clone `hermes-agent@9ab4a4f`; Plan 01 Task 0.2 re-verifies them on the installed release.
- Core code never names a game. Game material lives only in `games/<game>/` and `skills/<game>/`.
- Entity IDs: type prefix + `-` + ULID (`C-01K…`). Card IDs are the queue backend's task IDs (Kanban task id, or `K-<ULID>` for the file queue).
- The work queue holds only next-action cards (Pending / Claimed / Completed). Never research state.
- Message schema `ar-comms/0`; bodies written for the Preston-role agent; inbound messages carry Preston's full authority.
- Concurrency cap `kanban.max_in_progress` (default 2, configurable). No cap on concurrent evaluations.
- No hard wall-clock limit per iteration.
- `AUTORESEARCH_WORKSPACE_REMOTE` required; the runtime pushes after every canonical commit.
- No Nous-specific IDs, domains, or auth anywhere. Workspace path configurable.
- No systemd fallback driver; no Codex CLI dependency.
- Docs and doctrine in plain Simplified-Technical-English style; no gendered pronouns.

## Cross-cutting contracts (decided here; plans implement them)

### C1. Install, import, commands, bootstrap
- `distribution.yaml` **omits** `distribution_owned`, so Hermes ships the whole repository except user-owned paths (`hermes_cli/profile_distribution.py:343-352`). `config.yaml` is preserved on update; settings that must change on update are applied by bootstrap (C9).
- **Cron scripts** (`scripts/*.py`) are stdlib-only launchers. They sync `$HERMES_HOME/local/venv` with `uv.lock` **only when the lock hash differs from `local/venv/.lock-sha`**, under the file lock `local/venv.lock` so concurrent launchers never sync at the same time; then `os.execv` the venv Python with `-m autoresearch.runtime.<job>`. Missing `uv` → actionable error, exit 1.
- **Worker command:** bootstrap writes the shim `$HERMES_HOME/bin/autoresearch` (`exec <venv>/bin/python -m autoresearch "$@"`) and sets `AUTORESEARCH_CLI` to its absolute path in the profile `.env`. Skills and `AGENTS.md` always call `"$AUTORESEARCH_CLI"`. A fresh-worker-shell test proves it (Plan 01 Task 1.3).
- **The plugin** runs in the Hermes process and imports only stdlib-only modules: `autoresearch.plugin.{adapters,policy}` and `autoresearch.runctx`. A test imports exactly the plugin's entry module (`plugins/autoresearch/__init__.py`) with `python -S -I` to prove the whole import graph is stdlib-only. Other modules (including `ids`, which uses python-ulid) are not imported by the plugin.
- `plugins.enabled` includes `autoresearch`; shipped in `config.yaml` and re-applied by bootstrap with `hermes config set`.
- **Bootstrap** (first step of `queue-keeper`, idempotent) does only local setup: venv check → config migrations (C9) → CLI shim → canonical workspace init (seed, `AGENTS.md`, git, remote, `.supervisor/ledger/`) → Kanban board init (`hermes kanban --board <slug> init`; board slug = profile name) and a new **board epoch** id in `.supervisor/ledger/board.json` whenever the board is created. **Bootstrap never creates cards.** Card creation happens only in the admission step after preflight passes (C3, C6).

### C2. Canonical and worker workspaces; durable vs disposable state
- `RunContext` (`autoresearch.runctx`, stdlib) resolves `canonical` (`AUTORESEARCH_WORKSPACE` or `$HERMES_HOME/workspace`), `worker` (`HERMES_KANBAN_WORKSPACE`, or the serial fallback's `runtime/serial_current.json`), `card_id`, `run_id`.
- Model-directed writes (file tools, terminal) go only to the worker's worktree; the plugin's write policy enforces this. **Trusted code paths** (`card finish`, the softmax tool, plugin bookkeeping, runtime scripts) write canonical bookkeeping through our Python code, not through model file tools; the policy allows the `"$AUTORESEARCH_CLI" …` commands that perform them.
- **Durable state is committed** in canonical under `.supervisor/ledger/` (tracked, pushed): handoff records, request receipts, publication receipts, inbound delivery states, submission intents and outcomes, promotion state. **Disposable state** lives in `.supervisor/runtime/` (gitignored): locks, briefings, card metadata cache, tool events, cursors of local consumers, FakeSoftmax state.
- **Card branches are pushed:** `queue-keeper` pushes `refs/heads/card/*` each run, so unmerged results survive a host loss.
- All canonical mutations run under `canonical/.supervisor/runtime/canonical.lock`. Network fetches run outside the lock; results are written under it.

### C3. Admission, card lifecycle, handoff
- **Card requests:** workers and planners write `cards/requests/<Q-id>.json` in their worktree (`op: create|reprioritize|drop`). A request takes effect only after its branch merges. Its receipt `.supervisor/ledger/requests/<Q-id>.json` is the request's single durable state machine: `rejected` | `admitted{tasks: [{task_id, board_epoch}]}` → `handed_off{task_id, run_id}` (set when its handoff is committed) → `published{merge_sha}` | `superseded{by}` | `dropped`. Every other component derives request status from this receipt.
- **Admission transaction** (`queue-keeper`, after preflight passed): (1) `admit()` checks refs resolve at canonical HEAD and mode rules (C10, C11); (2) `hermes kanban --board <slug> create <title> --body <json> --branch card/<Q-id> --workspace worktree:<canonical>/.worktrees/<Q-id> --priority <int> --idempotency-key req:<Q-id> --json` **without `--assignee`** (an unassigned task is not dispatched; `kanban.default_assignee` is left unset); (3) record the returned task id in the receipt and write `runtime/cards/<task-id>.json` (mode, request id, refs) and `runtime/briefings/<task-id>.md`; (4) `hermes kanban --board <slug> assign <task-id> <profile>`. A crash between steps is repaired on the next run from the receipt (idempotency key returns the same task).
- The worker's base commit is wherever Hermes branches the worktree at claim time (canonical HEAD then). `card finish` records `base_sha = git merge-base card/<Q-id> <canonical HEAD>`.
- **Worker finish:** `"$AUTORESEARCH_CLI" card finish --outcome <o>` validates the committed content (it commits first), writes `.supervisor/ledger/handoffs/<task-id>/<run-id>.json` `{card, run_id, base_sha, result_sha, outcome, journal, responsible_inbound[], state: "committed"}` (trusted path), marks `runtime/cards/<task-id>.json` `finished=true` so the plugin blocks further model writes, then the worker calls the native `kanban_complete`. The completion hook is an observer only (`hermes_cli/kanban_db.py:2899-2906`).
- **Runtime processing** (poll): a handoff is processed only when its run is terminal: the Kanban run `run_id` has a terminal outcome, **or** its task belongs to an earlier board epoch (the board that held it is gone; the committed handoff is then adopted as is). Steps, each idempotent and recorded in the handoff file: if `contains(canonical, result_sha)` → `merged` (no revalidation) → else validate **at `result_sha`** using `git diff base_sha..result_sha` and `git show result_sha:<path>` (never a live directory) → `validated` (receipt stores the report) → C4 overlap check → merge → `merged` → publish (C5) → `published`. Invalid → `repair`; overlap/conflict → `reconcile`.
- **Repair and reconciliation cards** reference the original card's entity refs plus a runtime reference `ar://handoff/<task-id>/<run-id>` (resolvable to the record and its branch). When the repair/reconcile card's own handoff publishes, the original handoff state becomes `superseded` with a pointer; it is never processed again.
- A task that is `done` with no handoff record for its last run → repair card (refs as above; branch if it exists).

### C4. Conflicts and shared files
- Existing-entity write set W (`git diff base..result` mapped to entity ids, excluding entities created in the branch; deletions count) vs canonical's changed set M since `base`. W∩M ≠ ∅ → reconciliation, even when git would merge cleanly. Textual conflicts elsewhere → reconciliation.
- No shared append-only files: one file per record (`versions/<V-id>/version.json` plus `versions/<V-id>/events/<ULID>.json`, `evidence/index/<sha>.json`, `kb/log/<ULID>.md`, `comms/outbox/<seq>-<M-id>.json`). Aggregate views are generated by the runtime only.

### C5. Outbox publication
- Workers stage messages at `comms/staged/<M-id>.json` with ref `rev: "@result"`.
- Publisher (runtime, under the canonical lock): `next_seq = 1 + max(seq of files in comms/outbox/)` (no separate counter; derived from immutable committed files). For each staged message not already in `.supervisor/ledger/published/<M-id>.json`: write `comms/outbox/<seq:08d>-<M-id>.json` with `"@result"` replaced by the merge SHA, write the publication receipt, remove the staged file, then one commit, then push. Recovery: on start, any outbox file without a receipt or any receipt without a file is completed from the other before new publication; a staged file with a receipt is just removed. Thus each message gets exactly one sequence number across crashes.

### C6. Identity at every boundary
- Preflight (each `queue-keeper` run, before admission): `whoami` with the credential = `AUTORESEARCH_PLAYER_ID`, `subject_type=player`, token valid and not expiring within 2 h (warning) or expired (fail), disk ≥ 1 GB. Failure → pause (C7), runtime `help_request`, no admission.
- The softmax tool checks `whoami` **with the exact credential it will use, immediately before every mutating call** (upload, XP create, submit). No identity cache.
- The plugin blocks player-switch commands and any other credential path.

### C7. Pause and resume
- **Primary mechanism (board-scoped, under our control):** pause = unassign every pending (ready) task on our board (`hermes kanban --board <slug> assign <id> none`), recording each prior assignee in `.supervisor/ledger/pause.json`; stop admission. Resume = reassign exactly those tasks. Unassigned tasks are not dispatched. Running workers are stopped from harm by C6 boundary checks.
- `kanban.dispatch_profiles` is not used (it is home-scoped, not board-scoped, and removing it widens scope).
- Plan 01 Task 0.2 verifies that unassigned ready tasks are never claimed and that `assign … none` works on a ready task; if not, the release gate fails and James decides.

### C8. Promotion
- Promotion state is durable in `.supervisor/ledger/promotion.json` `{id, holder_task, run_id, board_epoch, experiment, version, platform_policy_version_id, candidate_ref, candidate_sha, stage: acquired|intent|submitted|recorded|published|aborted, updated_at}`.
- **Acquire** only when no promotion exists, or the existing one is `published` or `aborted`, or it was abandoned **in stage `acquired`** (holder run terminal or from an earlier board epoch, and no submit intent). A promotion in `intent`, `submitted`, or `recorded` is never taken over by a new promotion; only the runtime or a recovery card for the same promotion id advances it.
- **Before submit** (softmax tool, trusted): confirm the champion (latest `published` promotion, confirmed against the Softmax leaderboard for our player) equals the experiment's baseline, else refuse and request a re-evaluation card. Push the candidate branch (`card/<Q-id>`, which contains the candidate STRATEGY at `candidate_sha`), write stage `intent`, commit, and **push**; if either push fails, refuse to submit. Then submit; then stage `submitted` with the submission id (commit and push). The submit tool refuses any submit for a promotion already at `submitted` or later.
- **Runtime:** when the promotion branch merges, it copies the candidate STRATEGY to the champion path inside that merge (the worker staged it), stage `recorded`; after publication and push, stage `published`.
- **Reconcile** (runtime, each run; a holder counts as terminal when its run is terminal or from an earlier board epoch): `intent` + terminal holder → query Softmax submissions for `platform_policy_version_id`; found → `submitted`; not found → `aborted`. `submitted` + terminal holder **with no committed handoff for the holder's run** (whether or not the branch still exists) → admit one **promotion-recovery card**; when such a handoff exists, normal handoff processing drives `recorded` → `published` and no recovery card is created (a recovery card is created later only if that handoff ends in `repair`). The recovery card (P0, key `promote-recover:<id>`) adopts this promotion id, stages the champion from `candidate_sha` (fetched from the pushed branch), and finishes; its merge drives `recorded` → `published` without resubmitting. No new promotion while one is in `intent|submitted|recorded`.

### C9. Configuration ownership
| Setting | Policy | Where |
|---|---|---|
| `plugins.enabled: [autoresearch]` | always | config.yaml + bootstrap `hermes config set` |
| `kanban.max_in_progress` (2) | missing only (installer may tune) | config.yaml + bootstrap |
| `kanban.auto_decompose false`, `review_dispatch false`, `auto_subscribe_on_create false`; `default_assignee` unset | always | config.yaml + bootstrap |
| `memory.memory_enabled true`, `user_profile_enabled false`, `write_approval false`; `auxiliary.background_review.enabled false` | always | config.yaml + bootstrap |
| `tool_loop_guardrails.hard_stop_enabled true`; `agent.max_turns 150`; `agent.run_budget_seconds 2700` (soft) | missing only | config.yaml + bootstrap |
| `approvals.mode off`; `approvals.deny` globs | always | config.yaml + bootstrap |
| `skills.create_dir` = `$HERMES_HOME/local/skills-staging` (outside every workspace; `queue-keeper` sweeps new/changed skills into canonical `skills-proposed/` and publishes a suggestion) ; curator excludes shipped skills | always | bootstrap |
| `HERMES_WRITE_SAFE_ROOT` = canonical (covers `.worktrees/`) | always | profile `.env` via bootstrap |
| `terminal.env_passthrough` = `SOFTMAX_PLAYER_CREDENTIAL, AUTORESEARCH_PLAYER_ID, AUTORESEARCH_WORKSPACE, AUTORESEARCH_WORKSPACE_REMOTE, AUTORESEARCH_GAME, AUTORESEARCH_SOFTMAX, AUTORESEARCH_CLI` (exact names; no wildcards: `tools/env_passthrough.py:111-133`) | always | config.yaml + bootstrap |
| Auto-update off | always | bootstrap |
| `OPENROUTER_MODEL` (default `moonshotai/kimi-k3`) and fallback chain (`z-ai/glm-5.3`) | distribution default; installer may override | config.yaml model + `fallback_providers`; `.env` override |
| League id (GotA: `league_3c60897b-25cf-4b37-9d1a-8554c1198f28`), outcome and performance metrics, `min_episodes_floor` (40/side) | game contract; installer override | `games/<game>/GAME.md`; env override |

### C10. Calibration before experiments
- Admission of an experimentation card requires a noise record for the **current game version** (`stats/metric-noise.yaml` with matching `game_version`). When none exists, `queue-keeper` admits a P1 calibration card (`mode: maintenance`, idempotency key `calibrate:<game_version>`).
- Each experiment freezes its evaluation design at preregistration (metrics with directions and roles, δ, N, noise record revision). Later noise updates never change an existing experiment's thresholds.

### C11. Planning ownership and inbound delivery
- **Single planning owner:** at most one planning card may be pending, claimed, or finished-but-unpublished. Admission merges any additional planning request (scheduled, `proposed_topic`, inbound `task` with mode planning, worker request) into the planning backlog instead of creating a second card.
- Inbound kinds `reply`, `steer` (optional `proposed_topic`), `task`, `seen`. Each inbound has exactly one **responsible** card: `steer` urgency `now` → a new P0 card; `task` → its own card; `proposed_topic` → backlog for the planning card; `reply`/`steer` on a thread with an open card → that card; otherwise → planning backlog. Other concurrent briefings may show it read-only, marked "handled by <task>". Only the responsible card must acknowledge it; a `task` is acknowledged by the runtime when its card publishes. Delivery state per inbound in `.supervisor/ledger/inbound/<I-id>.json`: `received → routed(responsible) → acknowledged`. A late reply is delivered and flagged `late`.

### C12. Rebuild after board loss
- Detection (each `queue-keeper` run after preflight): the board has no tasks with our idempotency keys while the ledger lists admitted requests not yet published, or the board is missing → rebuild.
- Rebuild starts a new board epoch and gives **each request exactly one recovery action** from its receipt: `admitted` with no handoff → re-admit (new task id appended to `tasks` with the new epoch; key `req:<Q-id>:<epoch>`); `handed_off` → no new card, the committed handoff is adopted by normal processing (earlier-epoch rule in C3), and its repair/reconcile, if any, is the only follow-up; `published|superseded|dropped|rejected` → nothing. Then experiment lifecycle (`designed`→build, `built`→upload, `uploaded` with a candidate binding→launch, `awaiting-credits`→launch when credits > 0, `evaluating` with completed XP→review, `reviewed`+promote with no promotion → promote), promotion reconcile (C8), calibration (C10), and one planning card (C11). Lifecycle cards use the same rule: no card when an admitted-unpublished request already covers that experiment step. XP launches use `experiment.lifecycle.launch_attempts` in their keys and call `xp recover` before any create, so a rebuilt launch never duplicates a request.

## Contract amendments from Plan 01 Task 0.2 (APPROVED by James, 2026-10-07)

Source: `../findings/hermes-facts.md` (Hermes v0.21.5 verified on 2026-10-07). Each amendment overrides the contract text above where they differ.

- **A1 (install).** `uv tool install git+…@v2026.9.24` fails (Hermes `setup.py` refuses wheel builds). Install: clone `NousResearch/hermes-agent` at tag `v2026.9.24` (commit `f97608f`, "release v0.21.5") and run `uv tool install --python 3.12 -e .` in the clone; or the official `install.sh` on a host. Hermes source citations move from `9ab4a4f` to `f97608f`.
- **A2 (C1, C12 board).** Bootstrap creates the board with `hermes kanban boards create <slug>` (idempotent), not `kanban --board <slug> init` (fails on a new slug). Board DBs are host-wide at `<root>/kanban/boards/<slug>/kanban.db`; "board missing" (C12) means that file or its `boards list` entry is gone.
- **A3 (C7, C9 dispatcher settings).** One gateway per host runs the dispatcher with the **host default profile's** `kanban.*` config, across all boards. So: (1) our profile's `kanban.*` rows stay (they govern `hermes -p <profile> kanban dispatch` and profile tools) but do not govern the gateway; (2) `queue-keeper` preflight reads the host root config (read-only) and fails (pause + `help_request`) when `kanban.default_assignee` is set, when `kanban.dispatch_profiles` excludes our profile, or when `kanban.dispatch_in_gateway` is false and no other dispatcher runs; (3) `kanban.max_in_progress` is a **host** cap across all boards, set by the installer in the host root config (bootstrap never writes the root config); (4) C7 holds "provided the host dispatcher's `kanban.default_assignee` is empty" (checked by preflight).
- **A4 (C9 env).** Under the gateway a cron script receives only the `terminal.env_passthrough` names from the profile `.env`; every variable a launcher or runtime job reads must be on that list. Worker terminals see the whole profile `.env` minus provider keys, so the plugin (not env filtering) keeps credentials away from model commands. Cron script cwd is `$HERMES_HOME/scripts`; launchers must not depend on cwd.
- **A5 (models, preflight).** kimi-k3 tool calling inside Hermes is unverified (broker OpenRouter key out of credit; a direct API call to kimi-k3 made a correct tool call). Hermes sends no `max_tokens`, so OpenRouter reserves 131072 output tokens per kimi-k3 call (≈ $1.97 headroom per call), and a 402 becomes `rate_limited` and is retried every ~6 min forever. Preflight also checks OpenRouter key credit (`GET /api/v1/key` → `limit_remaining`) and pauses when it is below a threshold (default $10). Re-run the kimi-k3 card after the key is topped up, before Plan 01 M5.
- **A6 (C1 bin/).** `$HERMES_HOME/bin/` also holds Hermes's `tirith` binary; bootstrap writes only `bin/autoresearch` and never cleans `bin/`.
- **A7 (C1 plugin, C2 RunContext).** In the gateway process `os.environ["HERMES_HOME"]` is the host root. The plugin and `RunContext` take the profile home from `hermes_constants.get_hermes_home()` when importable (inside Hermes), else `HERMES_HOME`. Worker env names: `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_BRANCH`, `HERMES_KANBAN_BOARD`.
- **A8 (C3 runs).** Kanban run ids are integers per board DB (not globally unique); `handoffs/<task-id>/<run-id>.json` stays unique. A run is terminal when `ended_at` is set; `rate_limited` and `reclaimed` runs end while the task returns to `ready`.
- **A9 (C8 first champion; controller ruling, for James to confirm).** Before any promotion is `published`, the champion is the Softmax leaderboard champion for our player (read literally, "latest published promotion" would make the first promotion impossible). After the first publication, the ledger's latest `published` promotion must agree with the leaderboard; disagreement means "no champion" and submit refuses.
- **A10 (credits; finding from Plan 03 Task 3.1).** A Player credential cannot read credits (HTTP 403; no CLI command). Preflight records credits as unavailable (warning only), and `awaiting-credits` experiments are relaunched on exponential backoff (`xp recover` first; idempotency keys prevent duplicates) instead of "when credits > 0".
- **A11 (C8 liveness; from Plan 01 Task 3.6 review).** While `intent`, the holder's handoff waits (never merges). While `submitted`, the holder role belongs to the holder **task** (a retried run after `rate_limited`/`reclaimed` is the holder). Recovery is needed when the promotion is `submitted`, no holder-task run is live, and it is not `recorded` — including when the holder's handoff ended in repair, reconcile, superseded, or published-before-recorded. Recovery keys are `promote-recover:<P-id>:<attempt>`, at most one live attempt, at most 3 attempts, then a runtime `help_request`. A rejected holder handoff gets no generic repair card; the recovery card is the only follow-up. On `published`/`aborted`, requests and handoffs the promotion covered are closed (`superseded{by: P-id}`). CLI: `"$AUTORESEARCH_CLI" promotion adopt|stage-champion|status`.
- **Commands for Plan 01 Task 2.2:** see the "Commands for Task 2.2" section of the findings (`complete --force` only for a running task with a live worker; `block <id> <reason words>`; `unblock <id>`; `reclaim <id>`; `edit <id> --priority N`).

## Execution order (dependency graph)

```
Plan01 M0 (probe, Hermes facts) ─┬─> Plan01 M1 (package, install, ids, records, game loader) ─> Plan01 M2 (queue) ─> Plan01 M3 (runtime protocols) ─> Plan01 M4 (plugin) ─> Plan01 M5 (protocol slice on real Hermes)
                                 │                                    │
                                 │                                    └─> Plan03 M1 (KB) , Plan03 M2 (stats) , Plan03 M3 (softmax tool) , Plan03 M4 (doctrine, skills)
                                 └─> Plan02 M0–M1 (globals budget, contract, toolkit, gates) ─> Plan02 M2 (compiler)
Plan03 M3 Task 3.4 (softmax CLI incl. guarded submit) ─> Plan01 M5
Plan01 M5 + Plan03 M1–M4 ─> Plan03 M5 (config hardening) ─> Plan03 M6 (fake-game full cycle, then real GotA cycle, acceptance)
```
Plan 01 Milestone 5 proves the protocols (finish → merge → publish, reconciliation, promotion lock, pause, recovery) with scripted card bodies; the full research cycle is proven in Plan 03 Milestone 6 once stats, the softmax tool, and skills exist.

## Dates and decisions (James, 2026-10-07)
- **Goal: end of day Wed Oct 7.** Kanban go/no-go and the compiler target are both end of day today.
- **Kanban:** if Plan 01 Task 5.1 has not passed by then, the serial fallback (Plan 01 Task 5.2) is the v0 path.
- **Compiler:** there is **no BASIC fallback** (dropped by James). The semantic↔policy compiler, with full goal tracing, is the only build path; if it slips past the goal, the work continues on the compiler rather than switching paths.
- **Models:** open-weight only (James). Primary `moonshotai/kimi-k3` (top open-weight model on the BenchLM agentic leaderboard, 2026-10-07: #8, 69.7; Terminal-Bench 2.1 88.3), fallback `z-ai/glm-5.3` (#12, 67.5; MIT), tests `z-ai/glm-5.3-flash`. Plan 01 Task 0.2 verifies tool calling with each inside Hermes.
- **League:** `league_3c60897b-25cf-4b37-9d1a-8554c1198f28` ("Gods of the Arena"). Its score is "Emmett's Glory": winning heroes' lifetime XP per simulated minute; losses, draws, and timeouts score zero.
- **Player-only credential:** verified PASS on 2026-10-07 (`../findings/player-xp-probe.md`); C6 stands unchanged.
