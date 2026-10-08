---
name: diagnosis
description: Use when explaining a witnessed GotA policy failure.
---

# GotA diagnosis

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-05, D-07, D-10.

Adapted from optimizer-seed `7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`, `games/_template/skills/diagnosis/SKILL.md`
and co-gas `6742feb08f8b119591d09261cadc581065fa698b`, `.agents/skills/coworld-replay-diagnosis/SKILL.md`.
The adaptation supplies GotA instruments and constraints, not a new research system.

## Worker setup

Follow the core `queue` skill first. Run shared commands through the quoted shim.
Resolve tools without changing the worker cwd:

```sh
AR_HOME="$(dirname "$(dirname "$AUTORESEARCH_CLI")")"
AR_PYTHON="$AR_HOME/local/venv/bin/python"
mkdir -p .scratch
```

Use absolute paths for downloaded episode directories and output files below.
Use the installed profile environment; do not substitute a global CLI or Python.
Read `games/gota/GAME.md` and its linked mechanics and replay-format references
under AR_HOME. Current source readback at seed preparation was 2026.10.8.1 /
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`; resolve the current version for new work.
Replay decoding and equivalence tools remain recording-pinned/historical. A live
build qualification does not prove every current replay is supported. Historical
M1 and the v13 seed remain scoped to 2026.10.7.2 / `801749e5`, not a current
Games Bond champion assertion.

## Procedure and outputs

Use after `replay-inspection`, with core `diagnose`. Start with witness evidence,
not a low aggregate score. Check the refuted lever before creating another idea:

```sh
"$AUTORESEARCH_CLI" records find claim --where status=refuted
"$AUTORESEARCH_CLI" records show <C-id>
"$AR_PYTHON" "$AR_HOME/games/gota/tools/replay_stats.py" <ABS_EPISODES> --binary <ABS_RECORDING_EXPANDER> --actions --json <ABS_OUTPUT_JSON>
```

Check cheap causes first: VM/provider/worker failure versus policy behavior;
accepted action versus issued action; target/range legality versus target choice;
last-hit credit versus damage dealt; survival/respawn delay versus farming uptime;
team allocation versus subject XP. Logs alone may infer last hits; require verified
replay counts before calling credit exact. Pin mechanics source, policy version,
seat/team and witness tick. Follow observation -> memory -> chosen action ->
accepted action -> outcome to the earliest source owner.

Write two to four independent explanations using core hypothesis schema:
`statement`, `prediction`, `lever`, `topics`, `source`, `state: proposed`.
For example, lost XP may come from rejected casts, poor timing of accepted casts,
or dead-time; each needs a different witness and falsification. These are
hypotheses until the recording establishes them. Preserve a nearby successful
counterexample and name the distinguishing condition.

```sh
"$AUTORESEARCH_CLI" records new hypothesis --meta-file .scratch/hypothesis.json --body-file .scratch/hypothesis.md --slug <slug>
```

Output includes evidence refs, before metric, mechanism, pinned source location,
predicted metric and bounded next step. If observation coverage is missing,
record the gap and request instrumentation using core `diagnose`; do not claim
that an unobserved behavior failed or reopen a refuted lever without revise_if evidence.
