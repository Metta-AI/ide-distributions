---
name: communicate
description: Use when sending a message or acknowledging one.
---

# Communicate

This skill implements System Design §5.7 (communication). The full rules are in
`doctrine/communication.md`, which is part of `AGENTS.md`.

Doctrine upheld: D-02, D-18, D-19, D-20.

## Reads and writes

- Reads: the inbox messages in the briefing, and any entity a message references.
- Writes: staged outbound messages (`comms/staged/`), acknowledgements, and doctrine-proposal
  suggestions. The runtime publishes staged messages when the card's branch merges.

## When to send each kind

| Kind | Send it for |
| --- | --- |
| `note` | A decision or status change worth a human knowing. Send these sparingly. Also every acknowledgement. |
| `question` | A choice where human judgement adds value. Give the options and your recommendation, and say whether work is parked. |
| `suggestion` | Something a human can do, for example look at a replay. Also each doctrine proposal. |
| `help_request` | You are stuck or blocked. Say what you tried. Add refs. For a stuck signal, name the signal and give the options. |
| `highlight` | One message per event: a supported or falsified hypothesis, a newly verified claim, a promotion, or a surprising result. |

## Procedure

1. Write the body in a scratch file, for example `.scratch/msg.md`. Findings first: the
   answer is the first sentence (D-19). Point at entities with `ar://` refs; do not copy
   their content (D-20). Never send an empty message.
2. Check that each ref resolves:
   `"$AUTORESEARCH_CLI" comms resolve <ar://ref>`
3. Stage the message:
   `"$AUTORESEARCH_CLI" comms stage <kind> --title "<title>" --body-file .scratch/msg.md --ref <ar://ref>`
   Useful options: `--priority P0..P5`, `--expects-response` or `--no-expects-response`,
   `--thread <thread>`, `--in-reply-to <id>`, `--supersedes <M-id>`, `--expires <ISO time>`.
   A `--ref` can also be a JSON object `{"uri": "ar://…", "role": "subject|evidence|context", "note": "…"}`.
4. Acknowledge each `reply` and `steer` this card is responsible for (D-02):
   `"$AUTORESEARCH_CLI" comms stage note --title "Applied: <subject>" --body-file .scratch/ack.md --in-reply-to <I-id> --ref <ar://ref>`
   The outcome is applied, declined with a reason, or noted. Card finish fails while a
   responsible `reply` or `steer` has no acknowledgement. Do not acknowledge a `task` (the
   runtime does it when the card publishes) or a `seen`.
5. A message cannot change after it is published. To correct one, stage a new message with
   `--supersedes <M-id>`.
6. To see the state of an earlier message, for example whether a question has an answer:
   `"$AUTORESEARCH_CLI" comms status <M-id>`
   `"$AUTORESEARCH_CLI" comms get <M-id>`
   `comms get <inbound id>` prints an inbound message; your briefing already shows the ones
   delivered to this card.

## Proposals

- Skill proposal (D-18): a skill you write with the skill tool goes to the staging
  directory. The runtime copies it to `skills-proposed/<name>/` and publishes the
  `suggestion` with the ref `ar://skill-proposal/<name>`. That message is the D-18
  suggestion. Do not stage a second one. Do not use the skill as adopted until a human
  replies.
- Doctrine proposal: write `doctrine-proposed/<name>.md` in your worktree (what to change,
  why, the lessons and evidence behind it). Then stage
  `"$AUTORESEARCH_CLI" comms stage suggestion --title "Doctrine proposal: <name>" --body-file .scratch/msg.md --ref ar://doctrine-proposal/<name>`
- The runtime re-raises unanswered proposals each day. Planning briefings list them.

## Failure handling

- `comms stage` exit 1: the message is not valid (for example a bad ref or an empty body).
  Read `error.problems`, fix the message, and stage it again.
- `comms resolve` exit 1: the ref does not resolve. Fix the ref. Never send a message with a
  ref that does not resolve.
