---
name: meta-recon
description: Use when a scouting card updates the league meta hub.
---

# Meta recon

This skill implements the last steps of System Design §5.3 (scouting): update the league
meta hub and its claims, then write the journal. It adapts optimizer-seed `meta-recon`.

Doctrine upheld: D-04, D-07, D-14, D-16, D-22.

## Reads and writes

- Reads: the leaderboard, league snapshots, opponent claims and models, replays.
- Writes: the league meta hub and its claims, and the journal.

## Procedure

1. Read the latest league snapshots (`league-watch`) and the opponent profiles of this card
   (`opponent-profile`):
   `"$AUTORESEARCH_CLI" records find evidence --where kind=leaderboard-snapshot`
   `"$AUTORESEARCH_CLI" records find claim --where kind=opponent`
2. Name what changed in the field since the last snapshot: a new champion, a new entrant
   near the top, an opponent version change. A swing of an unchanged opponent's mean is
   noise, not a meta shift.
3. Write league claims (kind `strategy` or `behaviour`) for what wins now, with evidence and
   `revise_if` (D-07):
   `"$AUTORESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>`
4. Update the league meta hub: the standings snapshot ref, decoded strategies with transplant
   tags, the shifts since last time, and where the field looks weak. Every sentence cites a
   claim (D-16); prose is present tense (D-14). Create the hub if none exists:
   `"$AUTORESEARCH_CLI" records new hub --meta '{"title": "League meta", "game_version": "<game version>", "load_when": "planning or scouting"}' --body-file .scratch/hub.md`
5. For each weakness of the field that we could use, request an investigation card (D-22):
   `"$AUTORESEARCH_CLI" queue request create --title "Investigate <weakness>" --mode investigation --why "<claim and evidence>" --ref ar://claim/<C-id>`
6. Write the journal Decisions section (what you profiled, what you skipped as fresh, what
   changed). Finish with `complete-card`.

## Failure handling

- When our score is flat but our rank drops, the field improved. Request scouting before new
  hypotheses.
- Old conclusions rot. Before you lean on an old league claim, check its
  `last_checked_version` against the current game version.
