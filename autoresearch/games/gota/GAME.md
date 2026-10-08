---
name: gota
source: {repo: https://github.com/Metta-AI/polyworld, pin: coworld_manifest}
bindings:
  build: python3 games/gota/tools/build.py
  local_check: python3 -m games.gota.gates
  upload: python3 games/gota/tools/upload.py
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
These spellings match Polyworld `801749e5` (the live 2026.10.7.2 source);
`time_limit` is a timeout. It rejects invalid evidence and nonzero scores for nonwinning seats. Missing
metrics never become guessed zeros. Comparisons require at least 40 episodes;
calibration can raise that floor.

## Build and upload

`build CANDIDATE_DIRECTORY --out NEW_BUILD_DIRECTORY --manifest MANIFEST --json`
compiles the committed `CANDIDATE_DIRECTORY/STRATEGY.md` with Hermes and runs G1–G5.
The candidate may be `WORKSPACE/strategy/candidates/E` in a worker Git worktree;
the installed distribution may be a separate Git archive. Commit the candidate
input closure (strategy, skills, packing, comms) first. Development installations
also require clean tracked compiler inputs. Archive compiler inputs are hashed
separately; the candidate commit does not establish their Git provenance.
The output directory must not exist. Failed builds retain failed gate evidence
and are refused by the shared upload reader.

The host must install the locked Python dependencies, Nim 2.2.10 and the native
helpers using [native preparation](tools/native_build.py), and install Hermes
v0.21.5. Set `GOTA_HERMES_PYTHON` to that Hermes installation's Python executable,
`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `GOTA_TIRITH_BIN` to an officially
installed, verified executable tirith scanner. The adapter creates a separate compiler
Hermes home, copies that scanner into its fresh home and synchronously verifies readiness before
model execution, and restricts child writes to compiler scratch. Host provisioning
and credentials remain the installer's responsibility.

Supply a downloaded Coworld manifest with `--manifest` or `GOTA_MANIFEST`.
`game.runnable.source_url` must contain the qualified Polyworld commit
`801749e509e888b423ba44ee2188946c22e3ba85`; bootstrap does not supply a manifest.

`evaluate --results RESULTS_JSON --json` returns `data.episodes`, one metric entry
per seat with the real integer seed. The shared XP consumer must select its
subject seats and aggregate teammates before treating episodes as independent.
The binding never guesses policy identity. Positional `evaluate RESULTS_JSON
--seat N` retains the local metric-object form.

`upload E --build-dir BUILD_DIRECTORY` delegates to
`"$AUTORESEARCH_CLI" softmax upload E --build-dir BUILD_DIRECTORY`. Set the variable
to the installed shared CLI executable. That shared tool performs the C6 identity
check immediately before mutation; this wrapper has no alternate upload route.

## Qualification scope

NOTES-5 reports live 2026.10.8.1 / Polyworld 02eda3d3. This adapter currently
requires the M1-qualified 2026.10.7.2 pin; it refuses a newer manifest rather than
claiming qualification. Dynamic engine following is a separate follow-on.
