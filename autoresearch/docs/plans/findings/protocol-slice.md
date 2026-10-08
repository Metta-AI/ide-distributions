# Finding: protocol slice on real Hermes Kanban (Plan 01 Task 5.1)

**Verdict: GO for Kanban.** Every Step 4 assertion and both Step 5 faults pass on Hermes
v0.21.5 with real card workers (runs 4 and 5), after seven product fixes found by this slice
and the coordinator's end-to-end run (listed below).
2026-10-08, run by Claude for James.

Test: `tests/integration/test_protocol_slice.py` (marker `hermes`), card bodies in
`tests/integration/slice_cards/`. Run it with:

```bash
cd autoresearch
export OPENROUTER_API_KEY=...   # the test fails, not skips, without it
uv run pytest -m hermes tests/integration/test_protocol_slice.py -v -s
```

## Setup

- Hermes v0.21.5 (`v2026.9.24`, `f97608f`), editable uv tool. Fresh install of `git archive
  HEAD` into a throwaway `HERMES_HOME` (`conftest.install`), bare remote, fakegame,
  FakeSoftmax.
- Model `z-ai/glm-5.3-flash` through OpenRouter, set with `OPENROUTER_MODEL` in the profile
  `.env` (bootstrap writes it to `model.default`; fix 1).
