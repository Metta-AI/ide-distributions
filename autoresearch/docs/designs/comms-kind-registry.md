# Comms kinds: one definition per kind, one published contract

Status: **implemented**, 2026-10-08 (proposed the same day). Applies to `ar-comms/0` and does not change the
wire format. Where the implementation differs from the design below, see "Deviations" at the end.

## Problem

Today one message kind is defined in several places that have to be kept in step by hand:

| Concern | Outbound (agent → coach) | Inbound (coach → agent) |
|---|---|---|
| The list of kinds | `comms/envelope.py` `KINDS` | `comms/inbound.py` `INBOUND_KINDS`, `schemas/inbound.json` `kind` enum, CLI `choices` |
| Shape of a kind | not validated per kind (`schemas/message.json` covers only the envelope) | hand-written `_reply_problems`, `_steer_problems`, `_task_problems`, `_seen_problems` |
| One-line description | (none) | `runtime/routing.py` `_describe` (an if-chain) |
| What the runtime does with it | publisher, signals, promotion and others each build messages inline | `runtime/routing.py` `_route_one` (an if-chain) |
| Documentation | `doctrine/communication.md`, skills, System Design §7 | same, plus `docs/integrations/softmax-ide-bridge.md` |

Adding a primitive means finding and editing every one of these, and no test fails if one is missed.

Outside consumers have nothing machine-readable to code against. The Softmax IDE bridge (metta
`packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py`) copies our shapes into its
own pydantic models (`InboundTask`, `InboundSteer`, `Outbound`). Its doc says those models are "the place to
update when the distribution publishes its schema and contract tests".

## Goals

1. **Adding a kind is one schema file plus, for inbound kinds, one handler.** A conformance test fails if
   any other place is missing it.
2. **The contract is published as JSON Schema** (language-neutral; the repo already validates with
   `jsonschema`), with example messages that both our tests and the bridge's tests can use.
3. **The wire format stays as it is.** The bridge keeps working unchanged. New kinds and fields stay additive,
   as `ar-comms/0` already allows.
4. Behaviour does not change. This is a restructure and must pass the existing protocol tests unchanged.

Non-goals: a new envelope format, a schema version bump, or new transports. CloudEvents and AsyncAPI were
considered, and the reasons for not adopting them are below.

## Design

### 1. One JSON Schema per kind

```
schemas/comms/
  README.md                 # index: every kind, direction, one-line meaning (checked by a test)
  envelope.outbound.json    # today's message.json envelope (renamed; message.json stays as a $ref alias)
  envelope.inbound.json     # fields every inbound message has: id, kind, author, created|received_at
  outbound/note.json  question.json  suggestion.json  help_request.json  highlight.json
  inbound/reply.json  steer.json  task.json  seen.json
  examples/<direction>/<kind>/*.json  # valid examples, including the bridge's real shapes
  examples/invalid/<direction>/<kind>/*.json  # each with a "_why" naming the rule it breaks
```

Each kind schema uses `allOf: [{$ref: envelope}]` plus that kind's own fields, with `kind: {const: "<name>"}`.
The `x-autoresearch` keyword holds the metadata code needs: `direction`, `summary` (one line, for the README and
CLI help), `describe` (a format string used by the briefing, e.g. `"steer ({urgency}): {text}"`), and for
inbound kinds the routing handler name.

JSON Schema covers everything structural. Checks that need the workspace stay in Python. For example, a reply's
`in_reply_to` must name a *published* message, and a seen's ids must be published. These checks are named in the
schema's `x-autoresearch.checks` and implemented once.

### 2. A registry loaded from the schemas

`autoresearch/comms/kinds.py`:

```python
@dataclass(frozen=True)
class Kind:
    name: str
    direction: Literal["inbound", "outbound"]
    summary: str
    schema: dict            # resolved, validator-ready
    describe: str           # format string
    checks: tuple[str, ...] # names of workspace checks

def outbound_kinds() -> tuple[str, ...]: ...
def inbound_kinds() -> tuple[str, ...]: ...
def get(direction, name) -> Kind: ...
def problems(direction, message, canonical=None) -> list[str]: ...  # schema errors + named checks
def describe(message) -> str: ...
```

`envelope.KINDS`, `inbound.INBOUND_KINDS`, the CLI `choices`, the `inbound.json` enum and `reader`'s known-kind
filter all read from it. The hand-written `_*_problems` functions are replaced by schema rules. Error messages
keep their current `location: message` form, so CLI output and tests stay stable. Where a test asserts exact
wording, either keep the wording through a custom error message or update the test in the same commit with a
note.

### 3. Inbound handlers

`runtime/routing.py` keeps routing but dispatches through a table, `HANDLERS = {"task": _route_task, "steer":
_route_steer, "reply": _route_reply, "seen": _route_seen}`, instead of the if-chain. Each handler must keep its
current behaviour exactly. A test checks that the handlers and the registry's inbound kinds are the same set.
Outbound kinds need no handler. They are published by whatever card or runtime path writes them, and the
publisher validates them through the registry.

### 4. Conformance test (`tests/contract/comms/test_kind_registry.py`)

For every kind in the registry, it checks that:
- its schema passes `check_schema`;
- every valid example passes `problems()` and every invalid example fails it;
- it has a row in `schemas/comms/README.md`, a row in `doctrine/communication.md`, and, if inbound, a handler;
- the CLI `comms inbound` accepts it.

It also fails on any kind name that appears in those documents but not in the registry.

The adding-a-kind steps go in `schemas/comms/README.md`: add the schema and examples, add a handler if inbound,
add the doc rows, and run the test.

### 5. Bridge compatibility (pinned by examples)

