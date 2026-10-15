# Orchestrator build notes

Build 1 complete on branch `orchestrator`; committed scope is `orchestrator/` only.
Worktree: `/Users/jamesboggs/coding/ide-distributions-orchestrator`.

- Read the build brief, shared CLI contract, and v4.4 design. The shared research package remains Claude-owned; wrappers will be tested against its documented envelopes until the vendor copy arrives.
- Created the isolated worktree. Inspecting pinned Hermes APIs before implementing native board tools and the supervisor gate.

- Verified native create/notify/completion APIs in Hermes v0.21.6. Using its profile resolver and secret scope, Kanban connection and notify destination resolver directly.
- `card_create` adds explicit `entity`, `attempt`, and repair `card_sha` parameters (needed by the design's key/repair contract). Replays include archived cards; repair cards have no parents.
- The create transaction covers the local bare-origin fetch and branch creation as well as native creation; there is no reservation state. A branch left by an interrupted create is reused.
- Permit reads are absent from the shared CLI contract: the gate reads the existing cogents `c/asks/{id}` endpoint, exactly as the existing Softmax client does. No XP/promotion logic is duplicated.
- Using the audit-documentation skill for the required pre-commit audit, scoped to this distribution.
- Waiting transitions use the handoff run's native `task_runs` row ID in the same `handled` table. `event_ack` accepts that table as well as events/comments, so an answered permit or terminal XP does not wake forever after a review was commissioned. No additional state table.
- XP handoffs require `experiment.id` plus request IDs in `awaiting.xp`, since the shared `xp status` command takes an experiment. Credits wake when the readable balance is positive; cost/admission remains the worker/shared CLI's job.
- The ten-minute champion check uses wall-clock minute modulo ten instead of persisting a tick counter; `promotion status` owns comparison and interrupted-stage recovery. It wakes on conflict or in-flight promotion.

Progress: distribution/config/SOUL/doctrine, native board wrappers, HTTP tools, shared subprocess
adapters and supervisor are implemented. Default behaviour tests now cover CLI envelopes,
exact publish paths, bridge payloads, quiet/event/platform gates and transition acknowledgements.
Next: real Hermes concurrency/notify/close tests and clean-export installation.

Validation: 13 model-free tests pass. The first real-Hermes test found the machine's `hermes`
launcher uses a pre-3.14 environment without ruamel; pinned Hermes now declares 3.14-only
runtime dependencies. Preparing a separate Hermes environment under this distribution's
ignored `local/` directory using its real frozen lock, without altering the installed CLI.
- Review caught an ancestry edge case: a new queued branch starts at main and is already an
  ancestor. Reconciliation now flags a non-done merged branch only if it moved from the
  card's recorded base; untouched queued cards are not presented as completed work.
- Champion polling compares canonical champion summaries before/after shared promotion
  recovery, so recovery that finishes and clears `in_flight` still wakes for reporting.

Validation progress: native Hermes tests passed concurrency, duplicate refusal, fast-forward,
branch/skill/retry/runtime propagation, subscriptions, answer/unblock, close/archive, late
comments, and repair branching. Added explicit persisted-session lineage coverage and a
regression for untouched branches appearing merged. Default suite is now 15 passing tests.
The isolated Hermes environment is Python 3.14.4 with the pinned checkout's frozen lock;
the orchestrator itself remains Python 3.12 as requested.

Pre-commit documentation audit (audit-documentation skill): checked README tool inventory and
arguments against all 21 schemas, paths/config/env against manifest and context code, paused
cron record against the native format, and SOUL/doctrine against v4.4 cuts. Documented the
shared-vendor boundary, repair/no-parent behavior, handoff ack extension and separate Python
versions. No root documentation edited. Checkpoint ready; clean-export test follows commit.

Checkpoint `7554d81` committed. Clean-export scratch profile installation passed, including
real Hermes plugin discovery registering all 21 tools and the supervisor remaining paused.
Both Hermes tests pass with the isolated pinned interpreter. Tightening the gate subprocess
check to exercise the actual launcher with only the declared passthrough environment.


## Final result

Built all 21 tools, the installed-paused agent supervisor and stdlib launcher, profile config,
SOUL/doctrine, README, packaging/lockfile, and model-free/native tests. No custom action ledger,
reservation state, noise admission gate, receipt files, or liveness timers were added.

Validation passed:

- `uv run pytest -q`: **15 passed**, 2 Hermes tests deselected.
- `uv run pytest -q -m hermes`: **2 passed**, 15 default tests deselected, with the provisioned
  Hermes interpreter/PATH below.
- `uv run ruff check .` and `git diff --check`: passed.
- Clean `git archive` installation using `hermes profile install ... --name orchestrator --yes`:
  passed in a scratch host root. Real plugin discovery registered all 21 tools; cron stayed
  paused. The installed distribution synced its own Python 3.12 venv with `uv sync --frozen
  --no-dev`; a second launch reused it, and the installed gate imported successfully.
- The actual `scripts/supervisor_gate.py` launcher ran with only PATH, HERMES_HOME and declared
  research passthrough variables (no PYTHONPATH), using a contract fixture in its venv.
- Real board tests verify concurrent creation, duplicate entity refusal, ff-only preparation,
  native branch/skills/retry/runtime/session fields, notify+wake destination, durable answer,
  close/archive, comments after completion, explicit ack, archived-key replay and ungated repair.

Hermes source: `818c13be1dc4fd28987e1e881a9408224afd4535`. The machine's existing `hermes` launcher
was not modified. To repeat the Hermes tests here:

```sh
HERMES_PYTHON=/Users/jamesboggs/coding/ide-distributions-orchestrator/orchestrator/local/hermes/bin/python \
PATH=/Users/jamesboggs/coding/ide-distributions-orchestrator/orchestrator/local/hermes/bin:$PATH \
uv run pytest -q -m hermes
```

That ignored local environment was provisioned with the pinned Hermes project's real frozen
lock and Python 3.14.4. The distribution remains Python 3.12.

### Integration handoff / unverified

Claude still owns generating the vendored `ide_research_core`, shared ontology and game assets.
No shared implementation has been recreated here. The pyproject includes both package paths
and the existing autoresearch runtime dependency stack. CLI-backed operations were verified
against the agreed subprocess envelopes; end-to-end shared CLI behavior awaits that assembly.
The CLI `data` for XP status is the existing list of request rows; handoffs identify their
experiment separately. The extra permit GET and task_runs acknowledgements are documented above.

No real platform requests, model runs, promotion submissions or hosted IDE wake/rendering checks
were performed. Native subscription persistence is verified; hosted delivery is a separate test.
Hosting prepares clones/board/dispatch settings and resumes the supervisor once; that remains
outside this distribution's build scope. All commits are local; nothing was pushed.

Final documentation audit: the stronger launcher/install tests match the documented install and
validation claims; README, SOUL, doctrine, manifest, schemas and notes remain consistent.

**Codebase friction: 2/5.** Native Kanban APIs and the existing launcher/plugin pattern fit this
work well. The installed Hermes interpreter lagged the pinned source's Python 3.14 dependencies,
so testing needed a separately provisioned environment; no production workaround was added.

CODEX BUILD 1 DONE

## Round 11 fixes in progress

Read REVIEW-11 and inspected main at d774156 without changing it. Keeping edits on the
`orchestrator` branch under `orchestrator/`. Confirmed the shared promotion bootstrap now
requires `--version`, acceptance is `promotion accept`, and the shared softmax CLI still
has no read for one permit ask: retaining the existing cogents GET.

Addressing wake filtering, promotion quieting against canonical pause, event-scoped branch
checks, malformed XP handoffs, and the two promotion adapters. Checking the create-lock/gate
interaction with a concurrent database regression. Using the audit-documentation skill for
the pre-commit audit; updates will cover the new tools and conflict/bootstrap behavior.

Round 11 implementation and focused validation:
- Snapshot uses the eight native wake kinds; bookkeeping and orchestrator-authored comments
  are ignored without acknowledgements. The real-Hermes test checks parity with `_WAKE_KINDS`
  and confirms a researcher's late comment survives while our own comment/answer does not.
- Promotion polling still advances recovery, but unchanged in-flight state is quiet. A conflict
  wakes only until canonical research control is paused; champion changes still wake.
- Reconciliation fetches only eligible branches whose cards have pending actionable rows.
  Tests cover untouched bases, moved branches, old done cards and stopping fetches after ack.
- An XP wait missing experiment.id becomes an acknowledgeable `bad_handoff` task_runs item.
- Bootstrap now requires a version record; accept_champion calls shared promotion accept.
  SOUL/doctrine/README require pause + user notification on conflict and confirmation before accept.
- Fixed the lock concern by skipping schema writes once `handled` exists. A real native
  card_create was held inside its fetch while a separate gate call completed successfully;
  concurrent creates still returned the same card. No lock, store, ledger or gate added.
- Shared single-ask read check: main's softmax parser at d774156 has none. Existing cogents
  `c/asks/{id}` GET retained.

Pre-commit documentation audit: checked all five requested fixes against code and tests,
updated tool signatures and bootstrap/conflict operation in README/SOUL/doctrine, and preserved
historical Build 1 notes above. Default suite: 28 passed (2 Hermes deselected). Native board
scenario: 1 passed, including the fetch-lock regression. Ruff found one import-order issue,
now fixed. Clean-install validation exports HEAD, so the full Hermes suite follows the code
checkpoint to test these changes rather than the earlier commit's 21-tool installation.
The new accept tool is also in plugin.yaml's discovery list. Added a manifest/schema parity
check; the default suite is now 29 passing tests. Ruff and formatting checks pass.

## Round 11 complete

Code checkpoint: `f92f549` (`fix(orchestrator): wake only for actionable research changes`).
All five requested fixes and the database-lock check are addressed. The permit read is fine
as written: main has no equivalent shared CLI read, so no replacement was made.

Final validation:
- `cd orchestrator && uv run pytest -q`: **29 passed**, 2 Hermes tests deselected.
- `HERMES_PYTHON=/Users/jamesboggs/coding/ide-distributions-orchestrator/orchestrator/local/hermes/bin/python PATH=/Users/jamesboggs/coding/ide-distributions-orchestrator/orchestrator/local/hermes/bin:$PATH uv run pytest -q -m hermes`:
  **2 passed**, 29 default tests deselected, against checkpoint f92f549.
- The clean-export installation registered all **22 tools** and kept the supervisor paused.
  The native board scenario verified the wake-set match, own-comment filtering, late worker
  comments, concurrent idempotent creation, and a gate read during the create/fetch lock.
- `uv run ruff check .`, `uv run ruff format --check src/orchestrator tests`, and
  `git diff --check`: passed.

Final documentation audit: README, SOUL, doctrine, plugin discovery, tool schemas and test
expectations agree on version-required bootstrap and user-confirmed champion acceptance.
No new state store or admission rules. Changes are only under orchestrator/; no merge or push,
no real platform calls or model runs. Shared wrappers remain tested against subprocess
contracts; the shared source on main was inspected read-only, not copied or changed here.

**Codebase friction: 2/5.** Snapshot filtering and thin CLI adapters provided clear edit points.
The canonical pause reader needed moving out of Service for gate reuse, and the clean-export
installation test required a commit before validating the new tool inventory.

CODEX FIXES 11 DONE
