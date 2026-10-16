---
name: kb-maintenance
description: Use when a maintenance card keeps the knowledge base sound.
---

# Maintain the knowledge base

Implements System Design §5.6, except `lessons` and `calibrate-noise`.
Doctrine upheld: D-07, D-14, D-15, D-16, D-22, D-23, D-24.

The maintenance worker runs KB operations in its card worktree. Read the card's scope,
claims, hubs, current game version, source pins and journal. Do not edit generated views by
hand or modify the profile's main clone.

## Procedure

1. Read findings with `"$RESEARCH_CLI" kb lint`: evidence-grade mismatches, unresolved links
   and contradictions, orphan claims, uncited hub sentences and archived claims still cited.
   Apply deterministic fixes (grades, demotions) within the assigned
   maintenance scope with `"$RESEARCH_CLI" kb apply`.
2. For a game-version update, read `"$RESEARCH_CLI" softmax game-version`, then mark old claims
   with `"$RESEARCH_CLI" kb stale --version <game-version>`.
3. If the game contract names a source repository and the prior/current commits are known,
   renew unchanged code citations:
   `"$RESEARCH_CLI" kb renew --game-repo <path> --old <commit> --new <commit> --version <game-version>`
   Its `reverify` IDs are findings to investigate, not automatically scheduled work. Check
   those within this card's scope; recommend follow-up work for the others to the orchestrator.
4. Re-verify claims against current evidence. Link the evidence and update the checked version:
   `"$RESEARCH_CLI" records link <C-id> evidence '{"ref":"ar://evidence/<EV-id>","tier":"C","direct":true,"stance":"supports"}'`
   `"$RESEARCH_CLI" records update <C-id> --patch '{"last_checked_version":"<game-version>"}'`
   Close contradicted claims with `superseded_by` or `valid_to_version`; never delete them:
   `"$RESEARCH_CLI" records status <C-id> superseded --reason "<why>" --ref <ar://ref>`
   `"$RESEARCH_CLI" records update <C-id> --patch '{"superseded_by":"<C-id>"}'`
5. Repair the scoped hub prose: present tense, with a claim citation for every sentence.
   Read health flags with `"$RESEARCH_CLI" kb hubs`. Suggest splits/merges and other out-of-scope
   work with reasons and refs in `metadata.next` or `suggestion:` comments.
6. Use `"$RESEARCH_CLI" kb compact` when the assigned maintenance needs stale-claim compaction.
   `"$RESEARCH_CLI" kb index` regenerates index/backlinks; integration also regenerates canonical
   views. Never rewrite them manually. Review the changes so broad maintenance stays within
   the card's assigned scope; ask the orchestrator if that scope needs expanding.
7. Record the fixes, contradictions, remaining re-verification and stale plan threads in the
   journal. Recommend plan changes to the orchestrator rather than editing `plan.md`.
   Validate with `"$RESEARCH_CLI" records validate --all`, then finish through `card`.

If source pins are missing or code changed, re-verify from evidence; do not mark a claim
current merely because it still parses. If maintenance cannot proceed without a scope or
source decision, comment and block for the orchestrator. Tool faults follow D-26.
