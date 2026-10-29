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
| `store/` | The record store: SQLite per Coworld plus a workspace store; numbered migrations; token-marked transactions with an append-only `events` table and `change_log` detection; schema check and rebuild. One module per record kind (`goals`, `tasks`, `claims`, `experiments`, `versions`, `champion`, `directives`, `settings`, `capabilities`, `intents`, `sessions`, `ui_state`, `notices`, `pins`, `coworlds`), the field `registry`, `events.KINDS`, and `cli.py` (`research store apply|export|snapshot|events`). |
| `tools/` | The profile tools. `base.py` defines `ToolSpec`/`ActionSpec`/`ToolContext` and `run_tool` (validates every problem at once, never raises, scrubs store paths); `catalog.py` lists the modules and the role sets; one module per tool. |
| `plugin/` | The Hermes plugin: `register.py` (tools, hooks, prompt sections per role), `executor.py` (in-process or workspace subprocess), `briefing.py` and `session_context.py` (prompt sections), `turn.py` (the Orchestrator's chat-turn hook: context block, notices, changed-since, away summary), `advisory.py` + `rules.py` (the never-blocking advisory hook and the last-error hook). |
| `platform/` | The Softmax platform behind one `Platform` protocol: `real.py` on the official clients, `fake.py` (a persistent test double with fault injection), `identity.py`, `errors.py`, `wait.py` (retries, then a Task wait), `submission.py` (status ladder and the autonomous quality bar), `xp.py`, `credits.py`, `cli.py` (`research softmax`, `research platform check`). |
| `stats/` | Evaluation design, pairing, the decision rule (`confirmed`/`refuted`/`inconclusive`), noise and power. |
| `mixin.py`, `gitops.py`, `locks.py` | `MIXIN.md` front matter and mixin commands (detached runs with a resume token); git helpers (worktrees, pinned clones, `--no-ff` merges in a temporary worktree, citation staleness); file locks. |
| `coworld_setup.py`, `environment_guide.py` | Coworld switch, source fetch at the pinned commit, mixin import with prefixed skills, lesson import as claims; the environment guide the agents consult first. |
| `watcher/` | The model-free cron job: `context.py`, `tick.py` (the twelve steps), `handlers.py` (the event-ownership map), `wakes.py`, `dispatch.py`, `recovery.py`, `waits.py`, `messages.py`, `resume.py`, `merge.py`, `goals.py`, `champion_sync.py`, `checks.py`, `context_panel.py`, `drain.py`, `heartbeat.py`, `store_checks.py`, `hermes_api.py` (the runs API client and its fake), `cli.py` (`research watcher tick|status`, `research workspace drain`), `wakes/*.md` (wake prompts). |
| `contract/` | The client contract files (`context`, `events`, `ui-state` schemas, `CONTRACT.md`), the stdlib validator and `research context-schema`. |
| `serve.py`, `client.py` | `research serve`, the local seam server that plays the bridge, and the scripted client used by the end-to-end tests. |
| `prompts/` | The SOUL parts per role, `vocabulary.md` (from metta) and `build.py`; `tools/build_prompts.py` renders `orchestrator/SOUL.md` and `researcher/SOUL.md` and syncs the plugin manifests. |
| `skills/` | The shipped skills: `common/<category>/<skill>/`, `orchestrator/<skill>/`, `researcher/<skill>/`; `prepare.py` points `skills.external_dirs` at them. |
| `task_types/` | The four Task types with their completion checklists and Researcher instructions. |
| `prepare.py` | Profile preparation after install or update: core path marker, protected and retired config keys, the active Coworld's skill directories for every profile, shipped values over earlier defaults, stores, the watcher job, the workspace environment, the default Coworld. |
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
an OpenRouter key. Hermes v0.21.6 needs Python 3.14 for its own dependencies, so a local
`HERMES_BIN` is easiest to get from an isolated environment of the Hermes checkout
(`UV_PROJECT_ENVIRONMENT=<somewhere> uv sync --frozen --no-dev --extra messaging`, then
`<somewhere>/bin/hermes`); the `messaging` extra brings `aiohttp`, without which the gateway
starts but its API server does not, and the gateway enables that server from the home's
`.env`, not from the process environment.

## Add a tool, a record or a skill

- **Tool:** a module under `tools/` with `SPEC = tool(ToolSpec(...))`, listed in
  `catalog.TOOL_MODULES`; in-process tools stay stdlib only; `core/tests/test_tool_budget.py`
  pins the role sets (18 Orchestrator, 14 Researcher) and the executor table; run
  `uv run python tools/build_prompts.py --write --manifests`.
- **Record:** a migration (`store/migrations/<kind>/NNNN_*.sql`), a module under `store/`, a
  `RecordSpec` in `store/registry.py`, its event kinds in `store/events.py`, and a tool action.
- **Skill:** `skills/<root>/<category>/<name>/SKILL.md` with `name` equal to the directory and a
  one-sentence `description` of at most 60 characters; `tools/check_prompts.py` checks it.
