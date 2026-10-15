---
name: card
description: Use when starting, recovering or completing a research card.
---

# One card, one handoff

Implements the native worker lifecycle in design v4.4 §§4, 6.2 and 7, retaining the research
method of System Design §5.1 (start), §5.8 (completion) and §5.10 (planning recommendations).
Doctrine upheld: D-01, D-02, D-11, D-12, D-13, D-21, D-22, D-23, D-24, D-25, D-26, D-27.

## Start and recover

1. Read your own card with native `kanban_show`. Read its title, kind, `why` refs, deliverable,
   completion contract, `base_sha`, branch and per-card skills. Work in the assigned git
   worktree, not the profile's main clone. Repair cards use their assigned `repair/<id>` branch.
2. Before research, check the actual branch named on the card:
   `"$RESEARCH_CLI" workspace merged --branch card/<id>`
   Use `repair/<id>` instead for a repair card; do not manufacture a branch from the native
   task ID. If `data.merged` is true, the earlier attempt's pushed work is already integrated:
   call `kanban_complete` at once with the same summary and handoff metadata. Recover these
   from `kanban_show` and the integrated journal/records if the worker crashed before saving
   the handoff; use the returned `card_sha` and `main_sha`. Do not repeat uploads, XP or the
   journal. A branch not yet pushed returns `merged: false` and `card_sha: null`.
3. Read `AGENTS.md`, the Topics named by the card and `kb/index.md`. Resolve refs with
   `"$RESEARCH_CLI" records resolve <ar://ref>`. A planning card reads all of `plan.md`.
4. Apply the orchestrator's injected steering ahead of your own plan. Acknowledge it with
   a `note:` comment on your own card: applied, declined with a reason, or noted (`report`).
5. Do the card's bounded work. Investigation uses `replay-diagnosis` then `diagnose`; scouting
   uses `league-watch`, `opponent-profile`, `meta-recon`; design/build/launch uses `experiment`,
   `build-upload`, `xp-request`; review uses `fetch-artifacts`, `ab-compare`, `review-xp` and,
   for a promote decision, `candidate`; maintenance uses `kb-maintenance`, `lessons` or
   `calibrate-noise`. A repair card works from the failed pushed commit and resolves the
   integration problem described on the card.

## Working conventions

Run research commands as `"$RESEARCH_CLI" <group> <command>`. Read their JSON envelopes:
`{"ok":true,"data":...}` or `{"ok":false,"error":{"reason":...,"message":...}}`.
Use native Kanban tools for your own card. Never create, link or schedule cards, comment on
other cards, or invoke a different CLI/identity to bypass a refusal.

Record IDs come from `records new`, not invented names. Change the existing entities the
card names and create new records as needed. Put temporary downloads/builds in `.scratch/`
inside your worktree; integration excludes it. Persist evidence you cite outside scratch.

For game bindings, set `RESEARCH_HOME="$HERMES_HOME"` and
`RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"`. Read
`$RESEARCH_HOME/games/$RESEARCH_GAME/GAME.md`; `"$RESEARCH_CLI" game show` prints the contract
and path. Run installed game bindings from the profile directory with absolute worktree
input/output paths and the profile Python. Shared research commands run in the card worktree.
`"$RESEARCH_CLI" softmax game-version` supplies the current game version.
Use `GAME.md` for game mechanics and binding arguments; `card` and `candidate` define the
current lifecycle and upload handoff if a game document still describes an older workflow.

## Long commands

Use a foreground terminal call when the command fits its timeout. Otherwise use the terminal's
background mode, keep its `session_id`, and use native `process_manage` action `wait` on that
process. If the wait times out while the process still runs, wait again. Read the exit status,
output and retained reports. Do not spin on logs or heartbeat calls. This applies to local
builds/downloads, not XP: XP launches are handed off immediately (D-11).

## Planning and follow-up work

A planning card recommends priorities, merges/splits of proposed work, neglected Topics and
which experiments answer the strongest questions. Read the existing plan, active Topics,
experiments by status and the evidence. Avoid duplicate ideas and explain why to change course
when work is stuck. Create or update only the records within the card's scope.

