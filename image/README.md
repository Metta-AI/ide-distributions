# Self-contained workspace image

The workspace coordinator owns native admission and periodic watcher execution. The full plan is
[workspace orchestration](../docs/specs/workspace-orchestration.md).
The existing lifecycle v1 sleep/wake behavior remains enabled. Explicit Stop and idle sleep
use pause, drain, settle, and flush; lifecycle v2 is a separate future change.
Lost native receipts remain execution obligations until the supervisor recycles the container.
The next boot closes those obligations without replaying their inputs. This is automatic recovery,
not a forced platform hold or an operator command.

This image builds on distribution PR #116's `research workspace sleep` and
`research workspace wake`. It includes PR #117's source-acquisition and routed-profile fixes.
The image owns profile installation, dependency resolution, internal paths, gateway setup,
watcher activation and the translation from generic lifecycle requests to research commands.

Build from the repository root:

```sh
docker build --platform linux/amd64 -f image/Dockerfile -t ide-distribution:local .
```

The host supplies a durable `/workspace`, writable `/tmp`, and a read-only
`/run/workspace/bootstrap.json` containing `workspace_id`, `platform_url`, and
`credential`. Run the image entrypoint as its declared numeric user, with a read-only
root filesystem and no Linux capabilities. No source checkout or dependency installation
is needed outside the image.

The hosted image selects its bundled plugins through Hermes's supported
`HERMES_BUNDLED_PLUGINS` setting. It preserves the base image's plugins except
messaging-platform adapters: hosted traffic uses the built-in API transport.
Standalone orchestrator and researcher installations retain their normal plugin selection.

The lifecycle adapter sends replies over the platform's authenticated outbound workspace
connection. Explicit sleep starts the distribution command asynchronously; status reports
ready for sleep only after its successful exit. The image also initiates sleep after fifteen
minutes without work. Restart calls wake with the retained filesystem.

The release dispatcher passes the distribution commit to Metta's generic build workflow.
That workflow builds `image/Dockerfile`, runs `image/system_tests/start-stop.sh`, and publishes
an immutable image digest. Hosting consumes that digest without inspecting profile internals.
Deploy Metta #30090's format readers and release workflow before dispatching this release.

The image also owns the ide-next application adapter. It exposes native session opening,
durable message admission and cancellation, workspace snapshots, transcripts and run controls.
Chat messages are confirmed only after the workspace commits them. Each operation ID names
one input; reconnecting with that ID cannot create a second run. Messages wait behind the
previous run in their session, and queued messages survive sleep and restart.

Session opening and message intake perform only local validation and durable writes. They
never acquire the execution lock. One serial dispatcher creates native sessions, prepares
attachments and starts Hermes runs after intake. A newly accepted session has an empty
transcript until its native session is ready.
The open receipt and local session registration commit in one transaction. A temporary native
opening failure stays queued with its original identity; a permanent failure also fails its
queued messages. Neither stops the dispatcher from processing unrelated sessions.

Each successful intake emits one `workspace_intake` JSON event to stderr, correlated by
`op_id`, `session_id` and operation. Durations in milliseconds distinguish validation,
store opening, metadata preparation, writer acquisition, writing, commit, session registration
and total intake time. `writer_wait_ms` includes SQLite transaction setup. Replayed requests
emit the same measurements with `replayed: true`. Events contain no message or attachment
contents. Track total intake latency and its stage breakdown independently of attachment
preparation and Hermes execution; a slow writer wait indicates database contention rather
than time spent preparing or running a message.

The image obtains its spend-capped model credential from
`PUT /v2/workspaces/{workspace_id}/inference`, using the bootstrap credential. It installs
and renews the key internally; hosting never supplies profile paths or model environment
variables. Deploy that platform endpoint before this image. Queued chat dispatch waits for
a valid inference lease, including after restart. Lifecycle readiness remains independent
of the optional model service, so an unavailable budget does not prevent stopping a workspace.

