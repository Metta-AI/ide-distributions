---
name: ab
description: Use when reviewing paired GotA candidate evidence.
---

# GotA ab

This binding implements System Design §5.5 with the existing core procedures.
Doctrine upheld: D-06, D-09.

Adapted from optimizer-seed `7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`, `games/_template/skills/ab/SKILL.md`
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

Use core `ab-compare` and `review-xp` for the decision. Check completed status,
verified hashes and recording versions, stable player/policy IDs, subject seats,
matched seeds/opponents, and failed episode counts before calculating means.
Aggregate teammates per episode; never count repeated copies as independent N.

```sh
"$AUTORESEARCH_CLI" softmax xp status <E-id>
"$AR_PYTHON" "$AR_HOME/games/gota/tools/compare.py" <ABS_BASELINE_EPISODES> <ABS_CANDIDATE_EPISODES> --baseline <NAME:vN> --candidate <NAME:vN> --group team --target xp_mean --baseline-replay-stats <ABS_BASELINE_STATS_JSON> --candidate-replay-stats <ABS_CANDIDATE_STATS_JSON> --json <ABS_DIFF_JSON>
"$AUTORESEARCH_CLI" stats ingest --experiment <E-id> --rows .scratch/<E-id>-rows.jsonl
"$AUTORESEARCH_CLI" stats evaluate <E-id>
```

The comparison instrument is diagnostic: it uses independent-sample statistics,
not the shared frozen paired promotion rule. Its NAME:vN labels must be joined to
stable platform IDs. Use recording-compatible verified replay JSON for exact
kill/death/last-hit counts; without it telemetry is inferred. `--group class` or
`--group role` requires verified draft enums and sufficient per-group evidence.
Do not substitute `winning_xp_mean` or guessed tick frequency for platform glory.

Read ingest exclusions (failed, missing_metrics, wrong_version/seats/seed), then
pair count, signed changes, confidence intervals and failures from shared stats.
Require the preregistered N >= max(40, calibrated n_min), performance guardrails
glory/win_rate, primary thresholds and current champion baseline. All requests
must be terminal; partial or failed coverage is invalid/hold, never promoted by
an attractive subgroup. Unknown champion or changed baseline requires reevaluation.

```sh
"$AUTORESEARCH_CLI" records new result --meta-file .scratch/result.json --body-file .scratch/result.md
```

Use core `review-xp` Result schema; record raw scope/identities, pairing strength,
units, exclusions, outcome, activation witness and links to experiment/evidence.
Separate observed mechanism activation from competitive improvement. Never hand
edit shared stats outcomes, upload, or submit from a diagnostic tool verdict.
