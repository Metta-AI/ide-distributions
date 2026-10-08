---
name: complete-card
description: Use when ending a card, to journal, ack, and finish.
---

# Complete a card

This skill implements System Design §5.8 (card completion). Use it at the end of every
card, also when the work stopped early.

Doctrine upheld: D-01, D-02, D-12, D-22, D-23, D-24.

## Reads and writes

- Reads: the card, the journal, the inbox messages in the briefing.
- Writes: one journal entry with its Decisions section, acknowledgements, follow-up card
  requests, and the card outcome (the handoff record).

## Procedure

1. Write the journal entry. Each card run adds exactly one new journal with `card` set to
   this card. Put the body in a scratch file, for example `.scratch/journal.md`:

   ```markdown
   <What you did, what you found, and what is open. Use ar:// refs.>

   ## Decisions

   - <Decision>. Reason: <why>. Refs: ar://<type>/<id>.
   - <Proposed priority change, if any, and the planning card you requested for it.>
   ```

   Each top-level list item under `## Decisions` is one decision. Other messages can point
   at it as `ar://journal/<J-id>#d<N>`. Record every decision that is not an experiment
   decision, a submission, or an acknowledgement here (D-01). Then create the record:

   `"$AUTORESEARCH_CLI" records new journal --meta-file .scratch/journal.json --body-file .scratch/journal.md`

   with `.scratch/journal.json` like (no `outcome` yet; step 5 sets it):

   ```json
   {"iteration": <run number, see below>, "mode": "<card mode>",
    "card": "<the task id on the briefing's Task line>", "doctrine_version": "<N from the AGENTS.md header>",
    "unit": "<one line: the unit of work>", "refs": ["ar://…"]}
   ```

   - `iteration`: `$HERMES_KANBAN_RUN_ID` as an integer (Kanban run ids are integers per
     board). In the serial fallback (`$HERMES_KANBAN_RUN_ID` is not set; run ids look like
     `r-<ULID>`), use the number of runs of this card:
     `"$AUTORESEARCH_CLI" queue show <task id>` and count `data.runs`.
   - The doctrine version is `N` in the first line of `AGENTS.md`
     (`<!-- doctrine-version: N -->`).

   Create the journal without `outcome`: a journal cannot change once its outcome is set.
   Until step 5 you may change it freely, for example after a failed `card finish`:
   `"$AUTORESEARCH_CLI" records update <J-id> --body-file .scratch/journal.md`
   Do not make a second journal.
2. Acknowledge each `reply` and `steer` that this card is responsible for (D-02). See the
   `communicate` skill:
   `"$AUTORESEARCH_CLI" comms stage note --title "<outcome>: <subject>" --body-file .scratch/ack.md --in-reply-to <I-id> --ref <ar://ref>`
   State the outcome: applied, declined with a reason, or noted. Do not acknowledge a `task`;
   the runtime does that when this card publishes.
3. Request follow-up cards for further work, any number (D-22). Each card names its refs:
   `"$AUTORESEARCH_CLI" queue request create --title "<title>" --mode <mode> --why "<reason>" --ref <ar://ref>`
   A non-planning card never rewrites `plan.md`. To propose a priority change, record it in
   the Decisions section and request a planning card.
4. Validate the records:
   `"$AUTORESEARCH_CLI" records validate --all`
   Fix each problem it lists. Never edit generated files such as `kb/index.md` (D-24).
5. Choose one outcome, set it on the journal, then finish the card with the same value
   (`card finish` refuses a journal outcome that differs from `--outcome`):
   `"$AUTORESEARCH_CLI" records update <J-id> --patch '{"outcome": "<o>"}'`
   `"$AUTORESEARCH_CLI" card finish --outcome <o>`

   | Outcome | When |
   | --- | --- |
   | `done` | The unit of work is complete. |
   | `partial` | The unit stopped at a safe point and the rest has a follow-up, for example an experiment that waits as `awaiting-credits`. |
   | `dropped` | The unit is not worth doing any more (it is obsolete or another card covers it). The journal says why. |
   | `failed` | The unit could not be done. The journal says why. |
   | `blocked` | Work cannot go on without a human or the runtime, for example an identity refusal. Send a `help_request` first. |

   Set the outcome only right before `card finish`: after it the journal is frozen. To drop a
   different card that has not started, a planning card uses `queue request drop` (see
   `groom-queue`).

   `card finish` commits your worktree, validates the change, and writes the handoff.
   - Exit 0: `data` is the handoff record. Go to step 6.
   - Exit 1: `error.problems` lists what is wrong. Nothing was recorded. Fix each problem
     (a missing acknowledgement, a change to an entity that the card does not name, an
     invalid record) and run `card finish` again with the same outcome. The journal's
     outcome is already set, so its body cannot change any more: put new information in the
     records or requests you fix, not in the journal.
   - Exit 2: no card worker context. Stop and report it in your final message.
6. Call the native `kanban_complete` tool. Give a `summary` of one to three sentences, and
   give the `data` object of the `card finish` envelope as `metadata`. List the card ids you
   made with `kanban_create`, if any, in `created_cards` (normally none: request cards with
   `queue request create`).
   Serial fallback: when the `kanban_complete` tool does not exist, `card finish` alone
   ends the card.
7. Stop. After `card finish` the plugin blocks every file write in this card.

## Failure handling

- If `card finish` keeps failing on the same problem, finish with `--outcome failed`.
  Outcome `failed` records the handoff even when the content is not valid, and the runtime
  opens a repair card. The journal's outcome then differs from `failed`; that difference is
  listed in the handoff's problems with the others, which tells the repair card what
  happened.
- Do not skip `kanban_complete` after a successful `card finish`. The runtime processes a
  handoff only when the run is terminal.
