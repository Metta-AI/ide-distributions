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

**Action for Rohit: run the bridge as its own OS user and set `AUTORESEARCH_INBOX_WRITERS`** to that
user in the profile `.env` (see "Trust: who may write the inbox"). Without it, direct writes are
unauthenticated.

## Fixed on our side

The bridge matches §7 and Runtime §9.2 (the L0 file adapter: "Each inbound message is one JSON file
at `comms/inbox/<id>.json`", inbox writable by external agents). Our implementation was narrower than
that contract:

| Bridge behaviour | Before | Now |
|---|---|---|
| Writes `comms/inbox/<id>.json` itself and never commits | Routing read only files committed at HEAD, so bridge messages were never seen | queue-keeper adopts direct writes each run (`comms.inbound.adopt_direct_writes`): a valid regular file is committed with the exact bytes it validated; an invalid one (or a symlink, or a file whose owner is not allowed) is left as it is, gets one runtime `note` in reply to its id, and its id is recorded in `.supervisor/ledger/inbound-rejected/` for good |
| Ids `<player_id>:task:<key>` and `<player_id>:stop:<key>` | Delivery records and `in_reply_to` accepted only `I-<ULID>` | An inbound id is any `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}` without `..` (`INBOUND_ID`), except ids shaped like an outbox key (`M-...` or all digits); `schemas/inbound.json` and `schemas/message.json` follow it |
| `author: coach:<player_id>` | The author rule (consumer name) rejected `:` | `:` is allowed |
| `created`, no `received_at` | Routing required `received_at` | `created` is required and kept as the writer's claim; the delivery record's `received_at` is the time queue-keeper received the file (lateness uses it) |
| Waits while `comms/outbox/` is missing | The outbox appeared only with the first publication, which can be hours after install | Bootstrap creates `comms/inbox/` and `comms/outbox/` on every queue-keeper run |

## Where the bridge deviates from §7 (not changed on our side)

- **`on_behalf_of: human`.** The System Design amendment of 2026-10-07 removed this field: every
  inbound message from the Preston-role agent carries the human's full authority (§7 Inbound; Runtime
  §9.2 "Preston carries the human's authority"). We keep unknown fields and ignore this one, so it is
  harmless, but the bridge should stop sending it.
- **The bridge doc's "What is not exercised yet" section is stale.** It says no runtime consumes the
  inbox and that the message models wait for "the distribution publishes its schema and contract
  tests". The runtime now consumes direct writes, and the schemas (`schemas/message.json`,
  `schemas/inbound.json`) and the contract test above exist.

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
4. **A rejected id is final.** An invalid direct write stays in `comms/inbox/` uncommitted and is
   never delivered, and its id is recorded in `.supervisor/ledger/inbound-rejected/`. Rewriting the
   file under the same id does nothing: the runtime note is the one answer. A corrected message needs
   a new id (with this id scheme, a new task key). A file that is still not JSON 5 minutes after it
   was last modified is rejected too; write whole files and rename them into place.
5. **Latency.** queue-keeper runs every minute; adoption, routing and the card request happen in one
   run. The card then waits for a free slot.
6. **File access on the host: see "Trust" below.** Do not run the bridge as the `autoresearch` user.
7. **Ids with `:`** are fine on Linux and in git. A checkout of the workspace remote on Windows cannot
   hold them; macOS can.

## Trust: who may write the inbox

An inbound message carries the full authority of the human (§7, C11): a `reply` can answer the
agent's own question, a `steer` with urgency `now` gets a P0 card. So whoever can write
`comms/inbox/` speaks for the coach. The agent's own card workers run as the workspace user
(`autoresearch`), so if the bridge runs as that user too, nothing below the file layer can tell
a bridge message from one a worker forged. Runtime §9.3 says the file layer enforces the
single-writer rule with file permissions, which needs a separate user.

What we do:

- The Hermes plugin refuses, in every agent session, the `comms inbound` command and any write
  that resolves into the canonical `comms/inbox/` (file tools and shell redirects, `cp`, `tee`,
  ...), plus `python -c`/`execute_code` text that names `comms/inbox`. `comms inbound` itself
  refuses to run inside a card worker. This is best effort: a constructed path in interpreter code
  passes (policy docstring, "Residual risk").
- Adoption takes regular files only (no symlinks), and when `AUTORESEARCH_INBOX_WRITERS` is set
  (comma-separated uids or user names), only files owned by one of those users. Any other file is
  rejected with a runtime note, which also makes a forgery attempt visible to the human. When it is
  unset, queue-keeper adopts any valid file and logs in `keeper.log` that direct writes are
  unauthenticated.

What the deployment should do:

1. Run the bridge as its own user, for example `ide-bridge`.
2. Give that user write access to `comms/inbox/` only, and read access to `comms/outbox/`, for
   example a group that owns `comms/inbox/` (mode `2770`, setgid so files keep the group) and
   includes both users. The bridge also needs search (`x`) permission on the directories above it
   (the profile and workspace are `0700` on the box today). Files the bridge writes must be
   readable by `autoresearch`: use umask `027` (group read, no group write). A group-writable
   file (umask `007`) would let any member of the group rewrite a pending bridge file in place
   before queue-keeper adopts it, and the owner check would still pass.
3. Set `AUTORESEARCH_INBOX_WRITERS=ide-bridge` in the profile `.env`. It is on
   `terminal.env_passthrough`, so queue-keeper sees it under the gateway.

Getting the variable wrong:

- **An unknown user name** stops adoption only: direct writes wait in `comms/inbox/`, untouched
  and unanswered, committed inbound messages still route, and the step `inbox_writers` fails in
  `keeper.log` and escalates like any failing queue-keeper step (a `keeper_step_failed` signal,
  then a help request). Fixing the name adopts the waiting files on the next run.
- **A valid but wrong user** (the bridge runs as someone else) rejects every bridge write, each
  with a runtime note, and the rejections are final. The bridge closes those IDE tasks on the
  note. Fixing the variable does not bring them back: the bridge must send them again with new
  ids (new task keys). Check the variable before the bridge sends its first message.
