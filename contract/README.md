# IDE agent application contract 2.0.0

Source of truth: `image/contract.py`. `openapi.json` is generated with Pydantic, not maintained separately.
The schema document has no HTTP paths: it describes the existing operations carried by the platform relay.
Known names select exact argument types; unknown names are runtime refusals and never a callable fallback type.

The frontend pins this artifact by source commit and SHA-256, generates TypeScript with openapi-typescript
(`--default-non-nullable false`), and validates requests and fixtures with JSON Schema Draft 2020-12.
Optional server-defaulted fields stay optional. Response models permit native extensions; arbitrary snapshot
records remain JSON and run-control replies promise no unverified fields.

Current-wire scope agreed with the separately owned AIDE v1 design. No new transport, handshake or protocol negotiation.

## Message content

`session.message.args.message` is an OpenAI Responses user message: `role: user` and a
`content` array of `input_text`, `input_image`, or `input_file` parts. File parts carry exactly
one of `file_data` (base64), `file_url` (HTTPS), or a workspace-issued `file_id`. Unknown content
kinds are rejected. Raw audio is unsupported by the pinned runtime; clients send transcribed
speech as text. Session context and instructions are separate from user content.

The pinned Hermes image accepts native text and image parts but rejects native file parts.
The owned workspace adapter saves file bytes privately under content-addressed IDs, verifies
cached bytes, before admission. The profile registers Hermes `llm_request` middleware to translate files
into local references only in the outbound provider request. Hermes retains the original
OpenAI parts in its own transcript; clients never parse internal file references. The former platform
`attachment_download` tool and frontend attachment markers are removed.

Clients validate against this generated artifact. Deploy this runtime and matching clients as
a coordinated major-version cutover; an old client sending a string to `session.message`
will be rejected. Existing browser pending text sends migrate once without changing their
operation IDs. Existing workspace admissions retain their frozen native payload across the
SQLite migration, including uncertain sends. Coplay refuses to recreate an unresolved old
mailbox delivery under a new operation ID; resolve those deliveries before upgrading it.
