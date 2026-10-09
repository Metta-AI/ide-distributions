---
name: template
bindings:
  build: python3 games/template/tools/build.py
  local_check: python3 games/template/tools/check.py
  upload: python3 games/template/tools/upload.py
  evaluate: python3 games/template/tools/evaluate.py
  replay_inspect: python3 games/template/tools/replay_inspect.py
reference_types:
  example_type: games/template/tools/resolve_example.py
outcome_metrics:
  - {name: example_metric, direction: up}
performance_guardrails: [example_metric]
min_episodes_floor: 10
league_id: example-league
noise_source: league_episodes
layers: [example_layer]
compiler: none
source:
  repo: https://github.com/example/example-game.git
  pin: coworld_manifest
---
# Game guide (template)

Copy this directory to `games/<game>/` and fill in every field. `load_game` raises
`GameError` naming each missing or invalid field. A missing binding is a reported gap, never a
silent fallback. Core code never names a game.

- `name`: the game directory name.
- `bindings`: command-line strings for `build`, `local_check`, `upload`, `evaluate` and
  `replay_inspect`. Relative paths resolve from the repo root. All five are required.
  `<evaluate> --results <results.json> --json` prints `{"ok": true, "data": {"episodes":
  [{"episode", "seat", "metrics": {<outcome metric>: number}, "status"?:
  "completed"|"failed", "seed"?}]}}`: **one entry per seat** of each episode in that file,
  opponents included, each naming its `seat` (the participant position). The binding does
  not know which seats are ours; its callers decide:
  - xp-poll (XP requests) keeps the entries whose `seat` is in the design's `seats`, requires
    all of them (a missing or failed own seat records the episode as failed, with a reason),
    and **reduces our seats by the arithmetic mean** of each metric: one row per episode.
  - `softmax league-episodes` (league episodes, for noise) **keeps one row per seat**. It
    accepts an entry without `seat` only when the episode has a single seat.
- `reference_types`: extra reference types (`ar://<type>/<id>`), each mapped to a resolver
  command run from the repo root as `<command> <id>`, with `AUTORESEARCH_WORKSPACE` and
  `AUTORESEARCH_REV` set. Exit 0 with the content on stdout, or non-zero if not found. A type
  never shadows a core type. May be `{}`.
- `outcome_metrics`: list of `{name, direction}`; direction is `up` (higher is better) or `down`.
- `performance_guardrails`: metric names that must not regress. Each must be in `outcome_metrics`.
- `min_episodes_floor`: lowest allowed N (episodes per comparison). Measured noise may raise it.
- `league_id`: the league the `evaluate` binding plays in.
- `noise_source`: where metric noise is measured, for example `league_episodes`.
- `layers`: the STRATEGY.md layers that apply to this game.
- `compiler`: `pw`, `basic` or `none`.
- `source` (optional): `{repo, pin}`. `repo` is the game source repository (a git URL or a
  local path) that claims cite as `<path>:<a>-<b>@<sha>`. `pin` says how to read the commit
  the current game version runs; the only value is `coworld_manifest`: the commit in the
  `source_url` of the game runnable in the Coworld manifest. When the pin changes,
  queue-keeper fetches `repo` into `$HERMES_HOME/local/game-source/<game>/` and renews
  claims whose cited lines did not change (and requests re-verification for the others).
  Without `source`, claims only go stale on a game-version change.

The body must have a `## Baseline build` section: how to build the game's baseline policy
with the `build` binding (arguments, input, output directory) so `softmax bootstrap-champion`
can promote it as the first champion on a fresh install (amendment A14). The build writes the same
`version.json` as any build; every non-advisory gate must pass.

Describe the game here: its rules, primitives, limits, and host data for the STRATEGY.md layers.
