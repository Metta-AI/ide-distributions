---
name: lessons
description: Use when consolidating lessons from journals and decisions.
---

# Lessons

This skill implements the lessons step of System Design §5.6 (maintenance). It adapts
optimizer-seed `lessons-review`, without human graduation: a lesson graduates on recurrence.

Doctrine upheld: D-14, D-17, D-18.

## Reads and writes

- Reads: the journal (Decisions sections), experiment decisions, Results, existing lessons.
- Writes: lessons, doctrine proposals (`doctrine-proposed/`), and the journal.

## Procedure

1. Read the journals and experiment decisions since the last lessons pass:
   `"$RESEARCH_CLI" records find experiment --where status=reviewed`
   `"$RESEARCH_CLI" records show <E-id>`
   `"$RESEARCH_CLI" records find lesson --where status=tentative`
2. For each pattern that recurs (the same mistake, the same surprise, the same cost), find
   the lesson that covers it. If one exists, add the new instance:
   `"$RESEARCH_CLI" records update <L-id> --patch '{"recurrences": {"count": <n>, "refs": ["ar://…", "…"]}}'`
   If none exists, create a tentative lesson:
   `"$RESEARCH_CLI" records new lesson --meta-file .scratch/lesson.json --body-file .scratch/lesson.md`
   with `{"lesson": "<imperative sentence>", "status": "tentative", "scar": ["<incident and its ar:// ref>"], "recurrences": {"count": 1, "refs": ["ar://…"]}}`.
   A lesson is about how to research (method), not a fact about the game. A game fact is a
   claim.
3. Graduate a lesson on recurrence (D-17): at its second recurrence (a different card), or
   on explicit human steering, set it adopted:
   `"$RESEARCH_CLI" records status <L-id> adopted`
4. When an adopted lesson is about how the agent behaves (not about the game), write a
   doctrine proposal in `doctrine-proposed/<name>.md` and send its suggestion (see
   `report`, ref `ar://doctrine-proposal/<name>`). Do not edit `doctrine/` or
   `AGENTS.md`.
5. A refuted lever is a refuted mechanism claim, not a lesson. Its `revise_if` says what
   reopens it (D-17).
6. Finish with `card`. The journal lists each new, updated, and adopted lesson.

## Failure handling

- `records update` exit 1 on `recurrences`: give the whole `recurrences` object with the
  complete `refs` list, not only the new ref.
- Changing an existing lesson needs the card to name it (D-23). When it does not, suggest
  maintenance to the orchestrator with the lesson ref (`ar://lesson/<L-id>`).
