---
name: pudge-wars-basic-eval-design
description: Use when sizing a Pudge Wars Basic evaluation.
---

# Pudge Wars Basic eval-design

This binding implements System Design §5.4 with the existing core procedures.
Doctrine upheld: D-04, D-06, D-08, D-09, D-25.

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/eval-design/SKILL.md`, from the GotA `eval-design` binding in this
repository, and from the upstream mixin `games/pudge-wars/skills/eval-design/README.md`
at `Metta-AI/polyworld-pudge-wars@cd8111a5`. The core `experiment` and `calibrate-noise`
skills freeze a design (`stats design`); this skill names the game's knobs.

## Worker setup

Follow the core `card` skill first. Run shared commands through the quoted shim.
Resolve tools without changing the worker cwd:

```sh
RESEARCH_HOME="$HERMES_HOME"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
mkdir -p .scratch
```

Use absolute paths for episode directories, replays and output files below. Use the
installed profile environment; do not substitute a global CLI or Python. Run every game
tool from `"$RESEARCH_HOME"` with `"$RESEARCH_PYTHON"` as shown. Read
`games/pudge-wars-basic/GAME.md` and its linked `docs/mechanics.md`,
`docs/replay-format.md` and `docs/game-guide.md` under RESEARCH_HOME. This folder
targets Pudge Wars Basic only: league `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0`,
Coworld `pudge-wars-basic`. The knowledge here was written for version 0.1.8
(`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source `Metta-AI/polyworld-pudge-wars` at
`cd8111a5`, replay game version 38. The tools follow the live league by themselves: every
envelope's `engine` block names the version and commit that ran (binaries for a new commit
are built on first use). If `engine.live` is false the league could not be checked and the
run may be stale: say so in the record. If `knowledge.current` is false, treat
game-specific claims here as possibly stale and propose the maintenance card the
`knowledge.message` names (`kb renew ...`).

## Long commands

Use the core `card` skill's **Long commands** procedure for native builds
(`tools/native.py`), local episodes (`tools/match.py`), `tools/local_check.py`,
`tools/branch.py` and replay inspection of more than a few episodes. A league-length
local 3v3 takes about a second of simulation per episode plus decoding; a first native
build fetches the source and every `nimby.lock` dependency. After exit, read the exit
code, the JSON envelope (`ok`, `error.reason`) and the files the command wrote before
interpreting anything. Exit 1 is a failed check, 2 a rejected input, 3 a tool fault.

## Sensible line-ups

An episode has 6 seats: slots 0–2 are West, 3–5 East, contiguous teams. Take `seats` and
`opponent_count` from `## Seats and opponents` in `games/pudge-wars-basic/GAME.md`.

| Question | `seats` | Other seats | Why |
|---|---|---|---|
| Does the candidate play better as a teammate in the real field (default) | one subject seat, e.g. `[0]` | 5 sampled league policies (2 teammates, 3 enemies) | mirrors the league, where our champion fills one seat per episode |
| Does a whole team of the candidate beat the field (team coordination changes: lanes, rescue, healing) | `[0, 1, 2]` | 3 sampled enemies | coordination needs copies; aggregate the three seats per episode |
| Does it beat a named policy | as above | fixed `opponents` with an `opponent_rationale` | narrow question only |
| Crash and budget test | all seats the candidate | none | local `match.py` / `local_check`, never XP evidence |

Never mix line-up types inside one experiment. A one-seat design's team `win` depends on
two sampled teammates; use the subject's `deaths`, `kills` and `hook_landed_rate` as
primaries and `win`/`team_kill_margin` as guardrails.

## Episode-count floors

From 1,152 league seats (192 episodes, 2026-10-26), per seat-episode SDs: kills 3.7,
deaths 3.4, landed/thrown 9.1 points, win 0.48, team margin 12.3.

| Use | Count | Basis |
|---|---|---|
| Smoke (does it run, does the mechanism fire) | 2–10 local episodes, both sides | `local_check` G2/G3; debugging only (D-04) |
| Directional | < 100 episodes per arm | below `min_episodes_floor` |
| Verdict on win ±20 points / deaths ±1.3 | ≥ 100 pairs | two-arm normal approximation, α 0.05, power 0.8 |
| Verdict on deaths ±1.0 or accuracy ±3 points | ~150–180 pairs | same |
| Verdict on win ±10 points | ~390 pairs | rarely affordable; prefer per-seat metrics |

The calibrated `n_min` from `stats design` (noise record for the current version) wins
when larger. If no noise record exists, `stats design` refuses; follow
`calibrate-noise` (league rows come from `softmax league-episodes`). Do not use
`coworld power-analysis` here: it models the division as a free-for-all.

## Seat pinning

- One subject seat on a fixed slot is the right default: the league shows no side or
  slot effect (West 50.5% of 192; mean kills by slot 7.7–9.1), and per-seed pairing keeps
  the line-up identical across arms.
- Pin all three West seats only when the change is about coordination; it hides how the
  candidate plays beside other policies.
- **Side balance:** a fixed seat is always West. A change that mirrors coordinates by
  team (`riverWest`/`riverEast`, `homeX`, lane maths) can be right on one side and wrong
  on the other; check both sides locally (`local_check` runs seeds × sides) before
  spending XP, and report per-side results when the design allows them.
