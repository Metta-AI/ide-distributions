# Finding: Hermes v0.21.5 facts (Plan 01 Task 0.2)

**Result: 9 PASS, 1 FAIL (item 5: config location), 1 PARTIAL (item 11: kimi-k3 UNVERIFIED), plus Step 1 FAIL-with-workaround and one C1 command FAIL.** 2026-10-07, run by Claude for James.

## Setup
- Hermes: `hermes --version` → `Hermes Agent v0.21.5 (2026.9.24)`, install method `git`, Python 3.12.11. Source: clone of tag `v2026.9.24` at commit `f97608f` in `~/coding/tools/hermes-agent-v2026.9.24` (not edited). All citations below are `file:line` at `f97608f`. The overview cites `hermes-agent@9ab4a4f`; line numbers moved (for example `_owned_entries` is now `hermes_cli/profile_distribution.py:354`, not 343).
- Every command ran with `HERMES_HOME=/tmp/hh`. `~/.hermes` and `~/.softmax` were not touched.
- Throwaway distribution `arprobe` in the session scratchpad: `distribution.yaml` (no `distribution_owned`), `config.yaml`, `scripts/probe_env.py`, `plugins/autoresearch/{plugin.yaml,__init__.py}` (hook stubs that append JSON lines to `/tmp/hh-probe/plugin.jsonl`; `pre_tool_call` blocks terminal commands that contain `BLOCKME`; `pre_llm_call` returns `{"context": "...magic word is PERIWINKLE-42."}` when `is_first_turn`), `src/autoresearch/__init__.py`, `uv.lock`, `cron/jobs.json` (one no-agent job shipped as `enabled: true, state: scheduled`).
- Canonical test repo: a scratch git repo with a committed `AGENTS.md` that says "the AGENTS sentinel is TANGERINE-7".
- Model calls used the token-broker scope `openrouter.inference` only. The key was written to the probe profile `.env` from inside the exec'd child and removed after each run. A scan of `/tmp/hh`, `/tmp/hh-probe`, and the scratchpad for `sk-or-v1-` found nothing.
- Fake `SOFTMAX_PLAYER_CREDENTIAL=fake-cred-123` in the profile `.env`. No Softmax credentials were used.

## Step 1: install — FAIL (workaround in place)
- `uv tool install git+https://github.com/NousResearch/hermes-agent@v2026.9.24` fails: the Hermes `setup.py` refuses to build wheels.
- Workaround (done before this task): clone tag `v2026.9.24` (commit `f97608f`) to `~/coding/tools/hermes-agent-v2026.9.24` and install it as an editable uv tool from the clone. `uv tool list` shows `hermes-agent v0.21.5`. `hermes --version` prints v0.21.5.
- Amendment A1 below.

## 1. Profile install without `distribution_owned` — PASS
Command: `hermes profile install <scratch>/dist -y` → `✓ Installed 'arprobe' v0.0.1`, "This distribution ships cron jobs. They will NOT run automatically".

Evidence (`/tmp/hh/profiles/arprobe`): `scripts/probe_env.py`, `plugins/autoresearch/{__init__.py,plugin.yaml}`, `src/autoresearch`, `uv.lock`, `config.yaml`, `distribution.yaml` are present. `ls bin` → `No such file or directory`. Shipped cron record arrived as:
```json
{"id": "arprobe-env", "enabled": false, "state": "paused",
 "paused_reason": "Installed from a profile distribution; review it, then resume.",
 "next_run_at": null, "no_agent": true, "script": "probe_env.py", ...}
```
Source: no allowlist → whole payload minus `USER_OWNED_EXCLUDE` (`hermes_cli/profile_distribution.py:354-367`); `USER_OWNED_EXCLUDE` contains `bin`, `local`, `workspace`, `.worktrees`, `.env`, `auth.json` (`:37-55`); cron merge seeds `enabled: False, state: "paused"` (`cron/job_definition.py:48-66`, called from `profile_distribution.py:406`).

