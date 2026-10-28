---
name: report
description: Use when reporting findings to the orchestrator.
---

# Report to the orchestrator

Implements System Design §5.7 through native Kanban comments and handoff summaries.
Doctrine upheld: D-02, D-18, D-19, D-20, D-22, D-26.

Read `doctrine/communication.md`. The orchestrator is the reader; it talks to the user.

1. Start with the finding or question. State the consequence, your recommendation and any
   uncertainty. Explain the mechanics needed to understand a choice before asking for it.
2. Point to records rather than restating them. Resolve a ref when needed:
   `"$RESEARCH_CLI" records resolve <ar://ref>`.
3. Use native `kanban_comment` on your OWN card, with `task_id` and a body beginning with
   `note:`, `question:`, `suggestion:`, `help_request:` or `highlight:`. The comment is durable
   immediately. Give tool-fault reports the exact command, error and affected paths/refs.
4. Acknowledge injected steering with `note: applied`, `note: declined` plus a reason, or
   `note: noted`. It outranks your own plan. Do not ask the user directly.
5. Continue independent work. If an answer is necessary to proceed, follow `card` to preserve
   work and call `kanban_block` with the question as its reason. Never wait for XP this way.
6. Send one `highlight:` per supported/falsified hypothesis, verified claim, observed champion change
   or surprising result. Do not describe a candidate upload as a promotion.
7. Put proposed follow-up work in a `suggestion:` or `metadata.next`, with purpose and refs.
   The orchestrator decides what runs next; do not create a card yourself.

For a skill proposal, write `skills-proposed/<name>/` and cite
`ar://skill-proposal/<name>`. For doctrine, write `doctrine-proposed/<name>.md` and cite
`ar://doctrine-proposal/<name>`. Explain the evidence and the proposed change. Preserve D-18's
human adoption rule; do not edit installed skills or doctrine.

The final `kanban_complete` summary is also findings-first: what you established or produced,
why it matters, and what is still open. Detailed refs and waits belong in metadata. Never send
an empty comment or repeat a report solely because another turn began.
