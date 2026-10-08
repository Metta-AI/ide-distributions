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
- Writes: the claims and hubs this card's refs name, card requests, and the journal.
- Never runs: `kb apply`, `kb stale`, `kb renew`, `kb compact`, `kb index`. They change many
  claims at once, so card finish would reject them (D-23). queue-keeper runs them on
  canonical every run: code-citation renewal when the game source commit changes (games
  whose contract has `source`), stale marking on a game-version change, lint fixes,
  compaction and the generated views.
- Never writes: `kb/index.md`, backlinks, and other files under `kb/_generated/` (D-24).

## Procedure

1. Run the linter. It writes nothing:
   `"$AUTORESEARCH_CLI" kb lint`
   Read each finding: stale claims, code citations to renew, contradictions, orphans, hub
   sentences without claims, and grade changes. Grades, demotions, stale marks and
   compaction are the runtime's; do not repeat them by hand.
2. Work through the re-verification items and unresolved contradictions on the claims this
   card's refs name. Check each claim against current evidence and the current game version
   (`"$AUTORESEARCH_CLI" softmax game-version`). Renew a claim that still holds by adding
   the evidence and setting `last_checked_version`:
   `"$AUTORESEARCH_CLI" records link <C-id> evidence '{"ref": "<ar://evidence/EV-id>", "tier": "<S-E>", "direct": true, "stance": "supports"}'`
   `"$AUTORESEARCH_CLI" records update <C-id> --patch '{"last_checked_version": "<game version>"}'`
   Close a contradicted claim with `superseded_by` or `valid_to_version`; never delete it
   (D-15):
   `"$AUTORESEARCH_CLI" records status <C-id> superseded --reason "<why>" --ref <ar://ref>`
   `"$AUTORESEARCH_CLI" records update <C-id> --patch '{"superseded_by": "<C-id>"}'`
3. Fix the hub sentences the card's refs name. Every hub sentence must cite claims (D-16).
   Prose describes the present (D-14).
4. For every finding on a claim or hub this card does not name, request a maintenance card
   whose refs name it (D-22, D-23), grouping related claims in one card:
   `"$AUTORESEARCH_CLI" queue request create --title "Re-verify <subject>" --mode maintenance --why "<lint finding>" --ref ar://claim/<C-id>`
5. Check hub health and request split or merge cards for flagged hubs:
   `"$AUTORESEARCH_CLI" kb hubs --request`
6. Record stale open threads in the journal Decisions section, and request a planning card
   to archive them:
   `"$AUTORESEARCH_CLI" queue request create --title "Archive stale open threads" --mode planning --why "<threads>" --ref ar://plan`
7. Validate (`"$AUTORESEARCH_CLI" records validate --all`) and finish with `complete-card`.

## Failure handling

- A change to an entity that the card does not name fails card finish (D-23). Undo it and
  request a maintenance card that names it instead.
- The linter reports a stale claim that a game update should have renewed from unchanged
  source: the game contract may have no `source`, or the platform declares no source commit.
  Re-verify the claims this card names by hand and say so in the journal.