Note: `bin/` does not stay empty until our bootstrap. On the first agent run Hermes downloads its security scanner to `$HERMES_HOME/bin/tirith` (`tools/tirith_security.py:6,195`); after the worker runs, `bin/` held `tirith` (32 MB). Our shim `bin/autoresearch` can live beside it. `bin/` is user-owned, so updates do not remove either file.

## 2. Plugin loads in workers and the gateway; block; inject — PASS (gateway turn hooks UNVERIFIED)
- Enabled with `plugins.enabled: [autoresearch]` in the profile `config.yaml`. `hermes -p arprobe plugins list` → `autoresearch │ enabled │ 0.0.1 │ … │ user`.
- **Kanban workers:** each worker process logged `register` with `HERMES_KANBAN_TASK=<task id>`, then `on_session_start`, `pre_llm_call` with `is_first_turn: true, platform: cli`, then `pre_tool_call` for every tool (`kanban_show`, `terminal`, `write_file`, `kanban_complete`).
- **Block:** for `echo BLOCKME` the hook logged `blocked`; the model saw the block message and wrote `step2: blocked` (4 of 4 runs that reached the model).
- **Inject:** every worker wrote `magic: PERIWINKLE-42`. The word is not in the stored system prompt (checked in `state.db` `system_prompts`), so it came through the `pre_llm_call` injection into the user turn.
- **Gateway:** `hermes gateway run -v` logged `capability_check plugin=autoresearch …` and the plugin `register` hook fired in the gateway process, also with `plugins.enabled` removed from the root config. Plugin managers are per profile home (`hermes_cli/plugins.py:1566-1610`), so the gateway loads our profile's plugins in our profile's scope. In the gateway process `os.environ["HERMES_HOME"]` is the host root (`/tmp/hh`), not the profile: plugin code must resolve the home with `hermes_constants.get_hermes_home()`, not `os.environ`.
- UNVERIFIED: hooks firing in a gateway messaging turn for our profile (no messaging platform configured). Worker sessions spawned by the gateway dispatcher fire all hooks (item 5 gateway run).
- Docs: hook table and directives in `website/docs/developer-guide/plugins/index.md:1054-1082`.

## 3. Create with branch, worktree, priority, idempotency key — PASS
```
hermes kanban --board arprobe create "probe X" --body '{"q":"Q-X"}' --branch card/X \
  --workspace worktree:<canon>/.worktrees/X --priority 40 --idempotency-key req:Q-X --json
```
→ `{"id": "t_562ab168", "assignee": null, "status": "ready", "priority": 40, "workspace_kind": "worktree", "workspace_path": "<canon>/.worktrees/X", "branch_name": "card/X", ...}`. The same command with a different title returned the same object (`"id": "t_562ab168"`, title still "probe X").

After the claim: `git worktree list` → `<canon>/.worktrees/X ec1f1ff [card/X]` (branched from canonical `main` HEAD `ec1f1ff`); the worker's `git branch --show-current` printed `card/X`.

But the board must exist first: `hermes kanban --board arprobe init` on a new slug → `kanban: board 'arprobe' does not exist. Create it with hermes kanban boards create arprobe.` (exit 1). `hermes kanban boards create arprobe` works and is idempotent (second call: "Board 'arprobe' already exists.", exit 0); then `init` succeeds. The board DB is host-wide: `<root>/kanban/boards/<slug>/kanban.db`, where root = `HERMES_KANBAN_HOME` or the default Hermes root, never the profile dir (`hermes_cli/kanban_db.py:399-413`). Amendment A2.

