---
name: gota
bindings:
  build: python3 games/gota/tools/deferred.py build
  local_check: python3 -m games.gota.gates
  upload: python3 games/gota/tools/deferred.py upload
  evaluate: python3 games/gota/tools/evaluate.py
  replay_inspect: python3 games/gota/tools/replay_stats.py
reference_types:
  replay-segment: games/gota/tools/resolve_segment.py
outcome_metrics:
  - {name: glory, direction: up}
  - {name: win_rate, direction: up}
  - {name: xp_per_1k_ticks, direction: up}
  - {name: team_xp_lead, direction: up}
performance_guardrails: [glory, win_rate]
min_episodes_floor: 40
compiler: pw
league_id: league_3c60897b-25cf-4b37-9d1a-8554c1198f28
noise_source: league_episodes
layers: [P,K,S,SK,C,G,ST,A]
---
# Gods of the Arena

GotA has ten fixed hero seats: red seats 0–4 and blue seats 5–9.
Read [mechanics](docs/mechanics.md), [policy surface](docs/policy-surface.md),
[game guide](docs/game-guide.md), and [replay format](docs/replay-format.md).
The PW semantic compiler is the only candidate build path. COM is not supported.

## Commands

Run bindings from the `autoresearch/` distribution root with the project environment.
Append the arguments below to the exact front-matter command.

- `local_check ARTIFACT --manifest MANIFEST --baseline BASELINE --work-dir OUTPUT`
  runs the existing shared G1–G5 gates. See [gate reference](gates/README.md).
- `evaluate RESULTS_JSON --seat N` prints one JSON metric object for one subject
  seat in one episode. Seat is required; no seat is guessed from a policy name.
  This local extraction does not create a league experience request.
- `replay_inspect EPISODE_DIRECTORY --binary EXPANDER --json` inspects replay data.
  Alternatively use `--ref RECORDING_COMMIT` to build the pinned expander.
- Resolve `replay-segment` with
  `python3 games/gota/tools/resolve_segment.py 'PATH#ticks=START:END'`.
  PATH is a local existing file, relative to the current distribution root or
  absolute. Ticks form a nonempty half-open interval `[START, END)`. The resolver
  prints the absolute replay path and integer boundaries; it does not parse,
  truncate, or validate the replay's duration. Remote URLs are unsupported.

## Metrics

Each episode observation belongs to one subject seat; repeated copies in one
team are not independent observations. `glory` reads that seat's platform `scores`
value: Emmett's Glory, winning hero lifetime XP per simulated minute, zero on
loss, draw, or timeout. Do not recompute the score with a guessed tick frequency.
`win_rate` is the team outcome indicator (0 or 1). `xp_per_1k_ticks` is that seat's
`total_xp * 1000 / ticks`. `team_xp_lead` is 1 only when that seat has strictly
more lifetime XP than every teammate; ties are 0.

The extractor requires all ten finite, nonnegative score and XP values, positive
integer ticks, and a known outcome (`RedTeam`, `BlueTeam`, `draw`, `time_limit`).
These spellings match Polyworld `5287c437` `sim.nim:3853–3859`;
`time_limit` is a timeout. It rejects invalid evidence and nonzero scores for nonwinning seats. Missing
metrics never become guessed zeros. Comparisons require at least 40 episodes;
calibration can raise that floor.

## Deferred backends

The `build` binding exits 2 with an actionable gap: Phase C Task 2.5 must supply
`games/gota/tools/build.py`, compile through PW, run shared gates, and write build
provenance. The current adapter does not claim a successful build.

The `upload` binding exits 2: Plan 03 Tasks 3.3/3.4 must provide the guarded shared
softmax tool. C6 remains authoritative: verify player identity with the exact
credential immediately before every mutation. This game adapter provides no
alternate credential, direct coworld upload, or identity cache. Wire the binding
to the authoritative shared tool when that backend exists.