A spent key is not an expired one, so the image also keeps the *inference state*,
`.inference-state` beside the lease file (`core/src/research_core/inference.py`). The
profiles' `api_request_error` hook sets `budget_exhausted` when Hermes classifies a model call
as `billing` (a 402, or OpenRouter's 403 "Key limit exceeded"), except within two minutes of a
key change, when such errors come from runs still finishing on the previous key. It sets
`provider_unavailable` for five minutes after three outage errors within three minutes. The
lease loop sets `budget_exhausted` when the platform refuses a lease for that reason, and
renews no later than the refill (the lease's expiry minus the platform's ten-minute grace).
A budget block names the spent key's fingerprint and holds only while the lease file names
that same key, so installing a different key ends it with no second write that could be
lost. A budget block's `until` is the refill time shown to the user; the block itself holds
until the new key arrives, so the gate does not open on the spent key in the seconds after
the refill. An outage block lapses at its `until`. The lease file write is essential and a
failure raises; state writes run off the event loop, and a failed one is logged rather than
stopping the workspace. While the state blocks, chat, wakes and Researchers start no
runs, and `session.message` answers 503 with
`{"kind": "inference_unavailable", "reason": "budget_exhausted", "until": "<ISO time>"}`
(`InferenceUnavailable` in `image/contract.py`; `reason` and `until` are null when the lease
is only missing). Work resumes on its own once the state clears or lapses.

## Application contract

`image/contract.py` owns the existing application's request, reply and run-event schemas.
`contract/openapi.json` is generated from those exact Pydantic models; the adapter imports them.
This is a schemas-only OpenAPI 3.1 artifact, not an additional HTTP API. The platform
continues to own authentication, lifecycle and the opaque relay envelope.

Generate with `python tools/export_contract.py`; CI runs it with `--check`. Operation
requests are a closed union discriminated by `name`, with `op_id` and operation-specific
`args`. Extra request fields are rejected. `actor` is supplied by the image, never a client.
Context remains JSON. Native reply extensions are allowed; opaque snapshot records and
run-control replies make no stronger guarantees than their declared schema.

Metta vendors the artifact from an immutable agent commit and records its SHA-256. Its
frontend types, runtime request validator and browser fixture consume that artifact.
Changing a public shape requires regenerating the artifact and checking both consumers
before release. Existing operation names and wire bodies remain unchanged.
The separate AIDE v1 proposal owns the future bidirectional protocol.

The normal image smoke checks execute the real packaged adapter against its running
seam, including the previously accepted-by-the-stub invalid actor field. Real model
chat remains a target-environment acceptance check with an authorized inference lease.

Run `bash image/system_tests/start-stop.sh ide-distribution:local` after building. The smoke
uses a fresh volume, then restarts with the same volume under the hosting filesystem
restrictions. It checks adapter operations, explicit clean-sleep refusal, retained sessions and transport failures.
It also validates all sixteen startup timing events on each boot: profile install/update,
preparation, gateway readiness, application wake/readiness and total startup. Missing phases,
reused boot IDs, malformed events and extra telemetry fields fail the check.
Startup timing events are separate from the per-request intake events described above.

## Repeatable startup measurements

Use the same built image, machine and resource limits for before/after comparisons:

```sh
STARTUP_CYCLES=6 STARTUP_ARTIFACTS=/tmp/ide-startup-baseline \
  bash image/system_tests/start-stop.sh ide-distribution:local
```

The artifact directory must not already exist. It retains the image ID/architecture, checkout
revision, per-boot logs and per-stage median/minimum/maximum durations. The first boot uses an
empty volume; later boots retain state and run the same adapter, sleep and persistence checks.
`workspace_state` in each timing event distinguishes fresh from previously prepared workspaces.
This is workspace state, not whether the Kubernetes host or image cache was cold.

The readiness wait has a four-minute wall-clock budget between Docker probes. A stalled Docker
request can exceed that budget; Docker must be healthy before interpreting a benchmark.
Container timing ends at application health. Platform control readiness and browser usability
remain separate gates; the smoke's deliberately unavailable platform does not prove either.

The startup comparison workflow builds the PR base and candidate from their own source and
lockfile on one Linux runner. Each image runs one fresh boot and five retained boots with the same smoke command and
the hosted workspace limit of two CPU cores and 2 GiB memory. This compares the complete
proposed runtime change. Both image identities, import profiles, bytecode counts, and all per-boot
timing reports are retained as artifacts.

On container stop, the supervisor cancels ongoing startup/control work, terminates its owned
application and gateway processes, and waits for them to exit. A child that ignores termination
is killed after ten seconds. The smoke requires a zero container exit code and retains gateway
file logs alongside startup events when STARTUP_ARTIFACTS is set. These fixture logs contain no
real model credentials or user conversations.

## Startup timing

`workspace_startup` JSON lines join on `workspace_id` and `boot_id`, with `workspace_state`
set to `fresh` or `retained`. `startup_total` ends at application health. Two concurrent
phases follow: `relay_connected` ends after the authenticated websocket handshake, and
`inference_ready` ends only after the first lease is installed in every profile and its
expiry marker is persisted. Each reports started/passed and elapsed seconds. Missing
passed events indicate incomplete readiness, not zero latency. Renewal and reconnection
do not duplicate these first-readiness events.

These milestones do not claim a completed model turn. Use Metta's packaged chat harness
for container-to-completed-chat measurements and retained-session verification.

The container starts the gateway through Hermes's supported `python -m gateway.run` entrypoint.
The general CLI remains installed for tools and profile operations; its full dispatcher is not
needed to run the container-owned gateway. Packaged restart and transport checks exercise this
entrypoint, including gateway interruption and recovery.

Hermes's base image disables runtime bytecode writes, and hosting mounts its root filesystem
read-only. The image compiles Hermes's application modules and installed dependencies at build
time so each gateway restart can load cached bytecode. Tests and optional skill examples are
excluded from source compilation. The comparison artifact also contains gateway import timings
and bytecode counts for both images; compilation benefit must be established by the boot results.

The hosted local speech-model path is applied by profile preparation through Hermes's existing
configuration API. It is included in `profile_prepare` timing. Startup does not launch a separate
`hermes config set` process per profile. `IDE_LOCAL_STT_MODEL` selects the bundled model;
standalone preparation without that setting preserves its existing speech configuration.
The startup comparison workflow builds the PR base and candidate from their respective source
and lockfiles, then measures a fresh boot and five retained restarts for each.

## Container policy builds

The image packages `buildctl` alongside the locked Coworld CLI. Hosting supplies a private
BuildKit endpoint in `BUILDKIT_HOST`; profile preparation passes it to terminal commands.
A mixin builds with `coworld build-policy <context> --output <archive>` and declares
`artifact.kind: image`. Keep the archive outside the Docker build context. `version.create`
retains the archive by its byte digest; `upload` saves the image config digest separately
for the platform's upload lookup. Recovery needs those saved inputs, not the archive.

`image/system_tests/check-build-upload.py` is an opt-in proof inside the built image with a
real builder and platform Player credential. Pass `SOFTMAX_PLAYER_CREDENTIAL` and
`SOFTMAX_PLAYER_ID` as JSON on stdin, and provide writable `/workspace` and `BUILDKIT_HOST`.
It creates one uniquely named policy with two versions, verifies an ordinary upload and
recovery after a deliberately lost response and deleted archive, and never submits or evaluates.

## Distribution tool timing

`workspace_tool` schema version 1 is one JSON stderr line per completed `run_tool` invocation.
It covers registered distribution tools in both executors. Workspace subprocess telemetry is
relayed once by the plugin; the parent does not emit a second event. Native Hermes built-in
tools are outside this boundary.

Fields are `session_id` (native session, possibly empty outside Hermes), `role`, `tool`,
`action` (registered action or null), `outcome`, and `elapsed_ms`. Unknown tool names are
recorded as `unknown`. No arguments, results, prompt content or error text are emitted.
`elapsed_ms` uses a monotonic clock from tool-module loading through validation, execution,
JSON rendering and state-path scrubbing. It excludes workspace subprocess startup, Hermes
post-result processing, model calls and transport delivery. A timeout or terminated subprocess
can exit without an event; this is completion coverage, not an attempted-call denominator.

Outcomes distinguish `validation_rejected` (handler never ran), `tool_rejected` (`ToolError`),
`store_rejected` (`StoreError`), `internal_error` (unexpected error), `failed` (handler returned
false `ok`), `succeeded`, and `unknown_tool`. Count events, rather than summing elapsed time,
for tool counts; concurrent tool durations can overlap. Session IDs are join fields, not
metric tags. There is no inferred run ID or provider cache classification.

These are structured logs, not custom Datadog metrics. Release the normal distribution image
and verify log ingestion before making dashboard availability claims. A current model trace,
time to first token, provider cached-token usage, session restoration and cache-miss evidence
must come from the supported native runtime/provider surfaces. Retained workspace startup
alone does not prove a warm model context or provider key/value cache hit.

## Workspace turn breakdown

The profile's supported native lifecycle hooks emit payload-free `workspace_model_attempt`,
`workspace_native_tool` and `workspace_turn` JSON lines. Every record has `schema_version: 1`
and `stage: terminal`; these are completion records, not separate start and stop counts.
`role` is `orchestrator` or `researcher`. Native session/task/turn/request/tool-call IDs remain
log join fields; they must not become metric tags.

A model attempt runs from the observed `pre_api_request` callback to the native response end.
Its `elapsed_ms` is **not** Hermes's cumulative retry-window duration: that is retained as
`native_elapsed_ms` with `native_started_at`. `call_index` is the native logical request
index; `attempt_index` counts observed starts for that request, and `native_retry_count`
preserves Hermes's retry counter. `inter_attempt_gap_ms` includes retry wait and preparation;
it is not a separately measured backoff. `first_chunk_ms` and `post_first_chunk_ms` divide
an attempt only when the native hook supplies a first chunk. A chunk may contain reasoning
or tool data; neither field claims first-token or first-visible-text latency.

Native tool `elapsed_ms` measures observed pre/post hook boundaries, while `execution_ms`
is Hermes's separately reported execution duration. Missing pre callbacks produce null
bounds, not inferred start times. `status` is the native semantic status: an error result,
including a rejected distribution tool call, reports `error` and `outcome: failed`.
Repeated calls have distinct native IDs and an increasing `repeated_tool_index`; repetition
alone does not establish a bad retry. These records include native built-ins as well as
registered distribution tools. Do not add their counts to `workspace_tool` counts.

`workspace_turn.elapsed_ms` spans `pre_llm_hook_to_session_end`, after session/prompt setup.
Model and tool busy fields are clipped interval unions. `accounted_busy_ms` is their combined
union; `overlap_ms` records double coverage; `unaccounted_ms` is the remaining wall time.
`accounted_busy_ms + unaccounted_ms = elapsed_ms`, within millisecond rounding. Incomplete
callback pairs set `accounting_complete: false` and explicit missing counts. Exclude those
component estimates from complete-accounting distributions.

An exact existing native-message-row binding can attach the client `op_id`, its actual stored
`run_id`, and `workspace_total_ms` from durable message acceptance to session-end hook.
`admission_to_dispatch_ms` includes pre-dispatch waiting and preparation; `dispatch_to_hook_ms`
includes native admission and session/prompt setup. They are not pure queueing or restoration.
Their sum plus the hook window reconciles this wider envelope. Unbound/background turns keep
these values null. Terminal hook completion precedes native terminal publication and browser
delivery; those are separate boundaries.

`workspace_session_setup` measures the supported native create-session HTTP request. Its
`native_create_session_http` boundary reports `accepted`, `existing` or `failed`. It does not
attest agent reuse, restored context or a warm provider cache. `is_first_turn` only describes
conversation-history presence. Session restoration, provider cache-miss classification and
isolated retry backoff remain explicitly unavailable. Cache usage comes from native canonical
usage; zero loses provider field-presence information, so zero/absent cache buckets are null
with `cache_reporting: unavailable_or_zero`. Positive buckets are `positive_normalized`.

Search parsed Datadog attributes, for example
`service:hermes-runtime @event:workspace_turn @schema_version:1 @stage:terminal`.
Quoted JSON keys alone do not search the parsed attributes. Verify these events in the normal
packaged gateway's container logs and then in the adopted runtime's Datadog logs before
claiming deployed coverage. Historical message-to-message gaps are not model or tool spans.

The hook contract is pinned by `image/Dockerfile`. Relevant native implementations are
`agent/turn_api_request.py`, `agent/turn_response_intake.py`, `agent/api_request_hooks.py`,
`model_tools.py`, `agent/turn_context.py` and `agent/turn_finalizer.py`.
`core/tests/hermes/test_prepare_real.py::test_installed_native_timing_captures_model_and_rejected_tool`
checks installed profiles with the pinned agent and a scripted external HTTP provider. It
covers rate-limit retry timing, canonical cache usage, semantic tool rejection and interval
reconciliation without spending model credits. This component proof does not attest the
container logging path or deployed Datadog ingestion.

Some native fatal-error paths return before `on_session_end`. The existing coordinator emits
`workspace_run_observation` after its authoritative terminal transition, including failed runs
held for retry. This event's `message_acceptance_to_terminal_observation` envelope includes
polling delay: `native_terminal_at` and `terminal_observation_delay_ms` are unknown. It carries
native `completed`, `failed`, `cancelled` or `interrupted` outcomes. Use it for observed native
receipt/failure counts and coverage checks, separately from precise hook-window distributions.
Neither source reconstructs timings lost during a process crash.

The image passes non-secret `IDE_WORKSPACE_ID` and `IDE_RUNTIME_ID` to its child processes.
Timing records expose them as `workspace_id` and `runtime_id` log attributes; standalone CLI
invocations leave them null. Neither belongs in metric tags.

A fatal native return can omit its end hook. Before retaining a new turn, the observer reads
an authoritative snapshot of the pinned runtime's active conversation leases. The public
`SessionDB(read_only=True)` constructor resolves the routed profile database; a separate
SQLite connection uses `mode=ro`. The current hook's own turn must appear in that snapshot
before any prior state is discarded. Only absent/released/expired native leases are reclaimed,
including orphaned turns in other sessions; active model calls, tools and backoff are retained.
The pinned table/holder contract is checked by the installed-agent and packaged-gateway tests.
Supported finalize/reset/delete hooks also discard session state. No missing end timing is
invented; `workspace_timing_state` diagnostics explain the discard.

The profile retains at most 256 unfinished timing records. Reaching that diagnostics budget
declines tracking for a new turn; it never evicts an active concurrent turn or times out long
work. Subsequent callbacks for an untracked turn emit typed incomplete diagnostics rather
than fabricated durations or a summary. Watch `tracking_capacity` and `untracked_callback`
reasons when checking coverage. Request errors alone never discard state: fallback may follow.
