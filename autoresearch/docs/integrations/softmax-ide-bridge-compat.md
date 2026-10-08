# Softmax IDE bridge: compatibility with the autoresearch distribution

Checked on 2026-10-08 against `autoresearch/docs/integrations/softmax-ide-bridge.md` as merged on
`main` in PR #4 (commit `35c0000`), System Design §7 and Runtime §9.2. The contract test is
[`tests/protocol/test_bridge_contract.py`](../../tests/protocol/test_bridge_contract.py): it writes
bridge-shaped files into `comms/inbox/` the way the bridge does (whole file, then rename, no commit),
runs `route_all`, finishes the card, and reads `comms/outbox/` the way the bridge does.

**Verdict: compatible**, after the fixes on our side listed below (branch
`jboggs/autoresearch-v0-impl`). One field the bridge sends is outside §7 (`on_behalf_of`), and the
acknowledgement has a few behaviours the bridge should know about (see "Notes for the bridge").
The bridge's `Outbound` model must treat `in_reply_to` as optional (see "Checked and matching").

## Fixed on our side

The bridge matches §7 and Runtime §9.2 (the L0 file adapter: "Each inbound message is one JSON file
at `comms/inbox/<id>.json`", inbox writable by external agents). Our implementation was narrower than
that contract:

| Bridge behaviour | Before | Now |
|---|---|---|
| Writes `comms/inbox/<id>.json` itself and never commits | Routing read only files committed at HEAD, so bridge messages were never seen | queue-keeper adopts direct writes each run (`comms.inbound.adopt_direct_writes`): a valid file is committed unchanged; an invalid one is left as it is and gets one runtime `note` in reply to its id |
| Ids `<player_id>:task:<key>` and `<player_id>:stop:<key>` | Delivery records and `in_reply_to` accepted only `I-<ULID>` | An inbound id is any `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}` without `..` (`INBOUND_ID`); `schemas/inbound.json` and `schemas/message.json` follow it |
| `author: coach:<player_id>` | The author rule (consumer name) rejected `:` | `:` is allowed |
| `created`, no `received_at` | Routing required `received_at` | `created` stands for `received_at` (lateness, delivery record) |
| Waits while `comms/outbox/` is missing | The outbox appeared only with the first publication, which can be hours after install | Bootstrap creates `comms/inbox/` and `comms/outbox/` on every queue-keeper run |

## Where the bridge deviates from §7 (not changed on our side)

- **`on_behalf_of: human`.** The System Design amendment of 2026-10-07 removed this field: every
  inbound message from the Preston-role agent carries the human's full authority (§7 Inbound; Runtime
  §9.2 "Preston carries the human's authority"). We keep unknown fields and ignore this one, so it is
  harmless, but the bridge should stop sending it.
- **The bridge doc is stale in two places.** "No runtime consumes the inbox today" is no longer true,
  and the schema and contract tests it waits for now exist (`schemas/message.json`,
  `schemas/inbound.json`, the contract test above).

## Checked and matching

- `task`: `title`, `mode`, `why`, `refs` (`[]`), `priority` (`P2`) are the §7 shape. A title up to
  500 characters is accepted. A `mode` that is not one of the five research modes (`investigation`,
  `scouting`, `experimentation`, `maintenance`, `planning`) is rejected (see below), so map the IDE
  `area` onto these five exactly.
- `steer`: `text` and `urgency: now` are the §7 shape. It becomes a P0 card, and that card must answer
  it with a `note` whose `in_reply_to` is the steer id.
- Outbox files are `comms/outbox/<seq:08d>-<M-id>.json` (zero-padded to 8 digits). The `seq` in the
  name is the `seq` in the file. Every message has `id`, `seq`, `kind`, `title` and `body`, with the
  meanings the bridge assumes. **`in_reply_to` is present only when the message answers something**;
  it is absent, not `null`, otherwise (`schemas/message.json`: optional string; `thread` is the same).
  On the box today 4 of 9 outbox messages have it. The bridge's `Outbound` model must default it to
  `None`.
- Task acknowledgement: when the task's card is published, the runtime publishes a `note`
  (`from: runtime`, title `Task <id> done`) with `in_reply_to` set to the bridge's task id. The
  bridge's "a note in reply to a task id closes the IDE task" holds.
- Files are never rewritten or moved, so the bridge's retry ("finds the file and writes nothing")
  stays correct.

## Notes for the bridge

1. **A note in reply to a task id is not always "done".** The runtime also answers a task with a
   note when it cannot become a card: `Task <id> rejected` (bad refs, admission rejected it) or
   `Inbound <id> rejected` (the file is invalid: wrong `mode`, missing field, `id` not equal to the
   file name, no `created`). Both are acknowledgements under §7 ("applied, declined with a reason, or
   noted"). The IDE should show the note as the outcome rather than assume success.
2. **"Done" covers every card outcome.** `Task <id> done` is published when the card's work is
   published, whatever the card's outcome. The body names it (`outcome 'done'`, `'blocked'`, ...).
3. **A stop is a request, not a cancel.** The stop `steer` gives the agent a P0 card, and the agent
   decides what to do (for example drop the task's card if it is still pending). If the task's card is
   already running or finished, its `Task <id> done` note can still arrive after the stop. The bridge
   should not reopen or re-complete an IDE task it has cancelled.
4. **A rejected file is final.** An invalid direct write stays in `comms/inbox/` uncommitted and is
   never delivered. A corrected message needs a new id (with this id scheme, a new task key), because
   the old file is never re-read as new.
5. **Latency.** queue-keeper runs every minute; adoption, routing and the card request happen in one
   run. The card then waits for a free slot.
6. **File access on the host.** The workspace on the box is owned by the `autoresearch` user with mode
   `0700`. The bridge must write as that user (or the deployment must give it write access to
   `comms/inbox/` only, and read access to `comms/outbox/`, as Runtime §9.2 L0 describes). Files it
   writes must be readable by `autoresearch`.
7. **Ids with `:`** are fine on Linux and in git. A checkout of the workspace remote on Windows cannot
   hold them; macOS can.
