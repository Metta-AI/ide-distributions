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
platform (`RESEARCH_SOFTMAX=fake`), never on a real league.

## Seats and opponents

Two seats per episode, no teams. Our policy plays seat 0 and one opponent plays seat 1, so an
evaluation design uses `"seats": [0]` and `"opponent_count": 1`: `stats design` samples each
seed's opponent from the fake league (the house policy `fake-house:v1` and the
`fake-alpha`/`fake-beta`/`fake-gamma` policies of its episodes; pairing `seed_and_opponents`).
The fake platform's results do not depend on the opponent.

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

Run each binding from `"$RESEARCH_HOME"` (the profile directory; see the `card` skill) with
absolute paths.

- `build`: `python3 games/fakegame/tools/fake_build.py <candidate> --out <build dir> --json`.
  `<candidate>` is the candidate `STRATEGY.md`, or its directory
  (`<worktree>/strategy/candidates/<E-id>`). It writes `policy.json` (the artifact, with the
  parameters) and `version.json` in the build directory. Its one gate, `local_check`, always
  passes.
- `local_check`: `python3 games/fakegame/tools/fake_build.py --check <candidate> --json`.
- `upload`: not used by agents; `"$RESEARCH_CLI" softmax upload` uploads.
- `evaluate`: `python3 games/fakegame/tools/fake_eval.py --results <results.json> --json`
  (used by `softmax xp evidence` and `softmax league-episodes`). `--policy <id> --seed <n>` is a local run
  for debugging only: it scores by a hash of the policy id and does **not** follow the
  Mechanics above, so never use it as evidence (D-04).
- `replay_inspect`: `python3 games/fakegame/tools/fake_eval.py --replay --policy <id> --seed
  <n> --json` prints a per-tick summary. Fake XP requests store no replay files.

## Starter policy

The starter policy is the champion STRATEGY at install, `<worktree>/strategy/STRATEGY.md` (the
seed strategy). Build it with the `build` binding:
`python3 games/fakegame/tools/fake_build.py <worktree>/strategy --out <worktree>/.scratch/build/champion --json`,
check it with `python3 games/fakegame/tools/fake_build.py --check <worktree>/strategy --json`,
then upload it with `"$RESEARCH_CLI" softmax upload-build --name opening-rush --build-dir <worktree>/.scratch/build/champion`;
the orchestrator submits it as the first champion.
