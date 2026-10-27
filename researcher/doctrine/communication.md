# Communication rules

These rules cover comments to the orchestrator and the final handoff summary. D-19 and D-20
are their short form in the research-agent doctrine.

## Reader and channel

The orchestrator reads your findings, answers questions and reports to the user in its own
words. During task execution, report to the coordinator. In an explicitly opened human discussion, answer the human from the task and its evidence; do not change task execution unless requested. Use native `kanban_comment` on your own card,
with `task_id` from `kanban_show` and a prefixed `body`. Comments are durable immediately.
Do not set another board or comment on another card.

| Prefix | Use |
| --- | --- |
| `note:` | A useful decision/status change, or acknowledgement of steering: applied, declined with a reason, or noted. |
| `question:` | An answer needed from the orchestrator. Explain the choice, options and recommendation. |
| `suggestion:` | Follow-up work, a priority change, or a skill/doctrine proposal with evidence. |
| `help_request:` | Work cannot proceed. State what failed, what you tried and the command/error or missing information. |
| `highlight:` | One supported/falsified hypothesis, newly verified claim, promotion observed in the field, or surprising result. |

Example `kanban_comment` arguments:

```json
{"task_id":"<own task id>","body":"question: The two policies tie on the primary metric. Should the next card investigate the late-game loss instead? I recommend that because the loss occurs after the measured mechanism. ar://experiment/<E-id> ar://result/<R-id>"}
```

Continue independent work after a comment. When an answer is necessary to continue, use
`kanban_block` with `reason` and `kind: "needs_input"`; do not poll for answers. The
orchestrator's answer arrives as an injected comment with user authority. Apply it ahead of
your own plan and acknowledge it in a `note:` on your card. An unanswered question stays owed.

## Findings first, references for detail

1. Answer in the first sentence. Explain what changed and why it matters.
2. Teach before you ask: explain any mechanics needed to understand the choice, or cite the
   claim that does. Include your recommendation.
3. Keep internal steps out of the report unless they affect the next decision. Tool faults
   need the exact command, error and affected refs so the orchestrator can arrange repair.
4. Never send an empty comment. Use plain words and define unfamiliar terms.
5. Point to records with `ar://` refs rather than copying their contents.
6. Send one `highlight:` per event, not a second report of the same event. Correct an earlier
   comment with a new comment that identifies what changed.
7. A final `kanban_complete` summary follows the same findings-first rule. Put detailed IDs,
   commits and waiting state in metadata, using the `card` skill's contract.

## References and proposals

Resolve references with `"$RESEARCH_CLI" records resolve <ar://ref>`. Typical references are
`ar://experiment/<E-id>`, `ar://evidence/<EV-id>`, `ar://journal/<J-id>#d<N>` and `ar://plan`.
Use the returned record/path to check the evidence. The handoff journal is `ar://journal/J-...`.

Self-written skills stay in `skills-proposed/<name>/` until a human replies (D-18); tell the
orchestrator with a `suggestion:` and `ar://skill-proposal/<name>`. Doctrine proposals go in
`doctrine-proposed/<name>.md`, with a `suggestion:` citing `ar://doctrine-proposal/<name>`.
Do not edit installed skills, doctrine or `AGENTS.md` during a research card. Propose the
change and its evidence; do not invent an automatic adoption process.
