---
name: groom-queue
description: Use when a planning card grooms the queue and plan.
---

# Groom the work queue

This skill implements System Design §5.10 (planning grooms the work queue). Use it only in a
`planning` card. Only a planning card writes `plan.md`.

Doctrine upheld: D-01, D-12, D-13, D-21, D-22, D-23.

## Reads and writes

- Reads: `plan.md` (its Priorities), the Topics, the open threads, the work queue, the
  signals, and the open proposals and planning backlog in the briefing.
- Writes: `plan.md`, card requests (create, reprioritize, drop), Topics from
  `proposed_topic` messages, and the journal.

## Procedure

1. Read all of `plan.md`, the briefing (signals, open proposals, planning backlog), and the
   Topics (`"$AUTORESEARCH_CLI" records find topic --where status=active`, then
   `"$AUTORESEARCH_CLI" records show <T-id>`).
2. Read the queue:
   `"$AUTORESEARCH_CLI" queue list --state pending`
   `"$AUTORESEARCH_CLI" queue list --state claimed`
   `"$AUTORESEARCH_CLI" queue list --state blocked`
   Each card's `spec.request_id` is its request id (`Q-…`). Request operations name it.
3. Handle the planning backlog: create or merge a Topic for each `proposed_topic`
   (`"$AUTORESEARCH_CLI" records new topic --meta-file .scratch/topic.json --body-file .scratch/topic.md`),
   and acknowledge the inbound message with the `ar://topic/<T-id>` ref (see `communicate`).
4. Merge duplicate cards: keep one, drop the others with a reason that names the kept card.
   `"$AUTORESEARCH_CLI" queue request drop <Q-id> --reason "duplicate of <Q-id>"`
5. Split an oversized card: request the parts, then drop the original.
   `"$AUTORESEARCH_CLI" queue request create --title "<part>" --mode <mode> --why "<reason>" --ref <ar://ref> --priority <P>`
6. Re-prioritize cards against the plan Priorities and the Topics:
   `"$AUTORESEARCH_CLI" queue request reprioritize <Q-id> --priority <P0..P5> --reason "<reason>"`
   Keep the runtime's order: urgent inbound steering, then handoff repair, then reviews of
   completed XP, then the plan's order.
7. Drop stale or obsolete cards, each with a reason:
   `"$AUTORESEARCH_CLI" queue request drop <Q-id> --reason "<why it is stale>"`
   A drop affects only a card that has not started.
8. Request cards for under-served Topics and open threads (step 5 command). Answer each
   stuck signal in the briefing (§5.9, D-13).
9. Check that no two pending cards change the same entity. Merge them, or order them with
   `--order` or priorities.
10. Update `plan.md`: the Priorities, the active Topics, and the open threads. Record the
    stale open threads you archive.
11. Finish with the `complete-card` skill. The journal Decisions section lists each merge,
    split, priority change, and drop with its reason (D-01).

## Failure handling

- A request takes effect only after this card's branch merges. The runtime may reject a
  request (for example, refs that do not resolve, or a second planning card). Do not
  request a planning card from a planning card; the runtime makes one on a schedule.
- If the queue is empty or lost, rebuild the work from the workspace: open Topics,
  experiments by status (`"$AUTORESEARCH_CLI" records find experiment --where status=designed`),
  and open threads in `plan.md`.
