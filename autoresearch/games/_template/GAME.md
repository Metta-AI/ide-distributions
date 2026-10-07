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
---
# Game guide (template)

Copy this directory to `games/<game>/` and fill in every field. `load_game` raises
`GameError` naming each missing or invalid field. A missing binding is a reported gap, never a
silent fallback. Core code never names a game.

- `name`: the game directory name.
- `bindings`: command-line strings for `build`, `local_check`, `upload`, `evaluate` and
  `replay_inspect`. Relative paths resolve from the repo root. All five are required.
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

Describe the game here: its rules, primitives, limits, and host data for the STRATEGY.md layers.
