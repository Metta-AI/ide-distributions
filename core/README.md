# research-core

The shared package of the Softmax IDE research profiles. Both profile folders (`orchestrator/`,
`researcher/`) are thin shells; everything they do lives here. The design is
`docs/designs/ide-profile-refactor-2026-10-28-final.html`; the build plan with the locked
decisions is `docs/plans/ide-profile-refactor/`.

## Two environments

- **Hermes's interpreter** imports the stdlib-only base of this package (the plugin shim in each
  profile puts `core/src` on `sys.path`, from the path `prepare.py` records). Everything the plugin
  touches in process must import without third-party packages: `layout`, `refs`, `store/*`,
  `plugin/*`, `tools/base.py` and the in-process tools (`goal`, `task`, `directive`, `claim`,
  `settings`, `ui`). `core/tests/test_tool_budget.py` enforces it.
- **The workspace environment** (`$IDE_WORKSPACE/tools/venv`, built by `prepare.py` from the
  repository root `pyproject.toml` and `uv.lock`) has the `platform` extra: the official `coworld`
  and `softmax-cli` clients, PyYAML and numpy. The `research` CLI, the watcher and every tool
  marked `executor="workspace"` run there; the plugin starts them as
  `python -m research_core.cli tool <name> --args <json>`.

## Package map (`src/research_core/`)

