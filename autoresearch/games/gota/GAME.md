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
Semantic candidates use the PW compiler. Exact committed BASIC artifacts use
the prebuilt path below. COM is not supported.

## Commands

Run bindings from the `autoresearch/` distribution root with the project environment.
Append the arguments below to the exact front-matter command.

- `local_check ARTIFACT --baseline BASELINE --work-dir OUTPUT`
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
These spellings match Polyworld `801749e5` (the 2026.10.7.2 source);
`time_limit` is a timeout. It rejects invalid evidence and nonzero scores for nonwinning seats. Missing
metrics never become guessed zeros. Comparisons require at least 40 episodes;
calibration can raise that floor.

## Build and upload

`build CANDIDATE_DIRECTORY --out NEW_BUILD_DIRECTORY --json`
compiles the committed `CANDIDATE_DIRECTORY/STRATEGY.md` with Hermes and runs G1–G5.
The candidate may be `WORKSPACE/strategy/candidates/E` in a worker Git worktree;
the installed distribution may be a separate Git archive. Commit the candidate
input closure (strategy, skills, packing, comms) first. Development installations
also require clean tracked compiler inputs. Archive compiler inputs are hashed
separately; the candidate commit does not establish their Git provenance.
Compiler analysis defaults to build-owned scratch and is retained under
`BUILD_DIRECTORY/analysis` when produced; it does not write into the installed
distribution. The output directory must not exist. Gate results record `outcome` as `passed`,
`failed`, or `errored`, alongside the compatibility `passed` boolean. Failed
policy checks can feed compiler repair rounds. A gate or harness tool fault stops
the build immediately: `report.json`, `report.md`, `version.json` and the binding's
JSON output report status `errored`, with exception type, message and traceback
summary. No later gate or model round runs after the fault, and errored evidence
never becomes `repair_errors`. Startup faults retain an error report under the
requested output directory; they may have no artifact or provenance. Failed and
errored builds are refused by the shared upload reader.

