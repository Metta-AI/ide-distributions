# IDE agent application contract 1.0.0

Source of truth: `image/contract.py`. `openapi.json` is generated with Pydantic, not maintained separately.
The schema document has no HTTP paths: it describes the existing operations carried by the platform relay.
Known names select exact argument types; unknown names are runtime refusals and never a callable fallback type.

The frontend pins this artifact by source commit and SHA-256, generates TypeScript with openapi-typescript
(`--default-non-nullable false`), and validates requests and fixtures with JSON Schema Draft 2020-12.
Optional server-defaulted fields stay optional. Response models permit native extensions; arbitrary snapshot
records remain JSON and run-control replies promise no unverified fields.

Current-wire scope agreed with the separately owned AIDE v1 design. No new transport, handshake or protocol negotiation.
