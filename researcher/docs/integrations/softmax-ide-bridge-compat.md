# Softmax IDE bridge: compatibility with the autoresearch distribution

> Historical compatibility audit. The stop-steer behavior described below is superseded by
> the targeted cancellation contract in [the current bridge spec](softmax-ide-bridge.md).
> Metta PR #27807 sends `control/cancel`; do not deploy it with an older profile.


Checked on 2026-10-08 against `autoresearch/docs/integrations/softmax-ide-bridge.md` as merged on
`main` in PR #4 (commit `35c0000`), System Design §7 and Runtime §9.2. The contract test is
[`tests/protocol/test_bridge_contract.py`](../../tests/protocol/test_bridge_contract.py): it writes
bridge-shaped files into `comms/inbox/` the way the bridge does (whole file, then rename, no commit),
runs `route_all`, finishes the card, and reads `comms/outbox/` the way the bridge does.

**The contract is published** in [`schemas/comms/`](../../schemas/comms/README.md): one JSON Schema
per message kind on two envelopes, with valid and invalid example messages. The bridge's two messages
are in it as examples, byte for byte what `bridge.py`'s `deliver()` writes (provenance in that README),
and the contract test above writes exactly those files. The bridge can validate against these schemas
from a pinned ide-mvp commit instead of keeping its own copies of the shapes.

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
| Ids `<player_id>:task:<key>` and `<player_id>:stop:<key>` | Delivery records and `in_reply_to` accepted only `I-<ULID>` | An inbound id is any `[A-Za-z0-9][A-Za-z0-9._:-]{0,199}` without `..` (`INBOUND_ID`), except ids shaped like an outbox key (`M-...` or all digits); `schemas/inbound.json`, `schemas/comms/envelope.inbound.json` and the outbound envelope (`schemas/comms/envelope.outbound.json`, alias `schemas/message.json`) follow it |
| `author: coach:<player_id>` | The author rule (consumer name) rejected `:` | `:` is allowed |
| `created`, no `received_at` | Routing required `received_at` | `created` is required and kept as the writer's claim; the delivery record's `received_at` is the time queue-keeper received the file (lateness uses it) |
| Waits while `comms/outbox/` is missing | The outbox appeared only with the first publication, which can be hours after install | Bootstrap creates `comms/inbox/` and `comms/outbox/` on every queue-keeper run |
| `deliver` creates `comms/inbox/` (`mkdir -p`) and writes a task before our first bootstrap, when a coach clicks a research button early | Seeding copied into the existing directory and the seed commit swept the file in: routing then took it as already committed, so it skipped adoption's checks (validation, the `AUTORESEARCH_INBOX_WRITERS` owner check, the rejection note) and hid inside a commit labelled as the seed | Seed commits only the paths it wrote, so early inbox files stay untracked and routing adopts them like any direct write. Clone holds them next to the workspace directory (`.<workspace>-early-inbox/`) during checkout and puts them back; files a crash left there are put back on the next run. The bridge's in-flight `.<id>.json.tmp` files are never moved. A name that clashes with a file the clone brought moves to `.<workspace>-early-inbox-clashed/` with one bootstrap warning, never overwritten |

## Where the bridge deviates from §7 (not changed on our side)

- **`on_behalf_of: human`.** The System Design amendment of 2026-10-07 removed this field: every
  inbound message from the Preston-role agent carries the human's full authority (§7 Inbound; Runtime
  §9.2 "Preston carries the human's authority"). We keep unknown fields and ignore this one, so it is
  harmless, but the bridge should stop sending it.
- **The bridge doc's "What is not exercised yet" section is stale.** It says no runtime consumes the
  inbox and that the message models wait for "the distribution publishes its schema and contract
  tests". The runtime now consumes direct writes, and the published schemas (`schemas/comms/`)
  and the contract test above exist.

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
  it is absent, not `null`, otherwise (`schemas/comms/envelope.outbound.json`: optional string; `thread` is the same).
  On the box today 4 of 9 outbox messages have it. The bridge's `Outbound` model must default it to
  `None`.
- Task acknowledgement: when the task's card is published, the runtime publishes a `note`
  (`from: runtime`) with `in_reply_to` set to the bridge's task id. The bridge's "a note in reply
  to a task id closes the IDE task" holds. Its title is the outcome and the task's own title
  (`Done: Profile macromackie's opening`); its body is the card's completion summary (see
  "Outbox messages are not written for humans" below). The card, request, merge commit and
  outcome are in the additive `about` field, never in the title or body
  (`schemas/comms/examples/outbound/note/ack-of-ide-bridge-task.json`).
- Files are never rewritten or moved, so the bridge's retry ("finds the file and writes nothing")
  stays correct.

## Notes for the bridge