## 4. Unassigned ready tasks are never claimed; assign / `assign … none` — PASS
- Three real ticks: `hermes kanban --board arprobe dispatch --json` ×3 → each `"spawned": [], "skipped_unassigned": ["t_562ab168"]`; task stays `ready`, `assignee: null`.
- `assign t_562ab168 arprobe` → `dispatch --dry-run --json` shows `spawned: [{task_id: t_562ab168, assignee: arprobe}]`.
- `assign t_562ab168 none` on the ready task → "Assigned t_562ab168 to (unassigned)", exit 0; next dry-run → `spawned: []`, `skipped_unassigned: [t_562ab168]`.
- Source: ready loop skips rows with no assignee unless `default_assignee` resolves (`hermes_cli/kanban_db_dispatch.py:2354-2366`); the gateway uses the same `dispatch_once` per board (`gateway/kanban_watchers_dispatcher.py:175-211`). `assign` raises while the task is `running` under a claim (`hermes_cli/kanban_db.py:1547-1560`).
- Caveat for C7: with `kanban.default_assignee: default` in the **host root** config, the gateway logged `kanban dispatcher: default_assignee='default' (unassigned ready tasks will route to this profile)`. Then an unassigned task is assigned and run. C7 pause holds only when the dispatcher's config leaves `default_assignee` empty. See item 5 and A3.

## 5. Priority order, `max_in_progress`, `auto_decompose`, `review_dispatch`, no `default_assignee` — FAIL (config location; the behaviours hold)
Behaviour (PASS):
- Ready rows are taken `ORDER BY priority DESC, created_at ASC` (`kanban_db_dispatch.py:2222-2228`). Dry-run with priorities 50/30/10 → `spawned [t_70a489f4 (50), t_8c583144 (30)]`.
- Cap: `hermes -p arprobe kanban --board arprobe dispatch --dry-run --json` spawned 1 / 2 / 3 tasks with `kanban.max_in_progress` 1 / 2 / 3. Live: with cap 2, tick 1 spawned `[t_562ab168, t_fabe650a]` and left `t_fa134c3a` ready; it spawned on the next tick, after the first two were done.
- `max_in_progress` is a **host** cap: it counts running tasks on every board (`kanban_db_dispatch.py:2161-2197`).
- `auto_decompose` acts only on triage tasks, `review_dispatch` only on the review column (`kanban_db_dispatch.py:1760-1767, 2315`); our cards are neither. No `default_assignee` fill-in when it is empty (above).

Why FAIL: these settings are read from the config of the process that runs the dispatcher, not from our profile.
- `hermes -p <profile> kanban dispatch` reads that profile's `kanban.*` (`hermes_cli/kanban_ops.py:60-92`; verified with the cap change above).
- The gateway is one process per host. `gateway.multiplex_profiles` defaults to `true` and is "the ONLY supported topology" (`hermes_cli/config_defaults.py:2112-2136`); on first start the gateway wrote `gateway.multiplex_profiles: true` into `/tmp/hh/config.yaml`. Its embedded dispatcher reads `load_config()` of the launch (default) home **once at boot** (`gateway/kanban_watchers.py:203-264`), and runs every board under one machine-global lock `<root>/kanban/.dispatcher.lock` (`:236-247`). Evidence: with profile `kanban.default_assignee: arprobe` and root `kanban.default_assignee: default`, the gateway logged `default_assignee='default'` and `max_in_progress=2` (root value).
- So C9 rows "`kanban.max_in_progress` … config.yaml + bootstrap" and "`kanban.auto_decompose false`, `review_dispatch false` …; `default_assignee` unset" do not take effect when the gateway dispatches. Amendment A3.

## 6. `kanban show <id> --json` exposes runs with run id and terminal outcome — PASS
`hermes kanban --board arprobe show t_562ab168 --json` → top keys `task, latest_summary, parents, children, comments, events, runs`. One run:
```json
{"id": 1, "profile": "arprobe", "step_key": null, "status": "done", "outcome": "completed",
 "summary": "Probe steps executed: …", "error": null,
 "metadata": {"worker_session_id": "20261007_133535_0934b8"},
 "worker_pid": null, "started_at": 1791405334, "ended_at": 1791405347}
```
- Run ids are integers, unique per board DB (seen 1–10), not per task. Run `status`/`outcome` values seen: `running/None`, `done/completed`, `blocked/blocked`, `reclaimed/reclaimed`, `rate_limited/rate_limited`.
- The worker gets its run id in env `HERMES_KANBAN_RUN_ID` (`kanban_db_dispatch.py:2842`), plus `HERMES_KANBAN_TASK`, `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_BRANCH`, `HERMES_KANBAN_BOARD`, `HERMES_KANBAN_DB`, `TERMINAL_CWD` (`:2830-2870`). Not tested: whether `HERMES_KANBAN_*` reach terminal-tool children. They are not on any strip list (`tools/environments/local_env_policy.py:285-299`), so they should.
- `task` JSON has no `claim_lock` field; `status: running` plus a run with `ended_at: null` is the observable claim.