- Test shims (environment only, kept inside the test): the host root `config.yaml`
  (`kanban.max_in_progress: 2`, `dispatch_interval_seconds: 10`, no `default_assignee`; the
  installer's job under A3), a fake credential that names its player, a parked unassigned
  planning card so the planning cadence adds no unscripted card, and the `.scratch/go`
  files that release the waiting cards F and D.

## Dispatcher choice

**The real gateway**: `hermes gateway run` in the test `HERMES_HOME`, with the three shipped
cron jobs resumed (`hermes -p <profile> cron resume`). Its embedded dispatcher spawned A and
B on its first tick and C when a slot freed; its cron ran queue-keeper every minute. No
`kanban dispatch` loop was needed. Step 5 pauses the cron jobs and runs queue-keeper from
the test through the same launcher, interpreter and cwd as Hermes cron, with the env the
gateway gives a cron script (the profile `.env` names on `terminal.env_passthrough`, A4).
That makes the fault runs deterministic (a gateway cron run would race the faulted run).
The gateway stays up for dispatch throughout.

## What the slice proves

| Brief item | Result |
|---|---|
| A and B run concurrently from the same base | Both spawned on one dispatcher tick (cap 2); C waited for a slot. |
| One merges, the other reconciles on the same entity | Run 4: A `published`; B `reconcile` with `overlap: [H-x]` and a P0 reconcile request, whose card then ran and published (B became `superseded`). Run 3 had B merge and A reconcile. |
| Outbox seqs contiguous | Yes, across runtime and worker messages and the pause `help_request`. |
| Requested cards admitted with receipts | The merged card's follow-up request was admitted (receipt with task); its card ran and published. |
| Remote has `main` and `card/*` | Yes, for A, B and C; remote `main` equals canonical HEAD at the end. |
| Plugin events | `tool_events.jsonl` has terminal events for every card. |
| C reaches promotion `published`, one submission | Promotion of C's experiment `published`, holder C; FakeSoftmax submissions = seeded baseline + one for C; champion `strategy/STRATEGY.md` = C's candidate. |
| Wrong player id in `.env` | Next queue-keeper run paused: pending card P unassigned (recorded in `pause.json`), runtime `help_request`. Card D, running since before the change, ran `softmax submit` and got `{"ok": false, "error": {"reason": "identity", "message": "credential player 'ply_fake' does not match expected 'ply_wrong'"}}` (read from the stored tool result); no submission, no promotion acquired; D finished `blocked`. |
| `crash_after=merge` | The faulted run exited 70 with one handoff `merged` and its staged message unpublished; the next run published it with exactly one outbox entry, contiguous seqs; resume reassigned P. |

## Wall time and cost

| Run | Result | Wall | Sessions | Tokens (input / output / cache read) | Hermes estimate |
|---|---|---|---|---|---|
| 1 | Stopped: workers lacked card context (fix 5) | 14 min (waited) | 5 | 89k / 13k / 2.1M | $0.08 |
| 2 | Steps 1-4 passed; test bug (bytes vs str) | 5.4 min | 5 | 176k / 5k / 0.7M | $0.05 |
| 3 | Steps 1-5a product behaviour correct; test grepped the wrong log | 10.8 min | 7 | 295k / 10k / 1.5M | $0.10 |
| 4 | **PASSED** | 5.3 min (Step 3 handoffs final in 70 s) | 9 | 482k / 13k / 1.5M | $0.12 |
| 5 | **PASSED** on the final code (all seven fixes) | 8.6 min (Step 3 handoffs final in 311 s) | 9 | 520k / 12k / 1.7M | $0.13 |

OpenRouter key usage for the whole task (kimi-k3 check, two env probes, five slice runs):
**$0.92** by `GET /api/v1/key` (usage 0.0021 → 0.9189), against a $25 budget. The key's `usage`
figure lags by minutes, so per-run deltas in the test report can read 0.

## Bugs found and fixed (each its own commit with a test)

1. **`OPENROUTER_MODEL` did nothing** (`ca6a5f1`). Hermes workers read the model only from
   `config.yaml`; the README and `distribution.yaml` promised a `.env` override. Bootstrap now
   writes it to `model.default`, and it is on `terminal.env_passthrough`.
2. **`crash_after=merge` was not wired** (`2c42b06`). FakeSoftmax documented `crash_after` as
   read by runtime code; nothing read it. queue-keeper's publish hook now exits (code 70)
   between a merge commit and publication.
3. **FakeSoftmax `whoami` echoed the expected player** (`6c316b0`), so a wrong
   `AUTORESEARCH_PLAYER_ID` could never fail C6 in fake mode. A fake credential may name its
   player (`player_id`), and `whoami` reports it.
4. **A running worker kept its old identity** (`4b0525d`). Hermes fixes a worker's env at
   spawn (verified: a terminal command after a `.env` edit still saw the old value), so after
   a `.env` change preflight paused the agent while a running worker still acted as before.
   The softmax tool now reads `SOFTMAX_PLAYER_CREDENTIAL` and `AUTORESEARCH_PLAYER_ID` from
   `$HERMES_HOME/.env` at each call.
5. **Worker commands had no card context** (`63d748c`), the most serious one. Hermes v0.21.5
   removes `HERMES_KANBAN_TASK` and `HERMES_KANBAN_RUN_ID` from every subprocess of a worker
   (`agent/delegation_context.py` `scrub_kanban_env`), under both dispatchers. So
   `card finish`, `queue request create` and `softmax submit` failed in a real worker (run 1:
   workers exported ids by hand; C's submit was refused "promotion steps need a card worker
   context"). `HERMES_KANBAN_WORKSPACE` survives. The plugin now records
   `{task_id, run_id, worker}` in `runtime/workers/<worktree>.json` on the first turn,
   `runctx` reads it back, and the injected briefing ends with `- Run: <id>` (the journal
   `iteration`; `complete-card` updated). hermes-facts item 6 is corrected.
6. **`kanban_complete` without `card finish` dropped work** (`525fae7`; found by the
   coordinator's end-to-end run). The plugin now blocks `kanban_complete` on a card until
   `card finish` marked it finished.
7. **A planning card's orphan repair was rejected as `planning_owned`** (`175a4cd`; same
   source). Runtime follow-ups (created by the runtime, with an `ar://handoff/` ref) now
   continue the card's planning ownership instead of competing for it.

Test-only issues, fixed in the test: the shell lacks `$HERMES_KANBAN_RUN_ID` (card bodies now
take RUN and TASK from the briefing), `gitops.show` returns bytes, and `hermes kanban log`
does not include tool output (the identity refusal is read from `state.db`).

## Observations (no change made)

- A promotion card that finishes with outcome `failed` (run 1, C) is merged and published like
  any valid handoff; only invalid content goes to repair (C3 as written).
- `max_in_progress` must be set explicitly in the host root config: unset on macOS means no
  cap (`hermes_cli/config_defaults.py`, "MemTotal unreadable → no cap").
- Terminal children of a worker also carry a Kanban write fence for the worker's board root.
  Our worker CLI only reads the board (`queue.get`), so it is unaffected; a future worker
  command that writes the board from a terminal would be refused.
- glm-5.3-flash followed the scripted bodies exactly once card context worked (runs 2-4: zero
  failed commands in every worker).
- After a passing run an empty `it/hh/profiles/ar-it` skeleton reappeared once in the
  deleted tmp dir: a worker process (likely card P, re-dispatched by resume just before the
  gateway stopped) wrote after cleanup. Harmless; the test stops running workers by
  `worker_pid`, which can miss one spawned in the last tick.
- Another agent ran its own Hermes on this host during the runs; the shared key's usage may
  include some of its calls.
