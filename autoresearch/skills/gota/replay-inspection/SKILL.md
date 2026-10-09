---
name: replay-inspection
description: Use when inspecting completed GotA replay evidence.
---

# GotA replay-inspection

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-04, D-05, D-07.

Adapted from optimizer-seed `7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`, `games/_template/skills/replay-inspection/SKILL.md`
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

## Long commands

Use the core `queue` skill's **Long commands** procedure for episode runs, Nim
replay-expander builds (including `replay_stats.py --ref`), replay inspection,
`gota_local.py` and `gota_episodes.py`, and any build or local check below.
Run in the foreground with terminal `timeout` at most 600 seconds only when
known to fit. Otherwise start with terminal `background: true`, keep the
`session_id`, then call `process_manage` with `action: "wait"`, that `session_id`
and `timeout: 1800`. Repeat `wait` only on `status: "timeout"`, with nothing
between waits. No polling, repeated log calls or sleep/probing loops.
After exit, read the exit code, command output and retained evidence before
interpreting the results. Build/check receipts are named in GAME.md's
**Commands** section. Replay inspection writes the requested `<ABS_OUTPUT_JSON>`
and each decoded episode's `expanded-replay.jsonl` and
`expanded-replay.cache.json`; check their hashes, coverage and `errors`.

## Procedure and outputs

Use with core `replay-diagnosis`, before a policy correction. Find completed
requests and retrieve artifacts using its evidence procedure:

```sh
"$AUTORESEARCH_CLI" softmax xp status <E-id>
"$AUTORESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>
"$AR_PYTHON" "$AR_HOME/games/gota/tools/replay_stats.py" <ABS_EPISODES> --binary <ABS_RECORDING_EXPANDER> --actions --snapshot-every 100 --json <ABS_OUTPUT_JSON>
```

Use an expander with a receipt matching the replay's full recording commit. The
alternative `--ref <FULL_RECORDING_COMMIT>` builds a pinned expander with the
installed Nim 2.2.10 toolchain; it is not a request to decode with the live engine.
Verify replay SHA-256 against the evidence index, terminal episode status, game
version, and stable player/policy-version to seat mapping (red 0–4, blue 5–9).
Check nonempty parsed output and its integrity/coverage fields; missing fields or
a decoder failure are an observation gap. Never replace missing counts by zeros.

Inspect the earliest divergence: actor-visible target/range, issued action,
accepted execution, XP credit, death and subsequent progress. Analyst state can
show facts hidden from the policy; do not blame a policy for hidden information.
Distinguish exact counts from inferred log telemetry and record the integrity
method (state hashes, terminal reproduction, or trace comparison).

Persist the immutable replay through core evidence metadata (hash, local durable
path, game version, policy_versions, episode/xreq IDs and fetched_at):

```sh
"$AUTORESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json
"$AUTORESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>
```

The claim body names before metric, witness tick, source owner at a pinned commit,
nearby counterexample and coverage gaps. Follow the core claim schema and tiers:
a single replay supports a belief, not competitive strength. On an unexplained
failure request instrumentation through core `replay-diagnosis`; do not edit strategy.
