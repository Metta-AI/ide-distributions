---
name: card
description: Use when executing one native research assignment.
---

# One native assignment

Apply D-21, D-23, D-25 and D-26.

1. Read your task with `kanban_show`: objective, current instructions, native task/run,
   worktree, branch, inputs, constraints and acceptance evidence.
2. Work in that worktree. Read `AGENTS.md` and relevant records and game instructions.
   Never treat an integrated checkpoint as completion. Resume from actual artifacts and
   receipts; reconcile a remote operation before retrying an ambiguous response.
3. Use `"$RESEARCH_CLI"` and installed game bindings. Record evidence appropriate to the
   assignment. Journals are optional unless the assignment specifically requests one.
4. Apply current comments. Ask necessary questions with native comments and `kanban_block`.
   Report exact request IDs for pending experiments, credit shortages and permit waits.
   Blocking does not require integration or a completed journal.
5. Preserve outputs with `"$RESEARCH_CLI" workspace stage`. Keep its branch and commit receipt.
   This does not integrate main. Subsequent resumed work can produce another commit.
6. Complete the assignment with native `kanban_complete`, a factual summary, and relevant
   artifact references in native metadata. Do not invent completion evidence or metadata.
   Native completion is independent of the coordinator's acceptance of the deliverable.
7. Stop after completion. The coordinator inspects artifacts, integrates accepted work,
   arranges resolution of conflicts, and decides further work.

Use the terminal's native process wait for long local builds. Never repeatedly launch a
command because it has not finished. Preserve failed build reports, correct invalid inputs,
and do not bypass provenance checks. Respect budgets and denied permits.

For explicit build/upload work, use `candidate`'s explicit procedure. Statistical experiments
are optional objectives. Upload and evaluation receipts survive task retries; task IDs do not
replace operation identity. Never create/link/schedule other tasks or submit to a league.

A native completion can report an artifact without a canonical journal:

```json
{"summary":"Requested build completed; coordinator must verify and accept it.","metadata":{"artifacts":["builds/example/report.json"]}}
```