Put recommendations, reasons and refs in the journal and `metadata.next`, or a `suggestion:`
comment. Do not edit `plan.md`, change other cards or rebuild a lost board. The orchestrator
writes the plan and decides which follow-up cards to create. Further work is a recommendation,
not extra work in this card (D-22).

## Journal and integration

Write exactly one `J-` journal entry with a `## Decisions` section. Reuse it on a retry.
Create it with no `outcome` until the findings are ready, so its body can still be updated:

```sh
"$RESEARCH_CLI" records new journal --meta-file .scratch/journal.json --body-file .scratch/journal.md
```

Example metadata (replace placeholders):

```json
{"iteration": 1, "mode": "experimentation", "card": "<own task id>", "doctrine_version": 12,
 "unit": "<bounded deliverable>", "refs": ["ar://experiment/<E-id>"]}
```

Use the actual run number from `kanban_show`, the doctrine version in `AGENTS.md`, and the
card's research mode. A review uses `experimentation`; a repair uses `maintenance`. The journal
records findings, uncertainties and next work. Each top-level item under `## Decisions` gives
a decision, its reason and refs (`ar://journal/<J-id>#d<N>`). Experiment decisions live on the
experiment; acknowledge steering in comments, with a journal ref when useful. Include the
intended summary and handoff fields in the journal so a retry can recover them; the merged
commit IDs come from integration or the merge check.

Choose the research outcome: `done`, `partial` (safe unfinished work), `dropped` (obsolete),
`failed` (the research unit failed), or `blocked` (cannot continue without help). Validate the
records and correct the listed problems before the final step:

```sh
"$RESEARCH_CLI" records validate --all
"$RESEARCH_CLI" records update <J-id> --patch '{"outcome":"done"}'
"$RESEARCH_CLI" workspace integrate
```

Use your chosen outcome in the patch. Once set, the journal is immutable. `workspace integrate`
is the LAST workspace operation: it commits, pushes the card branch, validates, merges into
canonical main and pushes. Keep its returned commit IDs. Do not write more files after success.

## Complete or block

After successful integration, use native `kanban_complete` with a nonempty findings-first
`summary` and this `metadata` contract:

```json
{
  "outcome": "done",
  "journal": "ar://J-...",
  "records_changed": ["E-...", "R-..."],
  "merged": {"card_sha": "<integration result>", "main_sha": "<integration result>"},
  "experiment": {"id": "E-...", "status": "evaluating", "decision": null},
  "awaiting": {"xp": ["<request id>"]},
  "next": "Review these requests when terminal; ar://experiment/E-..."
}
```

Use `experiment: null` without an experiment. For a reviewed candidate, `decision` is the
recorded decision outcome, such as `promote`. `awaiting` is exactly one of `{"xp":[ids]}`,
`{"credits":true}`, `{"permit":"<ask id>"}` or null. A bootstrap handoff additionally carries
`version_id` and `platform_policy_version_id` from the upload. Set `merged` to null when
reporting work that was not integrated; never claim success after integration failed.
Do not set `created_cards` or override the board. Stop after completing.

- XP waiting is **not a block**. Integrate the launch state, complete with `awaiting.xp` and
  `experiment.id`, and let the orchestrator commission review when results arrive.
- A credit or permit wait completes `partial` with the corresponding `awaiting` value. Put
  every returned ask/request ID and any combined wait in the experiment, comment and `next`;
  do not invent a list-valued permit field. A declined permit is not a reason to retry.
- A question necessary to continue: record the question in the journal, comment on your own
  card, then `kanban_block` with a clear reason and
  `kind: "needs_input"`. Ordinary questions can be comments while independent work continues.
- Integration fails: comment with the error and the branch/commit information available, then
  `kanban_block` with `reason: "integrate: <reason>"`. The branch was pushed before validation
  and merge failures; a failure of the branch push itself must be reported as unpushed. The
  orchestrator arranges repair. Do not repeat integration to conceal the failure.
- Tool fault: record command/error/refs in the journal, send `help_request:` via `report`,
  integrate if that tool is usable, and block with `reason: "tool-fault: <command>: <error>"`
  and `kind: "needs_input"`. Do not retry the faulty tool. If integration also fails, report
  both failures and use the integration block reason. Do not rewrite a frozen journal just
  to record the delivery failure; preserve that failure in the durable comment.
