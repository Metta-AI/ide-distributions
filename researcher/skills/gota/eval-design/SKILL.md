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

Follow the core `card` skill first. Run shared commands through the quoted shim.
Resolve tools without changing the worker cwd:

```sh
RESEARCH_HOME="$HERMES_HOME"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
mkdir -p .scratch
```

Use absolute paths for downloaded episode directories and output files below.
Use the installed profile environment; do not substitute a global CLI or Python.
Read `games/gota/GAME.md` and its linked mechanics and replay-format references
under RESEARCH_HOME. Current source readback at seed preparation was 2026.10.8.1 /
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`; resolve the current version for new work.
Replay decoding and equivalence tools remain recording-pinned/historical. A live
build qualification does not prove every current replay is supported. Historical
M1 and the v13 seed remain scoped to 2026.10.7.2 / `801749e5`, not a current
Games Bond champion assertion.

## Long commands

Use the core `card` skill's **Long commands** procedure for episode runs, Nim
replay-expander builds (including `replay_stats.py --ref`), replay inspection,
`gota_local.py` and `gota_episodes.py`, and any build or local check below.
After exit, read the exit code, command output and retained evidence before
interpreting the results. Build/check reports are named in GAME.md's
**Commands** section. Replay inspection writes the requested `<ABS_OUTPUT_JSON>`
and each decoded episode's `expanded-replay.jsonl` and
`expanded-replay.cache.json`; check their hashes, coverage and `errors`.

## Procedure and outputs

Use with core `experiment` and `calibrate-noise`; do not invent a parallel
research protocol. Resolve the baseline and environment immediately before design:

```sh
"$RESEARCH_CLI" softmax leaderboard
"$RESEARCH_CLI" softmax game-version
"$RESEARCH_CLI" game show
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/gota/tools/evaluate.py" <ABS_RESULTS_JSON> --seat 0
```

The example seat is a placeholder for the verified subject seat. Check all ten
finite score/XP values, positive ticks and known terminal outcome. `glory` uses
platform scores (zero for loss/draw/timeout); `win_rate` is team outcome.
`xp_per_1k_ticks` and `team_xp_lead` describe the subject, not ten independent
observations. Repeated teammates share an episode and must be aggregated.

Use leaderboard `current_champion` as `baseline_version`, never another uploaded
version (`stats design` refuses it: `baseline_not_champion`). If no champion exists,
report that the orchestrator needs to commission the explicit bootstrap card below
before this experiment. If `conflict` is open,
send a `help_request:` comment and block. Do not bootstrap during an experiment card.
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
"$RESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>
"$RESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json
"$RESEARCH_CLI" records validate <E-id>
```

Use the exact core experiment/design JSON schemas. One request per seed and arm,
one episode each; sampled line-ups (or fixed refs with a rationale) give true
pairing, random/top_n selectors give seed-only pairing. Do not stop early after
peeking. Smoke episodes debug only.
A seat-specific question may pin one subject; a team question must preserve and
name the roster, not disguise teammate copies as additional N. Diagnose by
red/blue seat, draft class and opponent composition before pooling.

Continue through core `build-upload` and `xp-request` only within authorized work:
committed semantic STRATEGY -> PW compiler -> G1–G5 reports; an existing exact
BASIC baseline uses the explicit prebuilt path below;
local gate success proves mechanism, not hosted strength. Failed/partial evidence
cannot support a promotion.

## Rejected build inputs

A build with `status: rejected` and `error.reason: invalid_input` is a correctable input refusal, not a tool fault. Read `error.details.path` and `error.details.action`, correct and commit truthful inputs within the card scope, then retry with a new output directory. Preserve the rejected report; do not overwrite it, invent provenance, or bypass a check. If correction needs unavailable facts or authority, request help and block. This explicit result takes precedence over generic CLI-failure handling.

## Build tool faults

For either compiler or prebuilt builds, read the build JSON and retained reports
before continuing. A build with `status: errored` is a tool fault, not a strategy
problem. Stop this build path; do not retry the build or rewrite the candidate or
STRATEGY to repair it. Preserve the build directory and reports.

Follow core `report`: send a `help_request:` comment on your own card with the
exact error (type, message and traceback summary), the absolute build directory,
and the attempted command. Record the same facts in the journal's Decisions.
Use `card`'s tool-fault handoff; leave the build blocked pending a tooling fix.
Do not treat its error as policy evidence or a strategy repair task.

## Explicit bootstrap card: exact prebuilt starter

Use this section only when assigned a bootstrap card. Follow the core `candidate`
skill's bootstrap checks, then build the starter as follows.

Read `## Starter policy` and the sidecar contract in `games/gota/GAME.md`.
Follow its installed-profile commands from this card's worktree: copy
`$RESEARCH_HOME/games/gota/baselines/submitted-v13.bas` to
`strategy/candidates/bootstrap/submitted-v13.bas` and commit the original input
in this worktree. Generate v13-live, commit both `v13-live.bas` and
`v13-live.provenance.json` here, then build into this worktree's
`.scratch/build/champion`. Use absolute candidate/output paths and run the game
bindings from `"$RESEARCH_HOME"` with `"$RESEARCH_PYTHON"` as GAME.md shows.

This resolves the current engine, verifies committed input and transform hashes,
freezes exact bytes and runs G1–G4 without Hermes, model calls or scanner setup.
G4 is a baseline mirror screen. G5 is not executed: its advisory report says
`not applicable: prebuilt`, with `passed: false`. A completed build includes local
validation; do not run G1–G4 again for the same artifact and execution conditions.
Follow `GAME.md`'s **Local validation** contract: inspect the complete retained
reports, provenance, native measurements, and G2 episode evidence before upload.
All required gates and their budgets remain unchanged. An errored build follows
**Build tool faults** above. A failed or incomplete build does not qualify for reuse.

After the required checks pass, upload the starter:

```sh
"$RESEARCH_CLI" softmax upload-build --name opening-rush --build-dir .scratch/build/champion
```

This uploads v13-live without an experiment, frozen design or evaluation outcome.
It is unevaluated and is not yet the champion. Record `data.version_id` and
`data.platform_policy_version_id` in the journal's Decisions and handoff metadata.
Follow `candidate` and `card`: integrate, then complete with `experiment: null`
and recommend the orchestrator submit this starter before commissioning an
experiment against it. The researcher never submits. Use `candidate`'s failure
handling if the upload does not succeed.
