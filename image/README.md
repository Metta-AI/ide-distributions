# Self-contained workspace image

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

Lifecycle controls require a UUIDv8 request ID carrying the platform's ordered lifecycle-owner epoch. The unsigned
64-bit epoch occupies the UUID's first 48 application bits, its next 12 bits after the version, and four bits after the
variant. The remaining 58 bits identify the request. The runtime decodes and compares integer epochs; a random UUID is
not a valid owner. The envelope retains its existing `id` and `control` fields. Deploy the platform's UUIDv8 emitter to
every API replica before releasing this enforcing image. The previous image accepts those UUID IDs unchanged, which
permits this deployment order without a maintenance window.

The adapter saves the highest accepted epoch in the orchestrator's retained home. It rejects stale controls before and
after reading application status. A successor waits for an already-admitted sleep command to settle before its epoch is
accepted; caller cancellation does not cancel that local command. The packaged ownership fault check delays an old sleep
request across a successor's status reply and confirms the old request cannot pause the application.

The release dispatcher passes the distribution commit to Metta's generic build workflow.
That workflow builds `image/Dockerfile`, runs `image/system_tests/start-stop.sh`, and publishes
an immutable image digest. Hosting consumes that digest without inspecting profile internals.
Deploy Metta #30090's format readers and release workflow before dispatching this release.

The image also owns the ide-next application adapter. It exposes native session opening,
durable message admission and cancellation, workspace snapshots, transcripts and run controls.
Chat messages are confirmed only after the workspace commits them. Each operation ID names
one input; reconnecting with that ID cannot create a second run. Messages wait behind the
previous run in their session, and queued messages survive sleep and restart.

The image obtains its spend-capped model credential from
`PUT /v2/workspaces/{workspace_id}/inference`, using the bootstrap credential. It installs
and renews the key internally; hosting never supplies profile paths or model environment
variables. Deploy that platform endpoint before this image. Queued chat dispatch waits for
a valid inference lease, including after restart. Lifecycle readiness remains independent
of the optional model service, so an unavailable budget does not prevent stopping a workspace.

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
restrictions. It checks adapter operations, sleep, retained sessions and transport failures.
It also validates all sixteen startup timing events on each boot: profile install/update,
preparation, gateway readiness, application wake/readiness and total startup. Missing phases,
reused boot IDs, malformed events and extra telemetry fields fail the check.
Timing events run only during startup; no timing work is added to chat or relay traffic.

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

The packaging comparison workflow builds the pull request's source and lockfile twice on one
Linux runner: once with the base revision's Dockerfile and once with the proposed Dockerfile.
Each image runs one fresh boot and five retained boots with the same smoke command. This
isolates packaging changes; it does not compare different application revisions. Both build
identities, the baseline Dockerfile, and all per-boot timing reports are retained as artifacts.

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
