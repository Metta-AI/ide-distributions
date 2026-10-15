# Orchestrator build notes

Implementation in progress on branch `orchestrator`; scope is `orchestrator/` only.

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
