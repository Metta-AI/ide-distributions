# Self-contained workspace image

The workspace coordinator owns native admission and periodic watcher execution. The full plan is
[workspace orchestration](../docs/specs/workspace-orchestration.md).
The existing lifecycle v1 sleep/wake behavior remains enabled. Explicit Stop and idle sleep
use pause, drain, settle, and flush; lifecycle v2 is a separate future change.
Lost native receipts remain execution obligations until the supervisor recycles the container.
The next boot closes those obligations without replaying their inputs. This is automatic recovery,
not a forced platform hold or an operator command.

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

Session intake emits OpenTelemetry `session.open` and `session.message` spans, correlated
by `op.id`, `session.id`, and `replayed`. Child `intake.transaction` spans mark writer wait,
acquisition, admission write, registration, and commit with SDK-timestamped events. These
boundaries preserve the distinction between database contention and execution preparation.
No message or attachment contents are captured. See [OpenTelemetry](#opentelemetry) below.

The image obtains its spend-capped model credential from
`PUT /v2/workspaces/{workspace_id}/inference`, using the bootstrap credential. It installs
and renews the key internally; hosting never supplies profile paths or model environment
variables. Queued chat dispatch waits for
a valid inference lease, including after restart. Lifecycle readiness remains independent
of the optional model service, so an unavailable budget does not prevent stopping a workspace.

A spent key is not an expired one, so the image also keeps the *inference state*,
`.inference-state` beside the lease file (`core/src/research_core/inference.py`). The
profiles' `api_request_error` hook sets `budget_exhausted` when Hermes classifies a model call
as `billing` (a 402, or OpenRouter's 403 "Key limit exceeded"), except for a 402 that says a
smaller response is still affordable, and within two minutes of a key change, when such errors
come from runs still finishing on the previous key. It sets
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
before release.

## AIDE 1.0.0 backend

`image/aide.py` also connects every workspace to the platform's AIDE backend socket
(`/v2/workspaces/{id}/aide/1.0.0/backend`, subprotocol `aide.1.0.0`, the bootstrap credential)
and answers the [AIDE 1.0.0](https://softmax.com/docs/aide) JSON-RPC methods. It runs beside the
legacy connection, gates neither readiness nor sleep, and reconnects forever; no frontend reaches
it until the platform routes one to it. The contract stays in Metta; this image does not
bundle or validate it, the relay does.

Each method maps to the same seam and gateway calls as the legacy adapter. The AIDE request id
is the seam `op_id` or gateway `Idempotency-Key`, so retries need no new tables. The gateway
forgets a prompt once it is resolved, so an applied `input.respond` is also recorded at the
seam's `/input-response`, which an identical retry replays. Notifications go to the frontends
that named a session in the last ten minutes: run output (`chat.event`, `chat.chunk`,
`chat.message`, `chat.finished`) and session changes from the seam's event stream
(`state.changed`). Coworld sessions without a league have no AIDE scope and are not listed.

Run progress is durable. One consumer per open request reads the gateway's run stream,
translates each event to an AIDE `ChatEvent` (consecutive text deltas merge for 250 ms), appends
it to the workspace store's `request_events` log through the seam (`POST /message-events`), and
only then pushes it. `chat.events` reads that log alone (`GET /message-events`); the gateway's
in-memory run buffer only bridges a dropped run stream. Consumers start with the backend and
resume after their log's last row. A restart recycles the gateway too, so what had not reached
the log and pushes not yet sent are lost. A run the gateway lost ends `interrupted` once the seam
settles it, and its logged events stay readable for as long as the request record.

A session the gateway does not have reads as having no messages, so its requests still close. A
consumer follows its run stream again when the stream drops or a read for a push fails, since
neither moves its place in the log. Any other failure in a consumer, or a seam error answer to the
event watcher's `recover()` or `changed()`, ends the backend, which recycles the whole runtime.
That includes an append with no answer, such as a timeout: it may have committed, and the consumer
has already moved past that event, so going on would leave a gap. The next backend resumes from
the log, so an append that committed before the timeout is not pushed again and only `chat.events`
returns it. Nothing retries the failed call, so a failure that lasts across boots can crash-loop
the workspace.

A frontend subscribes to a session's pushes through any request naming that session, once the
backend has read the session and its scope is that session's own. A wrong scope is refused
(`forbidden`) to that request alone and is not recorded, because the event watcher reads with the
recorded scope and a refusal there would end the backend.

The backend's seam calls time out after 40 s (`SEAM_TIMEOUT_S`), which a write waiting out a default
`research store quiesce` stays inside. A write that had already begun waiting when the hold began
keeps its usual five seconds, and its `database is locked` answer recycles the runtime as above: 1 of
25 holds in a test at about 60 writes a second. A `--max-hold` above the default 30 s has no margin
either.

The normal image smoke checks execute the real packaged adapter against its running
seam, including the previously accepted-by-the-stub invalid actor field. Real model
chat remains a target-environment acceptance check with an authorized inference lease.

Run `bash image/system_tests/start-stop.sh ide-distribution:local` after building. The smoke
uses a fresh volume, then restarts with the same volume under the hosting filesystem
restrictions. It checks adapter operations, explicit clean-sleep refusal, retained sessions and transport failures.
It validates eight completed startup spans on each boot: profile install/update, preparation,
gateway readiness, application wake/readiness and total startup. Missing phases, reused boot
IDs, broken parent relationships and unexpected startup attributes fail the check.

## Repeatable startup measurements

Use the same built image, machine and resource limits for before/after comparisons:

```sh
STARTUP_CYCLES=6 STARTUP_ARTIFACTS=/tmp/ide-startup-baseline \
  bash image/system_tests/start-stop.sh ide-distribution:local
```

The artifact directory must not already exist. It retains the image ID/architecture, checkout
revision, per-boot logs and per-stage median/minimum/maximum durations. The first boot uses an
empty volume; later boots retain state and run the same adapter, sleep and persistence checks.
`workspace.state` on the startup span distinguishes fresh from previously prepared workspaces.
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
file logs alongside startup spans when STARTUP_ARTIFACTS is set. These fixture logs contain no
real model credentials or user conversations.

## Startup tracing

`workspace.startup` spans join on resource attributes `workspace.id` and
`service.instance.id` (the boot ID). `workspace.state` is `fresh` or `retained`.
The root ends at application health. Child spans cover profile installation/preparation,
gateway readiness, and application wake/readiness. Concurrent `workspace.inference_ready`
and `workspace.relay_connected` spans end at first credential installation and first
websocket handshake respectively. Cancelled readiness is an error observation, never a
successful zero-duration milestone. Missing completion can also mean process termination.
Renewal and reconnection do not duplicate these first-readiness spans.

These milestones do not claim a completed model turn or browser delivery.

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

## OpenTelemetry

All timing/tracing uses the OpenTelemetry Python SDK. The locked workspace package and
native build requirements select version 1.45.1, already shipped in the pinned Hermes image.
No packages install at runtime. Standalone profile installations need the same native SDK.

Every process uses the SDK's `ConsoleSpanExporter` to emit one standard span JSON object per
line on stderr. This is enabled without a feature flag or collector. Span `start_time` and
`end_time` replace custom elapsed fields. Existing dashboards querying `workspace_startup`,
`workspace_intake`, or `workspace_tool` need migration to these spans; old events are removed.
Console JSON is diagnostic output, not the OTLP wire format.
A tool of an action's own reports as the grouped tool and action it stands for: `claim_write` as
`gen_ai.tool.name` `claim` and `tool.action` `write`, the same as a `claim` call naming `write`.

To export to any OTLP/HTTP backend or an OpenTelemetry Collector, configure
`OTEL_EXPORTER_OTLP_ENDPOINT` (base URL) or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (full trace URL)
on the workspace container. The SDK handles protobuf, batching, retry, TLS and shutdown.
Standard `OTEL_EXPORTER_OTLP_HEADERS`, timeout, certificate and batch-processor settings apply.
The supervisor inherits these settings into both interpreters. Export is asynchronous with a
bounded SDK queue; backend outages or abrupt process termination can lose spans. Console
output remains independent. `OTEL_SERVICE_NAME` defaults to `ide-distributions`.

Hosting must configure an accessible, authorized endpoint before claiming remote trace delivery.
The distribution neither provisions a collector nor assumes a Datadog endpoint. Changing
backend routing requires exporter/collector configuration, not instrumentation changes.
The pinned Relay plugin configuration explicitly disables its separate prompt/response
exporter, so configuring an OTLP endpoint does not enable content capture.

Spans and boundaries:

| Span | Meaning |
| --- | --- |
| `workspace.request` | Adapter request, through response consumption; header event and child validation/annotation spans |
| `session.open`, `session.message` | Durable intake, with child transaction events |
| `hermes.session.create` | Native session-create HTTP exchange, including replay |
| `invoke_agent` | Observed pre-LLM callback to session-end callback |
| `chat` | One observed physical model attempt; retries have separate spans |
| `execute_tool`, `tool.boundary=native` | Native pre/post-tool callbacks, including built-ins |
| `execute_tool`, `tool.boundary=distribution` | Distribution validation, execution, rendering and scrubbing |
| `workspace.run.observed` | Durable acceptance to coordinator terminal observation, including polling delay |

Native model/tool spans are children of the turn. Hermes isolates tool workers, so distribution
tool handlers explicitly bind the unique active turn for their native session. Subprocesses
carry W3C trace context through `TRACEPARENT`/`TRACESTATE`; adapter HTTP requests use the same
propagator through normal trace headers. Durable asynchronous intake and native execution
continue the platform W3C context carried in relay metadata. Durable message admissions retain
that original context across retries, queued dispatch and restarts. Native model/tool turns recover
the exact native message binding and link their asynchronous turn spans to the admitted request
using standard OpenTelemetry span links. Direct native sessions have their own trace. No latest-run
heuristic or process-global session context is used.

Distribution outcomes preserve `validation_rejected`, `tool_rejected`, `store_rejected`,
`internal_error`, `failed`, `succeeded` and `unknown_tool`. Unknown tool/action inputs are
not logged. Native semantic tool errors and failed model attempts set error status. No
arguments, results, messages, prompts, credential values or exception text are captured.
Token usage uses `gen_ai.usage.*`; positive native cache buckets are evidence of reported
usage, while absent/zero buckets do not prove a cache miss. The first-chunk event does not
claim first visible text or first-token latency.

Do not sum overlapping span durations. Waterfalls expose concurrency and gaps; exact busy
unions and unaccounted-time summaries can be calculated from exported intervals. The runtime
no longer maintains a second interval-accounting system. Native and distribution tool spans
measure different boundaries and must not be added together as independent tool counts.

The Hermes adapter retains only live callback correlation. Read-only authoritative leases
reclaim orphaned observations without evicting active calls or backoff. An observed orphan or
missing callback ends with `observation.complete=false` and a reason. Its end is an observation
boundary, not an invented native completion timestamp. Capacity rejection emits a diagnostic
span. Session restoration, isolated retry backoff, and frontend delivery are not inferred.
