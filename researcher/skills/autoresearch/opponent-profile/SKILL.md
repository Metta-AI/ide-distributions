---
name: opponent-profile
description: Use when a scouting card profiles one opponent in depth.
---

# Opponent profile

This skill implements the opponent steps of System Design §5.3 (scouting): choose
opponents, skip fresh profiles, and profile each chosen opponent in depth.

Doctrine upheld: D-04, D-05, D-07, D-14, D-15, D-16, D-23.

## Reads and writes

- Reads: league snapshots, profiled opponents (`kb/opponents/<opponent-id>/`), opponent
  replays, opponent models, opponent hubs.
- Writes: opponent claims with evidence, the opponent hub, the opponent model
  `kb/opponents/<opponent-id>/STRATEGY.md`, evidence records, and the journal.

## Procedure

1. Choose opponents from the latest league snapshot (`league-watch`) and from the opponents
   already profiled (one directory per opponent under `kb/opponents/`). Read an existing
   profile with `"$RESEARCH_CLI" records resolve ar://opponent/<opponent-id>`.
2. Skip an opponent whose policy version has not changed and whose profile is fresh.
   Re-profile when the profile is older than 7 days (the default freshness limit) or when
   the opponent's policy version changed.
3. Get completed episodes where the opponent played. Our XP evidence names the opponents of
   each request (`rows.jsonl` field `opponents`). Download replays of those requests into
   scratch:
   `"$RESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>`
4. Inspect the replays with the game's `replay_inspect` binding (see `card` for `GAME.md`).
   Apply the replay-analysis gate of `replay-diagnosis` step 3 (D-05). Record each inspected
   replay as evidence (`replay-diagnosis` step 4).
5. Decode what the opponent does into mechanism statements: its rule, its apparent
   parameters, and the measured effect. "They are good at X" is not a finding. One replay is
   an anecdote (tier D); a pattern across several hash-verified replays is tier C.
6. Write each strength and weakness as an opponent claim (D-07):
   `"$RESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>`
   with `"kind": "opponent"`, `"opponent": "<opponent-id>"`, `status`, `confidence`,
   `evidence`, `revise_if` (for example "the opponent's policy version changes"), and
   `topics`. Close a claim that no longer holds with `superseded_by` or `valid_to_version`
   (D-15).
7. Tag each opponent mechanism for transplant: copyable (works for us as is),
   prerequisite-first (name the missing capability: that becomes the lever), or not-copyable
   (its benefit depends on the opponent's other strengths). Note counters: a predictable
   strong move is a target.
8. Write or update the opponent model `kb/opponents/<opponent-id>/STRATEGY.md` (what it
   does now, in present tense, D-14) and its hub (a hub record with
   `"opponent": "<opponent-id>"`). Every hub sentence cites a claim (D-16).
9. Finish with `meta-recon`, or with `card`.

## Failure handling

- No completed episode with this opponent: do not profile from guesses. Request an
  experimentation card that evaluates against this opponent with a fixed seat, or record the
  gap in the journal.
- No command fetches league replays of other players' games. Use XP evidence only.
- Changing an existing opponent hub or claim needs the card to name it (D-23).
