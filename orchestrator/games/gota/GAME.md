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
host_requirements:
  env: [GOTA_HERMES_PYTHON]
  tools: [nim]
---
# Gods of the Arena

Read [mechanics](docs/mechanics.md), [policy surface](docs/policy-surface.md),
[game guide](docs/game-guide.md), and [replay format](docs/replay-format.md).
Semantic candidates use the PW compiler. Its Hermes child resolves the owning
profile’s OpenRouter credential through the installed Hermes API, then runs
with a separate scratch home. Provider keys stay out of the worker terminal
environment and build receipts. Exact committed BASIC artifacts use
the prebuilt path below. COM is not supported.

## Seats and opponents

GotA has ten fixed hero seats: red seats 0–4 and blue seats 5–9.
The design's `seats` are the slots our candidate fills in the candidate arm and
our champion fills in the baseline arm. For a seat-specific question, use the
verified subject seat (`seats: [N]`, where N is 0–9). For a team question, use
one complete team: `seats: [0, 1, 2, 3, 4]` for red or
`seats: [5, 6, 7, 8, 9]` for blue. Seats on the same team cooperate and share an
episode; their measurements are aggregated into one episode observation.

Set `opponent_count` to `10 - len(seats)`: 9 for one subject or 5 for a complete
team. These league-sampled policy refs fill every remaining slot in ascending
order, including teammates when only one subject is ours. `stats design` samples
the line-ups at design time and pins one per seed for both arms of the pair.
A narrow fixed `opponents` set instead requires an `opponent_rationale` stating
why those policies answer the question.

## Commands

Run bindings from `"$RESEARCH_HOME"` (the installed profile directory) with
`"$RESEARCH_PYTHON"` in place of `python3`, as the core `card` skill sets them, and
absolute worktree paths. Append the arguments below to the exact front-matter command.

Default `build` runs compilation/static checks only. Full `local_check` and
`build --full-evaluation` can take tens of minutes; use the card's long-command
procedure. Cold native preparation and compiler model generation can also take
time. See [gate durations](gates/README.md#durations-and-waiting) for measurement scope.

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

After the process exits, read the exit code and command JSON, then the retained
evidence before continuing. A build writes `NEW_BUILD_DIRECTORY/report.json`,
`report.md`, `version.json` (including `gates`, `engine` and `native_builds`)
and, when available, `policy.bas` and `coworld_manifest.json`. Each executed gate
in `version.json` records `seconds` (wall time), and a completed build records
`started` and `finished` (ISO UTC); a startup fault's error report has neither. A
compiler build copies its compiler child's Hermes `state.db` (passed, failed, or
raised) to `$HERMES_HOME/gota-compiler/<build id>-<scratch suffix>/state.db`, so
the compiler session can still be read after the build's scratch directory
is deleted; a copy that fails is a warning on stderr, never a build failure. The
copies are not pruned: disk use grows by one full `state.db` (transcripts
included) per compiler build. A local check
writes `OUTPUT/gates.json`, `engine_identity.json`, `coworld_manifest.json` and
hashed native measurement JSON files containing `native_build` receipts.
Follow G2's `summary.episode_dir` to the episode results and logs; build locators
are relative to the build directory. Read failed/errored receipts too; a process
exit alone is not evidence of passing gates.

### G2 episode runner

