# IDE agent application contract

Source of truth: `image/contract.py`. `openapi.json` is generated with Pydantic, not maintained separately.
The schema document has no HTTP paths: it describes the existing operations carried by the platform relay.
Known names select exact argument types; unknown names are runtime refusals and never a callable fallback type.

The frontend pins this artifact by source commit and SHA-256, generates TypeScript with openapi-typescript
(`--default-non-nullable false`), and validates requests and fixtures with JSON Schema Draft 2020-12.
Optional server-defaulted fields stay optional. Response models permit native extensions; arbitrary snapshot
records remain JSON and run-control replies promise no unverified fields.

Current-wire scope agreed with the separately owned AIDE v1 design. No new transport, handshake or protocol negotiation.

## Message content

`session.message.args.message` is an OpenAI user message: `{"role": "user", "content": "Hello"}`,
or with a list of text, image, input-audio and file parts as `content`. Hermes receives text
directly and stages attachments through its own attachment call
(`core/src/research_core/native_attachments.py`). Transcribed speech is ordinary text.

Application context and instructions are separate from user content. Hermes's native turn
context hook supplies the agent's context without changing the stored user message.

Clients validate against this generated artifact; a bare string as `message` is rejected.

## Session scope

`session.open` binds the frontend's `session_context.coworld` and `league` for the life of that session.
A workspace-only assistant declares `scope: "workspace"` with null Coworld and league.
The reply's `session_scope` is the canonical binding. Conflicting messages return HTTP 409
with `kind: "scope_mismatch"` and create no admission. Existing sessions are never reassigned.

`workspace.snapshot` accepts Coworld and league IDs to return only that game's root chat sessions.
With `session_id`, it returns only that session and its admissions. Expected scope mismatches
return `scope_mismatch`. `session.messages` validates the same selection before reading Hermes.
Clients pass IDs; runtime owns filtering and validation.
Coworld snapshots and store streams require an explicit `coworld` selector. No platform-owned active-game table is involved.
