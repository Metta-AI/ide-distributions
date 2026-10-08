# Task 2.1: pinned compiler source differs from Plan 02

Initial status: blocked before vendoring; resolved by VERDICT-C1 amendment below. Inspected on 2026-10-07 against committed
`personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84`.
Autoresearch base: `91071d0b7761febd11ed37172f35627e7a216e9b`.

Only committed source was read. `git archive` extracted `paintbot_pw_lab/`,
`pyproject.toml`, and `.python-version` to `/tmp/ide-pw-6b0bffa5-baseline`.
The source checkout was not changed. No real agent, model, API, hosted episode,
or build was invoked.

## Source mapping

Source paths below are relative to `paintbot_pw_lab/` at the pinned commit.

| Plan 02 file or contract | Actual source |
| --- | --- |
| `strategy_format.py` | `tools/strategy_format.py` |
| `strategy_basic.py` | `tools/strategy_basic.py`; imports `strategy_comms.py` at lines 31–32 |
| `strategy_build.py` | `tools/strategy_build.py`; imports `pw_cli` and `pw_release` at lines 14–15 |
| `pw_cli.py` | `tools/pw_cli.py`; imports `pw_release` at line 44 |
| `strategy_report.py` | No such file in the complete pinned Git tree |
| Build reports | `tools/strategy_build.py:427–461`, `finalize`, writes `report.json` and `report.md` |
| Compile orchestration | `tools/pw_strategy.py:parser/run`; not `pw_cli.py` |
| Five-level semantic report | `tools/strategy_audit.py`, levels at line 18, `report_markdown` at line 215, output at line 351; reconstruction in `strategy_audit_runtime.py` |

The pinned `docs/strategy-compiler-maintainers.md:3–5` explicitly states that no
generic Polyworld backend or game-adapter API exists. Its ownership table and
generalization checklist describe the couplings, rather than an already extracted
pipeline. Its input-inventory prose still names `reference/base.bas`, which is
stale against the pinned implementation. The missing report module is therefore not a simple renamed counterpart:
build finalization and semantic auditing have different inputs and responsibilities.

## Dependency and input closure

The four existing named modules require `strategy_comms.py` and `pw_release.py`
for import. The compile driver also requires `pw_strategy.py` and
`strategy_gates.py`. Gates import `pw_local.py`, `pw_terrain.py`, and
`pw_release.py`; `pw_local.py` imports NumPy. Telemetry and semantic auditing add
`pw_intent.py`, `pw_episodes.py` (both import pandas), `strategy_audit_runtime.py`,
and reviewed Paintbot baseline/comms evaluators. These are source dependencies,
not missing third-party packages that can replace an absent local module.

`strategy_build.source_files:54–66` inventories the compiler directory, all
`strategy_*.py`, source skills, optional `comms.md`, dispatcher, intent/local/
release/terrain/CLI tools, `release.env`, the Paintbot policy surface, and
`reference/base-bassy-28030de6.bas`. Paths/build roots at lines 17–21 remain
Paintbot-bound. Runtime and agent instructions are also compilation inputs.
Copying the five planned names alone cannot preserve the original pipeline.

## Unchanged fixture baseline

Used project-locked Python **3.12.11** with no dependency changes:

```bash
cd /Users/jamesboggs/coding/ide-mvp-codex/autoresearch
uv run --locked python -m pytest \
  /tmp/ide-pw-6b0bffa5-baseline/paintbot_pw_lab/tools/tests/test_strategy_format.py \
  /tmp/ide-pw-6b0bffa5-baseline/paintbot_pw_lab/tools/tests/test_strategy_build.py \
  /tmp/ide-pw-6b0bffa5-baseline/paintbot_pw_lab/tools/tests/test_strategy_basic.py \
  -q -k 'not runtime_matches_reference_in_engine and not belief_lines_in_engine and not shout_and_pwc_in_engine and not kill_switch_silences_every_line'
```

Result: **2 failed, 62 passed, 5 deselected, 1 warning**. Tests and Paintbot
fixtures were unchanged. The warning is an unknown `asyncio_mode` option in the
source test configuration. Deterministic failures:

1. `test_strategy_build.py:111` expects `screen_gate(screen(.51, .6), 'm1')`
   to fail. Pinned `strategy_gates.py:38–39` accepts a complete interval with
   `high >= 0.5`, so it passes. This is source/test disagreement.
