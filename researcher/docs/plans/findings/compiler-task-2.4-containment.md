# Task 2.4: unintended agent dispatch and containment

Status: **STOPPED; ruling required before resuming Task 2.4.**
Base: `8a2e9e740b4d3ee5e56f7199a70bd9bf0fdbbeda`.
Date: 2026-10-07. Times below are Pacific Time.

## Observation and cause

Task 2.4 authorized fake Hermes tests and local implementation only. Its initial
red test prepended a fake `hermes` binary to the inherited PATH and called the
existing generation seam with agent `hermes`. The old command selector handled
Claude explicitly and returned a Codex command for every other string. Thus the
unsupported Hermes name launched the installed real Codex, bypassing the fake.
The test did not adequately isolate executable resolution before testing an
unimplemented backend. That was the containment failure.

Seven Codex v0.160.1 CLI processes started at approximately 16:15:28–16:15:41.
Six logged status-400 `invalid_request_error` responses saying the test model
`test/openweight` was unsupported with a ChatGPT account; each log contains the
error twice. The seventh logged `turn interrupted` after the test was stopped.
These logs establish request attempts, not successful model generation. They do
not establish how many network requests occurred or whether any cost accrued.
No model answer, tool output, or generated unit was observed.

The unintended command was:

```text
codex exec --ignore-user-config --ephemeral --sandbox workspace-write \
  --model test/openweight -C <pytest scratch repository> -
```

Stdin was the ordinary generic compiler prompt. The working directories were
seven temporary independent Git repositories under the test run's `pytest-605`
directory. The test parent exited 130 on interruption. Exact child exit codes
were not retained. The temporary directories were cleaned by the existing
context manager and no stage unit outputs were present. A process/cwd inspection
found no residual child from this test; unrelated existing Codex sessions were
left alone. No broker call or explicit credential read was made by the task.
The real CLI used its existing account context; this was not authorized.

## Containment

- The parent stopped broader Task 2.4 work immediately after the incident report.
- `agent_command` now raises `UsageError` for every name except `claude` and
  `codex`. Prepare validates the backend before looking up its model.
- Regression tests check command dispatch and run the real scratch-generation
  setup for unsupported names. Their PATH contains only explicit local Git and
  Python symlinks and a fake Hermes executable; a second subprocess allowlist
  rejects any attempt to invoke another command. They verify no agent launches
  and no generated output or agent log is created.
- The unfinished desired-Hermes tests were moved to
  `/tmp/ide-mvp-codex-task-2.4-pending-tests.py`, retaining the isolated harness
  for later authorized work. They are not committed or masked as passing tests.
- Diagnostic copies and their metadata are retained locally under
  `/tmp/ide-mvp-codex-task-2.4-incident/`. No credential values are in this finding.

This is a narrow guard fix; the generation snapshot, Git HEAD/refs/index checks,
regular-output checks, existing backend argv, and supported agents are unchanged.
Hermes remains unsupported. No real M1, engine simulation, source decomposition,
equivalence tool, or goal-audit adapter was implemented.

## Validation and documentation audit

From `autoresearch/`, the focused validation was:

```sh
uv run --locked --offline --with pandas pytest \
  tests/unit/gota/compiler/test_agent_dispatch.py \
  tests/unit/gota/compiler/test_strategy_build.py -q
# 25 passed
```

The pandas overlay is the existing provisional local dependency arrangement;
no dependency or lockfile changed. Ruff passes for the new test file;
`git diff --check` and relative documentation link checks also pass. The audit-documentation skill was applied to
this guard change. The backend guide, this finding, and compiler provenance now
state the exact supported-agent boundary and stopped Task 2.4 status. No external
pricing or account checks were made; spend remains unverified.


## C2 acceptance and local resumption

The user accepted containment in VERDICT-C2 and authorized Task 2.4 local-only
resumption. The historical account above is preserved. A session-wide Python
process audit guard now blocks real named agent executables (including symlink
resolution) and shell command strings; dispatch tests additionally restrict PATH
and process calls to local Git/Python and the fake backend. Hermes is now an
explicit supported backend; unknown values still fail closed. Tests never invoke
a real agent version probe. No subsequent model/broker/credential action was performed by the implementer.
The parent later used the NOTES-2-authorized isolated credential context for live
version verification/download only, with no model/broker/spend action.

Shared dependency work locked pandas and merged at ee140c52; the provisional
pandas overlay above describes only the containment test run. The reviewer
assessed all seven earlier requests as server400/no generation and effectively
no spend. Independent provider billing was not queried, and that assessment is
not a new billing verification. The resumed implementation and evidence are in
[the backend guide](../../../games/gota/compiler/docs/gota-backend.md) and compiler
PROVENANCE. Real M1 remains blocked on OpenRouter credit.
