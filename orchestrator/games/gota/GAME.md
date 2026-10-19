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

Treat `build` and `local_check` as long commands: their G3/G4 workload takes
approximately 55 minutes, measured on the autoresearch EC2 test box
(m7i.xlarge), 2026-10-08. This is not a separate timing for each gate or for
model generation. Host speed, policy, cache reuse and compiler repair rounds
can change the total. See
[gate durations](gates/README.md#durations-and-waiting) for the scope.
Follow the core `card` skill's **Long commands** procedure: start each build
or full local check with the terminal tool's `background: true`, keep its
`session_id`, then call `process_manage` with `action: "wait"`, that `session_id`
and `timeout: 1800`. Repeat `wait` only on `status: "timeout"`; do nothing else
between waits. Do not poll, read logs repeatedly, or use sleep/probing loops.
Other episode runs, Nim replay-expander builds, `gota_local.py`,
`gota_episodes.py` and replay inspection use this same pattern unless known to
fit a foreground terminal `timeout` of at most 600 seconds.

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

A completed build includes local validation. The build and `local_check` invoke the
same shared gate modules. For a successful build, review its retained evidence
instead of launching a second full `local_check` on the same artifact and execution
conditions. This applies to experiment candidates and bootstrap builds.

Require `status: passed` and complete applicable receipts in `version.json`: G1–G3
must pass; G4 must have run without a tool error. Compiler builds also require an
executed G5 without a tool error. For prebuilt builds only, G5 must explicitly say
`applicable: false` and `not applicable: prebuilt`. G4/G5 policy failures remain
advisory and must be reported. Missing, incomplete, failed, or errored build evidence
does not qualify for reuse. Follow the build failure procedure; a standalone check
cannot turn a failed build into an uploadable one.

Read `report.json`, `report.md`, `version.json`, the frozen `policy.bas`, manifest,
native receipts and measurement reports, and the G2 episode results/logs named by
`summary.episode_dir`. Verify the artifact SHA256 matches `policy_sha256`. Reuse
applies only to the recorded engine, manifest, runner, baseline, seeds, tick budget,
and native tool identity. Do not reuse evidence for changed artifact bytes or
execution conditions: produce a new build for upload. Standalone `local_check`
remains available for diagnostic reruns or artifacts without completed build evidence;
use the gate reference for its arguments and retain its reports.

No gate, seed, or budget is reduced. The upload reader checks the build status,
reported gates and artifact hash; it does not consume a separate local-check directory.
Local gate success establishes compatibility, not competitive strength or a promotion.

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
freezes the exact committed artifact bytes and runs the existing G1–G4 gates.
It skips Hermes, model generation and scanner setup entirely. Native preparation,
manifest resolution and the host contract check still apply. G4 compares the
artifact against its own immutable bytes as a baseline mirror; this checks the
screen machinery, not strength against an incumbent. G5 compiler goal traces are
not applicable: its receipt has `applicable: false`, `passed: false`,
`advisory: true`, and summary `not applicable: prebuilt`. It has no executed
`outcome`. Failed G1–G3 refuse upload; any tool error stops the build with retained
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

Run the build below in its own terminal call with `background: true`. The G3/G4
workload is approximately 55 minutes, measured on the autoresearch EC2 test box
(m7i.xlarge), 2026-10-08. This is not a measured total for every host or a separate
55-minute timing for each gate. A completed build includes local validation; do not
repeat its full gate workload before upload. Keep the build `session_id`
and call `process_manage` with `action: "wait"`, that `session_id`,
`timeout: 1800`; repeat only if `status: "timeout"`, with no polling or sleep
loops. Each long-command block below sets its own paths for a fresh shell;
start it from the card's worktree.

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

The build records G1–G4 local checks and retains the full evidence. Review those
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