G2 runs one competition episode with the candidate in all ten seats, at the
manifest variant's seed, `max_ticks` and draft mode. By default (`GOTA_G2_RUNNER`
unset or `native`) it runs the native `gota_match` binary through
`tools/gota_local.py`, for the build's resolved engine commit (`engine.env`'s pin
when none is resolved). It needs Nim-built native helpers, not Docker, so it runs in
the hosted sandbox. Set `GOTA_G2_RUNNER=docker` to run pinned `coworld run-episode`
instead; that needs Docker and the release's container image. `details.runner` in the
G2 receipt names the runner. The native runner reports a tool error (`errored`) when
the manifest's `game_config` differs from the one the binary was built with. The
evidence each runner reads back is in the [gate reference](gates/README.md#evidence-and-limits).

The native runner runs `gota_match` with `COGAME_LLM=off` and kills it, with its
process group, after the gate timeout (default 600 seconds); a timeout is a tool
error (`errored`). It writes the episode under the build's `episode/` directory,
including the recording `replay.bin` (the file size grows with the recorded actions).
The native match binary's receipt covers `tools/gota_match.nim`, so a build order
that was already in flight when this change deployed finds its cached binary stale
and reports `errored` ("stale or modified native build") once, until the binary is
rebuilt offline with `tools/build_gota_match.sh`.

Native G2 does not prove:

- that the published container image (`game.runnable.image` in the manifest) starts,
  contains this engine, or runs the policy. It runs the same engine source, built
  locally with Nim 2.2.10 on the host's own platform, not the image's linux/amd64
  build;
- the coworld runner path: staging the artifact as an uploaded player file,
  the per-seat player processes and their protocol and timeouts, and coworld's own
  `results.json` and replay writer;
- policy behavior that depends on LLM host calls: native G2 runs with `COGAME_LLM=off`,
  so no LLM request is made (and none is paid for);
- anything set in the manifest outside the variant's `game_config`, such as the
  runnable's `env` (for example `COGAME_PLAYER_SEATS_SCHEMA`): the native runner
  does not read it.

A Docker G2 on a host with Docker, or a league episode, is the evidence for those.

## Local validation

A default build runs G1 only: native compilation, host schema, and static storage/source
ceilings. A successful receipt has `status: passed`, `validation_scope: build`,
`evaluation: {"status": "not_run"}`, and only its actual G1 result. It is uploadable.
Do not run full local evaluation merely to satisfy a build/upload-only request.

Full evaluation is explicit: use `build --full-evaluation` to run G1–G5 before
finalizing, or run `local_check` separately against the frozen artifact. Full builds
record `validation_scope: full`; G1–G3 must pass, G4/G5 policy failures are advisory,
and any tool error fails the full build. Prebuilt full builds mark G5 not applicable.
Standalone evaluation writes `evaluation.json` and `gates.json`; it never rewrites
an immutable build or upgrades a failed build into an uploadable one.

Read the build's report, version, artifact hash, manifest and native receipts.
An evaluation receipt applies only to its artifact, engine, manifest, baseline,
seeds and ticks. Reuse complete matching evaluation evidence instead of repeating it.
Neither compilation nor evaluation proves competitive strength or authorizes promotion.
G3's 80,000 instruction / 200,000 work soft ceilings are local headroom checks;
the engine enforces its actual runtime limits when executing an uploaded policy.
No evaluation seeds, tick budgets or native runtime limits are reduced.

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
compiles the committed `CANDIDATE_DIRECTORY/STRATEGY.md` with Hermes and runs G1. Add `--full-evaluation` for G1–G5.
The candidate may be `WORKSPACE/strategy/candidates/E` in a worker Git worktree;
the installed distribution may be a separate Git archive. Commit the candidate
input closure (strategy, skills, packing, comms) first. Development installations
also require clean tracked compiler inputs. Archive compiler inputs are hashed
separately; the candidate commit does not establish their Git provenance.
Compiler analysis defaults to build-owned scratch and is retained under
`BUILD_DIRECTORY/analysis` when produced; it does not write into the installed
distribution. Retained G2 and legacy G5 `summary.episode_dir` locators inside the
build are relative to `BUILD_DIRECTORY`, including attempt receipts. Executed
command arguments and error tracebacks preserve their original paths.
The output directory must not exist. Gate results record `outcome` as `passed`,
`failed`, or `errored`, alongside the compatibility `passed` boolean. Failed
policy checks can feed compiler repair rounds. A gate or harness tool fault stops
the build immediately: `report.json`, `report.md`, `version.json` and the binding's
JSON output report status `errored`, with exception type, message and traceback
summary. No later gate or model round runs after the fault, and errored evidence
never becomes `repair_errors`. Startup faults retain an error report under the
requested output directory; they may have no artifact or provenance. Failed and
errored builds are refused by the shared upload reader.

Set the compiler agent's per-round timeout with `--agent-timeout SECONDS` or the
optional `GOTA_AGENT_TIMEOUT_S`, and its cumulative generation budget with
`--agent-budget SECONDS` or `GOTA_AGENT_BUDGET_S`. Environment settings belong in the
command environment or the worker profile's `.env`. Each flag overrides its matching
environment setting; an absent or empty environment value uses the default:
1200 seconds per round and 1800 seconds across all rounds. Both values must be
positive integer numbers of seconds. Invalid numeric settings stop before manifest
lookup or model execution; with `--json`, malformed flag values also return an
`errored` JSON report and retain startup error reports under the requested output.

Each subprocess gets the smaller of the per-round timeout and the remaining overall
allowance, with up to three rounds per build. The compiler measures generation with
a monotonic clock; assembly and gate time do not consume this allowance. To increase
the allowance on a slower host, raise the overall budget as well as the per-round
timeout. Native preparation and gates are outside the generation budget.
An agent timeout stops the build as `errored`, retains `agent.log` and the partial
`hermes-stream-*.jsonl` under the output directory, and reports the configured duration
and the exact stream filename, including when the timeout consumes the remaining
budget. A completed generation proceeds to assembly and gates even if its elapsed
time reaches the budget. If those checks request a repair but no generation allowance
remains, the build stops as `errored` before another generation. A final-round
structural policy error remains `failed`. Tool timeouts never request a repair, even
when some budget remains. Prebuilt builds ignore both settings because they run no
compiler agent.

The card's configured 5400-second run budget is soft: Hermes injects a wrap-up notice
at 80%, or 4320 seconds, rather than enforcing a build deadline. The card's runtime cap
is hard: the orchestrator creates cards with `max_runtime_seconds` 7200 by default, and
the Hermes dispatcher then stops the worker and re-queues the card. The documented G3/G4
workload takes about 3300 seconds on the test box. The default 1800-second cumulative
generation allowance plus that estimate totals 5100 seconds, leaving about 300 seconds
for other work but exceeding the wrap-up threshold. A repair after a G3 failure repeats
the roughly 55-minute gate workload; two gate runs alone take about 6600 seconds and
can exceed the card budget whatever generation timeout is chosen. Setup overhead,
host speed, and separate local checks can increase the total further.

The timeout choice is informed by timings measured on the EC2 test box, 2026-10-09.
Passed builds had intervals between stream creation and last write of 691 and 185 seconds
for one two-round build, and 210 and 533 seconds for two one-round builds. These are
lower bounds on generation wall time, not exact process runtimes. A 777-second stream
interval belonged to a build that timed out after 900 seconds; another timed-out
stream stopped writing after 20 seconds. The 1377-second interval belonged to an
errored build whose report said the compiler exited 1. Neither is evidence of a
successful slow round. The 1200-second per-round default provides headroom over the
observed successful intervals, and the 1800-second total allows more than one shorter
round. A separate idle timeout
is deferred: these timestamps do not establish a safe maximum quiet interval, and
reliable idle detection would require supervising the process and its output.

The host must install the locked Python dependencies, Nim 2.2.10 and the native
helpers using [native preparation](tools/native_build.py), and install Hermes
with its built-in command protections. Set `GOTA_HERMES_PYTHON` to that Hermes
installation's Python executable, `OPENROUTER_API_KEY`, and `OPENROUTER_MODEL`.
The adapter creates a separate compiler Hermes home and restricts child writes to
compiler scratch. The compiler uses Hermes's native command approval checks;
no separate scanner is required. Output paths, Git refs and the index are checked
after generation before accepting the build. Host provisioning
and credentials remain the installer's responsibility. The front matter's
`host_requirements` lists the variables and tools workers need; a worker that finds one
missing blocks its card with a `help_request:` reason.

Online builds resolve the current league through `"$RESEARCH_CLI" softmax
game-version` and the shared credential-preserving platform transport. Set
`RESEARCH_GAME=gota`, `RESEARCH_CLI`, `HERMES_HOME`, and the profile's
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
(cd "$RESEARCH_HOME" && "$RESEARCH_PYTHON" games/gota/tools/build.py "$PWD/strategy/candidates/E" --out "$PWD/.scratch/build/E" --json)
(cd "$RESEARCH_HOME" && "$RESEARCH_PYTHON" games/gota/tools/build.py "$PWD/strategy/candidates/E" --out "$PWD/.scratch/build/E-historical" --offline --manifest /path/to/2026.10.7.2-manifest.json --json)
```

`evaluate --results RESULTS_JSON --json` returns `data.episodes`, one metric entry
per seat with the real integer seed. `softmax xp evidence` keeps only the design's seats and
reduces them by the arithmetic mean, one row per episode (`games/_template/GAME.md`).
The binding never guesses policy identity. Positional `evaluate RESULTS_JSON
--seat N` retains the local metric-object form.

`upload E --name NAME --build-dir BUILD_DIRECTORY` delegates to
`"$RESEARCH_CLI" softmax upload E --name NAME --build-dir BUILD_DIRECTORY`. Set the variable
to the installed shared CLI executable. That shared tool performs the C6 identity
check immediately before mutation; this wrapper has no alternate upload route.

Choose a descriptive `--name` of at most 23 lowercase letters, digits, or hyphens.
The examples use `opening-rush`; choose a name for your policy and reuse it on retries.
The uploader appends your full player ID, keeping different players’ names distinct.

## Starter policy

The prebuilt `v13-live.bas` is GotA's starter policy and the shipped strategy's policy by construction: `make_v13_live.py` transforms submitted v13, its adjacent provenance file records that relationship, and equivalence to the seed `STRATEGY.md` is shown only by M1 on engine `801749e5`.

`build CANDIDATE_DIRECTORY --out NEW_BUILD_DIRECTORY --json --prebuilt FILE.bas`
freezes the exact committed artifact bytes and runs G1. Add `--full-evaluation`
to run G1–G4 and record G5 as not applicable.
It skips Hermes, model generation and scanner setup entirely. Native preparation,
manifest resolution and the host contract check still apply. G4 compares the
artifact against its own immutable bytes as a baseline mirror; this checks the
screen machinery, not strength against an incumbent. G5 compiler goal traces are
not applicable: its receipt has `applicable: false`, `passed: false`,
`advisory: true`, and summary `not applicable: prebuilt`. It has no executed
`outcome`. Failed required checks (G1 by default; G1–G3 for full evaluation) refuse upload; any tool error stops the build with retained
evidence and refuses upload. No prebuilt build attempts model repair.

For `policy.bas`, the required sidecar is `policy.provenance.json`, not
`policy.bas.provenance.json`. The artifact, adjacent `FILE.provenance.json`, and declared original input must
be inside the candidate directory and committed unchanged. The sidecar schema is
`gota-prebuilt/1`: `artifact_sha256`, `input: {path, sha256}`, `transform`, and
`engine: {commit, coworld_version, source_url, manifest_sha256}`. Input paths are
relative to the sidecar. For handwritten artifacts, use `transform: {name:
identity}`, name the artifact itself as the input, and record its exact hash and
resolved engine identity. For v13-live, the generator below records the original
hash, output hash, transform tool hash and resolved engine automatically. Build
reproduces the transformation before gates and verifies the committed closure
again after gates. The frozen manifest fingerprint must match the resolved engine
before and after each gate; a mismatch stops the build as `errored`.
The frozen sidecar, native receipts, manifest, reports and
`version.json` stay in the build directory; shared upload preserves provenance
under `version.build.compiler.prebuilt_provenance`. In prebuilt mode, the shared
`strategy_sha256` field is the committed BASIC source hash (equal to
`policy_sha256`), and `source_path` names that BASIC file. `compiler.mode` is
`prebuilt`; these fields do not claim STRATEGY.md compilation or semantic proof.

The prebuilt starter policy requires no game test fixtures. This does not
establish fixture-free operation of the full compiler profile.
The installed distribution includes the byte-identical original at
`games/gota/baselines/submitted-v13.bas`, with its historical origin in the
adjacent provenance file. On a fresh install, use this shipped input to make
v13-live, the first champion. Work inside the card's worktree; the
bootstrap candidate has no experiment id. Resolve the installed environment and
keep output in the card's scratch space:

```sh
RESEARCH_HOME="$(dirname "$(dirname "$RESEARCH_CLI")")"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
CARD_WORKTREE="$PWD"
CANDIDATE="$CARD_WORKTREE/strategy/candidates/bootstrap"
BUILD="$CARD_WORKTREE/.scratch/build/champion"
mkdir -p "$CANDIDATE"
cp "$RESEARCH_HOME/games/gota/baselines/submitted-v13.bas" "$CANDIDATE/submitted-v13.bas"
git -C "$CARD_WORKTREE" add strategy/candidates/bootstrap/submitted-v13.bas
git -C "$CARD_WORKTREE" commit -m "Record exact submitted v13 bootstrap input"
(cd "$RESEARCH_HOME" && "$RESEARCH_PYTHON" games/gota/tools/make_v13_live.py "$CANDIDATE/submitted-v13.bas" "$CANDIDATE/v13-live.bas")
git -C "$CARD_WORKTREE" add strategy/candidates/bootstrap/v13-live.bas strategy/candidates/bootstrap/v13-live.provenance.json
git -C "$CARD_WORKTREE" commit -m "Record mechanical v13-live bootstrap candidate"
```

Run the build below in its own terminal call. Default compilation does not wait
for full local evaluation. Cold native preparation may still take time; use the
card's long-command procedure when needed and retain its session ID.

```sh
RESEARCH_HOME="$(dirname "$(dirname "$RESEARCH_CLI")")"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
CANDIDATE="$PWD/strategy/candidates/bootstrap"
BUILD="$PWD/.scratch/build/champion"
(cd "$RESEARCH_HOME" && "$RESEARCH_PYTHON" games/gota/tools/build.py "$CANDIDATE" --out "$BUILD" --prebuilt "$CANDIDATE/v13-live.bas" --json)
```

After exit, read the command JSON, `$BUILD/report.json`, `$BUILD/report.md`,
`$BUILD/version.json` and `$BUILD/gates.json`. Check the recorded gate outcomes,
engine/native receipts, frozen `$BUILD/policy.bas`,
`$BUILD/prebuilt.provenance.json` and `$BUILD/coworld_manifest.json` before
upload. Apply the **Local validation** evidence requirements above. Preserve error
reports if the build stops.

Generation resolves the current engine and makes only the authorized
identifier/division token changes, with no model call. For an explicit historical
build, append `--offline --manifest PATH` to both generator and build. Existing
outputs are immutable; use fresh output paths.

The default build records G1 compilation and explicitly marks evaluation not run. Review its
receipts under **Local validation**; G5 compiler coverage does not apply to prebuilt
artifacts. After a successful build and evidence review, follow the core `candidate` skill's
"Bootstrap card" procedure. The orchestrator makes a bootstrap card only when no champion and
no promotion in flight exist. Upload the starter build:

```sh
"$RESEARCH_CLI" softmax upload-build --name opening-rush --build-dir "$BUILD"
```

The first champion's version is uploaded by `softmax upload-build`, never by
`softmax upload`. It needs no experiment, frozen evaluation design, evaluation outcome or
decision. The first league submission is v13-live, unevaluated; local checks do not establish
competitive strength. The command checks C6 identity and the build, uploads the exact artifact
and records a version with `experiment: null` that keeps the prebuilt provenance. Its response
includes `data.version_id` and `data.platform_policy_version_id`. Record them in the journal's
Decisions and in the `kanban_complete` metadata, run `workspace integrate`, and complete the card. The
orchestrator then submits the version with `promotion bootstrap` and records the champion. The
shipped `strategy/STRATEGY.md` remains the champion strategy source.

## Core loop: the recommended starter

For a coach's direct request (the `core-loop` skill) the starter is the coworld's published
player file, not a compiled strategy. Take the coworld id from the card or the coach line, and
`PLAY_URL` from the same place (it is the coworld's `play.md`).

1. Read `PLAY_URL` (`curl -fsSL "$PLAY_URL"`). It names the recommended player file's sha256.
2. Fetch it: `GET {server}/v2/coworlds/{coworld_id}/player-files/{sha256}` into
   `.scratch/starter/` (`{server}` is `${COGAMES_API_URL:-https://softmax.com/api}`; send no
   credential unless the server answers 401). Verify `sha256sum` of the bytes equals the
   requested sha256 before using them. A mismatch, a 404, or any other status stops the card:
   report the exact URL and status.
3. Put the file at `strategy/candidates/core-loop/<name>.bas` (the requested change applied
   to the text, or unchanged for a first program) and commit it. These gates are REQUIRED to
   produce an uploadable program and are the only ones to run: the prebuilt build below runs
   G1-G4 (G1-G3 block upload). Skip every research gate: no noise record, no stats design,
   no baseline comparison, no `local_check`, no seeds.
4. Write the identity provenance sidecar `<name>.provenance.json` (`gota-prebuilt/1`, transform
   `identity`, the artifact as its own input) and commit it:

```sh
RESEARCH_HOME="$(dirname "$(dirname "$RESEARCH_CLI")")"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
CANDIDATE="$PWD/strategy/candidates/core-loop"
(cd "$RESEARCH_HOME" && "$RESEARCH_PYTHON" - "$CANDIDATE" <name> <<'PY'
import json, sys
from pathlib import Path
from games.gota.tools.engine_identity import resolve
from games.gota.tools.prebuilt import ENGINE_FIELDS, digest, dump
candidate, name = Path(sys.argv[1]), sys.argv[2]
artifact = candidate / f"{name}.bas"
identity = resolve(None, offline=False)
dump(candidate / f"{name}.provenance.json", {
    "schema": "gota-prebuilt/1", "artifact_sha256": digest(artifact),
    "input": {"path": artifact.name, "sha256": digest(artifact)},
    "transform": {"name": "identity"},
    "engine": {key: identity[key] for key in ENGINE_FIELDS}})
PY
)
git add strategy/candidates/core-loop && git commit -m "Record core-loop program <name>"
```

5. Build (a long command, as above; run it in the background and wait):
   `build "$CANDIDATE" --out "$PWD/.scratch/build/core-loop" --prebuilt "$CANDIDATE/<name>.bas" --json`.
   Read the exit code and `version.json`; G1-G3 must pass and G4 must have run without a tool
   error (G4 policy failures are advisory: report them, do not stop). Then upload with
   `"$RESEARCH_CLI" softmax upload-build --name <name> --build-dir .scratch/build/core-loop`.

## Qualification scope

Live readback on 2026-10-08 resolved 2026.10.8.1 / Polyworld
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`; native preparation, baseline compilation,
the supported host contract, and one full native 31200-tick mirror match were
checked locally. All ten seats ran with no VM errors. This does not extend the
prior paid M1 semantic qualification, which remains bound to its immutable
2026.10.7.2 / `801749e509e888b423ba44ee2188946c22e3ba85` receipts. The historical
oracle and audit keep those identities; a new engine needs its own qualification.

G1 failures block default builds and may enter compiler repair rounds. With
`--full-evaluation`, G1–G3 policy failures block the build. G4 and G5 then run and carry `advisory: true` in build receipts. Their `failed`
outcomes appear in `report.json` warnings and the Markdown report, while a build
with passing G1–G3 remains passed. An `errored` outcome from any gate stops the
build immediately and makes it errored, including advisory gates. Local check
returns 0 for advisory failures, 1 for blocking failures, and 3 for tool errors.

### Rejected build inputs

Initial caller-input validation returns `status: rejected`, `error.reason: invalid_input`,
`error.details.path` and `error.details.action`, with exit 2. It runs no gates and
provides no uploadable artifact. Correct and commit truthful inputs, preserve the
rejected report, and retry with a new output directory. A rejection is not policy
evidence. Engine, native receipt, unexpected runtime, and post-start input-integrity
faults remain `errored` and stop the build; do not retry those as input corrections.

Only explicit input rejections have this recovery contract. Malformed provenance
JSON or missing schema fields are not classified by this change; unexpected parser
exceptions still produce `errored`. Do not infer retryability from exception text.
