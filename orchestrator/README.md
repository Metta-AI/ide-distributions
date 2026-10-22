# Orchestrator

A Hermes profile that leads research for one player/game and delegates implementation to
native Kanban researchers on the explicitly named `autoresearch` board. API chat and cron
turns expose only the `orchestrator` toolset. The supervisor is an agent cron job
(`cron/jobs.json`) that runs every minute; its gate script, `scripts/supervisor_gate.py`,
skips the model when nothing needs attention.

## Install and configure

Requires Hermes v0.21.6, Python 3.12 for this distribution, uv and Git.
The pinned Hermes runtime itself uses Python 3.14; use its provisioned interpreter for
the `hermes` tests, independently of this profile's Python 3.12 venv. Hosting prepares the bare canonical
repository, this profile's main clone, researcher profiles/clones, the board and native
dispatcher ([docs/hosting.md](../docs/hosting.md) lists the metta changes). Clone origins must
point to that local bare repository. Use a clean export,
not a checkout containing `.venv`, when installing:

```sh
git archive HEAD orchestrator | tar -x -C /path/to/empty-export
hermes profile install /path/to/empty-export/orchestrator --name orchestrator --yes
```

Set the installed profile's `.env` with `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`,
`SOFTMAX_PLAYER_CREDENTIAL`, `COGAMES_API_URL`, `RESEARCH_PLAYER_ID`, `RESEARCH_GAME`,
`RESEARCH_WORKSPACE` (this profile's clone) and `RESEARCH_CANONICAL` (the bare repo).
The manifest documents defaults. The credential accepts a bare token or player-session
JSON. Hermes's routed profile secret scope supplies HTTP/CLI credentials; a different
profile's ambient credential is not used. Gate subprocesses get the configured passthrough.

Then prepare the profile once, after at least one researcher profile has run its own
bootstrap (the first researcher creates the bare canonical repository and seeds it from its
`games/<game>/seed`; this profile ships no seed, so on an empty canonical repository it fails
with "no seed for game"):

```sh
HERMES_HOME=<profile> python3 <profile>/scripts/bootstrap.py
```

It syncs `$HERMES_HOME/local/venv` from `uv.lock`, clones the canonical repository into
`RESEARCH_WORKSPACE`, writes the `research` CLI shim to `$HERMES_HOME/bin/research` and adds
`RESEARCH_CLI` to `.env`. The board is created once per host with
`hermes kanban boards create autoresearch`.

`src/ide_research_core/`, the ontology at the end of `SOUL.md` and `games/<game>/GAME.md` are
copies made by `python3 tools/sync_shared.py` at the repository root. Edit the sources
(`shared/`, `ontology/`, `researcher/games/`) and rerun the script; do not edit the copies.

The installed supervisor starts paused. Hosting should resume it once after preparation,
preserving later deliberate pauses. With the host root selected:

```sh
hermes -p orchestrator cron resume supervisor
```

Configure the host dispatcher with `dispatch_profiles: [researcher-1, ...]`,
`max_in_progress` equal to researcher count, and `max_in_progress_per_profile: 1`.
Connect the IDE bridge to the orchestrator profile's API session. A coach chat reply is
published by the bridge; only background wake/cron turns call `ide_say`.

## Tools

| Tools | Behavior |
|---|---|
| `ide_say(text, key, goal_id?)` | POST a chat line with a stable event key, normally `say:<table>:<rowid>`. |
| `ide_task_update(task_id, patch)` | PATCH an IDE task with `set`, observed `base_rev`, and stable `idempotency_key`. |
| `worker_skills` | Read the assigned worker’s installed skill names and descriptions. |
| `card_create` | Prepare the assignee clone/branch and create native worktree work; return actual subscription status. |
| `card_show`, `card_list` | Read native cards, runs and comments. |
| `card_comment`, `card_answer` | Durable comment; answer additionally unblocks. |
| `card_close` | Native done with nonempty summary/metadata, or archive; never forces completion of a live claim. |
| `status` | Cards by state, canonical record summary, durable pause and promotion state. |
| `records_find`, `records_show` | Shared canonical record reads. |
| `plan_write`, `control_write` | Publish plan.md or pause/resume through shared workspace publish. |
| `promotion_submit(experiment)`, `bootstrap_champion(version)` | Shared promotion workflow; bootstrap requires the integrated starter version record (`V-...`). |
| `accept_champion()` | Record the league's current champion after the user confirms. |
| `workspace_integrate(branch)` | Shared integration of a pushed card/repair branch. |
| `xp_status(experiment)`, `credits`, `leaderboard` | Shared platform reads. |
| `latest_league_result` | Latest recorded match for this player in the current league, including round/episode IDs, policy versions, status, scores, and replay. Returns `available: false` before a replay exists. Read-only; verify the returned policy version before attributing a result to a new upload. |

Describe the objective, inputs, constraints and completion criteria in the card body.
Omit `skills` by default: the researcher discovers and loads its own procedures, or reports
why it cannot complete the task. Task skills are optional preloads, not capability grants.
When a task requires a specific procedure, call `worker_skills` before requesting that
preload. Unknown selections return `invalid_worker_skills` with the available catalog
before creating a branch or task. Cards without preloads do not query the worker catalog.

`card_create` requires `command_id`, `assignee`, `title`, and `body`. Replaying a
command returns the same native task, including after archive. Reusing its ID with
different arguments fails. Creation and its immutable arguments commit in one native
transaction. Serial reconciliation does not serialize the workers themselves.

All coordinator inputs enter one durable inbox. Its identity deduplicates delivery;
its oldest unfinished row owns execution. A failed turn or dead process leaves that
same row recoverable. Completing the turn acknowledges it automatically. There is no
separate wake receipt, command ledger, event acknowledgement, or replacement-task graph.

Native notifications alone deliver task events. Cron polls external XP, credit and
permit waits, and periodically requests promotion review through the same inbox.
`status` reads native tasks and current external waits. Resume blocked or triaged tasks
with `card_answer`; task completion never depends on Git ancestry.

Researchers build, smoke-test, and commit the complete artifact. The coordinator calls
`upload_build` on the latest completed native run, integrates its receipt commit, and
uses `submit_requested_version` for an explicitly requested submission. Cancellation
revokes publication before stopping the native worker. Requested submission is not
statistical improvement; autonomous promotion still requires reviewed evidence.

## Development and verification

```sh
cd orchestrator
uv run pytest -q
HERMES_PYTHON=<hermes>/bin/python PATH=<hermes>/bin:$PATH uv run pytest -q -m hermes
uv run ruff check .
```

Default tests use local Git repositories, a local HTTP server and a subprocess CLI fixture;
no model or platform credentials are required. Hermes tests use its installed interpreter,
a temporary host root and real native board APIs. Put the Hermes install's own `bin`
directory first on `PATH`: a `hermes` launcher that resolves `python3` through `PATH` would
otherwise run in this project's venv, which lacks Hermes's dependencies. The clean-install test exports committed HEAD, so commit
before running those tests. `tests/test_end_to_end.py` installs a researcher and this profile
on one host and plays one card from `card_create` through a scripted worker to `status`, with no model and the fake platform. Hosted bridge delivery is
a separate integration check that these tests do not establish.

For an explicit coach build/upload-only request, assign the researcher the `candidate` skill's
"Explicit build/upload-only request" procedure. `softmax upload-build` uploads a checked build
without an experiment, evaluation or league submission. Require the intended source change in
the generated artifact and both returned version IDs; promotion remains a separate authorized action.


## Shared IDE components and draft notes

The `ide_workspace` tool calls the hosted IDE's MCP server, authenticating directly with the existing scoped player credential.
The endpoint is `/ide/mcp` on the existing `COGAMES_API_URL` origin (normally `https://softmax.com`).
No new profile environment setting or retained-workspace configuration migration is needed.
Deploy and qualify the Metta workspace SDK, migration and public route before merging this distribution change:
Metta's `preserve` release workflow polls this repository's main branch, so merging can trigger an image release.
Running hosts adopt that image through the existing explicit Pause/Resume path, preserving their storage.

The tool is loaded with the orchestrator toolset. Its schema explains component discovery, layout validation,
revision checks, and draft notes. No legacy chat record or token exchange is required. Retain `request_id` on retries.
The server derives the owner from the validated player credential and fixes league scope to GoTA. Inspect structured MCP results rather than treating
an HTTP response as success. Draft notes do not edit, build, upload, or release policy source.
