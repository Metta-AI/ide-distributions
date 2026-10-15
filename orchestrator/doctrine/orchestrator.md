# Orchestrator doctrine

1. Start from `status` and `board_reconcile`, not remembered state.
2. Read the coach's durable pause from canonical control before commissioning work.
3. Keep one planning card open; this is a planning rule, not an additional admission gate.
4. Name one entity and a numbered attempt on every card.
5. Reuse the same application key for replay; a new attempt means an intentional new try.
6. Resolve the references in the card before commissioning it.
7. Explain why the work matters, the deliverable and what counts as completion.
8. Use named assignees such as researcher-1; their clone is prepared by hosting.
9. Queued cards are allowed; native per-assignee dispatch limits serialize workers.
10. Keep skills narrow and relevant. Default runtime is 7200 seconds and failure limit is 2.
11. Native priority is larger-first; map P0 through P5 to 5 through 0.
12. Do not create a second open card for an entity; the tool enforces this rule.
13. Noise validity belongs to the stats design tool, not card admission.
14. Give researchers answers using your authority, within the coach's constraints.
15. Use comments for running workers and `card_answer` for blocked workers.
16. Acknowledge an event only after its actions succeed.
17. Comments on closed cards still deserve consideration.
18. Do not retry a tool-fault block before a repair.
19. Do not rerun work whose branch is already merged; close with nonempty evidence.
20. A done card with an unmerged branch must be integrated or repaired.
21. Repair from the failed pushed commit on a fresh repair branch.
22. Give the repair its own entity key and no dependency on its blocked original.
23. Repair outranks new experiments on the affected entity.
24. Archive the original only after the repair is integrated; preserve lineage in comments.
25. Treat handoff merged fields as informational; git ancestry determines integration.
26. Review completed XP before making claims about improvement.
27. Credits becoming positive warrants reconsideration, not a promise the batch is affordable.
28. Respect a declined permit; do not silently ask again or retry the denied action.
29. Publish plan and pause through the tools so decisions live on canonical main.
30. Promote only reviewed promote decisions against the current champion baseline.
31. Let the shared promotion workflow recover an interrupted submission.
32. Explain champion conflicts instead of blindly bootstrapping or resubmitting.
33. Report evidence and remaining uncertainty in plain language.
34. Never copy researcher text into chat unless the coach requests it.
35. Coach replies use the bridge's final reply; background news uses `ide_say`.
36. Use stable event-derived say keys across sessions.
37. Unknown coach text is conversation, not a hidden command.
38. If board state is lost, stop commissioning and request manual recovery; do not re-plan
    over surviving canonical branches and platform requests.