The host must install the locked Python dependencies, Nim 2.2.10 and the native
helpers using [native preparation](tools/native_build.py), and install Hermes
v0.21.5. Set `GOTA_HERMES_PYTHON` to that Hermes installation's Python executable,
`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `GOTA_TIRITH_BIN` to an officially
installed, verified executable tirith scanner. The adapter creates a separate compiler
Hermes home, copies that scanner into its fresh home and synchronously verifies readiness before
model execution, and restricts child writes to compiler scratch. Host provisioning
and credentials remain the installer's responsibility.

Online builds resolve the current league through `"$AUTORESEARCH_CLI" softmax
game-version` and the shared credential-preserving platform transport. Set
`AUTORESEARCH_GAME=gota`, `AUTORESEARCH_CLI`, `HERMES_HOME`, and the profile's
`SOFTMAX_PLAYER_CREDENTIAL` before building. The canonical Coworld manifest is
fetched and retained as `BUILD_DIRECTORY/coworld_manifest.json`. The receipt in
`version.json.engine` records the Coworld version/id, runnable source URL, full
engine and Bassy commits, manifest hash, native fingerprints and host contract hash.
Native preparation fetches only these exact engine/dependency commits; no model
runs before their receipts and the supported scalar/record/storage/limit contract
have been checked. A release with a changed host contract fails with an explicit
unsupported-contract error and needs compiler review.

`--manifest PATH` is optional online: when supplied, it must match the fetched
live manifest exactly. Historical builds require explicit `--offline --manifest
PATH` (or `--offline` with `GOTA_MANIFEST`); only this mode selects `engine.env`'s
historical pin. Lookup, auth, manifest and native failures never fall back offline.
The direct `local_check` command follows the same rules and writes its identity
and fetched manifest into its fresh work directory.

```sh
uv run --locked python games/gota/tools/build.py /workspace/strategy/candidates/E --out /workspace/versions/new --json
uv run --locked python games/gota/tools/build.py /workspace/strategy/candidates/E --out /workspace/versions/historical-new --offline --manifest /path/to/2026.10.7.2-manifest.json --json
```

`evaluate --results RESULTS_JSON --json` returns `data.episodes`, one metric entry
per seat with the real integer seed. xp-poll keeps only the design's seats and
reduces them by the arithmetic mean, one row per episode (`games/_template/GAME.md`).
The binding never guesses policy identity. Positional `evaluate RESULTS_JSON
--seat N` retains the local metric-object form.

`upload E --build-dir BUILD_DIRECTORY` delegates to
`"$AUTORESEARCH_CLI" softmax upload E --build-dir BUILD_DIRECTORY`. Set the variable
to the installed shared CLI executable. That shared tool performs the C6 identity
check immediately before mutation; this wrapper has no alternate upload route.

## Exact prebuilt baseline and first-install bootstrap

`build CANDIDATE_DIRECTORY --out NEW_BUILD_DIRECTORY --json --prebuilt FILE.bas`
freezes the exact committed artifact bytes and runs the existing G1–G4 gates.
It skips Hermes, model generation and scanner setup entirely. Native preparation,
manifest resolution and the host contract check still apply. G4 compares the
artifact against its own immutable bytes as a baseline mirror; this checks the
screen machinery, not strength against an incumbent. G5 compiler goal traces are
not applicable: its receipt has `applicable: false`, `passed: false`,
`advisory: true`, and summary `not applicable: prebuilt`. It has no executed
`outcome`. Failed G1–G3 refuse upload; any tool error stops the build with retained
evidence and refuses upload. No prebuilt build attempts model repair.

The artifact, adjacent `FILE.provenance.json`, and declared original input must
be inside the candidate directory and committed unchanged. The sidecar schema is
`gota-prebuilt/1`: `artifact_sha256`, `input: {path, sha256}`, `transform`, and
`engine: {commit, coworld_version, source_url, manifest_sha256}`. Input paths are
relative to the sidecar. For handwritten artifacts, use `transform: {name:
identity}`, name the artifact itself as the input, and record its exact hash and
resolved engine identity. For v13-live, the generator below records the original
hash, output hash, transform tool hash and resolved engine automatically. Build
reproduces the transformation before gates and verifies the committed closure
again after gates. The frozen sidecar, native receipts, manifest, reports and
`version.json` stay in the build directory; shared upload preserves provenance
under `version.build.compiler.prebuilt_provenance`. In prebuilt mode, the shared
`strategy_sha256` field is the committed BASIC source hash (equal to
`policy_sha256`), and `source_path` names that BASIC file. `compiler.mode` is
`prebuilt`; these fields do not claim STRATEGY.md compilation or semantic proof.

The installed distribution includes the byte-identical original at
`games/gota/baselines/submitted-v13.bas`, with its historical origin in the
adjacent provenance file. Copy it into the worker candidate and commit that input
first. Generate v13-live in one command; this resolves the current engine and
makes only the authorized identifier/division token changes, with no model call:

```sh
mkdir -p /workspace/strategy/candidates/E
cp games/gota/baselines/submitted-v13.bas /workspace/strategy/candidates/E/submitted-v13.bas
git -C /workspace add strategy/candidates/E/submitted-v13.bas
git -C /workspace commit -m "Record exact submitted v13 bootstrap input"
uv run --locked python games/gota/tools/make_v13_live.py /workspace/strategy/candidates/E/submitted-v13.bas /workspace/strategy/candidates/E/v13-live.bas
git -C /workspace add strategy/candidates/E/v13-live.bas strategy/candidates/E/v13-live.provenance.json
git -C /workspace commit -m "Record mechanical v13-live bootstrap candidate"
uv run --locked python games/gota/tools/build.py /workspace/strategy/candidates/E --out /workspace/versions/E-prebuilt --prebuilt /workspace/strategy/candidates/E/v13-live.bas --json
```

Use the installed profile's Python in workers (see the GotA eval-design skill).
For an explicit historical build, append `--offline --manifest PATH` to both
generator and build. Existing outputs are immutable; use a fresh output path.

The build already records G1–G4 local checks. For a separate `local_check`, run
only these applicable gates against the frozen artifact and manifest; G5 compiler
coverage does not apply to this artifact:

```sh
uv run --locked python -m games.gota.gates /workspace/versions/E-prebuilt/policy.bas --baseline /workspace/versions/E-prebuilt/policy.bas --manifest /workspace/versions/E-prebuilt/coworld_manifest.json --work-dir /workspace/checks/E-prebuilt --gate g1_lint --gate g2_episode --gate g3_budget --gate g4_screen
```

**Shared first-install upload handoff is pending.** The current shared upload
requires an experiment with status `built` and a previously frozen evaluation
design that names an existing baseline policy version. An install with no
baseline cannot yet satisfy that contract. Preserve the successful local build
in the same card, journal the missing baseline/design contract and send a
`help_request`; do not fabricate a baseline or evaluation design. The shared
owner must supply the bootstrap record transition before first upload can proceed.

For an already valid built experiment, the supported next step is unchanged:
`"$AUTORESEARCH_CLI" softmax upload E --build-dir /workspace/versions/E-prebuilt`.
It performs C6 and returns the local version record id; record that id and its
`candidate_binding.platform_policy_version_id` from `records show E` in the card
journal. This local prebuilt path has not uploaded or established a platform baseline.

## Qualification scope

Live readback on 2026-10-08 resolved 2026.10.8.1 / Polyworld
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`; native preparation, baseline compilation,
the supported host contract, and one full native 31200-tick mirror match were
checked locally. All ten seats ran with no VM errors. This does not extend the
prior paid M1 semantic qualification, which remains bound to its immutable
2026.10.7.2 / `801749e509e888b423ba44ee2188946c22e3ba85` receipts. The historical
oracle and audit keep those identities; a new engine needs its own qualification.

Only G1–G3 policy failures block a build and may enter compiler repair rounds.
G4 and G5 still run and carry `advisory: true` in build receipts. Their `failed`
outcomes appear in `report.json` warnings and the Markdown report, while a build
with passing G1–G3 remains passed. An `errored` outcome from any gate stops the
build immediately and makes it errored, including advisory gates. Local check
returns 0 for advisory failures, 1 for blocking failures, and 3 for tool errors.
