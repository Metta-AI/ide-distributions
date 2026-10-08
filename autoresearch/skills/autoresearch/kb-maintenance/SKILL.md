---
name: kb-maintenance
description: Use when a maintenance card keeps the knowledge base sound.
---

# Knowledge-base maintenance

This skill implements System Design §5.6 (maintenance), except lessons (the `lessons`
skill) and the noise record (the `calibrate-noise` skill).

Doctrine upheld: D-07, D-14, D-15, D-16, D-22, D-23, D-24.

## Reads and writes

- Reads: claims, hubs, the linter report, `plan.md`, the journal.
- Writes: claims (grades, stale marks, renewals, archive moves), hubs, `kb/log/`,
  `kb/archive/`, card requests, and the journal.
- Never writes: `kb/index.md`, backlinks, and other files under `kb/_generated/` (D-24). The
  runtime regenerates them after each merge. Do not run `kb index` in a card.

## Procedure

1. Run the linter. It writes nothing:
   `"$AUTORESEARCH_CLI" kb lint`
   Read each finding: stale claims, code citations to renew, contradictions, orphans, hub
   sentences without claims, and grade changes.
2. Apply the fixes the linter may make (grades and demotions):
   `"$AUTORESEARCH_CLI" kb apply`
3. When the game version changed, mark the claims checked against older versions stale. The
   current game version is the `game_version` of `stats/metric-noise.yaml`:
   `"$AUTORESEARCH_CLI" kb stale --version <game version>`
4. When `GAME.md` names the game source repository and you know the old and new game
   commits, renew code citations whose cited lines did not change. For changed lines it
   requests re-verification cards:
   `"$AUTORESEARCH_CLI" kb renew --game-repo <path> --old <sha> --new <sha> --version <game version>`
5. Work through the re-verification items and unresolved contradictions that this card
   names. Close a contradicted claim with `superseded_by` or `valid_to_version`; never
   delete it (D-15):
   `"$AUTORESEARCH_CLI" records status <C-id> superseded --reason "<why>" --ref <ar://ref>`
   `"$AUTORESEARCH_CLI" records update <C-id> --patch '{"superseded_by": "<C-id>"}'`
   Every hub sentence must cite claims (D-16). Prose describes the present (D-14).
6. Compact when stale claims are too many (at 10% of claims it archives the longest-stale
   down to 2.5%; it never archives a claim with a pending re-verification request):
   `"$AUTORESEARCH_CLI" kb compact`
7. Check hub health and request split or merge cards for flagged hubs:
   `"$AUTORESEARCH_CLI" kb hubs --request`
8. Record stale open threads in the journal Decisions section, and request a planning card
   to archive them:
   `"$AUTORESEARCH_CLI" queue request create --title "Archive stale open threads" --mode planning --why "<threads>" --ref ar://plan`
9. Validate (`"$AUTORESEARCH_CLI" records validate --all`) and finish with `complete-card`.

## Failure handling

- `kb renew` exit 1 (for example, the game repo cannot be read): skip renewal, note it in
  the journal, and go on.
- A change to an entity that the card does not name fails card finish (D-23). Request a
  maintenance card that names it instead.
