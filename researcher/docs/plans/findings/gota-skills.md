# GotA skills — Plan 02 Task 1.5

> **Historical record.** This document records the `autoresearch` harness in `Metta-AI/ide-mvp`. The
> native-Kanban researcher and orchestrator in this repository supersede it, so its paths,
> commands, environment variables and mechanisms (for example the comms inbox and outbox,
> the queue-keeper and other cron jobs, spend management) may no longer exist. For the
> current design, see [orchestrator-implementation-notes.md](../../../../docs/designs/orchestrator-implementation-notes.md).

Five game bindings supply replay inspection, evaluation design, diagnosis, A/B
review and batch survey. They reuse `skills/autoresearch/` procedures and records;
no separate research system or submission route is added.

## Sources and adaptation

Read all five `games/_template/skills/*/SKILL.md` files in optimizer-seed at
`7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`. Their game-binding structure supplies
metrics, rosters, failure vocabulary, decompositions and interesting episodes.
Read co-gas `.agents/skills/coworld-replay-diagnosis/SKILL.md` with `git show` at
`6742feb08f8b119591d09261cadc581065fa698b` (the local checkout HEAD differs).
Its completed-evidence/hash/version/identity gate, causal trace and observation-gap
handling supply the replay discipline. Tools retain their existing lab provenance.

The current engine seed readback is 2026.10.8.1 /
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`; historical M1 is still 2026.10.7.2 /
`801749e5`. Skills do not assert that historical replay/equivalence tooling
supports all current recordings, or that v13 is the current Games Bond champion.

## Documentation audit

Checked every advertised shared command/flag against `cli.build_parser`, and
GotA instrument flags against real `--help` paths. Verified the worker setup
against core `queue` and `runtime/bootstrap.py` (`shim_path`, `shim_text`): the
profile owns `bin/autoresearch` and `local/venv/bin/python`. Absolute tool and
input/output paths keep worker cwd intact. Checked metrics and coverage against
`GAME.md`, `tools/evaluate.py`, `tools/replay_stats.py`, and `tools/compare.py`.
The comparison adapter's independent-sample statistics are explicitly diagnostic;
shared paired `stats evaluate` remains the promotion authority. Schema examples
are delegated to the existing core skills instead of duplicated here.

No seed, M1, compiler or shared runtime changes were needed. New behavior is
instructional and documented directly in the five skills. Existing core game-name
exclusion remains core-only; common frontmatter, doctrine, and shared CLI checks
now include the game bindings. GotA tool conformance runs whitelisted scripts with
`--help` only, never model execution, native compilation or evaluation actions.
Unknown script/flag and missing shared command regressions fail closed.

## Validation

TDD: before the skill files existed, the extended harness failed with the missing
five game skills (1 failed, 135 passed). After adding them, 171 conformance tests
passed. Ruff check and format check passed. See the task report for final focused
validation output and commit identity.
