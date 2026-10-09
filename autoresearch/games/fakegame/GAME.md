---
name: fakegame
bindings:
  build: python3 games/fakegame/tools/fake_build.py
  local_check: python3 games/fakegame/tools/fake_build.py --check
  upload: python3 games/fakegame/tools/fake_build.py --upload
  evaluate: python3 games/fakegame/tools/fake_eval.py
  replay_inspect: python3 games/fakegame/tools/fake_eval.py --replay
reference_types:
  replay: python3 games/fakegame/tools/fake_ref.py
outcome_metrics:
  - {name: score, direction: up}
  - {name: errors, direction: down}
performance_guardrails: [score]
min_episodes_floor: 10
league_id: fake-league
noise_source: league_episodes
layers: [parameters]
compiler: none
---
# Fake game

Test-only game used by protocol tests and the fake research cycle. It runs on the fake Softmax
platform (`AUTORESEARCH_SOFTMAX=fake`), never on a real league.

## Seats and opponents

Two seats per episode. Our policy plays seat 0. The league opponent is the house policy
`fake-house:v1` in seat 1. An evaluation design uses `"seats": [0]` and
`"opponents": ["fake-house:v1"]` (pairing `seed_and_opponents`).

## Mechanics

The fake platform plays a policy by the parameters of its `STRATEGY.md`, the `- <name>:
<number>` items under `## Parameters`. The seed strategy has `aggression: 3` and
`patience: 5`.

- `score` (up; the performance guardrail): about 50, plus a term set by the seed (the board,
  +-4), plus 0.5 per `aggression` point above 3, plus episode noise (+-1) that depends only on
  the seed, the episode, the seat and `aggression`.
- `errors` (down): a random integer 0 to 2 per episode, plus `8 - patience` when patience is
  below 8. Each `patience` point up to 8 removes about one error per episode. `patience` does
  not change `score`.

These rules are the fake game's documentation (tier A evidence: official docs). League
episodes (`softmax league-episodes`) come from three house policies and measure the noise.

## Bindings

Run each binding from `"$AR_HOME"` with absolute paths.

- `build`: `python3 games/fakegame/tools/fake_build.py <candidate> --out <build dir> --json`.
  `<candidate>` is the candidate `STRATEGY.md`, or its directory
  (`<worktree>/strategy/candidates/<E-id>`). It writes `policy.json` (the artifact, with the
  parameters) and `version.json` in the build directory. Its one gate, `local_check`, always
  passes.
- `local_check`: `python3 games/fakegame/tools/fake_build.py --check <candidate> --json`.
- `upload`: not used by agents; `"$AUTORESEARCH_CLI" softmax upload` uploads.
- `evaluate`: `python3 games/fakegame/tools/fake_eval.py --results <results.json> --json`
  (used by xp-poll and `softmax league-episodes`). `--policy <id> --seed <n>` is a local run
  for debugging only: it scores by a hash of the policy id and does **not** follow the
  Mechanics above, so never use it as evidence (D-04).
- `replay_inspect`: `python3 games/fakegame/tools/fake_eval.py --replay --policy <id> --seed
  <n> --json` prints a per-tick summary. Fake XP requests store no replay files.

## Baseline build

The baseline is the champion STRATEGY at install, `<worktree>/strategy/STRATEGY.md` (the seed
strategy). Build it with the `build` binding:
`python3 games/fakegame/tools/fake_build.py <worktree>/strategy --out <worktree>/.scratch/build/baseline --json`,
check it with `python3 games/fakegame/tools/fake_build.py --check <worktree>/strategy --json`,
then promote it as the first champion with `"$AUTORESEARCH_CLI" softmax bootstrap-champion --build-dir <worktree>/.scratch/build/baseline`.
