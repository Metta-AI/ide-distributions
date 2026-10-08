# Communication rules

These rules cover every message you send. Rules D-19 and D-20 in the research-agent doctrine are the same rules, in short form.

## Who reads your messages

A Preston-role agent reads them. It can read every file in the workspace. It tells the human what matters, in its own words. Write for that agent: put the finding and the question in the message, and point to the workspace for detail.

You never send a message and then wait. The system delivers messages when a reader comes. The answer arrives in a later card run (see "Inbound messages").

## How to send

Stage each message in your worktree. The runtime publishes it when your card completes.

```
"$AUTORESEARCH_CLI" comms stage <kind> --title "<title>" --body-file <file> --ref <ar:// ref>
```

Useful options: `--priority P0..P5`, `--expects-response` or `--no-expects-response`, `--thread`, `--in-reply-to <M-ID>`, `--supersedes <M-ID>`, `--expires <time>`. A message cannot be edited after it is published. To correct one, send a new message with `--supersedes`.

## The five outbound kinds

| Kind | Use it for | Reply expected |
| --- | --- | --- |
| `note` | A decision or status change that a human should know. Also the acknowledgement of an inbound message. | Set per message with `expects_response`. |
| `question` | A choice where human judgement adds value. Give the options, your recommendation, and say whether work is parked until the answer comes. | Yes. An unanswered question stays owed. |
| `suggestion` | Something a human can do, for example raise credits or look at a replay. Also each skill proposal and each doctrine proposal. | Set per message. A skill proposal stays open until a reply. |
| `help_request` | You are stuck or blocked. Say what you tried. Add refs. For a stuck signal, name the signal and give the options. | Set per message. |
| `highlight` | One event: a supported or falsified hypothesis, a newly verified claim, a promotion, or a surprising result. | Set per message. |

For a skill proposal, add the ref `ar://skill-proposal/<name>`.

## Rules for the body

1. Findings first (D-19). Put the answer in the first sentence. Do not start with background.
2. An unanswered question stays owed. If an inbound message asks you something, answer it before you raise new topics.
3. Teach before you ask (D-19). Do not ask for a strategic choice that depends on mechanics you have not explained. Explain the mechanics in the same message, or point to the claim that explains them.
4. Keep the backstage backstage (D-19). Do not describe tool errors, retries, or internal steps unless they change what the human must do.
5. Never send an empty message (D-19). Every message needs a clear title and a body with content.
6. Point, do not restate (D-20). Refer to entities with `ar://` refs. Do not copy their content into the body.
7. Send one `highlight` for each event that is listed in the `highlight` row above. Do not send a second one for the same event.
8. Write plain, short sentences. Define a term the first time you use it.

## References

A reference has the form `ar://<type>/<id>[#anchor]`. It can also carry:

- `rev`: the workspace git commit that the reference points at.
- `role`: `subject`, `evidence`, or `context`.
- `note`: a short explanation of why the reference is there.

The types are: topic, hypothesis, experiment, result, version, claim, hub, lesson, evidence, xreq, episode, journal, plan, and file. There are two more types: `skill-proposal`, and a journal decision as `ar://journal/J-NNNN#dN`. A game can add more types.

To read a reference, run `"$AUTORESEARCH_CLI" comms resolve <uri> [--rev <commit>]`.

## Inbound messages

There are four inbound kinds: `reply`, `steer`, `seen`, and `task`. The briefing of your card lists the messages that were delivered to it.

- Acknowledge every `reply` and every `steer` (D-02). Send a `note` with `--in-reply-to <M-ID>`. State the outcome: applied, declined with a reason, or noted.
- Acknowledge a `task` when the card that was created from it completes.
- A `seen` message only marks other messages as read. Do not acknowledge it.
- A steer outranks your own plan (D-02).
- A reply can arrive after its question expired, or after the work moved on. Read it, decide what to do, and acknowledge it. You may discard a stale answer. If you do, acknowledge it as noted.
- Card completion checks that each delivered `reply` and `steer` has an acknowledgement.
