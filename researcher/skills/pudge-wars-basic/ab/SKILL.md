---
name: pudge-wars-basic-ab
description: Use when comparing Pudge Wars Basic policy versions.
---

# Pudge Wars Basic ab

This binding implements System Design §5.5 with the existing core procedures.
Doctrine upheld: D-04, D-06, D-09.

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/ab/SKILL.md`, from the GotA `ab` binding in this repository, and
from the upstream Pudge mixin `games/pudge-wars/skills/eval-design/README.md` at
`Metta-AI/polyworld-pudge-wars@cd8111a5`. The core `ab-compare` and `review-xp` skills
carry the comparison standard; this skill supplies what is game-shaped.

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
Coworld `pudge-wars-basic` 0.1.8 (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source
`Metta-AI/polyworld-pudge-wars` at `cd8111a5`, replay game version 38. Resolve the
current version with `softmax game-version` before new work; a newer source commit
needs a rebuilt headless binary (`tools/native.py build --commit <SHA>`) before replays
recorded on it decode.

## Long commands

Use the core `card` skill's **Long commands** procedure for native builds
(`tools/native.py`), local episodes (`tools/match.py`), `tools/local_check.py`,
`tools/branch.py` and replay inspection of more than a few episodes. A league-length
local 3v3 takes about a second of simulation per episode plus decoding; a first native
build fetches the source and every `nimby.lock` dependency. After exit, read the exit
code, the JSON envelope (`ok`, `error.reason`) and the files the command wrote before
interpreting anything. Exit 1 is a failed check, 2 a rejected input, 3 a tool fault.

## Metrics that matter

`evaluate.py` emits one entry per seat with these metrics (GAME.md is authoritative):

| Metric | Dir | Results field | Why it matters here |
|---|---|---|---|
| `win` | up | `winner_team` vs `per_seat[i].team`; 0.5 for `"draw"` | the league scores a seat 1 only when its team has strictly more team kills (`scores[i]`) |
| `team_kill_margin` | up | `west_kills` − `east_kills`, signed for the seat's team | finer than win/loss; every league game ends at 30, so margin is the loser's shortfall |
| `kills` | up | `per_seat[i].kills` | personal enemy kills; excludes rot suicides |
| `deaths` | down | `per_seat[i].deaths` | **what separates the leaders** (3.6–4.8 vs field 8.3); every death is an enemy team kill |
| `hook_landed_rate` | up | `hooks_landed / hooks_thrown` | the team with the higher ratio won 90% of league games |

Guardrails: `win` and `team_kill_margin`. Traps: `hooks_landed` counts ally saves and
allied-mine grabs as well as enemy latches and headshots, so `hook_landed_rate` can rise from hooking
teammates; pair it with `kills` and with enemy-only accuracy from `replay_inspect`
(D-09: a rate needs its volume counterparts). `hooks_thrown` (~20 per seat-minute for
every policy) measures game length, not quality. Σ seat kills can trail the team total
(rot suicides) and deaths can exceed enemy kills (ally Rot, uncredited drowning).

## Decompositions

Split before any verdict:

- **Subject vs teammates.** In a one-seat design the subject's teammates are sampled
  league policies; report the subject seat's `kills`, `deaths`, `hook_landed_rate`
  separately from team `win`. In a whole-team design (three subject copies) aggregate the
  three seats per episode first; they are one observation, not three.
- **Side.** West (slots 0–2) vs East (3–5). The league shows no side effect (West 50.5%
  of 192), so a large per-side gap points at a geometry bug in the candidate (lane
  mirroring, `riverWest`/`riverEast` use), not at the map.
- **Slot within a team.** Spawn lanes are shuffled per seed, so slot is not a role; but
  Base assigns lanes by id rank, so a candidate that changes lane logic should be split by
  rank.
- **Roles.** Pudge has no classes. Use behavioural roles from replays: rune collector,
  front (smallest |x − lip|), rescuer; and game phase (first 5 minutes vs later, when
  upgrades and +35 HP per kill accumulate).
- **Format.** Never pool `league-3v3` with `duel-1v1` results.

## Taint

Drop or flag an episode before any mean:

- **No `results.json`** and a `player_failure.json` with `failed_policy_index`: a seat
  failed to load. If the index is our seat it is our compile/package failure (count it
  against the candidate, and it counts toward league disqualification); otherwise the
  episode is invalid for both arms.
- **`BASIC VM disabled`** for a seat (`player_status.json` `exit_code 1`, or a
  `pudge <id> BASIC error:` line in a local stdout log): that seat stood still from the
  failure tick. Our seat: a candidate failure, not noise. Another seat: the line-up was
  broken; keep it visible as a separate count.
- **Platform failure**: XP episode status failed/cancelled, missing replay, a results hash
  that does not match the download receipt. Platform, not policy; exclude and report.
- **Wrong identity**: game version not the frozen one, a seat mapped to an unexpected
  policy version, repeated seed. Exclude (`stats ingest` reports these).
- **Time-limit endings** are legal but rare in the league (0 of 192); a cluster of them
  signals a passive candidate and belongs in the result, not in taint.

## Tests and floors

Use the shared paired statistics (`stats evaluate`) for the decision; `compare.py` is a
diagnostic. Measured per seat-episode SDs on 1,152 league seats (2026-10-26): kills 3.7,
deaths 3.4, landed/thrown 9.1 points, win 0.48, team signed margin 12.3.

| Effect | Episodes per arm (two-arm, α 0.05, power 0.8) |
|---|---|
| win ±20 points | ~100 |
| win ±15 points | ~175 |
| win ±10 points | ~390 |
| deaths ±1.0 / game | ~180 |
| kills ±1.0 / game | ~215 |
| landed/thrown ±3 points | ~145 |

`min_episodes_floor` is 100: below it a result is directional only. Pairing by seed and
line-up (the frozen design) reduces these numbers; the calibrated `n_min` from
`stats design` wins when it is larger. Deaths and accuracy carry more signal per episode
than win; report them beside win. Local `match.py` screens never count (D-04).

## Procedure and outputs

Use core `ab-compare` and `review-xp` for the decision. Check terminal status, verified
replay hashes, game version, stable policy-version IDs per slot, subject seats, matched
seeds and line-ups, and failed episode counts before calculating means.

```sh
"$RESEARCH_CLI" softmax xp status <E-id>
"$RESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/evaluate.py" --results <ABS_RESULTS_JSON> --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/compare.py" <ABS_BASELINE_EPISODES> <ABS_CANDIDATE_EPISODES> --group team --json > .scratch/<E-id>-compare.json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/compare.py" <ABS_BASELINE_EPISODES> <ABS_CANDIDATE_EPISODES> --group seat --metric deaths --json > .scratch/<E-id>-deaths.json
"$RESEARCH_CLI" stats ingest --experiment <E-id> --rows .scratch/<E-id>-rows.jsonl
"$RESEARCH_CLI" stats evaluate <E-id>
```

For enemy-only accuracy and clash outcomes, decode both arms with
`replay_inspect.py <ABS_EPISODE_DIR> --seat <N> --json` (see `pudge-wars-basic-replay-inspection`)
and compare `hooks[]` results by victim team. `compare.py` uses independent-sample
statistics, not the frozen paired rule; a diagnostic verdict never promotes.

Read ingest exclusions (failed, missing_metrics, wrong_version/seats/seed), then pair
count, signed changes, intervals and failures. Require the preregistered
N ≥ max(100, calibrated n_min), guardrails `win` and `team_kill_margin`, the primary
thresholds and the current champion as baseline. Partial coverage is invalid/hold,
never a promotion by an attractive subgroup.

```sh
"$RESEARCH_CLI" records new result --meta-file .scratch/result.json --body-file .scratch/result.md
```

Use core `review-xp`'s Result schema: raw scope, identities, pairing, units, exclusions,
outcome and the mechanism activation witness (for example: the candidate's deaths fell
and its replays show the new retreat branch firing). Separate activation from
competitive improvement. Never hand-edit shared stats outcomes, upload or submit.
