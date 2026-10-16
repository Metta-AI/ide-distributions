# Task 2.5 bindings and host prerequisites

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

The M1-qualified compiler remains the only build path. This task changes source
root handling and adds adapters; it does not rerun M1 or alter its immutable inputs.
The installed distribution can be a Git archive separate from the worker worktree.

Run from the installed `autoresearch/` distribution root (AR_HOME). Install locked
Python dependencies with `uv sync --locked`, Hermes v0.21.5, and Nim 2.2.10 first.
Native preparation uses the qualified source pin and receipt checks:

```sh
uv run --locked python games/gota/tools/native_build.py prepare 801749e509e888b423ba44ee2188946c22e3ba85
uv run --locked python games/gota/tools/native_build.py gate_probe 801749e509e888b423ba44ee2188946c22e3ba85
uv run --locked python games/gota/tools/native_build.py gota_match 801749e509e888b423ba44ee2188946c22e3ba85
```

Rohit/the host owner supplies credentials and a downloaded manifest. Its
`game.runnable.source_url` is
`https://github.com/Metta-AI/polyworld/tree/801749e509e888b423ba44ee2188946c22e3ba85/examples/gods_of_the_arena`.
The adapter requires that pin. Bootstrap does not export a manifest.

Set `GOTA_HERMES_PYTHON` to the installed Hermes Python, `GOTA_TIRITH_BIN` to an
executable scanner installed and verified through the official Hermes tooling,
`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `AUTORESEARCH_CLI` to the shared CLI.
The build copies the scanner into an isolated compiler home and synchronously
checks `ensure_installed()` resolves it. That API otherwise starts a daemon download
and returns None, so the scanner is a required provisioning step. No scanner or
security check is disabled. The compiler uses a fresh home without the outer
research profile's plugins/persona and limits writes to compiler scratch.

```sh
uv run --locked python games/gota/tools/build.py /worker/strategy/candidates/E --out /builds/E --manifest /inputs/coworld-manifest.json --json
uv run --locked python -m games.gota.gates /builds/E/policy.bas --manifest /inputs/coworld-manifest.json --baseline tests/fixtures/gota/v13-live.bas --work-dir /checks/E
uv run --locked python games/gota/tools/evaluate.py --results /episodes/results.json --json
uv run --locked python games/gota/tools/evaluate.py /episodes/results.json --seat 0
uv run --locked python games/gota/tools/upload.py E --build-dir /builds/E
```

Output must be new. The candidate STRATEGY/skills/comms/packing inputs must be
committed and clean. Installed development compiler files must also be committed
and clean. Archive compiler files are hashed separately, without claiming their
bytes were committed by the candidate's Git SHA. Finalized failed builds retain
gates/evidence and the shared upload reader refuses them. No real upload or paid
call was performed for this task.

The evaluate binding emits one entry per seat with real seed, validated scores,
XP, outcome and ticks. Real `binding_evaluate` and `episode_rows` consume it.
Shared XP polling still needs subject-seat selection and teammate aggregation
(REQUESTS C3); ten seats must not all become candidate observations or independent
samples. This is an integration blocker outside GotA ownership, not permission to
guess identity in the game adapter.

## Latest live state and validation scope

**Update 2026-10-08 (later commits):** two statements below are superseded. The
adapter no longer rejects a manifest outside the `801749e5` pin: online builds and gates
follow the live engine ([gota-live-engine.md](gota-live-engine.md)); only `--offline`
selects the historical pin. And xp-poll now keeps only the design's seats and reduces
them by the arithmetic mean, one row per episode (`9787d75`,
`src/autoresearch/runtime/xp_poll.py`, contract in `games/_template/GAME.md`).

NOTES-5 reports live 2026.10.8.1 / Polyworld 02eda3d3. Dynamic resolution and
qualification for that release are a separate follow-on. The adapter intentionally
rejects a manifest outside the M1-qualified 801749e5 pin. This does not establish
current live gameplay compatibility. After merging shared 0fa8bb8, `xp_poll._rows`
still loops over every evaluated entry without subject-seat filtering/aggregation;
the NOTES-5 claim that XP picks our seats is not verified by this checkout.

The actual archive/worker integration test uses the real seed and source closure,
real prepare/generate/assemble/finalize, a PATH-isolated fake Hermes, stubbed
security readiness and expensive gates, and the real shared upload reader. It
checks candidate commit/path/strategy hash, policy hash and installed compiler
hashes. It proves binding plumbing, not live gate success or new-engine semantics.
