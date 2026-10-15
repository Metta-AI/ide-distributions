# Researcher prompt migration notes

In progress on `researcher-prompts`, based on `56eef8a`, in the isolated docs worktree.
Scope: SOUL, doctrine, skills, the two named unit tests, and this required notes file.

Using the audit-documentation skill to check prompt instructions against the Round 10 CLI
and lifecycle contract, preserve research methods, and audit documentation before committing.
Main has concurrent implementation changes; they remain untouched.

Read the full existing doctrine and all skill procedures. Replacing the legacy lifecycle,
keeping the replay/evidence, preregistration, one-change and paired-evaluation methods.
D-01..D-20 retain those rules in substance; only obsolete role/transport references change
(e.g. acknowledgements are own-card comments, the orchestrator owns submission and review).
The inherited GotA skills' technical build reports remain evidence, renamed from “receipts”
to avoid confusion with the removed integration receipt mechanism.

New `card`, `report` and `candidate` skills now describe native lifecycle and metadata. XP
launches complete with awaiting state; review workers fetch/inspect artifacts themselves;
maintenance workers run KB commands in their worktrees. Bootstrap uploads only.

Source readback clarifications: `workspace merged` checks the pushed remote branch (an
unpublished fresh branch returns false), and a branch-push failure can precede a durable
remote branch. Prompts distinguish that failure from validation/merge failures. Review/repair
journals use existing schema modes experimentation/maintenance; bootstrap adds the two upload
version IDs to handoff metadata. Multiple permit asks remain in the record/comment/next while
`awaiting.permit` retains the contract's single ask ID.

GotA bindings now use explicit bootstrap cards and upload-only handoffs; paired sample size,
recording-pinned replay tools, engine/input identity and G1–G5 method details remain. Long-process
handling is shared through `card`. Replacing implementation-importing prompt tests with file-only
contract checks so concurrent shared CLI edits do not affect this branch’s validation.

Validation is now file-only: native handoff examples, allowed researcher command/flag paths,
removed transport/role boundaries, doctrine IDs, skill metadata and GotA instrument flags read
via AST. An initial run used the old tests before the test rewrite (expected migration failures);
the rewritten run found only a test selector matching D-27 in the introduction, now corrected.
The project-local uv environment was provisioned normally; no lockfile/config changes.

## Documentation audit and handoff

Audited SOUL, all doctrine, 17 core skills and 5 GotA skills against BRIEF-10 and the
shared CLI contract. Read back workspace merge/integration, records schemas, unchanged
stats/KB/Softmax command flags and GotA instrument arguments. Doctrine is version 12.
D-01..D-20 keep their research rules; transport and submission ownership now match the brief.
The lifecycle, reporting and candidate skills cover recovery, one journal, own-card comments,
XP completion, integration failures, candidate evidence and upload-only bootstrap.

The journal preserves the intended summary/handoff fields before integration so a retry
can recover them; merge IDs come from integration or the merge check. Review comments precede
one final integration/completion. No second journal or post-integration workspace writes.

Integration follow-up for Claude (outside this branch's ownership):
- `researcher/games/gota/GAME.md` still refers to the removed queue/submit/complete-card
  skills and `softmax bootstrap-champion` (readback on main: lines 65 and 389–410).
  Its helper examples also use AR_HOME/AR_PYTHON. Update that document's workflow/env examples
  alongside the migration. The new `card` explicitly makes card/candidate authoritative for
  lifecycle, leaving GAME.md authoritative for mechanics and binding arguments.
- Main's config and README still had old skill/env/runtime references at audit time; those
  belong to your ongoing migration. In particular, register card/report/candidate and remove
  queue/complete-card/groom-queue/communicate/submit from the configured skill list.
- This branch implements the supplied post-migration contract. Its base still contains old
  CLI/runtime code; the passing file-only tests are prompt-contract checks, not a live-worker
  or platform integration claim. No implementation, config, README or game-binding files edited.

## Validation

- `cd researcher && uv run pytest -q tests/unit/test_skills.py tests/unit/test_doctrine.py`:
  189 passed.
- `uv run ruff check tests/unit/test_skills.py tests/unit/test_doctrine.py`: passed.
- `uv run ruff format` on those two files and `git diff --check`: clean.
- Removed-command, old env/transport and skill-reference scans: clean in the owned prompts.
- Tests read prompt/source files and parse frontmatter/JSON/AST only; no shared CLI imports,
  instrument subprocesses, platform calls or new dependencies.

**Codebase friction: 2/5.** Skills were easy to locate and research methods were separable
from lifecycle text. Repeated worker instructions and tests importing the changing CLI added
some migration work; the narrow file-only contract tests now avoid that coupling.

Completed on local branch `researcher-prompts`, based on `56eef8a`. No push.

CODEX PROMPTS DONE
