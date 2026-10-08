# Communication rules

These rules cover every message you send. Rules D-19 and D-20 in the research-agent doctrine are the same rules, in short form.

## Who reads your messages

A Preston-role agent reads them. It can read every file in the workspace. It tells the human what matters, in its own words. Write for that agent: put the finding and the question in the message, and point to the workspace for detail.

You never send a message and then wait. The system delivers messages when a reader comes. The answer arrives in a later card run (see "Inbound kinds").

## How to send

Stage each message in your worktree. The runtime publishes it when your card completes.

```
"$AUTORESEARCH_CLI" comms stage <kind> --title "<title>" --body-file <file> --ref <ar:// ref>
```

Useful options: `--priority P0..P5`, `--expects-response` or `--no-expects-response`, `--thread`, `--in-reply-to <M-ID>`, `--supersedes <M-ID>`, `--expires <time>`. A message cannot be edited after it is published. To correct one, send a new message with `--supersedes`.

## Outbound kinds

| Kind | Use it for | Reply expected |
| --- | --- | --- |
| `note` | A decision or status change that a human should know. Also the acknowledgement of an inbound message. | Set per message with `expects_response`. |
| `question` | A choice where human judgement adds value. Give the options, your recommendation, and say whether work is parked until the answer comes. | Yes. An unanswered question stays owed. |
| `suggestion` | Something a human can do, for example raise credits or look at a replay. Also each skill proposal and each doctrine proposal. | Set per message. A skill proposal stays open until a reply. |
| `help_request` | You are stuck or blocked. Say what you tried. Add refs. For a stuck signal, name the signal and give the options. For a tool fault (D-26), name the command and the error text. | Set per message. |
| `highlight` | One event: a supported or falsified hypothesis, a newly verified claim, a promotion, or a surprising result. | Set per message. |

For a skill proposal, add the ref `ar://skill-proposal/<name>`.

## Rules for the body

1. Findings first (D-19). Put the answer in the first sentence. Do not start with background. An unanswered question stays owed.
2. Teach before you ask (D-19). Do not ask for a strategic choice that depends on mechanics you have not explained. Explain the mechanics in the same message, or point to the claim that explains them.
3. Keep the backstage backstage (D-19). Do not describe tool errors, retries, or internal steps unless they change what the human must do. A tool fault (D-26) always does.
4. Never send an empty message (D-19). Every message needs a clear title and a body with content.
5. Point, do not restate (D-20). Refer to entities with `ar://` refs. Do not copy their content into the body.
6. Send one `highlight` for each event that is listed in the `highlight` row above. Do not send a second one for the same event. These highlight events are the events that D-20 means by "§5.7".
7. Write plain, short sentences. Define a term the first time you use it.

## References

A reference has the form `ar://<type>/<id>[#anchor]`. It can also carry:

- `rev`: the workspace git commit that the reference points at.
- `role`: `subject`, `evidence`, or `context`.
- `note`: a short explanation of why the reference is there.

The types are: topic, hypothesis, experiment, result, version, claim, hub, lesson, evidence, xreq, episode, journal, plan, and file. There are more types: `skill-proposal`, `doctrine-proposal`, `opponent` (an opponent's hub, claim, or model), and a journal decision as `ar://journal/J-NNNN#dN`. A game can add more types.

To read a reference, run `"$AUTORESEARCH_CLI" comms resolve <uri> [--rev <commit>]`.

## Inbound kinds

The briefing of your card lists the inbound messages that were delivered to it.

| Kind | What it is | Acknowledge it |
| --- | --- | --- |
| `reply` | An answer to one of your messages: a choice, text, or both. | Yes (D-02). Send a `note` with `--in-reply-to <inbound id>`. State the outcome: applied, declined with a reason, or noted. |
| `steer` | Direction from the human. A steer outranks your own plan (D-02). | Yes, the same way as a `reply`. |
| `task` | A request for work. The runtime makes a card from it. | No. The runtime acknowledges it when the card that was created from it publishes. |
| `seen` | It only marks other messages as read. | No. |

Card completion checks that each delivered `reply` and `steer` has an acknowledgement.
