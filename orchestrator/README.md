# Orchestrator

A Hermes profile that leads research for one player, on any game the host can research, and
delegates implementation to native Kanban researchers on the explicitly named
`autoresearch` board. Each card names its game. It has everything a researcher has except the
researcher's restrictions: the same skills, game folders and template, doctrine, Hermes
toolsets, timeouts and terminal environment (`config.yaml`). API chat and cron turns expose the
`orchestrator` toolset plus Hermes's `terminal`, `file`, `code_execution`, `skills`, `web`,
`browser`, `vision`, `todo`, `session_search`, `delegation` and `memory` toolsets: full shell,
file and Python access on the profile host. Dangerous commands go through
Hermes approvals (asked in chat, denied in unattended cron turns). The supervisor is an agent cron job
(`cron/jobs.json`) that runs every minute; its gate script, `scripts/supervisor_gate.py`,
skips the model when nothing needs attention.

## GitHub repositories

Use `github_repositories` and `github_clone` for the owner's connected GitHub account.
Pass `commit` with the full SHA when the league manifest pins a release. Authorization
exists only during this tool call; later terminal fetches do not retain it. Request a new
pinned clone through the tool when another revision is needed.
Clones live under the hosted profile's `repositories/` directory, separate from canonical
research. Private repositories require repository consent in Softmax IDEa; public HTTPS
clones do not. Neither a local `gh` login nor a GitHub token on the host is required.

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
`SOFTMAX_PLAYER_CREDENTIAL`, `COGAMES_API_URL`, `RESEARCH_PLAYER_ID`,
`RESEARCH_WORKSPACE` (this profile's clone) and `RESEARCH_CANONICAL` (the bare repo).
The manifest documents defaults. The credential accepts a bare token or player-session
JSON. Hermes's routed profile secret scope supplies HTTP/CLI credentials; a different
profile's ambient credential is not used. Gate subprocesses get the configured passthrough.

Then prepare the profile once. Whichever profile bootstraps first creates the bare canonical
repository; this profile ships every shipped game's folder, seed included, so it can be first:

```sh
HERMES_HOME=<profile> python3 <profile>/scripts/bootstrap.py
```

It syncs `$HERMES_HOME/local/venv` from `uv.lock`, clones the canonical repository into
`RESEARCH_WORKSPACE`, writes the `research` CLI shim to `$HERMES_HOME/bin/research` and adds
`RESEARCH_CLI` to `.env`. The board is created once per host with
`hermes kanban boards create autoresearch`.

`src/ide_research_core/`, the ontology at the end of `SOUL.md`, the game folders under
`games/` and the game skills under `skills/<game>/` are copies made by `python3 tools/sync_shared.py` at the repository root. Edit the sources
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
| `attachment_download(sha256)` | Save an attachment from the current workspace to a private local cache, verifying its size and SHA-256 (maximum 25 MiB). Use the hash in conversation context, then read the returned path. The tool resolves the signed URL internally; never transcribe it into a command. It uses the current Player credential and cannot read another workspace's attachments. |
| `capabilities(query?, kind?)` | Every tool and skill this session has, with one-line descriptions, filtered by keyword. |
| `ide_workspace_schema`, `ide_workspace` | Read the deployed workspace contract, then use only supported operations and scope. Nested protocol errors and MCP refusals return `ok: false`. |
| `ide_say(text, key, goal_id?)` | POST a chat line with a stable event key, normally `say:<table>:<rowid>`. |
| `ide_task_update(task_id, patch)` | PATCH an IDE task with `set`, observed `base_rev`, and stable `idempotency_key`. |
| `github_repositories(page?)`, `github_clone(repository, commit?)` | List and clone repositories through the owner’s GitHub connection in IDEa settings. The broker keeps provider credentials server-side and returns a separate checkout path and commit. The optional full SHA selects a pinned release using the same authorization. |
| `worker_skills` | Read the assigned worker’s installed skill names and descriptions. |
| `card_create(..., game?)` | Prepare the assignee's clone of the card's game (its own idempotent `research workspace bootstrap --game`, which also refreshes doctrine) and branch, and create native worktree work; return actual subscription status. The game is recorded in the creation command. |
| `card_show`, `card_list` | Read native cards, runs and comments. List filters expose typed fields and sort values (`updated`, not `updated_at`); the default limit is 20, maximum 200. |
| `card_files`, `card_report`, `card_artifacts` | Discover completed committed files, read report pages, and inspect or export evidence into this coordinator's private cache. Accepts source, large logs, results and binary replays without integrating the worker branch. |
| `card_comment`, `card_answer` | Durable comment; answer additionally unblocks. |
| `card_close` | Native done with nonempty summary/metadata, or archive; never forces completion of a live claim. |
| `status(game?)` | Cards by state, pending waits and the host-wide budget, plus one game's canonical record summary, pause, plan and promotion state. |
| `records_find`, `records_show` (`game?`) | Shared canonical record reads. |
| `plan_write`, `control_write` (`game?`) | Publish plan.md or pause/resume through shared workspace publish. |
| `upload_build(id, run_id, build_dir, name, experiment?)` | Upload a completed run's committed build in the card's game. |
| `champion_submit(experiment)`, `bootstrap_champion(version)` | Shared promotion workflow in the card's game; bootstrap requires the integrated starter version record (`V-...`). |
| `accept_champion(game?)` | Record the league's current champion after the user confirms. |
| `workspace_integrate(id, run_id, commit)` | Integrate a completed run's staged commit into the card's game. |
| `game_list` | Shipped and installed games with each one's `league_id`, whether each has a research workspace yet, and how an installed one was installed. |
| `game_prepare(game)` | Create a game's research workspace (canonical repository seeded from the game folder, plus this profile's clone), or refresh it. Idempotent; no card or researcher needed. |
| `game_install(id, run_id, name, replace?)` | Install the `games/<name>/` folder a completed card committed, host-wide; returns its check report (problems refuse, gaps to report). |
| `discover_leagues(query?, offset?, limit?)` | Search live leagues and games by name or ID; returns Coworld IDs and documentation links. |
| `league_players(league_id, query?, offset?, limit?)` | Search ranked players in an explicit league. Owned-player context establishes ownership, not rank. |
| `game_support(league_id, document?, offset?, limit?, expected_sha256?)` | Return compact game identity, installed bindings and document IDs/hashes by default. Select a document for at most 6,000 Unicode characters (default 4,000); continue with `next_offset` and the original hash. A changed document fails rather than mixing releases. |
| `leaderboard(league_id, player_id)` | Read the selected player's current champion; never defaults to the workspace carrier. |
| `xp_status(experiment, game?)`, `credits` | Shared research platform reads. |
| `latest_league_result(league_id, player_id)` | Latest recorded match for the selected player and league, including round/episode IDs, policy versions, status, scores, and replay. Returns `available: false` before a replay exists. Read-only; verify the returned policy version before attributing a result to a new upload. |

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