2. `test_strategy_build.py:192` creates `reference/base.bas`; `source_files`
   requires `reference/base-bassy-28030de6.bas`. The test fails at line 209 with
   `UsageError: input must be a regular file`.

An initial run without deselection gave **7 failed, 62 passed**. Five failures
were engine-test imports of pandas, which is absent from the locked destination
environment. No engine or hosted call ran. Those five are excluded from the
deterministic baseline above; this is not engine qualification.

Follow-up preflight used `uv run --locked --with pandas` with the same full
three-file command and no `-k` filter. The isolated overlay installed pandas
**3.0.6**, without changing project dependencies or lockfiles. Before running,
the archived `tools/.cache/coworld-v0.3.124/tmp/paintbot-coworld` was confirmed
absent. Result: **2 failed, 62 passed, 5 skipped, 1 warning**; all five engine
tests reached their existing missing-engine skips. This resolves the environment
import failures, but does not prove final project dependencies or engine behavior.

The current command reference, `docs/tools/pw_strategy.md:94–98`, documents
M1 `high >= 0.5` and agrees with implementation. The older compilation design
at line 294 and the test require an interval containing 0.5. Recommend preserving
the implemented/current command-reference screen and correcting the stale test
in the destination after a ruling. This screen does not establish Task 2.4's
separate action-stream equivalence claim. No expectation was edited.

## Independent review

`/tmp/ide-mvp-codex-task-2.1-review.md` independently verified the absent module,
report ownership, dependency paths, both source/test contradictions, and unchanged
archive bytes against the source commit. The reviewer did not rerun the tests;
execution counts above come from the implementer's recorded runs.

## Minimal ruling needed

Approve a corrected Task 2.1 inventory using the actual pinned pipeline and its
required local/runtime inputs with provenance. Retain build reporting in
`finalize`; retain or explicitly stage `strategy_audit` and its dependencies for
the later required five-level goal attribution. Do not invent a
`strategy_report.py` alias or stub.

Replace the impossible zero-failure unchanged-baseline requirement with the
recorded untouched baseline of two source test defects, followed by isolated
destination test corrections: use `base-bassy-28030de6.bas` in the reference
fixture and assert the current documented/implemented M1 screen rule. Keep the
pin, source implementation, and untouched archive evidence. This requests an
inventory/acceptance amendment, not a gate redesign or real LLM build.

No compiler source, ported tests, or task completion commit was produced. Task
2.2 and later tasks remain unstarted.

## Approved C1 resolution and destination result

VERDICT-C1 approved the actual source closure, staged semantic-audit ownership,
and the two destination-only expectation corrections. The initial block above is
historical evidence. Implementation retains actual source names under
`games/gota/compiler/tools/` and lab-relative runtime/input paths. No aliases or
stubs were added. `pw_release.REPO` now points to the enclosing checkout rather
than `games/gota`; destination tests point to the vendored tools. The reference
BASIC remains byte-identical with sidecar provenance. All copies and original
hashes are in `games/gota/compiler/PROVENANCE.md`.

The audit-baseline import of `pw_scout.wilson` requires `pw_scout`, `pw_fights`,
`pw_metrics`, and `pw_public`; these are included in the staged closure. No
historical evidence corpus or native binary was copied. Audit qualification
remains separate, especially because provenance comments alter runtime hashes.

Ported tests plus four new local-import/input-inventory/config/gate-byte checks:
**68 passed, 5 skipped** with `uv run --locked --with pandas pytest
 tests/unit/gota/compiler -q` (run from autoresearch). Shared pandas dependency
is still pending; the isolated overlay is provisional. New code passes Ruff.
Broad Ruff reports **116 inherited findings**, identical in count and every
rule bucket to unchanged pinned source under the same destination config. No
suppression or configuration changes were made; preserved source modes explain
14 inherited EXE001 findings. No full lint-green claim is made.

Documentation audit verified copied-file provenance, actual module/report
ownership, exact reference exception, source root correction, and limitations
against source/tests. Current pinned command reference agrees with high >= .5;
the older design/test and maintainer reference basename are stale. No policy
behavior change, native/model execution, or remote action occurred. Task 2.2
remains unstarted.