## 7. `terminal.env_passthrough` delivers exact names to a cron script and a worker terminal — PASS
- **Cron script, under the multiplex gateway (the real topology):** shipped job resumed, gateway ran it → `{"cwd": "/private/tmp/hh/profiles/arprobe/scripts", "HERMES_HOME": "/tmp/hh/profiles/arprobe", "SOFTMAX_PLAYER_CREDENTIAL": "fake-cred-123", "AUTORESEARCH_CLI": "/tmp/hh/profiles/arprobe/bin/autoresearch", "AUTORESEARCH_NOT_PASSED": null, "OPENROUTER_API_KEY_present": false}`. `AUTORESEARCH_NOT_PASSED` was in the profile `.env` but not in `env_passthrough`, and was dropped. So under the gateway, passthrough is the only way a profile `.env` value reaches a cron script (`cron/scheduler_script.py:366-379`; `tools/env_passthrough.py:147-167`).
- **Cron script, single-profile CLI** (`hermes -p arprobe cron run arprobe-env`): all `.env` names pass, listed or not, because the CLI process loads the profile `.env` and the sanitizer strips only the provider/secret blocklist (`tools/environments/local.py:310-340`). Provider keys (`OPENROUTER_API_KEY`) never pass.
- **Worker terminal** (CLI dispatcher and gateway dispatcher): the worker's `echo` printed `CRED=fake-cred-123 CLI=/tmp/hh/profiles/arprobe/bin/autoresearch NOTPASSED=should-not-appear`. Workers are standalone `hermes -p <profile>` processes that load the profile `.env`, so non-blocklisted names reach the terminal with or without passthrough.
- Cron script cwd is `$HERMES_HOME/scripts` (the script's directory; `cron/scheduler_script.py:379`), not the profile root.
- Names are exact (no wildcard handling in `tools/env_passthrough.py:86-115`).

## 8. `hermes config set` writes nested keys — PASS
`hermes config set kanban.max_in_progress 2`, `… plugins.enabled '[autoresearch]'`, `… terminal.env_passthrough '[SOFTMAX_PLAYER_CREDENTIAL, AUTORESEARCH_CLI]'`, `… approvals.mode off`, `… auxiliary.background_review.enabled false` → nested YAML:
```yaml
kanban: {max_in_progress: 2, auto_decompose: false}
plugins: {enabled: [autoresearch]}
terminal: {env_passthrough: [SOFTMAX_PLAYER_CREDENTIAL, AUTORESEARCH_CLI]}
approvals: {mode: "off"}          # stays the string "off", not false
auxiliary: {background_review: {enabled: false}}
```
Flow-style lists are parsed. `hermes config unset <key>` also exists. `hermes -p <profile> config set` writes the profile config. Source `hermes_cli/config.py:3479`.

## 9. Real `cron/jobs.json` record from `hermes cron create` (no-agent script job, created paused) — PASS
Command: `hermes cron create "every 5m" --name probe-job --script probe.py --no-agent --paused --paused-reason "task 0.2 probe"` (script at `$HERMES_HOME/scripts/probe.py`). Record, verbatim:
```json
{
  "id": "7990a71ee14d",
  "name": "probe-job",
  "prompt": "",
  "skills": [],
  "skill": null,
  "model": null,
  "provider": null,
  "base_url": null,
  "script": "probe.py",
  "no_agent": true,
  "monitor_script": null,
  "monitor_url": null,
  "monitor_state": null,
  "context_from": null,
  "schedule": {
    "kind": "interval",
    "minutes": 5,
    "display": "every 5m"
  },
  "schedule_display": "every 5m",
  "repeat": {
    "times": null,
    "completed": 0
  },
  "enabled": false,
  "state": "paused",
  "paused_at": "2026-10-07T13:30:49.366649-07:00",
  "paused_reason": "task 0.2 probe",
  "created_at": "2026-10-07T13:30:49.366649-07:00",
  "next_run_at": null,
  "last_run_at": null,
  "last_status": null,
  "last_error": null,
  "last_delivery_error": null,
  "last_delivery_unverified": null,
  "failure_streak": 0,
  "deliver": "local",
  "origin": null,
  "enabled_toolsets": null,
  "workdir": null
}
```
The store is `{"jobs": [ … ], "updated_at": "…"}`. A shipped `cron/jobs.json` may carry only authored fields (`cron/job_definition.py:13-18`): the minimal shipped record above merged correctly. `hermes cron run <id>` refuses a paused job ("resume it before running"); after `resume`, `cron run` runs it at once.

## 10. Worker cwd is the worktree; committed `AGENTS.md` loads — PASS
- The worker's `pwd` printed `<canon>/.worktrees/X` (and `/Y`, `/G`); `write_file` wrote `<canon>/.worktrees/X/result.txt`. The dispatcher sets `TERMINAL_CWD` to the workspace (`kanban_db_dispatch.py:2838`).
- Every worker answered `agents: TANGERINE-7` with no file-read tool call (tool sequence: `kanban_show`, `terminal`, `terminal`, `write_file`, `kanban_complete`). The stored system prompt of all four sessions contains "The following project context files have been loaded and should be followed: ## AGENTS.md # Probe AGENTS.md …TANGERINE-7".

## 11. Tool calling through OpenRouter — PARTIAL: glm-5.3-flash PASS, glm-5.3 PASS, kimi-k3 UNVERIFIED
Each card (per-task `--model <id> --provider openrouter`): run one terminal command, run a command that the plugin blocks, write `result.txt` with the file tool, call `kanban_complete`.

| Model | Task | Result | Evidence |
|---|---|---|---|
| `z-ai/glm-5.3-flash` | t_562ab168 (CLI dispatcher), t_9774331c (gateway dispatcher) | PASS | run `done/completed`, 12 s; all 5 tool calls; correct `result.txt` |
| `z-ai/glm-5.3` | t_fabe650a | PASS | run `done/completed`, 16 s; all 5 tool calls; correct `result.txt` |
| `moonshotai/kimi-k3` | t_fa134c3a | UNVERIFIED | two runs `rate_limited`: OpenRouter `HTTP 402: … You requested up to 131072 tokens, but can only afford 63269` |

Why kimi-k3 is blocked:
- The broker key `openrouter.inference` had `limit 500, usage 499.05, limit_remaining 0.949` USD (from `GET /api/v1/key`). The broker description says "$50 hard limit"; the key's real limit is $500 and it is nearly used up.
- Hermes sends no `max_tokens` on main-loop calls unless something sets it (`agent/transports/chat_completions.py:331-339`; the CLI does not pass a config value). OpenRouter then reserves the model's full output limit: 131072 × $15/M ≈ $1.97 per kimi-k3 request. There is no config knob for this in v0.21.5 (searched `config_defaults.py`, `cli.py`, `agent_init.py`).
- Outside Hermes, a direct OpenRouter call to `moonshotai/kimi-k3` with `max_tokens: 600` and one tool returned `finish_reason: "tool_calls"`, `run_command({"command":"pwd"})`, cost $0.00105. So the model does tool calls on OpenRouter; Hermes's tool loop with kimi-k3 is still unproven.
- Operational fact: a 402 ends the run as `rate_limited` ("requeued without counting a failure"), and the dispatcher re-spawns it about every 6 minutes with no limit. The fallback chain was not exercised (the task pinned its model).
- This is a decision for James: top up or raise the broker key, then re-run this one card (the probe setup can be rebuilt in minutes). Amendment A5.

Approximate spend: four glm runs plus one direct kimi call, all below $0.05.

## Commands for Task 2.2
All take `--board <slug>` right after `kanban`. Verified on v0.21.5 unless marked "source".

| Purpose | Command | Notes |
|---|---|---|
| Create board | `hermes kanban boards create <slug>` | Idempotent, exit 0 both times. `hermes kanban --board <slug> init` after it is optional; on a missing board `init` exits 1. |
| Board exists? | `hermes kanban boards list` / DB at `<root>/kanban/boards/<slug>/kanban.db` | Root is host-wide, not the profile. |
| Create task | `hermes kanban --board B create <title> --body-file <path> --branch card/<Q> --workspace worktree:<canon>/.worktrees/<Q> --priority <n> --idempotency-key <k> --json` | Without `--assignee` → `status: ready, assignee: null`. Same key → same task JSON. Use `--body-file` for JSON bodies. |
| Assign / unassign | `hermes kanban --board B assign <id> <profile>` / `… assign <id> none` | Raises while `running` under a claim. |
| Show one | `hermes kanban --board B show <id> --json` | `{task:{…}, latest_summary, parents, children, comments, events:[{kind,…}], runs:[{id:int, profile, step_key, status, outcome, summary, error, metadata:{worker_session_id}, worker_pid, started_at, ended_at}]}` |
| List | `hermes kanban --board B list --json [--status ready] [--archived] [--sort priority]` | JSON array of task objects, same keys as `show.task`: `id, title, body, assignee, status, priority, tenant, workspace_kind, workspace_path, branch_name, project_id, created_by, created_at, started_at, completed_at, result, skills, max_runtime_seconds, max_retries, model_override, provider_override, session_id, workflow_template_id, current_step_key, completion_contract, last_failure_error`. Archived tasks are hidden unless `--archived`. |
| Runs only | `hermes kanban --board B runs <id> --json` | |
| Edit priority | `hermes kanban --board B edit <id> --priority <n>` | Works on a ready task (verified 5 → 77). `--title`, `--body` also work. |
| Complete a pending task | `hermes kanban --board B complete <id> --result <text> [--summary <s>] [--metadata '<json>']` | **No `--force` needed** for a ready task or a claimed task with no live worker (adds a run `done/completed`). `--force` is needed only for a `running` task whose worker PID is alive (`kanban_db.py:2723-2776`, `kanban.py:918-927`). On a done task: exit 1 "unknown id or terminal state". |
| Block | `hermes kanban --board B block <id> <reason words…>` | Reason is positional; `--reason` is rejected. Run → `blocked/blocked`. |
| Requeue a blocked task | `hermes kanban --board B unblock <id> [--reason <text>]` | → `ready` (or `todo` while parents are open). Exit 1 if not blocked/scheduled. |
| Requeue a running task | `hermes kanban --board B reclaim <id>` | → `ready`; run `reclaimed/reclaimed`. `reassign <id> <profile> --reclaim` does both. |
| Archive | `hermes kanban --board B archive <id>…` | Frees the idempotency key ("non-archived task with this key"). |
| One dispatcher pass | `hermes -p <profile> kanban --board B dispatch [--dry-run] [--json]` | Uses `<profile>`'s `kanban.*` config. JSON: `reclaimed, crashed, timed_out, stale, auto_blocked, promoted, reaped_terminal_workers, spawned:[{task_id, assignee, workspace}], skipped_unassigned, skipped_nonspawnable, skipped_per_profile_capped, auto_assigned_default, respawn_guarded, rate_limited, skipped_locked, memory_pressure`. |
| Continuous dispatch | `hermes gateway run` (foreground) or `hermes gateway start` (launchd/systemd) | Embedded dispatcher ticks every `kanban.dispatch_interval_seconds` (60) across **all** boards with the default profile's config; one per host (`<root>/kanban/.dispatcher.lock`). `hermes kanban daemon` is deprecated. `kanban.dispatch_in_gateway: false` or `HERMES_KANBAN_DISPATCH_IN_GATEWAY=0` turns it off. |
| Worker identity | env `HERMES_KANBAN_TASK`, `HERMES_KANBAN_RUN_ID`, `HERMES_KANBAN_WORKSPACE`, `HERMES_KANBAN_BRANCH`, `HERMES_KANBAN_BOARD` | Set by the dispatcher (`kanban_db_dispatch.py:2830-2870`). |

## Amendments needed (proposed; James decides; `00-overview.md` not edited)
- **A1 (Tech stack, Step 1).** Replace the install line with: "Clone `NousResearch/hermes-agent` at tag `v2026.9.24` (commit `f97608f`) and install it as an editable uv tool from the clone; `uv tool install git+…@v2026.9.24` fails because the Hermes `setup.py` refuses to build wheels." Re-point "Facts about Hermes cite `hermes-agent@9ab4a4f`" to `f97608f` (line numbers moved).
- **A2 (C1 bootstrap, C12 detection).** Replace `hermes kanban --board <slug> init` with `hermes kanban boards create <slug>` (idempotent), optionally followed by `init`. Board DBs are host-wide at `<root>/kanban/boards/<slug>/kanban.db`, so "board missing" (C12) means that file or the `boards list` entry is gone.
- **A3 (C9 table, C7; item 5).** `kanban.*` dispatcher settings in our profile `config.yaml` do not govern the gateway dispatcher. It reads the host default profile's config once at boot and serves all boards. Proposed:
  1. Keep the `kanban.*` rows in our `config.yaml` (they govern `hermes -p <profile> kanban dispatch` and the profile's own tools), but say so.
  2. Add a **preflight check (read-only)** in `queue-keeper`: read the host root config (`hermes config get` without `-p`, or the YAML at the default root). Fail preflight (pause, `help_request`) when `kanban.default_assignee` is set (breaks C7 pause), when `kanban.dispatch_profiles` is set and leaves out our profile, or when `kanban.dispatch_in_gateway` is false and no other dispatcher runs.
  3. State that `kanban.max_in_progress` is a **host** cap across all boards and lives in the host root config. The installer (Rohit) sets it; bootstrap does not write the root config. Changes need a gateway restart.
  4. C7 text: "Unassigned tasks are not dispatched **provided the host dispatcher's `kanban.default_assignee` is empty** (preflight checks this)."
- **A4 (C9 env passthrough; note, not a FAIL).** Under the one-gateway topology a cron script gets only the `terminal.env_passthrough` names from the profile `.env`. Every variable a `scripts/*.py` launcher or runtime job needs must be on the list, including any added later (for example `HERMES_WRITE_SAFE_ROOT` if a script reads it). Worker terminals see the whole profile `.env` minus provider keys, so the plugin, not env filtering, must keep the credential away from model commands. Cron script cwd is `$HERMES_HOME/scripts`; launchers must not depend on cwd.
- **A5 (Models; item 11).** kimi-k3 in Hermes is UNVERIFIED because the broker key has $0.95 left of a $500 limit. Before Plan 01 M5: top up or raise the key and re-run the kimi-k3 card. Also record: (a) Hermes v0.21.5 sends no `max_tokens` on main-loop calls, so OpenRouter reserves 131072 output tokens per kimi-k3 request (≈ $1.97 of credit headroom needed per call); (b) a 402 becomes `rate_limited` and is retried about every 6 minutes forever, so preflight should check OpenRouter key credit (`GET /api/v1/key` → `limit_remaining`) and pause when it is low.
- **A6 (C1 worker command; note).** `$HERMES_HOME/bin/` also holds Hermes's `tirith` binary after the first agent run. Bootstrap must write only `bin/autoresearch` and never clean `bin/`.
- **A7 (C1 plugin; note).** In the gateway process `os.environ["HERMES_HOME"]` is the host root. `autoresearch.runctx` and the plugin must take the profile home from `hermes_constants.get_hermes_home()` (and the `HERMES_KANBAN_*` env in workers), never from `os.environ["HERMES_HOME"]` alone.
- **A8 (C3; note).** Kanban run ids are integers per board DB. `handoffs/<task-id>/<run-id>.json` stays unique because task ids are random, but a run id alone is not a global key. A run is terminal when `ended_at` is not null; `rate_limited` and `reclaimed` runs end while the task goes back to `ready`.

## Task 1.3 observations (fresh-install integration, 2026-10-07)
Observed by `tests/integration/test_install.py` (Hermes v0.21.5, throwaway `HERMES_HOME` under pytest's tmp dir). Nothing here contradicts the amendments; these are details the plan did not state.
- **Install lands at `<HERMES_HOME>/profiles/<name>`** (`--name ar-it` overrides the manifest name). The whole payload ships, including `tests/`, `docs/`, `.gitignore`, `pyproject.toml` (C1: no `distribution_owned`). Install writes **no `.env`**, only `.env.EXAMPLE`; the installer (or a test) must create `.env`.
- **Install pre-creates an empty `workspace/`** in the profile (Hermes's bootstrap user dirs: `workspace`, `memories`, `sessions`, `logs`, `plans`, `home`, `skills`, `skins`). Bootstrap must key on `workspace/.git`, not on `workspace/` existing (it does).
- **`hermes profile update <name>` re-reads the recorded `source:`** in the profile's `distribution.yaml` (for a local directory: its absolute path, staged in place). There is no "update from another directory": an update test modifies the copy at the recorded path. Update kept `workspace/x`, `memories/MEMORY.md`, and `config.yaml` (a `kanban.max_in_progress: 3` set with `hermes -p ar-it config set` survived) and replaced `scripts/queue_keeper.py` with the changed copy.
- **Board DB root for a profile under `<root>/profiles/<name>`** is `<root>` (`hermes_constants.get_default_hermes_root`, `hermes_cli/kanban_db.py:399-413`): the launcher run with `HERMES_HOME=<root>/profiles/ar-it` created `<root>/kanban/boards/ar-it/kanban.db`.
- **The launcher's `uv sync --frozen` also installs the `dev` group** (pytest, ruff) into `local/venv`, because uv syncs default groups. Fixed in the Task 1.3 fix round: the launcher now runs `uv sync --frozen --no-dev`.
- The worker tests (`$AUTORESEARCH_CLI` in a worker shell; plugin `post_tool_call` writing `workspace/.supervisor/runtime/tool_events.jsonl`) were not run: they need `OPENROUTER_API_KEY` and the broker key is out of credit. Their setup (task create with `--model z-ai/glm-5.3-flash --provider openrouter --max-retries 1`, worktree workspace, `dispatch --dry-run` selects the task) was checked without a model call. `hermes kanban log <id>` exits 1 before a worker has spawned.

## Task 5.1 observations (config migrations, 2026-10-07)

Source: Hermes v0.21.5 (`v2026.9.24`, `f97608f`), read-only.

- **Auto-update.** Hermes has no automatic update. It only runs a passive check (banner and CLI) that `updates.check: false` turns off (`hermes_cli/banner.py` `check_for_updates`; default in `hermes_cli/config_defaults.py`). `hermes profile update` and `hermes update` run only when an operator starts them. Bootstrap sets `updates.check: false` ("auto-update off").
- **Curator and shipped skills.** There is no per-skill exclusion key. The curator touches only skills with a `created_by: agent` record (or `agent_created: true`) and, when `curator.prune_builtins` is true, bundled built-ins (`tools/skill_usage.py` `list_agent_created_skill_names`, `is_curation_eligible`). Skills shipped by a distribution have neither, so they are excluded by default; bootstrap pins `curator.prune_builtins: false`. Caveat: `hermes curator adopt <skill>` flips the opt-in flag, so an operator must not adopt a shipped skill.
- **`skills.create_dir`** exists (`agent/skill_utils.py` `get_skill_create_dir`, used by `tools/skill_manager_tool.py`); new skills land there instead of `skills/`.
