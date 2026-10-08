# `ar-comms/0`: the message contract

These schemas are the published contract between the research agent and external agents (a
Preston-role agent, the Softmax IDE bridge). They are JSON Schema 2020-12, and each file's `$id`
is `autoresearch/comms/<path>`, so a consumer registers them all and resolves the `$ref`s between
them. The runtime validates with the same files (`autoresearch.comms.kinds`).

- `envelope.outbound.json`: the fields every outbound message has (agent to coach,
  `comms/outbox/<seq:08d>-<M-id>.json`). `../message.json` is an alias of it.
- `envelope.inbound.json`: the fields every inbound message has (coach to agent,
  `comms/inbox/<id>.json`).
- `outbound/<kind>.json`, `inbound/<kind>.json`: one schema per kind, the envelope plus that
  kind's own fields. `x-autoresearch` holds the metadata the runtime reads: `direction`,
  `summary`, and for inbound kinds `describe` (the briefing text) and `checks` (rules that need
  the workspace, such as "these ids are published"; implemented in `autoresearch.comms.inbound`).
- `examples/<direction>/<kind>/*.json`: valid messages.
- `examples/invalid/<direction>/<kind>/*.json`: invalid messages. Each has a `_why`, which is
  text that one of its problems contains.

Versioning is additive within `ar-comms/0`: new optional fields and new kinds may appear.
Readers ignore fields and kinds they do not know.

## Kinds

| Kind | Direction | Meaning |
| --- | --- | --- |
| `help_request` | outbound | The agent is stuck or blocked and says what it tried. |
| `highlight` | outbound | One notable event: a supported or falsified hypothesis, a verified claim, a promotion, a surprising result. |
| `note` | outbound | A decision or status change a human should know; with in_reply_to, the acknowledgement of an inbound message. |
| `question` | outbound | A choice where human judgement adds value; a reply is expected. |
| `suggestion` | outbound | Something a human can do, such as raise credits or look at a replay; also each skill or doctrine proposal. |
| `reply` | inbound | An answer to a published message (in_reply_to): a choice, text, or both. |
| `seen` | inbound | Marks published messages as read by its author; nobody acknowledges it. |
| `steer` | inbound | Direction that outranks the agent's plan; urgency now gets a P0 card, and it may propose a topic. |
| `task` | inbound | A request for work: it becomes its own card request with these fields. |

The meaning column is each schema's `x-autoresearch.summary`, word for word.

## Problems

The runtime reports each problem as `location: message`. The location is the JSON path joined
with `/` (`(message)` for the whole message). The message is the failing schema node's
`x-message` when it has one, otherwise the validator's own text. A missing property is reported
at its own path, with that property's `x-message` (or `required`).

## The Softmax IDE bridge examples

`examples/inbound/task/ide-bridge-task.json` and `examples/inbound/steer/ide-bridge-stop.json`
are the exact bytes the bridge's `deliver()` writes: `InboundTask(...).model_dump_json()` and
`InboundSteer(...).model_dump_json()`, made with pydantic 2.12.3 (the version in metta's
`uv.lock`) from `packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py` at
metta main `86b1a3c5e0348b3e72980fa6230f005318e634fa`. The bridge file last changed in
`e76aa6a21784cc87812b991ccb1dff5b848f91aa`; its message models and `deliver()` are the same as at
`8b6042dbfece760f1e00af5a00be0b9b6ab4d82e`. The inputs were player
`ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1`, task key `tsk_42`, `created` 2026-10-08T20:00:00Z, and
mode `scouting`. `tests/protocol/test_bridge_contract.py` writes these files into the inbox and
routes them.

What the bridge relies on in our outbound messages is written into
`envelope.outbound.json`: the file name is `<seq>-<id>.json` with an integer `seq`, and `title`
and `body` are always strings. `examples/outbound/note/ack-of-ide-bridge-task.json` is the note
that closes a bridge task.

## Adding a kind

1. Write `outbound/<kind>.json` or `inbound/<kind>.json`: copy a kind of the same direction, set
   `$id`, `kind.const`, and `x-autoresearch` (`direction`, `summary`; inbound: `describe`, and
   `checks` if it needs the workspace). Give each rule an `x-message`.
2. Add at least one valid example under `examples/<direction>/<kind>/`, and an invalid example
   with a `_why` for each rule.
3. Inbound only: add a routing handler to `HANDLERS` in `src/autoresearch/runtime/routing.py`,
   and the implementation of any new check to `CHECKS` in `src/autoresearch/comms/inbound.py`.
4. Add the kind's row to the table above, to `doctrine/communication.md` (and bump
   `doctrine/VERSION`), and, if outbound, to `skills/autoresearch/communicate/SKILL.md`.
5. Run `uv run --locked pytest tests/contract/comms/test_kind_registry.py`. It fails on any
   place that is missing the kind, and on any kind a document lists that has no schema.