`examples/inbound/task/ide-bridge-task.json` and `examples/inbound/steer/ide-bridge-stop.json` are byte-for-byte
the files `bridge.py` writes, as of metta main `8b6042db`:
- id `<player_id>:task:<key>`;
- author `coach:<player_id>`;
- `created`, not `received_at`;
- `on_behalf_of: "human"`;
- `refs: []`, `priority: "P2"`.

The existing `tests/protocol/test_bridge_contract.py` reads its fixtures from there. The outbound examples
include a note with `in_reply_to` set to a bridge task id, and messages without `in_reply_to`.

Two things the bridge assumes are written into the outbound envelope schema's description, so they cannot change
by accident:
- outbox file names are `<seq>-<id>.json`, with `seq` an integer;
- `title` and `body` are always strings.

The bridge can then replace its hand-copied models with validation against `schemas/comms/` from a pinned
ide-mvp commit. That is Rohit's choice. We publish the contract, and he does not have to adopt it.

## Alternatives considered

- **Keep the code and add a checklist.** This is the cheapest option, but nothing enforces the checklist. It
  fails the main goal.
- **Pydantic models as the source of truth** (as the bridge uses). It gives good ergonomics in Python, but it
  adds a production dependency the profile does not have. The published contract would then be generated
  output, not the file we edit. JSON Schema is already our record format (`schemas/`).
- **CloudEvents envelope.** It is a widely used standard envelope (`specversion`, `type`, `source`, `data`).
  Adopting it would rename every field and break the bridge, all for interoperability with tooling we do not
  use. It could be revisited at an `ar-comms/1`.
- **AsyncAPI document** describing the inbox and outbox channels. It is the standard way to publish an
  event-driven contract, and it can `$ref` the per-kind schemas unchanged. It is deferred because the schemas
  and examples are what consumers actually use. It can be added later on top of this layout without moving
  anything.

## Risks

- Moving inbound validation from Python to schemas can change edge cases: whitespace-only text (needs
  `pattern: "\\S"`), and `choice` or `text` at least one (needs `anyOf`). The invalid examples must cover each
  current rule before the Python checks are deleted.
- The location of `message.json` is referenced by other code and docs, so it stays as an alias.

## Deviations (implementation, 2026-10-08)

The layout, registry, handler table, examples and conformance test are as designed. These details differ:

1. **Workspace checks are not in `kinds.problems()`.** It is `problems(direction, message)`, schema only. The
   checks a kind names in `x-autoresearch.checks` are implemented in `comms.inbound.CHECKS` and run by
   `inbound.inbound_problems(canonical, message)`, only once the schema passes. `kinds` cannot import the
   publisher (the publisher imports `envelope`, which imports `kinds`), and no outbound kind needs a check. A
   message with both schema problems and unpublished seen ids now reports only the schema problems.
2. **The only named check is `published_ids` (seen).** The code before this change checked a reply's
   `in_reply_to` for the M-id shape only, not that it is published. That behaviour is kept, so it is a schema
   pattern, not a check. The example in section 1 was wrong.
3. **`describe` is a list of format-string parts**, joined with no separator; a part is left out when a field
   it names is missing, null or `""`. One format string cannot reproduce today's briefing text, which has
   optional pieces (a reply's choice and text, a steer's proposed topic).
4. **No handler name in `x-autoresearch`.** `routing.HANDLERS` is keyed by kind name, and the test checks that
   its keys equal the registry's inbound kinds. A handler name in the schema would be a second copy.
5. **Name and direction come from the file path** (`<direction>/<kind>.json`). The test checks that
   `x-autoresearch.direction`, `kind.const` and `$id` agree with it.
6. **`schemas/inbound.json` (the delivery record) no longer lists the kinds.** Its `kind` is a string; the
   message was validated against the registry before any record exists. This removes a place to edit.
7. **Kinds are in file-name order** (alphabetical): `envelope.KINDS` is now `help_request, highlight, note,
   question, suggestion` and `INBOUND_KINDS` `reply, seen, steer, task`. Only the "is not one of" message and
   CLI help order change.
8. **Problem wording.** The form `location: message` is kept. Locations use `/` in both directions (inbound
   used `proposed_topic.objective`, now `proposed_topic/objective`; the one contract test that asserted it was
   updated). Messages come from `x-message`, which keeps the old inbound texts. A missing property is reported
   at its own path (`title: required`, before `(message): 'title' is a required property`). A bad task ref is
   reported at `refs/<index>`. Problems are sorted and deduplicated. An unknown kind now also lists envelope
   problems.
9. **The direct-write reserved-id rule moved into the schema.** `envelope.inbound.json` `id` refuses ids shaped
   like an outbox key (`M-...`, all digits); `inbound._RESERVED_ID` is gone.
10. **More places are checked.** The conformance test also checks the kind table in
    `skills/autoresearch/communicate/SKILL.md`, that every invalid example's problems contain its `_why`, and
    that every kind has a valid example. `doctrine/communication.md` now has an `Inbound kinds` table and an
    `Outbound kinds` heading (it said "five"); `doctrine/VERSION` is 3. The old doctrine outbound-kinds test
    moved into the conformance test.
11. **Bridge provenance.** The examples were made from `bridge.py` at metta main `86b1a3c5`; the bridge last
    changed in `e76aa6a2`, and its message models and `deliver()` are the same as at `8b6042db`.

Not done, and worth knowing when adding an inbound kind: whether a card must acknowledge a kind is still code
(`runtime.handoff.ACK_REQUIRED_KINDS = {"reply", "steer"}`), as are the kind-specific rules in routing (a
`reply` decides lateness and derived status). A new kind defaults to "no acknowledgement required".
