# Softmax IDE bridge: the external agent on `comms/`

The Softmax IDE reaches autoresearch through one process on the Hermes box: the **bridge** sidecar in
[`packages/observatory-hosted-worker`](https://github.com/Metta-AI/metta/tree/main/packages/observatory-hosted-worker)
of the metta repo (PR [#26790](https://github.com/Metta-AI/metta/pull/26790)). It is the "external agent" of the
Runtime design §10: it is the only writer of `comms/inbox/` and a reader of `comms/outbox/`, through the Softmax player
API on one side and the workspace filesystem on the other. The IDE never reaches the box, and the bridge never writes
the board, the outbox, or research records.

This page records what the bridge relies on, so the distribution and the bridge can be checked against each other.
The Runtime design is the authority for the comms contract; where this page and it disagree, the bridge is wrong.

## Where the bridge looks

- The workspace is `AUTORESEARCH_WORKSPACE`, passed to the bridge at provisioning (`--workspace`), by default the
  profile's own `workspace/`. Both containers mount the same volume, so the path is the same on both sides.
- `comms/inbox/` and `comms/outbox/` directly under it. The bridge creates `comms/inbox/` if it is missing and waits
  while `comms/outbox/` is missing (the harness has not initialized yet).

## What the bridge writes to `comms/inbox/`

One immutable file per message at `comms/inbox/<id>.json`, written whole then renamed. A retry finds the file and
writes nothing. Flat JSON: the envelope fields and the kind's fields together.

| Field | Value |
|---|---|
| `id` | `<player_id>:task:<task key>` for a task, `<player_id>:stop:<task key>` for its cancellation |
| `created` | ISO 8601, UTC |
| `author` | `coach:<player_id>` |
| `on_behalf_of` | `human` (a coach action in the IDE; chat-proposed work is not sent yet) |
| `kind` | `task` or `steer` |

A `task` carries `title` (the IDE task text, at most 500 chars), `mode` (one of the five research modes, from the IDE
task's `area`), `why` (the task's prediction and done-when, or the title when both are empty), `refs` (always `[]`
for now) and `priority` (always `P2` for now). A `steer` carries `text` (`Stop task <inbound task id>: <title>`) and
`urgency` `now`; it is sent when the coach cancels a task that was forwarded.

Not sent yet: `reply` and `seen`. The IDE has no surface for answering a `question` or marking messages read.

## What the bridge reads from `comms/outbox/`

- Files named `<seq>-<id>.json`. The bridge skips by the leading `seq` in the name without reading files it has
  already relayed, so the name's `seq` must match the message's `seq`.
- Fields it reads: `id`, `seq`, `kind`, `title`, `body`, `in_reply_to`. Everything else is passed through untouched.
- Every message is said once in the IDE chat, in `seq` order, as `<title>\n\n<body>` with the kind as an aside.
- A `note` whose `in_reply_to` is one of the bridge's inbound task ids is the acknowledgement of that task
  (System Design: "acknowledge a task when its card completes"). The bridge then marks the IDE task done with the
  note as its outcome. Any other kind, or a note answering something else, is said in chat only. A task
  acknowledged `about.outcome: blocked` gets one later follow-up note with the final outcome and `about.follows`
  only when a rerun or repair card finishes its work; several kinds of blocked task never get one and stay paused
  until the coach cancels them (see [softmax-ide-bridge-compat.md](softmax-ide-bridge-compat.md), "Notes for the
  bridge", note 2, for when it is sent and when it is not).

## What is not exercised yet

The distribution reads only inbox files committed at canonical HEAD (`comms.delivery.inbox`), and
the supported way to add one is `"$AUTORESEARCH_CLI" comms inbound <kind> --author <name>`, which validates the
message, assigns an `I-<ULID>` id, adds `schema` and `received_at`, and commits. A file the bridge writes directly into
the working tree, with an id like `<player_id>:task:<key>` and an author like `coach:<player_id>` (a `:` is not
allowed in a consumer name), is not read by queue-keeper. The bridge-to-distribution path is therefore not wired yet,
and the acknowledgement path has been proved against fixture files only. The bridge's
message models (`InboundTask`, `InboundSteer`, `Outbound` in `bridge.py`) are the place to update when the
distribution publishes its schema and contract tests.