| Module | What it is |
|---|---|
| `layout.py` | The workspace on disk: `tools/`, `.state/store.db`, one folder per Coworld (`code/`, `source/`, `worktrees/`, `documents/`, `.state/`). |
| `refs.py` | The one reference parser: `G-3`, `T-42#failure`, platform ids, policy labels, URLs. |
| `store/` | The record store: SQLite per Coworld plus a workspace store; numbered migrations; token-marked transactions with an append-only `events` table and `change_log` detection; schema check and rebuild. One module per record kind (`goals`, `tasks`, `claims`, `experiments`, `versions`, `champion`, `directives`, `settings`, `capabilities`, `intents`, `sessions`, `ui_state`, `notices`, `pins`, `coworlds`), the field `registry`, `events.KINDS`, and `cli.py` (`research store apply|export|snapshot|events|backup|restore-check|quiesce`). |
| `tools/` | The profile tools. `base.py` defines `ToolSpec`/`ActionSpec`/`ToolContext` and `run_tool` (validates every problem at once, never raises, scrubs store paths); `catalog.py` lists the modules and the role sets; one module per tool. |
| `plugin/` | The Hermes plugin: `register.py` (tools, hooks, prompt sections per role), `executor.py` (in-process or workspace subprocess), `briefing.py` and `session_context.py` (prompt sections), `turn.py` (the Orchestrator's chat-turn hook: context block, notices, changed-since, away summary), `advisory.py` + `rules.py` (the never-blocking advisory hook and the last-error hook), `repeats.py` (a short refusal in place of a repeated identical one, and a notice when Hermes's loop guard halts a turn), `inference_hook.py` (the `api_request_error` hook that records failed model calls in the inference state). |
| `platform/` | The Softmax platform behind one `Platform` protocol: `real.py` on the official clients, `fake.py` (a persistent test double with fault injection), `identity.py`, `errors.py`, `wait.py` (retries, then a Task wait), `submission.py` (status ladder and the autonomous quality bar), `xp.py`, `credits.py`, `cli.py` (`research softmax`, `research platform check`). |
| `stats/` | Evaluation design, pairing, the decision rule (`confirmed`/`refuted`/`inconclusive`), noise and power. |
| `inference.py` | The inference gate every run start passes: the lease file (the installed key's expiry, fingerprint and when it changed) and the inference state (`.inference-state` beside it: why model work is blocked and until when, which key ran out, and the last failed model call). A budget block ends when the lease file names a different key. Stdlib only; the plugin hook, the image's lease loop and the runs client share it. |
| `mixin.py`, `gitops.py`, `locks.py` | `MIXIN.md` front matter and mixin commands (detached runs with a resume token); git helpers (worktrees, pinned clones, `--no-ff` merges in a temporary worktree, citation staleness); file locks. |
| `coworld_setup.py`, `environment_guide.py` | Coworld switch, source fetch at the pinned commit, mixin import with prefixed skills, lesson import as claims; the environment guide the agents consult first. |
| `watcher/` | Model-free coordinator helpers run by `research serve`: observation, waits, messages, wakes, domain checks, sleep and drain. `research watcher status` reports durable state; `research workspace sleep|wake|drain|recover` exposes lifecycle operations. |
| `contract/` | The client contract files (`context`, `events`, `ui-state` schemas, `CONTRACT.md`), the stdlib validator and `research context-schema`. |
| `serve.py`, `client.py` | `research serve`, the local seam server that plays the bridge (plus `GET /` for the client page and `GET /status` for the watcher's status), and the scripted client used by the end-to-end tests. |
| `run_failures.py` | Why a chat run failed (budget, rate limit, provider down, too large, misconfigured), from the inference state, the run's `turn_exit_reason` and its error text; `research serve` holds a budget, rate-limit or provider failure as a `waiting` message and resumes it with a continuation prompt. Stdlib only. |
| `profile_models.py` | Each profile's model for the bridge's `/models` routes: read from the profile's `config.yaml`, checked against OpenRouter's tool-capable models, written with `hermes config set`. |
| `ui/` | `index.html`, the self-contained client page `research serve` serves at `/`: chat with context, the event streams, UI state (show, panels, asks), notices, the records, user edits and a raw request form, in the Ink & Print house style. `docs/local-end-to-end.md` ("A client page") says how to open it. |
| `scripted_agent.py` | `ScriptedAgent`, the model-free Orchestrator that plays the runs of `FakeHermesRuns` with real tool calls: the acceptance scenario and `tools/ui_dev.py` (the page's development stack) share it. |
| `prompts/` | The SOUL parts per role, `vocabulary.md` (from metta) and `build.py`; `tools/build_prompts.py` renders `orchestrator/SOUL.md` and `researcher/SOUL.md` and syncs the plugin manifests. |
| `skills/` | The shipped skills: `common/<category>/<skill>/`, `orchestrator/<skill>/`, `researcher/<skill>/`; `prepare.py` points `skills.external_dirs` at them. |
| `task_types/` | The four Task types with their completion checklists and Researcher instructions. |
| `prepare.py` | Profile preparation after install or update: core path marker, protected and retired config keys, the active Coworld's skill directories for every profile, shipped values over earlier defaults (not the model settings, but for one Orchestrator migration off `kimi-k3`), stores, the watcher job, the workspace environment, the default Coworld. |
| `frozen_sessions.py` | Called by `prepare.py`: when a release changes the tool schemas or the instructions, open chats drop their stored copies and take the new ones on their next turn (`docs/hosting.md`, "Open chats after a release"). |
| `cli.py` | The `research` command; each subcommand module registers itself. |

## Run the tests

```bash
uv sync                        # from the repository root (Python 3.12)
uv run ruff check . && uv run pytest -q
HERMES_BIN=/path/to/hermes uv run pytest -q -m hermes core/tests/hermes
uv run python tools/build_prompts.py --check && uv run python tools/check_prompts.py
```

The fake platform (`IDE_PLATFORM=fake`) needs no network; the `hermes`-marked tests install the
profiles into a temporary Hermes home; the dispatch spike and the real acceptance run also need
an OpenRouter key. The pinned Hermes (v0.22.6) runs on Python 3.14 in the image, so a local
`HERMES_BIN` is easiest to get from an isolated environment of the Hermes checkout
(`UV_PROJECT_ENVIRONMENT=<somewhere> uv sync --frozen --no-dev --extra messaging`, then
`<somewhere>/bin/hermes`); the `messaging` extra brings `aiohttp`, without which the gateway
starts but its API server does not, and the gateway enables that server from the home's
`.env`, not from the process environment. `docs/local-end-to-end.md` turns the real acceptance
run into a local end-to-end setup: the one-command run, an interactive session that stays up
(`IDE_E2E_KEEP=1`, optionally on the real platform), and the same inside the hosted image.
`core/tests/hermes/test_frozen_sessions_real.py` needs no key: its docstring has the command that
runs it inside the pinned Hermes image, so it tests the Hermes version that ships.

## Upload recovery

`tools/upload.py` sends a Version's bytes once per call and never retries the write (a retry
could send twice when the error came after the request reached the platform). Before the first
send it saves the exact upload inputs coworld deduplicates on (`policy_upload_inputs`: name,
player, file hash, file size, attributes) on the `upload` intent (`intents.begin(request=...)`,
kept under `result["request"]`). An existing intent is never sent again blindly: the tool reads
`POST /stats/policies/lookup` (`Platform.lookup_policy_upload`) with the saved inputs, as the
Player, which works after the local artifact is gone. A match is adopted; the platform's definite
"no such version" (`200 null`) allows one automatic send per intent (`intents.reopen` records
`{"lookup": "not_found"}` on the earlier attempt); a second absence, a `done` intent the platform
no longer shows, or a lookup that errors or is refused leaves the upload unresolved: `ok: false`
naming the intent and the inputs, a high-urgency notice, nothing sent. `user_requested: true`
always sends again. The listing's `player_file_content_hash` is served to team callers only, so
it is not used for recovery. Fake faults: `lookup=503|403|unavailable|not_found`.

## One intent, applied once

Commands (`transitions.py`, spec: `docs/specs/workspace-orchestration.md`) get their identity from
code. The pinned Hermes never runs a call again itself and drops identical calls in one response,
so a retry is always the model's new call: a new tool-call id, the same arguments, sometimes after
a restart's "effect is UNKNOWN" notice. So a tool-call id cannot recognise a retry, and arguments
alone cannot separate it from a deliberate second action or a call after a refusal.
`request_generated` uses what can: an identical call joins the session's command whose finished
result has not been returned to it yet, and is a new intent afterwards. A call Hermes cancelled
while it waited counts as not returned.
`core/tests/scenarios/test_retry_once.py` runs these cases. `ActionSpec.hidden_params` are the
fields such code fills in: accepted from chats whose frozen schema still lists them, offered
nowhere.

## Add a tool, a record or a skill

- **Tool:** a module under `tools/` with `SPEC = tool(ToolSpec(...))`, listed in
  `catalog.TOOL_MODULES`; in-process tools stay stdlib only; `core/tests/test_tool_budget.py`
  pins the role sets (17 Orchestrator, 14 Researcher) and the executor table; run
  `uv run python tools/build_prompts.py --write --manifests`. Describe the tool, every field
  and every key, shortly: every request carries the whole list. Build field schemas with
  `tools/_args.py`: an object field lists its keys (`obj`, `objects`; `free_object` only for
  caller-defined keys such as a JSON body) and a field of several types uses `any_of`, because
  Hermes's sanitizers turn a bare object into an empty one and an untyped field into a string.
  In a multi-action tool a field shared by actions keeps one shape, and `ToolSpec.properties`
  adds which actions require or take it; `core/tests/tools/test_tool_schemas.py` checks all
  of this.
- **Record:** a migration (`store/migrations/<kind>/NNNN_*.sql`), a module under `store/`, a
  `RecordSpec` in `store/registry.py`, its event kinds in `store/events.py`, and a tool action.
- **Skill:** `skills/<root>/<category>/<name>/SKILL.md` with `name` equal to the directory and a
  one-sentence `description` of at most 60 characters; `tools/check_prompts.py` checks it.
