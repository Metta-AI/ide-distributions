---
name: survey
description: Use when surveying completed GotA episode batches.
---

# GotA survey

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-04, D-07, D-17.

Adapted from optimizer-seed `7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`, `games/_template/skills/survey/SKILL.md`
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

Use core `league-watch`, `opponent-profile` or `meta-recon` for scouting and core
`replay-diagnosis` for a witnessed failure. Survey completed evidence first:

```sh
"$AUTORESEARCH_CLI" records find evidence --where kind=xp-result
"$AUTORESEARCH_CLI" softmax leaderboard
"$AR_PYTHON" "$AR_HOME/games/gota/tools/evaluate.py" --results <ABS_RESULTS_JSON> --json
"$AR_PYTHON" "$AR_HOME/games/gota/tools/replay_stats.py" <ABS_EPISODES> --binary <ABS_RECORDING_EXPANDER> --json <ABS_OUTPUT_JSON>
```

Build a finding-first table: episode/request, full game version, stable subject
and opponent policy IDs, seed, observed seat/team/draft, terminal status, glory,
win, XP per 1k ticks and team XP lead. Verify hashes and decoding coverage before
using replay fields. Keep failed/provider incidents visible as a separate count;
missing metrics stay missing. Do not invent values to complete the table.

Split by version, red/blue side, opponent roster and verified draft class. Flag
zero glory with high XP (loss), low XP with deaths/respawn gaps, accepted actions
without expected credit, close wins and repeated failure clusters. A flag chooses
a witness for inspection; it is not the cause. List sample size and teammate
aggregation; sparse subgroups and shifting field composition weaken conclusions.

```sh
"$AUTORESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>
"$AUTORESEARCH_CLI" records new lesson --meta-file .scratch/lesson.json --body-file .scratch/lesson.md
```

Use core claim schema with tiered evidence and revise_if. Keep observations
separate from hypotheses; one replay is a belief. For a research mistake use core
`lessons` schema: imperative lesson, status tentative, scar with incident ref,
recurrences count 1 and refs. Imported failed-experiment red flags remain tentative
with provenance until independent recurrence; do not promote them to game laws.
The survey output names the interesting episode, why, what remains unknown and
the next bounded investigation. It does not establish a current champion or
competitive improvement from historical summaries.
