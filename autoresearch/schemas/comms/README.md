# `ar-comms/0`: the message contract

These schemas are the published contract between the research agent and external agents (a
Preston-role agent, the Softmax IDE bridge). They are JSON Schema 2020-12. Each file's `$id` is
its path under `schemas/` (for example `comms/inbound/task.json`), so `$ref`s resolve by reading
files from that directory (see "Validating with these schemas"). The runtime validates with the
same files (`autoresearch.comms.kinds`).

- `envelope.outbound.json`: the fields every outbound message has (agent to coach,
  `comms/outbox/<seq:08d>-<M-id>.json`). `../message.json` is an alias of it.
- `envelope.inbound.json`: the fields every inbound message has (coach to agent,
  `comms/inbox/<id>.json`).
- `outbound/<kind>.json`, `inbound/<kind>.json`: one schema per kind, the envelope plus that
  kind's own fields. `x-autoresearch` holds the metadata the runtime reads:
  - `direction` and `summary` (one line, used in CLI help);
  - outbound: `expects_response`, the default of that field (default false);
  - inbound: `ack`, who acknowledges it once it is routed to a card: `card` (the card stages a
    `note` in reply, and card finish requires it), `runtime` (the runtime publishes a note when
    the card publishes) or `none`;
  - inbound: `describe`, the briefing text, as format-string parts (a part is left out when a
    field it names is missing or empty), and `checks`, rules that need the workspace (such as
    "these ids are published"; implemented in `autoresearch.comms.inbound`).
- `examples/<direction>/<kind>/*.json`: valid messages.
- `examples/invalid/<direction>/<kind>/*.json`: invalid messages. Each has a `_why`, which is
  text that one of its problems contains.

Versioning is additive within `ar-comms/0`: new optional fields and new kinds may appear.
Readers ignore fields and kinds they do not know. An inbound message's `schema` field is not
checked.

Anchored patterns end in `(?!\n)$`, because in Python's `re` (which `jsonschema` uses) `$` also
matches before a final newline.

## Kinds

What each kind means is its schema's `x-autoresearch.summary` and `title`.

| Kind | Direction | Schema |
| --- | --- | --- |
| `help_request` | outbound | `outbound/help_request.json` |
| `highlight` | outbound | `outbound/highlight.json` |
| `note` | outbound | `outbound/note.json` |
| `question` | outbound | `outbound/question.json` |
| `suggestion` | outbound | `outbound/suggestion.json` |
| `control` | inbound | `inbound/control.json` |
| `reply` | inbound | `inbound/reply.json` |
| `seen` | inbound | `inbound/seen.json` |
| `steer` | inbound | `inbound/steer.json` |
| `task` | inbound | `inbound/task.json` |

## Validating with these schemas

With `jsonschema` 4.18 or later, in Python. Run it from the `autoresearch/` directory of an
ide-mvp checkout, or set `SCHEMAS` to its `schemas/` directory:

```python
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = Path("schemas")  # each schema's $id is its path under this directory


def retrieve(uri: str) -> Resource:
    return Resource.from_contents(json.loads((SCHEMAS / uri).read_text(encoding="utf-8")))


def problems(message: dict, schema: str) -> list[str]:
    """schema: for example "comms/inbound/task.json", or "message.json" for any outbound message."""
    validator = Draft202012Validator({"$ref": schema}, registry=Registry(retrieve=retrieve))
    return [error.message for error in validator.iter_errors(message)]
```

`tests/contract/comms/test_kind_registry.py` runs this block, as it is written here, on the
examples.

Other tools need the same mapping from `$id` to file. check-jsonschema 0.38.2 does not work
out of the box (checked 2026-10-08): it resolves each file's relative `$id` against that file's
own path, so `comms/inbound/task.json` becomes `comms/inbound/comms/inbound/task.json`, and its
`$ref` to `../envelope.inbound.json` then names a file that does not exist. Other validators
(ajv, ...) are not checked; register each schema under its `$id` before validating.

## Problems

The runtime reports each problem as `location: message`. The location is the JSON path joined
with `/` (`(message)` for the whole message). The message is the failing schema node's
`x-message` when it has one, otherwise the validator's own text. A missing property is reported
at its own path, with that property's `x-message` (or `required`).

## The Softmax IDE bridge examples

`examples/inbound/task/ide-bridge-task.json` and `examples/inbound/steer/ide-bridge-stop.json`
(and `examples/inbound/task/ide-bridge-task-fractional-seconds.json`, a `created` with
microseconds as the bridge's default `datetime.now(UTC)` gives) are the exact bytes the bridge's
`deliver()` writes: `InboundTask(...).model_dump_json()` and
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
   `$id`, `kind.const`, and `x-autoresearch` (`direction`, `summary`; outbound:
   `expects_response` if true; inbound: `ack`, `describe`, and `checks` if it needs the
   workspace). Give each rule an `x-message`.
2. Add at least one valid example under `examples/<direction>/<kind>/`, and an invalid example
   with a `_why` for each rule.
3. Inbound only: add a routing handler to `HANDLERS` in `src/autoresearch/runtime/routing.py`,
   and the implementation of any new check to `CHECKS` in `src/autoresearch/comms/inbound.py`.
4. Add the kind's row to the table above and to `doctrine/communication.md` (its "Reply
   expected" or "Acknowledge it" column must agree with the schema), and bump
   `doctrine/VERSION` so installed profiles re-seed `AGENTS.md`. Outbound: add a row to
   `skills/autoresearch/communicate/SKILL.md`. Inbound with `ack: card`: name it in that
   skill's "Acknowledge each" step.
5. Run `uv run --locked pytest tests/contract/comms/test_kind_registry.py`. It fails on any
   place that is missing the kind, and on any kind a document lists that has no schema.
