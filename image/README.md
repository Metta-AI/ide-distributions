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
