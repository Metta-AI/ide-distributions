---
name: meta-recon
description: Use when a scouting card updates the league meta hub.
---

# Meta recon

This skill implements the last steps of System Design §5.3 (scouting): update the league
meta hub and its claims, then write the journal. It adapts optimizer-seed `meta-recon`.

Doctrine upheld: D-04, D-07, D-14, D-16, D-22, D-23.

## Reads and writes

- Reads: the league standings, league snapshots, opponent claims and models, replays.
- Writes: the league meta hub and its claims, and the journal.

## Procedure

1. Read the latest league snapshots (`league-watch`) and the opponent profiles of this card
   (`opponent-profile`):
   `"$RESEARCH_CLI" records find evidence --where kind=leaderboard-snapshot`
   `"$RESEARCH_CLI" records find claim --where kind=opponent`
   If no snapshot of this card exists, read the standings directly:
   `"$RESEARCH_CLI" softmax standings`
2. Name what changed in the field since the last snapshot: a new champion, a new entrant
   near the top, an opponent version change. A swing of an unchanged opponent's mean is
   noise, not a meta shift.
3. Write league claims (kind `strategy` or `behaviour`) for what wins now, with evidence and
   `revise_if` (D-07):
   `"$RESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>`
   A new claim is always allowed. Change an existing league claim (new evidence, a status
   change) only when the card's refs name it (D-23); otherwise suggest maintenance with
   the new evidence and `ar://claim/<C-id>` in your comment or `metadata.next`.
4. Update the league meta hub only when the card's refs name it (D-23); otherwise suggest maintenance
   to the orchestrator, citing `ar://hub/<HUB-id>` as in step 3. The hub holds the
   standings snapshot ref, decoded strategies with transplant tags, the shifts since last
   time, and where the field looks weak. Every sentence cites a claim (D-16); prose is
   present tense (D-14). Create the hub if none exists (the game version comes from
   `"$RESEARCH_CLI" softmax game-version`):
   `"$RESEARCH_CLI" records new hub --meta '{"title": "League meta", "game_version": "<game version>", "load_when": "planning or scouting"}' --body-file .scratch/hub.md`
5. For each usable weakness, propose an investigation (D-22) to the orchestrator with
   the question, evidence and `ar://claim/<C-id>` in `metadata.next` or a `suggestion:`.
6. Write the journal Decisions section (what you profiled, what you skipped as fresh, what
   changed). Finish with `card`.

## Failure handling

- When our score is flat but our rank drops, the field improved. Request scouting before new
  hypotheses.
- Old conclusions rot. Before you lean on an old league claim, check its
  `last_checked_version` against the current game version
  (`"$RESEARCH_CLI" softmax game-version`).
