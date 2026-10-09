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

Use leaderboard `current_champion` as `baseline_version`. Run the starter-policy
bootstrap below in this same card only when both `current_champion` and `conflict`
are null. If `conflict` is open, send a `help_request` and finish blocked. Finish
a successful bootstrap card before designing an experiment in a follow-up card
(core `experiment` step 3).
A copied v13 STRATEGY is not proof of policy identity. Freeze full game version,
champion policy version, subject seats, unique paired seeds, metrics/directions,
effect thresholds, anti-gaming guardrails and noise revision before results.
Take `seats` and `opponent_count` from `## Seats and opponents` in
`games/gota/GAME.md`. `stats design` samples league line-ups at design time and
freezes one per seed in `lineups` with its `league_snapshot`; both arms face the
same line-up in the same slots. Use fixed `opponents` instead of
`opponent_count` only with an `opponent_rationale` explaining the narrow question.
Require N >= max(40, calibrated n_min) independent pairs. Calibration must match
the current game version; absent calibration blocks experimentation.

```sh
"$AUTORESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>
"$AUTORESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json
"$AUTORESEARCH_CLI" records validate <E-id>
```

Use the exact core experiment/design JSON schemas. One request per seed and arm,
one episode each; sampled line-ups (or fixed refs with a rationale) give true
pairing, random/top_n selectors give seed-only pairing. Do not stop early after
peeking. Smoke episodes debug only.
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


## Missing champion: exact prebuilt bootstrap in the same card

Read `## Starter policy` and the sidecar contract in `games/gota/GAME.md`.
Follow its installed-profile commands from this card's worktree: copy
`$AR_HOME/games/gota/baselines/submitted-v13.bas` to
`strategy/candidates/bootstrap/submitted-v13.bas` and commit the original input
in this worktree. Generate v13-live, commit both `v13-live.bas` and
`v13-live.provenance.json` here, then build into this worktree's
`.scratch/build/champion`. Use absolute candidate/output paths and run the game
bindings from `"$AR_HOME"` with `"$AR_PYTHON"` as GAME.md shows.

This resolves the current engine, verifies committed input and transform hashes,
freezes exact bytes and runs G1–G4 without Hermes, model calls or scanner setup.
G4 is a baseline mirror screen. G5 is not executed: its advisory receipt says
`not applicable: prebuilt`, with `passed: false`. Read the retained reports and
run the separate G1–G4 `local_check` command in GAME.md before bootstrap.
Both the build and separate full local check have the approximately 55-minute
G3/G4 workload reported on EC2 in GAME.md; run each with `background: true`
and wait on its own `session_id` as **Long commands** says. After each exits,
read the build's `report.json`, `report.md`, `version.json`, `gates.json` and
provenance, then the check's `gates.json`, `engine_identity.json`,
`coworld_manifest.json`, native measurement receipts and G2 results/logs before
bootstrap. An errored build follows **Build tool faults** above.

Follow the core `submit` skill's "Bootstrap card" checks, then:

```sh
"$AUTORESEARCH_CLI" softmax bootstrap-champion --build-dir .scratch/build/champion
```

This uploads and submits the first champion through the normal promotion path,
without an experiment, frozen design or evaluation outcome. The first league
submission is v13-live, unevaluated. Bootstrap never uses `softmax upload`.
Record `data.version_id`, `data.platform_policy_version_id`, `data.promotion_id`
and `data.submission_id` in the journal's Decisions and send the core `submit`
highlight. Request the follow-up experiment as core `experiment` step 3 says,
then finish through `complete-card`, outcome `done`; this card's merge publishes
the promotion. The follow-up designs against that champion. Use the core `submit`
refusal/recovery instructions if bootstrap does not succeed.
