---
name: eval-design
description: Use when freezing a paired GotA evaluation design.
---

# GotA eval-design

This binding implements System Design §5.4 with the existing core procedures.
Doctrine upheld: D-06, D-08, D-09, D-25.

Adapted from optimizer-seed `7ebea87334331e8410f4c0f2ccf5e2376d81cd1f`, `games/_template/skills/eval-design/SKILL.md`
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

Use with core `experiment` and `calibrate-noise`; do not invent a parallel
research protocol. Resolve the baseline and environment immediately before design:

```sh
"$AUTORESEARCH_CLI" softmax leaderboard
"$AUTORESEARCH_CLI" softmax game-version
"$AUTORESEARCH_CLI" game show
"$AR_PYTHON" "$AR_HOME/games/gota/tools/evaluate.py" <ABS_RESULTS_JSON> --seat 0
```

The example seat is a placeholder for the verified subject seat. Check all ten
finite score/XP values, positive ticks and known terminal outcome. `glory` uses
platform scores (zero for loss/draw/timeout); `win_rate` is team outcome.
`xp_per_1k_ticks` and `team_xp_lead` describe the subject, not ten independent
observations. Repeated teammates share an episode and must be aggregated.

Use leaderboard `current_champion`; when no baseline policy version exists, use
the exact prebuilt bootstrap below in this same card. A copied v13 STRATEGY is
not proof of that identity. Freeze full game version, baseline policy version,
subject seats, fixed opponent refs, unique paired seeds, metrics/directions,
effect thresholds, anti-gaming guardrails and noise revision before results.
Require N >= max(40, calibrated n_min) independent pairs. Calibration must match
the current game version; absent calibration blocks experimentation.

```sh
"$AUTORESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>
"$AUTORESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json
"$AUTORESEARCH_CLI" records validate <E-id>
```

Use the exact core experiment/design JSON schemas. One request per seed and arm,
one episode each; fixed opponents make true pairing, random/top_n selectors give
seed-only pairing. Do not stop early after peeking. Smoke episodes debug only.
A seat-specific question may pin one subject; a team question must preserve and
name the roster, not disguise teammate copies as additional N. Diagnose by
red/blue seat, draft class and opponent composition before pooling.

Continue through core `build-upload` and `xp-request` only within authorized work:
committed semantic STRATEGY -> PW compiler -> G1–G5 receipts; an existing exact
BASIC baseline uses the explicit prebuilt path below;
local gate success proves mechanism, not hosted strength. Failed/partial evidence
cannot support a promotion. This design skill does not itself authorize spending.

## Build tool faults

For either compiler or prebuilt builds, read the build JSON and retained reports
before continuing. A build with `status: errored` is a tool fault, not a strategy
problem. Stop this build path; do not retry the build or rewrite the candidate or
STRATEGY to repair it. Preserve the build directory and receipts.

Follow core `communicate`: write `.scratch/build-error.md` with the exact reported
error (type, message and traceback summary), the absolute build directory, and
what command was attempted. Stage the request through the shared CLI:

```sh
"$AUTORESEARCH_CLI" comms stage help_request --title "GotA build tool fault" --body-file .scratch/build-error.md --expects-response
```

Record the same error, build directory and staged message ID in this card's
journal. Leave the build path blocked pending a tooling fix; do not treat its
error as policy evidence or a strategy repair task.


## Missing baseline: exact prebuilt path in the same card

Read the bootstrap and sidecar contract in `games/gota/GAME.md`. Copy
`$AR_HOME/games/gota/baselines/submitted-v13.bas` into the card's candidate
folder and commit that original input. Use absolute candidate paths:

```sh
"$AR_PYTHON" "$AR_HOME/games/gota/tools/make_v13_live.py" <ABS_CANDIDATE>/submitted-v13.bas <ABS_CANDIDATE>/v13-live.bas
```

Commit both generated `v13-live.bas` and `v13-live.provenance.json`, then build:

```sh
"$AR_PYTHON" "$AR_HOME/games/gota/tools/build.py" <ABS_CANDIDATE> --out <ABS_FRESH_BUILD> --prebuilt <ABS_CANDIDATE>/v13-live.bas --json
```

This resolves the current engine, verifies committed input and transform hashes,
freezes exact bytes and runs G1–G4 without Hermes, model calls or scanner setup.
G4 is a baseline mirror screen. G5 is not executed: its advisory receipt says
`not applicable: prebuilt`, with `passed: false`. Read the retained local gate
reports; use the separate G1–G4 local_check command in GAME.md if required.

**The shared first-install upload handoff is pending:** upload requires a built
experiment and a frozen design naming an existing baseline. Preserve this local
build in the same card; journal the missing bootstrap contract and send a
`help_request` with the build directory. Do not fabricate that baseline/design,
start another card, or use a direct coworld upload. Once the shared owner supplies
the record contract, continue through the shared guarded upload and record its
local version id and experiment `candidate_binding.platform_policy_version_id`.
For an already valid built experiment, the existing upload command is:

```sh
"$AUTORESEARCH_CLI" softmax upload <E-id> --build-dir <ABS_FRESH_BUILD>
"$AUTORESEARCH_CLI" records show <E-id>
```