1. **A note in reply to a task id is not always "done".** The runtime also answers a task with a
   note when it cannot become a card: `Not started: <task title>` (bad refs, admission rejected
   it; the body says why and what to send instead) or `Not delivered: the message is not valid`
   (the file is invalid: wrong `mode`, missing field, `id` not equal to the file name, no
   `created`; the body lists the problems). Both are acknowledgements under §7 ("applied, declined
   with a reason, or noted"). The IDE should show the note as the outcome rather than assume
   success. To tell them apart without parsing the title, read `about.outcome`: `rejected` for
   these two, the card's outcome for an acknowledgement.
2. **The acknowledgement covers every card outcome.** It is published when the card's work is
   published, whatever the card's outcome. The title starts with the outcome: `Done:`,
   `Partly done:`, `Dropped:`, `Failed:` or `Blocked:`; `about.outcome` has the raw value.
   A `blocked` task is not finished. The runtime publishes exactly one follow-up `note` for it,
   with the same `in_reply_to`, the final `about.outcome`, `about.follows` set to the `blocked`
   note's id, and `runtime_key` `ack-final:<inbound id>` (the task's `I-` id, not a card id) (the delivery record names it in
   `final_ack_by`), when a later handoff ends the work with `done`, `partial`, `dropped` or
   `failed`. That later handoff comes from one of these:
   - the same card, when queue-keeper requeued it before its `blocked` handoff was published
     (the request was still open, so the rerun could hand off);
   - the repair card queue-keeper opens for a card whose `blocked` handoff is published and that
     Hermes then holds blocked (not a `tool-fault:` block), or that a human unblocks (the rerun
     could not hand off: the runtime closes it and opens the repair card instead, for a rerun
     it had to stop only once Hermes has ended its worker, 2 minutes or more after the stop; a
     rerun that already completed is repaired the same way);
   - a repair of a repair, up to 2 repairs deep (`MAX_REPAIR_CHAIN`);
   - the recovery card queue-keeper opens when one of these cards' handoffs cannot merge (a
     repair or reconcile of the handoff, for example a merge conflict) or a run completes
     without a handoff. The walk is bounded at 4 hops: each repair in a full chain may need one
     recovery.

   A repair can publish its final outcome before the `blocked` handoff it repairs; the follow-up
   then goes out right after the `blocked` note, in the same publication.

   The bridge shows the task paused on the first note and closes it on the follow-up. A task first
   acknowledged with a final outcome gets no follow-up, and a second `blocked` publishes nothing.
   These blocked tasks get **no** follow-up, so the IDE task stays paused:
   - a card that finished `blocked` and then completed (`kanban_complete`), as most agent blocks do
     (for example `league-watch` on an identity refusal): nothing reruns it;
   - a `tool-fault:` card no human has unblocked yet (D-26), or a card held blocked at a spend cap;
   - a repair card that the agent or the planner drops before it runs;
   - a card a promotion covers (its promotion's recovery card carries the work on, outside the
     repair chain);
   - a chain that reached the repair limit: its last card stays blocked, and one P1 help request
     asks a human.

   To close such a task in the IDE, the coach cancels it. That closes the IDE task, and the bridge
   sends the stop `steer` (note 3), which costs one P0 card for a task whose work is already over.
   The operator can instead unblock a `tool-fault:` card once the tool is fixed, which opens the
   repair card that sends the follow-up.

3. **A stop is a request, not a cancel.** The stop `steer` gives the agent a P0 card, and the agent
   decides what to do (for example drop the task's card if it is still pending). If the task's card is
   already running or finished, its acknowledgement can still arrive after the stop. The bridge
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

On Softmax hosting there is no second user: the gateway, the bridge, the coach chat and the
research workers share one uid (plan D9, option (a) for the first deploy), so
`AUTORESEARCH_INBOX_WRITERS` is not set there and direct writes are unauthenticated (README,
"Softmax hosting", "What is not isolated"). The steps below are what a deployment with a separate
bridge user does.

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

## Outbox messages are not written for humans: add a translation step

Outbox messages are written for a Preston-role agent that can read the whole workspace
(`doctrine/communication.md`: "It tells the human what matters, in its own words."). They carry
message and record ids, `ar://` references and assumed context on purpose. The bridge says every
outbox message in the IDE chat as `<title>\n\n<body>`, and it uses the acknowledging note as the IDE
task's outcome text, so a coach sees the agent-facing text verbatim. Two real messages from a
live run showed the problem:

- A task acknowledgement, which became the IDE task's outcome:
  `Task I-01M4EJ7ABV097KEMKSBV76N9T9 done` / `Card t_6c0a018c (request
  Q-01M4EJ9RQMHVH6XK3N4T760MNB) created for inbound task I-01M4EJ7ABV097KEMKSBV76N9T9, finished
  with outcome 'done' and was published at f5e50b4909eb…`.
- A research note listing four `ar://hypothesis/H-…` references, with unexplained terms
  ("kill-steal", "hysteresis band") and a pointer to a journal entry.

What changes on each side:

- **The bridge (recommended):** pass each outbox message through the Hermes chat agent the
  bridge already runs, with an instruction such as "tell the coach what this means and whether
  they need to do anything". Say the result in chat. The agent can resolve the `ar://` references
  with the profile CLI (`"$AUTORESEARCH_CLI" comms resolve <uri>`). Keep the raw message available
  as a detail. Use the same plain text for the task outcome when closing an IDE task.
- **The distribution (done):** every runtime-generated message now says in plain words what
  happened and what, if anything, the reader must do (`src/autoresearch/comms/runtime_messages.py`).
  The first example now reads `Done: <task title>`, and its body is the card's own completion
  summary, then what follows from the outcome and the titles of the messages the card sent. The
  summary is the one the card's run ended with on the board (the `summary` it gave
  `kanban_complete`, which complete-card asks to be plain sentences for the coach), or the
  opening paragraph of its journal when the board summary names ids. For a blocked card the
  journal comes first, because its board summary is the block reason. When both name ids, each id
  is replaced by what it names ("the hypothesis", "the card", "a commit"). Ids and commits moved
  into `refs` and the additive `about` field. These messages are still agent-facing and partly
  the agent's own words, so the translation step is still the place where text becomes fit for a
  human.

Our own test console (`autoresearch-shim/`) does the same: its feed shows the chat agent's
plain-language version of each message by default, and the raw message behind "Details".