- Seeds: unique integers, one pair each; never reuse the upstream used-evidence seed lists
  in `docs/game-guide.md`.

## Pacing

- A hosted 3v3 episode took ~10 s wall and cost about $0.003 (one observation,
  2026-10-26); 100 pairs is about 200 episodes. Game length ≈ 7 game-minutes (median
  13.4k ticks), always to 30 kills.
- League rounds run every 32 minutes; XP requests are separate from rounds but the field
  shifts as players resubmit (`auto_champion: always`). Launch both arms together and do
  not compare an arm run today with one run against last week's field.
- Never wait on XP inside a session (D-11); never stop early after peeking (D-09).
- Local `match.py`: about a second per league-length episode; use `--jobs` for sweeps.

## Procedure and outputs

Use with core `experiment` and `calibrate-noise`. Resolve the baseline and environment
immediately before design:

```sh
"$RESEARCH_CLI" softmax leaderboard
"$RESEARCH_CLI" softmax game-version
"$RESEARCH_CLI" game show
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/evaluate.py" <ABS_RESULTS_JSON> --seat 0
```

The seat is a placeholder for the verified subject seat. Check that the results have
six seats, a known `winner_team` and `end_reason`, `end_tick` > 0 and an integer `seed`.
Use leaderboard `current_champion` as `baseline_version` (`stats design` refuses another:
`baseline_not_champion`). If no champion exists, report that the orchestrator needs the
bootstrap card below. Freeze game version, champion, seats, seeds, metrics with
directions and minimum effects, guardrails `win` and `team_kill_margin`, and the noise
revision before any result. One conceptual change per experiment (D-08).

```sh
"$RESEARCH_CLI" records new experiment --meta-file .scratch/experiment.json --body-file .scratch/experiment.md --slug <slug>
"$RESEARCH_CLI" stats design <E-id> --request-file .scratch/design-request.json
"$RESEARCH_CLI" records validate <E-id>
```

Make the candidate in the card worktree: copy `strategy/STRATEGY.md` and
`strategy/policy.bas` to `strategy/candidates/<E-id>/`, make the one change in
`policy.bas`, and describe it in that `STRATEGY.md` (what changed, which component, the
expected metric movement). A neural candidate is a package directory
`strategy/candidates/<E-id>/package/` with exactly one `.bas`. Then build and check
locally (absolute paths; run from RESEARCH_HOME):

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/build.py" --source <ABS_WORKTREE>/strategy/candidates/<E-id>/policy.bas --strategy <ABS_WORKTREE>/strategy/candidates/<E-id>/STRATEGY.md --out <ABS_BUILD_DIR>
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/local_check.py" <ABS_BUILD_DIR>/policy.bas --baseline <ABS_WORKTREE>/strategy/policy.bas --work-dir <ABS_CHECK_DIR> --seeds 10 --jobs 2 --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/match.py" --seat <ABS_BUILD_DIR>/policy.bas:3 --seat base:3 --variant league-3v3 --seeds 1..4 --out <ABS_MATCH_DIR> --json
```

`local_check` G1–G3 must pass (no compile failure, no candidate VM disabled on any seed
or side); G4 is an advisory screen. Local results prove the mechanism runs, not hosted
strength (D-04). Continue through core `build-upload` and `xp-request` only within
authorized work. Failed or partial evidence cannot support a promotion (D-06).

## Rejected build inputs

A build with `status: rejected` and `error.reason: invalid_input` is a correctable input refusal, not a tool fault. Read `error.details.path` and `error.details.action`, correct and commit truthful inputs within the card scope, then retry with a new output directory. Preserve the rejected report; do not overwrite it, invent provenance, or bypass a check. If correction needs unavailable facts or authority, request help and block. This explicit result takes precedence over generic CLI-failure handling.

## Build tool faults

Read the build JSON and retained reports before continuing. A build with
`status: errored` (exit 3) is a tool fault, not a strategy problem: a native build that
cannot fetch `github.com`, a missing Nim toolchain, a decoder crash. Stop this build
path; do not retry it or rewrite the candidate to repair it. Preserve the build
directory. Follow core `report` and `card`'s tool-fault handoff (D-26): a
`help_request:` comment on your own card with the exact error, the absolute build
directory and the command; record the same facts in the journal.

## Explicit bootstrap card: the Base starter

Use this section only when assigned a bootstrap card. Follow the core `candidate`
skill's bootstrap checks, then read `## Starter policy` in
`games/pudge-wars-basic/GAME.md` for the exact command. The starter is the package's
`base` player file (SHA-256 `f38ba2929c91f985d73afa5a54ec3e5da582e5e71122069cbc2847b19fc48511`),
shipped as `baselines/base.bas` and as the seed's `strategy/policy.bas`, described by
`strategy/STRATEGY.md`:

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/build.py" --starter base --out <ABS_WORKTREE>/.scratch/build/champion
```

Check the build's `policy_sha256` equals the hash above and every non-advisory gate
passed. Copy the complete build into tracked `build-evidence/<build-id>`, stage it and
complete with `experiment: null`. The coordinator uploads and submits (D-25). The starter
is unevaluated; claim no improvement.
