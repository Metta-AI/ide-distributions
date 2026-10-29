# Status of the IDE profile rebuild

Updated October 28, 2026, evening (Pacific). Branch `jb/idd-profile-refactor`, not pushed.
The plan is `00-overview.md` … `07-skills-soul-setup.md` with the binding amendments in
`08-amendments.md`; this file says what landed and what is still open. `git log` on the branch
lists every commit.

## Landed

Every stack entry of the plan is implemented and committed:

1. **Core package and profile shells** (`core/`, root `pyproject.toml` + `uv.lock`,
   `orchestrator/`, `researcher/`; the old profiles, `shared/`, `ontology/` and
   `tools/sync_shared.py` removed; `play/` untouched).
2. **Store**: SQLite per Coworld plus a workspace store, migrations, token-marked transactions,
   the events table, every record module, `research store apply|export|snapshot|events`.
3. **Tools**: 18 Orchestrator and 14 Researcher tools with actions, validated at once, never
   raising; the plugin (tools, hooks, prompt sections, the turn hook with notices, changed-since,
   away summary and reactions); the two executors (in-process and workspace).
4. **Platform**: one protocol, the real client on `coworld` and `softmax-cli`, the fake with
   faults, identity, waits, submission ladder and quality bar, XP, credits, `research softmax`,
   `research platform check --read-only`.
5. **Watcher**: the twelve-step model-free tick, the event-ownership map, wakes (setup, review,
   management, anti-stall) with their prompts, dispatch, recovery, waits, messages, resume, the
   mechanical merge (now emitting `submission.candidate` for merged candidates), Goal
   conditions, champion sync, tool checks, the context panel, drain and `has_work`.
6. **Seam**: contract files and validator, `research serve`, the scripted client, the
   acceptance scenario on the fake platform (`core/tests/scenarios/test_seam_end_to_end.py`).
7. **Skills, SOULs, task types, preparation**: 18 common, 8 Orchestrator and 2 Researcher
   skills; generated SOULs (2,462 / 1,927 words) and manifests; the four task types;
   `prepare.py` with protected and retired keys, the active Coworld's directories for every
   profile, shipped values over earlier defaults, stores, the watcher job, the workspace
   environment, `IDE_DEFAULT_COWORLD` and `--check`.
8. **Checks and docs**: `tools/check_prompts.py` (structural, plus a model check with a key),
   `tools/build_prompts.py --check`, the nightly platform-check workflow, `docs/hosting.md`,
   `core/README.md`, the profile READMEs, banners on the retired docs.

## Verified

| Check | Result |
|---|---|
| `uv run ruff check .` and `ruff format --check .` | clean |
| `uv run pytest -q` | 1,447 passed, 2 skipped (model-gated), 6 deselected (hermes) |
| `python tools/build_prompts.py --check`, `python tools/check_prompts.py` | pass |
| `cd play && uv run pytest -q` | 81 passed |
| `HERMES_BIN=… uv run pytest -m hermes core/tests/hermes/test_prepare_real.py` (real Hermes v0.21.6) | 3 passed (install and prepare) |
| S1 spike and the real-Hermes acceptance run (model key from the token broker) | both passed; see "Real model runs" below |

## Real model runs

Both ran on the evening of October 28, 2026 (Pacific) against Hermes v0.21.6 in an isolated
environment, with `moonshotai/kimi-k3` through an OpenRouter key from the token broker.

- **Acceptance scenario on a real gateway** (`core/tests/hermes/test_seam_real_hermes.py`):
  **passed**. "Submit the starter as my champion" on a new workspace: the Orchestrator's chat
  run switched to `cow_fake` with the request pending, the setup wake built V-1, uploaded it
  and submitted it, and the fake platform held the submission as the profile's Player after
  7 watcher ticks (38 s). The first attempt had surfaced three defects, all fixed before the
  passing run: the plugin read `os.environ`, which a multiplexing gateway keeps free of the
  profile's `.env` (now `executor.profile_env` merges Hermes's secret scope); Hermes's
  post-turn self-improvement review patched four shipped skills (now a protected key, off);
  and the model called `ui.notify` without `text` repeatedly (validation errors now list the
  fields received, and the descriptions name the field).
- **S1 dispatch spike** (`core/tests/hermes/test_spike_s1.py`, report in
  `docs/spikes/s1-dispatch.md`): **passed**. Three Researcher Sessions in one profile started,
  one steered (accepted; kept pending when the one-step reply finished first), one stopped
  (cancelled), the gateway killed and restarted in 2.7 s, the third run's status durable
  through the key, its Session resumed and completed, each Session's history holding only its
  own turns, and the idempotency key replaying the run id for the same body and answering
  409 for another. In the passing run the model had answered before the kill; the 21:16 run
  exercised the kill under a live run (it read `interrupted` after the restart and the Session
  resumed and completed). Two harness defects were fixed on the way: a kill interrupts every
  live run, so run 1 is awaited first; and the history route answers in `data`, which the
  runs client now reads.

## Open items and known seams

- `claim` exposes no `update` action although the store has one; the skills say a claim's
  text, tags and links do not change once written. Decide whether to expose it.
- `task.wait` has no kind that resolves on the watcher's rerun of the checks; the repair skill
  tells the agent to rerun the fixed command itself and read `workspace.capabilities` once.
- The checks' upload dry run writes no `intents` row (the upload tool does).
- `experiments.methods.candidate` cannot be set after an Experiment starts; the experiment tool
  falls back to the Version built for it.
- `require_holder` gives the watcher no sanctioned way to write Task-scoped Experiment runs; it
  skips them, and it releases Tasks as the old holder's Session actor.
- The seam's full contract layer (`session-open` capabilities, panels, the agent-context panel
  as a client surface) ships in its own stack, as the design says.
- The plan's `tools/e2e.py` demo (a runnable `research serve` with the scripted agent that
  prints the event stream) was not built; the scenario tests under `core/tests/scenarios/`
  cover the same path.
- Hosting changes listed in `docs/hosting.md` (per-Session turn serialisation,
  `sleep_through_jobs`, the read-only core checkout, one API key across profiles) are for metta.
