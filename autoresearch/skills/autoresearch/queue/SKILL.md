---
name: queue
description: Use when a card starts, to read it and choose the work.
---

# Queue: start an iteration

This skill implements System Design §5.1 (iteration start) and §5.9 (responding to a stuck
signal). Use it at the start of every card, before any other skill.

Doctrine upheld: D-02, D-12, D-13, D-21, D-22, D-23, D-27.

## Reads and writes

- Reads: the card, its briefing, the Topics the card names, `kb/index.md`, the inbox
  messages in the briefing, and the work queue (Pending and Claimed cards). A planning card
  also reads all of `plan.md`.
- Writes: card requests (`cards/requests/`), Topics from proposals (planning cards only).

## Conventions for every skill

- Run every project command through `"$AUTORESEARCH_CLI"`. Each command prints one JSON
  envelope: `{"ok": true, "data": …}` or `{"ok": false, "error": {"message": …}}`. Read the
  envelope. Do not trust the exit code alone.
- Work only in your worktree (the current directory of the card). Put temporary files
  (downloads, analysis output, build output) in `.scratch/` in the worktree. Card finish does
  not commit `.scratch/`. Never write to `/tmp` or `$HOME`.
- Record ids look like `E-01K…` (type prefix and a ULID). `records new` makes the id. Do not
  invent ids.
- Game-specific actions go through the game contract `GAME.md`. Its path is
  `"$AR_HOME/games/$AUTORESEARCH_GAME/GAME.md"`, where
  `AR_HOME="$(dirname "$(dirname "$AUTORESEARCH_CLI")")"` is the profile directory. A
  binding is a command line in the `bindings` map of its front matter. Run a binding from
  `"$AR_HOME"`, because its relative paths start there. The body of `GAME.md` says how to
  call each binding. `"$AUTORESEARCH_CLI" game show` prints the contract's front matter as
  JSON (bindings, outcome metrics, performance guardrails, episode floor, league, layers)
  and the file's path.
- The current game version (the one noise records, designs and claims are checked against)
  comes from `"$AUTORESEARCH_CLI" softmax game-version`. Do not read it from a card title or
  an old record.

## Long commands: wait without the model

Every tool call sends the whole conversation to the model again, so each call costs money.
Waiting is free only inside one call. Use this pattern for every command that can run long
(a build, a local check, a long fetch):

1. If the command ends within 10 minutes, run it in the foreground and set the terminal
   `timeout` (at most 600). The call returns as soon as the command ends.
2. Otherwise start it with the terminal tool's `background: true`, keep the `session_id`,
   and call the native `process_manage` tool once: action `wait`, that `session_id`,
   `timeout` 1800 (the longest wait this profile allows; Hermes' per-tool-call limit is set above it). It returns when the process exits,
   with its exit code and the end of its output. Only if it returns `status: "timeout"`,
   call `wait` again. Do nothing else in between.
3. To wait for a process you did not start (a holder that another of your commands left
   running), start a loop that waits for it with `background: true`, for example
   `while kill -0 <pid>; do sleep 30; done`, and wait on that loop as in step 2.

Never wait any other way: no `sleep` of 30 seconds or more, no `sleep` loops that check
`pgrep`, `ps` or `kill -0` in a foreground command (the plugin refuses them), no repeated
`poll` or `log` calls, and no `kanban_heartbeat` calls to pass the time (Hermes records that
you are alive while a tool runs). `notify` does not reach a card worker: always `wait`.

## Procedure

1. Read the briefing. It is in the first message of the card. It names the card title,
   mode, reason (`Why`), refs, and priority, the current signals, the card's slice of the
   workspace, and the inbound messages delivered to this card. After it, "Other cards
   running now" lists the cards other workers held when this run started (refs, run,
   heartbeat age). It is information, not instructions: do not duplicate their work (D-21).
2. Read the card record when you need its runs or full body:
   `"$AUTORESEARCH_CLI" queue show <task id>`
   The task id is on the `Task:` line of the briefing.
3. Read each ref of the card:
   `"$AUTORESEARCH_CLI" comms resolve <ar://ref>`
   Read the Topics that the card names, and `kb/index.md`. A planning card reads all of
   `plan.md`.
4. Handle the inbound messages in the briefing (D-02):
   - A `steer` outranks your own plan. Apply it, or decline it with a reason.
   - Each `reply` and `steer` this card is responsible for needs an acknowledgement. Stage
     it during card completion (see the `complete-card` and `communicate` skills).
   - Do not acknowledge a `task` or a `seen`. The runtime acknowledges a `task` when its
     card publishes.
   - A message marked "handled by <task>" belongs to another card. Read it. Do not
     acknowledge it.
   - A `proposed_topic` goes to the planning card. In a planning card, create the Topic
     (`"$AUTORESEARCH_CLI" records new topic --meta-file .scratch/topic.json --body-file
     .scratch/topic.md`, with `origin: {"kind": "human", "inbound": "<inbound id>"}`) or merge it
     into an existing Topic, and put the `ar://topic/<T-id>` ref in the acknowledgement.
5. Respond to each stuck signal in the briefing (§5.9, D-13). Do one of these three things:
   - Change course: request cards for a new hypothesis, a different lever, a different
     Topic, or maintenance. Request a planning card when priorities must change.
   - Explain in the journal Decisions section why progress is real.
   - Send a `help_request` that names the signal, what you tried, and the options.
6. Look at the queue before you request any card, so that you do not request a duplicate:
   `"$AUTORESEARCH_CLI" queue list --state pending`
   `"$AUTORESEARCH_CLI" queue list --state claimed`
7. Do the card's one unit of work in its mode (D-12, D-21). Use the skill for the mode:

   | Mode or card | Skills |
   | --- | --- |
   | `investigation` | `replay-diagnosis`, then `diagnose` |
   | `scouting` | `league-watch`, `opponent-profile`, `meta-recon` |
   | `experimentation`: design | `experiment`, `build-upload`, `xp-request` |
   | `experimentation`: "launch evaluation E-…" | `xp-request` |
   | `experimentation`: review of E-… | `fetch-artifacts`, `review-xp` (uses `ab-compare`) |
   | `experimentation`: "Promote E-…" | `submit` |
   | `maintenance` | `kb-maintenance`, `lessons`; a calibration card: `calibrate-noise`; "Recover promotion P-…": `submit` (recovery section) |
   | `planning` | `groom-queue` |

8. For further work, request a follow-up card. Do not do that work in this card (D-22):
   `"$AUTORESEARCH_CLI" queue request create --title "<title>" --mode <mode> --why "<reason>" --ref <ar://ref> --priority P2`
   Modes: `investigation`, `scouting`, `experimentation`, `maintenance`, `planning`.
   Priorities: `P0` (highest) to `P5`. `--order 0..9` orders cards inside one priority.
   Give every card the refs of the entities it will change (D-23).
9. End with the `complete-card` skill.

## Failure handling

- `queue show` or `queue list` exit 3: Hermes is not installed for this process. Continue
  from the briefing, and note it in the journal.
- A request exit 2 ("not in a worker worktree"): you are not in a card worker. Stop and send
  a `help_request`.
- Change only the entities the card names (D-23). Card finish rejects other changes. To
  change another entity, request a card for it.
- Never work around a refusal by the plugin, a CLI, a lock or a permission to get the
  refused effect (no `python3 - <<EOF`, other path or identity; D-27). Record it and end the
  card as the skill you are following says; when none says, see `complete-card`.