## Conversation context and game support

Conversation intent takes precedence over current UI and workspace hints. Clients should
supply owned-player IDs/names and current selections near the start of each turn. They are
context, not a fixed binding or credential grant. Discovery and match reads take explicit
IDs and never write profile environment, canonical records or running cards.

Ordinary greetings, navigation and goal setup do not initialize the research board. Notebook
work starts from supplied evidence; research state is refreshed for task changes and supervision.

`game_support` returns an index first. Pass a document ID and its `sha256` as
`expected_sha256` to read the participation guide, manifest or installed research guide.
Offsets count Unicode characters, not bytes. Continuing beyond offset zero requires the hash;
`document_changed` means reload the index. The Coworld is read by its exact API ID, avoiding
the CLI's catalog-wide summaries lookup. This response replaces the former inline manifest
and participation text; update the plugin, shared CLI and SOUL together.

Production release follows `ide-distributions/main` through Metta’s
[Deploy Hermes agent workflow](https://github.com/Metta-AI/metta/blob/main/.github/workflows/deploy-hermes-agent.yml).
With `HERMES_WORKSPACE_UPDATE_POLICY=preserve`, it polls the source every five minutes
and qualifies the image before release. No separate Metta source-lock bump is needed
for this path. Existing workspaces keep their selected image until the owner pauses
and resumes them; the update preserves their workspace and identity. A merged source
PR is not proof that image qualification or rollout has completed.

A shipped or installed contract is matched by
its exact declared league ID, not by guessed directory paths. Missing bindings are reported separately
from platform availability. This permits research and match analysis for games without a
local build/evaluation adapter. The existing automated promotion loop still uses its installed
game contract and scoped credential; using a different player for writes requires the proper
credential and authorization, not a prompt override.

### Several games, and making support for a new one

A host researches any shipped or installed game, each in its own canonical repository
(`canonical-<game>.git` beside `RESEARCH_CANONICAL`) with a clone per profile
(`workspace-<game>`). Cards and tools without a game use the neutral workspace; `RESEARCH_GAME` scopes selected tasks only.
The minute supervisor reads each waiting run's game from its card and polls promotion in
every game that has a workspace. The design is
[docs/designs/multi-game-research.md](../docs/designs/multi-game-research.md).

When the user asks for research on a game with no research support, the orchestrator creates
a researcher card (in any existing game) that preloads `game-create`. The researcher fetches
the game's source from GitHub, scaffolds `games/<game>/` from the researcher's template
(installing a public optimizer-seed mixin when one exists), fills it and runs `game check`.
The orchestrator then calls `game_install` on that card's completed run, never integrates
its branch, tells the user the remaining gaps, and creates the game's cards with `game`.

Run `tests/test_platform_discovery.py` against a real local Observatory fixture using
`COGAMES_API_URL` (the frontend proxy), `SOFTMAX_PLAYER_CREDENTIAL`,
`RESEARCH_TEST_PLAYER`, and two comma-separated `RESEARCH_TEST_LEAGUES`. Use a credential
player different from the research player. This system test loads both games and switches
back without an installed default game, creating a workspace, or changing profile settings.
It is skipped without an explicit fixture and refuses non-loopback servers.

Before rollout, evaluate complete conversations for these cases:

- Empty desktop projects and board, existing hosted policy/matches: discover and cite activity.
- UI hints name one game; the user asks about another: use the requested game's support.
- User switches games and then returns: explicit tool targets follow each request.
- Workspace credential and owned default player differ: resolve the intended player from context.
- Multiple plausible owned players: ask one focused question after discovery.
- Published game has no installed research adapter: read its guide and identify the missing operation.
- Platform read fails: report an unavailable read, never infer no activity or require a new recording.

Score each conversation by correct target, cited live evidence, relevant support, and preserved
write authority (all four required to pass). Report completed cases, errors and timeouts
separately. The deterministic platform test verifies tool behavior; it does not measure model
selection or instruction-following quality. These conversation evaluations remain a rollout gate.

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

### Permission and source discovery

`host_capabilities` (shared with the researchers, `ide_research_core.host_capabilities`) reports, without running
anything or exposing environment values: installed executables (git, uv, gcc, nim, docker, ...), whether Docker has a
daemon, which places are writable (profile, workspace, `TMPDIR`, `/tmp`, the installed games and venv, the game build
folder), whether `/tmp` can run programs, whether this is a hosted workspace, and each game's missing host requirements.
`game_support` reports static readiness per operation, including installed-template markers and missing host requirements.
Configured bindings are not execution-verified. `game_starter` reads bounded UTF-8 pages of a published starter,
verifies its manifest SHA-256 and distinguishes it from the selected player's submitted version.
`card_report` reads a committed report from the recorded workspace of the latest completed card run,
checks commit membership in that card branch, and never integrates or executes the report.
If native completion cleaned up the worktree, the reader restores it through Hermes from the retained card branch.

Review a completed experiment through these tools:

1. Get its latest completed run and full commit from `card_show`.
2. Call `card_files(task_id, run_id, commit)` to discover the tree recursively. `directory`
   restricts the subtree; `recursive=false` lists one level. Follow `next_offset` for bounded
   pages, rather than guessing report paths or making a separate call for every match folder.
3. Use `card_report` for bounded text pages. Use `card_artifacts(task_id, run_id, commit, paths)`
   for SHA-256 hashes, byte sizes and Git blob IDs. Optional `expected_sha256` maps paths to
   hashes from the report or index; each file reports whether its expected hash matched.
4. Set `export=true` on `card_artifacts` to copy source, logs, JSON/CSV results and binary replays
   into a fresh private snapshot under this profile's `cache/card-artifacts/`. The returned
   `directory` preserves relative paths; `manifest` records the task, run, commit and file hashes.
   Native file and terminal tools can then analyze this coordinator-owned copy.

Each batch accepts at most 64 regular files totaling 256 MiB. `status=mismatch` exports nothing;
otherwise status is `inspected` or `exported`. Exports contain committed bytes even when a worker
worktree has uncommitted changes. Symlinks, submodules and unsafe paths cannot be exported.
Each export creates a separate snapshot, so a retry never overwrites an earlier analysis.
These tools share completed-run and branch checks, recover cleaned worktrees through Hermes,
never integrate a branch, and never execute copied files. Hash agreement establishes byte identity;
review result rows and evaluation evidence separately. Active worker files remain unavailable.
Failures carry stable reasons: `card_not_completed`, `workspace_unavailable`, `artifact_unavailable`,
`invalid_artifact_path`, `invalid_artifact_selection`, `artifact_limit`, or `invalid_export_cache`.
Refresh native task state, correct the selection, or reduce the batch according to that reason;
these conditions do not imply missing account credentials.
