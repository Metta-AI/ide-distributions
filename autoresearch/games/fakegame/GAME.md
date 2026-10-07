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

Test-only game used by protocol tests and the fake research cycle. It has no real rules.
`fake_build.py` hashes a candidate's `strategy/STRATEGY.md`. `fake_eval.py` returns
deterministic per-episode `score` (higher is better) and `errors` (lower is better) from a
hash of (policy, seed), with a small policy-dependent shift in the mean score.
