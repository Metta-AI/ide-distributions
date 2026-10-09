# autoresearch

An autonomous research agent that improves game policies (first game: Gods of the Arena,
GotA) without human approval, and reports to an external "Preston-role" interface agent
through a file-based communication interface.

`autoresearch/` is the root of a Hermes Agent profile distribution. Install it with
`hermes profile install`. This file is the operations guide: install, configure, run,
restore, update.

Design documents (open the HTML files in a browser):

- [System Design](docs/designs/autoresearch-system-design-2026-10-06.html): what the agent
  does and how it does research. This is the authority for research behavior.
- [Runtime](docs/designs/autoresearch-runtime-2026-10-06.html): how it runs on Hermes.
- [Open questions](docs/designs/autoresearch-open-questions-2026-10-07.html).
- [Implementation plan](docs/plans/2026-10-07-autoresearch-v0/00-overview.md): contracts
  C1-C12 and amendments A1-A12. The contract numbers in this file refer to it.
- [Verified Hermes facts](docs/plans/findings/hermes-facts.md): what was checked on Hermes
  v0.21.5.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| `git` | any recent | the canonical workspace and its remote |
| `uv` | any recent | the profile venv, and installing Hermes |
| Hermes Agent | v0.21.5 (tag `v2026.9.24`) locally; v0.21.6 on the EC2 image | runs the cron jobs, the workers and Kanban. Both are supported ([image check](docs/plans/findings/hermes-image-compat.md)). |
| Nim | 2.2.10 | building GotA native tools and running native gate/replay tests; pure Python tests need no Nim |

Install Hermes by one of two ways (amendment A1). A plain `uv tool install git+...` fails,
because Hermes `setup.py` refuses wheel builds.

```sh
git clone --branch v2026.9.24 https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
uv tool install --python 3.12 -e .
hermes --version
git --version
uv --version
nim --version
```

Or use the official `install.sh` from the Hermes repository on a host.

## Install the profile

```sh
hermes profile install /path/to/ide-mvp/autoresearch --name autoresearch
```

This creates the profile `autoresearch` under `<host root>/profiles/`. The host root is
`~/.hermes` unless `HERMES_HOME` is set in your shell. Use that name everywhere below. The
Kanban board has the same name. The three cron jobs arrive paused.

Set `PROFILE` once. Every path below is relative to it:

```sh
export PROFILE="${HERMES_HOME:-$HOME/.hermes}/profiles/autoresearch"
```

Do not confuse the two homes. The host root holds all profiles. `$PROFILE` is the home of
this one profile. The `autoresearch` command and the cron scripts need `HERMES_HOME` to be
`$PROFILE`.

## Configure `.env`

Create `$PROFILE/.env` (mode 0600). Hermes never overwrites it.

```dotenv
OPENROUTER_API_KEY=sk-or-...
SOFTMAX_PLAYER_CREDENTIAL=cga_...
AUTORESEARCH_PLAYER_ID=ply_...
AUTORESEARCH_WORKSPACE_REMOTE=git@github.com:your-org/autoresearch-workspace.git
```

| Key | Required | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Key for the research model. Hermes keeps it from model commands. |
| `SOFTMAX_PLAYER_CREDENTIAL` | yes | The credential of the Softmax player the agent acts as, in one of two forms. (1) A bare `cga_...` agent token: the token Softmax hosting provisions for the hosted player (on that host it is the value under `tokens` in `~/.softmax/credentials.yaml`). It does not expire, so preflight never warns about expiry; if Softmax stops accepting it (agent revoked, player disabled), the agent pauses and sends a help request. Because it does not expire, a leaked copy works until the agent token is revoked: see risk 1 in [acceptance-checklist.md](docs/plans/findings/acceptance-checklist.md) section 2.8. (2) A Player session, as JSON `{"token": "ply_...", "expires_at": "2026-10-08T12:00:00Z"}` in single quotes: one entry of `player_sessions.<server>.cache` in the `~/.softmax/credentials.yaml` that `coworld player use <ply_id>` writes. It lasts about 24 hours; preflight warns 2 hours before expiry and pauses at expiry. A bare `ply_` token is rejected. Format details: [softmax-client.md](docs/plans/findings/softmax-client.md). |
| `AUTORESEARCH_PLAYER_ID` | yes | Id of that player. Every Softmax call checks that the credential belongs to it. |
| `AUTORESEARCH_WORKSPACE_REMOTE` | yes | Git remote of the canonical workspace. **The repository must exist before the first run.** An empty repository is fine. A relative path is relative to `$PROFILE`. |
| `COGAMES_API_URL` | no | Softmax API server, for example `http://localhost:8000/api` for a local stack. Default `https://softmax.com/api`. Every `softmax`/`coworld` call the agent makes passes it as `--server`, and the credential is used only for that server. Softmax hosting writes it to the profile `.env`. |
| `OPENROUTER_MODEL` | no | Model id. Default `moonshotai/kimi-k3`, fallback `z-ai/glm-5.3`. When set, bootstrap writes it to `model.default` in the profile `config.yaml` (Hermes workers read the model only from there). |
| `AUTORESEARCH_GAME` | no | Directory name under `games/`. Default `gota`. |
| `AUTORESEARCH_WORKSPACE` | no | Path of the canonical workspace. Default `$PROFILE/workspace`. |
| `AUTORESEARCH_SOFTMAX` | no | `fake` in tests: use the fake Softmax tool. Do not set it in production. |
| `AUTORESEARCH_QUEUE` | no | Leave unset. `file` selects the file queue, but no shipped job runs its cards (see below). |
| `AUTORESEARCH_INBOX_WRITERS` | no | Comma-separated uids or user names allowed to write `comms/inbox/` directly (for example the Softmax IDE bridge's own user). When set, queue-keeper adopts only inbox files they own and rejects the rest. An unknown user name stops adoption (not routing) and fails the step `inbox_writers`; a valid but wrong user gets every direct write rejected for good. When unset, it adopts any valid file and logs that direct writes are unauthenticated: any process running as the workspace user could then send inbound messages, which carry the human's authority. Set it whenever an external agent writes the inbox. |
| `AUTORESEARCH_CARD_MODELS` | no | Model per card mode, as `mode=model` pairs separated by commas. Default `planning=z-ai/glm-5.3,scouting=z-ai/glm-5.3,maintenance=z-ai/glm-5.3`: mechanical cards run on the cheaper model, `investigation` and `experimentation` on `model.default`. A mode set to `default` (or the whole value `none`) uses `model.default`. queue-keeper pins the model when it creates the card (`hermes kanban create --model ... --provider openrouter`), so a change applies to new cards only; `hermes kanban --board autoresearch set-model <task> <model>` changes a card already on the board. A bad entry (unknown mode, no model) is skipped, so that mode uses `model.default`; queue-keeper logs it, records the step failure `card_models`, and sends one help request per distinct value. |
| `AUTORESEARCH_MAX_CARD_RUNS` | no | Runs per card. Default 4. queue-keeper stops a card that would start, or has started, one more run: it blocks it (`hermes kanban block --kind capability`, or `needs_input` when its last block was `capability`, so Hermes' unblock-loop breaker does not send it to triage; reason `spend-cap: ...`) and sends one help request. Only `running` and `ready` cards can be blocked; others are skipped. A stop that fails is retried every minute and reported once per failure streak; it grants no new allowance. A stopped card is never requeued or repaired; unblocking it gives it another full allowance. The blocked-card check (queue-keeper and `health`) also keeps a blocked card blocked, without requeue or repair, once it has reached this cap or the cost cap. The caps also run while preflight fails and the agent is paused. `0` turns the cap off. |
| `AUTORESEARCH_MAX_CARD_USD` | no | Estimated model spend per card, in USD. Default 4. Same stop as above. The estimate is Hermes' own (`state.db`, `sessions.estimated_cost_usd`), summed over the sessions of the card's runs and their compression and subagent sessions. `0` turns the cap off. |
| `AUTORESEARCH_DAILY_BUDGET_USD` | no | Estimated model spend per UTC day of every session of this profile. Default 25. At the budget queue-keeper pauses dispatch (the usual pause: pending cards unassigned, no admission) and sends one help request a day; running cards finish under their own caps. It resumes by itself on the next UTC day, or after you raise the value. `0` turns it off. |
| `AUTORESEARCH_FALLBACK_PRICES` | no | USD per million tokens for a session Hermes could not price (its estimate missing, for example when OpenRouter pricing was unreachable), as `kind=price` pairs. Default `input=3,output=12,cache_read=0.75,cache_write=3`, above kimi-k3's observed rate on purpose. queue-keeper logs each session priced this way once. |
| `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` | no | First wait before an experiment refused for credits is relaunched (doubles per refusal, at most 8 hours). Default 30. |

Bootstrap writes `AUTORESEARCH_CLI` (the path of the `autoresearch` command) and
`HERMES_WRITE_SAFE_ROOT` (the canonical workspace) into the same `.env`. Do not set them
by hand.

The GotA `build` binding also needs `GOTA_HERMES_PYTHON` (the Python of the installed
Hermes) and `GOTA_TIRITH_BIN` (an officially installed tirith scanner), plus the GotA native
helpers prepared once on the host. Details: [games/gota/GAME.md](games/gota/GAME.md),
"Build and upload".

## Host configuration

One Hermes gateway runs one Kanban dispatcher for the whole host. It uses the config of the
host default profile, not the config of `autoresearch` (amendment A3). The profile
configuration cannot fix this. Set it once on the host:

```sh
hermes config set kanban.max_in_progress 2
hermes config set kanban.dispatch_in_gateway true
hermes config unset kanban.default_assignee
hermes gateway status
```

- `kanban.default_assignee` must stay unset. If it is set, cards without an assignee
  start by themselves, and pause (C7) cannot hold them.
- `kanban.max_in_progress` is a cap for all boards on the host. The installer sets it.
  Bootstrap never writes the host config, and a profile value of `kanban.max_in_progress`
  that you tuned is never overwritten.
- The gateway dispatcher must be on. Preflight reads the host config on each run. It pauses
  the agent and sends a help request when one of these three settings is wrong.

Start the gateway if it is not running (`hermes gateway start`).

## Resume the cron jobs

The jobs arrive paused. Review them, then resume them:

```sh
hermes -p autoresearch cron resume queue-keeper
hermes -p autoresearch cron resume xp-poll
hermes -p autoresearch cron resume health
hermes -p autoresearch cron list
```

| Job | Runs | Does |
|---|---|---|
| `queue-keeper` | every 1 minute | Bootstrap, preflight, merge of finished cards, publication, admission of new cards, planning, spend caps (per card and per day, see `AUTORESEARCH_MAX_CARD_RUNS` and the rows after it). The only writer of the canonical workspace. |
| `xp-poll` | every 5 minutes | Collects the results of Softmax experience (XP) requests as evidence, and asks for review cards. |
| `health` | every 15 minutes | Checks blocked cards (requeued once, then repaired; a `tool-fault:` block is never requeued and gets one help request per blocked run; a card stopped at a spend cap, or blocked at its run or cost cap, is left blocked), free disk, and the credential; each problem becomes one help request per day. Log: `.supervisor/runtime/health.log`. |

To start a `queue-keeper` run now instead of waiting for the schedule:

```sh
hermes -p autoresearch cron run queue-keeper
```

`cron run` claims the job and runs it in the foreground, then prints `Ran now: succeeded.` or
`Ran now: failed.` This is the same in Hermes v0.21.5 and v0.21.6 (checked in source:
`tools/cronjob_tools.py`, `_action_run`). It does not wait for the gateway. If the scheduler
already holds the job it prints "Already being fired by the scheduler" and does not run it
again; if the CLI inherits a gateway session it may instead report that the job runs in the
background. `hermes -p autoresearch cron tick` runs every due job once, by hand.

To run only bootstrap (no cards), use a subshell. It loads the profile `.env`, because the runtime
reads `AUTORESEARCH_WORKSPACE_REMOTE` and the other keys from the environment. `uv` must be
on `PATH`, because the launcher runs `uv sync`:

```sh
( set -a; . "$PROFILE/.env"; set +a; HERMES_HOME="$PROFILE" python3 "$PROFILE/scripts/queue_keeper.py" --bootstrap-only )
```

The first `queue-keeper` run syncs the venv (`uv sync` from `uv.lock`), writes the CLI shim,
creates the canonical workspace, pushes `main` to the remote, and creates the Kanban board.
Later runs skip each step that is already done.

## Kanban go/no-go switch

The queue is Hermes Kanban. The Kanban go/no-go passed (GO, 2026-10-08,
[protocol-slice.md](docs/plans/findings/protocol-slice.md)), so v0 has no other queue.
`AUTORESEARCH_QUEUE=file` selects a file queue that keeps its cards in the canonical runtime
directory, but the serial job that would run those cards (Plan 01 Task 5.2) is not built.
Do not set it on a host: cards would be admitted and never run.

## Operate

### Operator shell

`AUTORESEARCH_CLI` is only in the profile `.env`, and the CLI needs `HERMES_HOME` to be the
profile home. Softmax commands also need `SOFTMAX_PLAYER_CREDENTIAL` and
`AUTORESEARCH_PLAYER_ID`, and `COGAMES_API_URL` when the server is not softmax.com. Prepare each operator shell like this (after `PROFILE` is set):

```sh
set -a; . "$PROFILE/.env"; set +a; export HERMES_HOME="$PROFILE"
```

Now `HERMES_HOME` is the profile home in this shell. Run `hermes` commands for the host
(`hermes config`, `hermes gateway`) in a different shell, or with `HERMES_HOME` unset.
`hermes -p autoresearch ...` commands work in both.

Read state with the CLI:

```sh
"$AUTORESEARCH_CLI" queue list
"$AUTORESEARCH_CLI" comms list
"$AUTORESEARCH_CLI" promotion status
"$AUTORESEARCH_CLI" softmax whoami
```

Look at the Kanban board with `hermes kanban --board autoresearch list`.

On a fresh install there is no champion. The first card that needs one bootstraps it by
itself (amendment A14): it builds the game's shipped strategy (the `## Baseline build`
section of `games/<game>/GAME.md`) and runs
`"$AUTORESEARCH_CLI" softmax bootstrap-champion --build-dir <build dir>`, which uploads it and
promotes it through the normal promotion, without an experiment. So the first league
submission is the shipped strategy, unevaluated. Check it with
`"$AUTORESEARCH_CLI" promotion status` and `"$AUTORESEARCH_CLI" softmax leaderboard`.

Where things live:

| What | Where |
|---|---|
| Profile home | `$PROFILE/` |
| Canonical workspace (git) | `$PROFILE/workspace/` |
| Durable ledger (committed, pushed) | `$PROFILE/workspace/.supervisor/ledger/` |
| Pause state | `$PROFILE/workspace/.supervisor/ledger/pause.json` (exists only while paused) |
| Spend-cap stops of a card (committed) | `$PROFILE/workspace/.supervisor/ledger/health/spend/<task-id>.json` |
| Hermes session usage and cost estimates (read by the spend caps) | `$PROFILE/state.db` (`sessions`) |
| Disposable runtime state | `$PROFILE/workspace/.supervisor/runtime/` |
| Per-experiment locks (`softmax xp create`, `upload`, `submit`) | `$PROFILE/workspace/.supervisor/runtime/locks/xp-<E-id>.lock` |
| queue-keeper log | `$PROFILE/workspace/.supervisor/runtime/keeper.log` |
| Hermes logs, cron output | `$PROFILE/logs/`, `$PROFILE/cron/` |
| Card worktrees | `$PROFILE/workspace/.worktrees/` |
| venv and CLI shim | `$PROFILE/local/venv/`, `$PROFILE/bin/autoresearch` |
| Staged skills (not adopted) | `$PROFILE/local/skills-staging/` |
| Hermes memory | `$PROFILE/memories/MEMORY.md` |
| Memory snapshot (committed) | `$PROFILE/workspace/.supervisor/hermes-memory.md` |
| Outbound messages (read by external agents) | `$PROFILE/workspace/comms/outbox/<seq>-<M-id>.json` |
| Inbound messages | `$PROFILE/workspace/comms/inbox/<id>.json` |

External agents send inbound messages in one of two ways: `"$AUTORESEARCH_CLI" comms inbound`
(validates, gives an `I-` id, commits), or by writing `comms/inbox/<id>.json` directly with
their own id and a `created` time (the Softmax IDE bridge does this). queue-keeper commits a
valid direct write unchanged on its next run. It answers an invalid one (or a symlink, or a
file whose owner is not in `AUTORESEARCH_INBOX_WRITERS`) once with a runtime `note` in reply
to its id and records the id in `.supervisor/ledger/inbound-rejected/`; a rejected id is
final. Run the external agent as its own OS user and set `AUTORESEARCH_INBOX_WRITERS`: the
agent's own workers run as the workspace user, and the plugin blocking their inbox writes is
best effort. See
[softmax-ide-bridge-compat.md](docs/integrations/softmax-ide-bridge-compat.md).

The message contract (every outbound and inbound kind, as JSON Schema with example messages)
is [`schemas/comms/`](schemas/comms/README.md); that README also says how to add a kind.

A step that fails does not stop the run. It writes a line to `keeper.log` and shows as a
`keeper_step_failed` signal. The agent pauses itself when preflight fails (identity,
expired or rejected credential, low disk, low OpenRouter credit, wrong host Kanban config). It sends
a help request. It resumes by itself when preflight passes again. When Softmax cannot be
reached to check the credential (network error, timeout, server error), queue-keeper skips
that run instead; only the third such run in a row pauses, with one help request. It also
pauses when today's estimated spend reaches `AUTORESEARCH_DAILY_BUDGET_USD`, and then resumes
only once the spend is under the budget again (the next UTC day). The spend caps still run
while the agent is paused and on a skipped run, because running cards keep running.

Workers wait on long commands without model calls: a build or local check runs with the
terminal tool's `background: true` and one `process_manage` wait of up to 1800 s
(`terminal.timeout` in the profile config, which caps a wait). The run budget
(`agent.run_budget_seconds`, 5400 s) is long enough for a build card to wait out its gates;
bootstrap raises the earlier shipped defaults (2700 s, and Hermes' 180 s for
`terminal.timeout`) to these values and keeps any other value you set. The raise is one-way:
if you want exactly 2700 or 180, set a nearby value (2701, 181), or bootstrap raises it again. The plugin refuses foreground
`sleep` of 30 s or more and loops that sleep while checking a process.

## Restore on a new host

Privacy note: the memory snapshot is committed to the workspace and pushed to
`AUTORESEARCH_WORKSPACE_REMOTE`. Anything the agent writes to memory is visible to everyone
with access to that remote. queue-keeper writes the snapshot only when the content changed.

The canonical workspace is a git repository, and queue-keeper pushes it to
`AUTORESEARCH_WORKSPACE_REMOTE` on each run (`main` and the `card/*` branches). The Hermes
memory is not in that repository by itself: queue-keeper copies it to
`.supervisor/hermes-memory.md` after each merge pass, and that file is pushed with `main`.

1. Install Hermes and the profile as above. Write the same `.env` (new credential if the
   old one expired or was revoked).
2. Run bootstrap once (this is the command that clones the workspace; `uv` must be on `PATH`):

```sh
( set -a; . "$PROFILE/.env"; set +a; HERMES_HOME="$PROFILE" python3 "$PROFILE/scripts/queue_keeper.py" --bootstrap-only )
```

   When the remote has a `main`, bootstrap clones it into `$PROFILE/workspace/`. It does
   not seed a new history.
3. Copy the memory snapshot back:

```sh
mkdir -p "$PROFILE/memories"
cp "$PROFILE/workspace/.supervisor/hermes-memory.md" "$PROFILE/memories/MEMORY.md"
```

4. Resume the cron jobs. The board is new on a new host, so queue-keeper starts a new board
   epoch and rebuilds the cards that were admitted but not finished (C12).

## Update

```sh
hermes profile update autoresearch -y
```

This replaces the files the distribution owns (code, doctrine, skills, cron job definitions)
and keeps `.env`, `config.yaml`, memories, the workspace, and `local/`. The next
`queue-keeper` run re-applies the configuration rules in contract C9 with
`hermes config set` and re-seeds `AGENTS.md` when `doctrine/VERSION` changed. Hermes has no
automatic update; `updates.check` is set to false so it does not even check.

## Known gaps

- **Serial fallback is not shipped.** `AUTORESEARCH_QUEUE=file` selects the queue backend,
  but the `research-serial` cron job and its pre-run script (Plan 01 Task 5.2) are not
  built: the Kanban go/no-go passed (GO, `docs/plans/findings/protocol-slice.md`), so Kanban
  is the v0 queue.
- **GotA is bound but not yet proven end to end.** `games/gota` has real `build`,
  `local_check`, `evaluate` and `upload` bindings (Plan 02 Task 2.5). The build follows the
  live engine. The compiler passed M1 on 2026.10.7.2 only; a newer engine has no semantic
  qualification yet, and no real research cycle has run on GotA
  ([acceptance-checklist.md](docs/plans/findings/acceptance-checklist.md), section 3.2).
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is set to false. Do not run `hermes curator adopt` on a shipped
  skill.
- **Credits.** A Player credential cannot read credits (HTTP 403). Preflight only warns,
  and experiments that wait for credits are retried with backoff (A10).
- **Gateway turn hooks** of the plugin are unverified (see hermes-facts.md, item 2).
- **Spend caps see only this profile's sessions.** GotA compiler child runs (their own
  `HERMES_HOME`) and spend outside the profile are not counted. A card's cost needs the
  plugin's `session_start` event for each run, so sessions from before this feature count
  only through their `session_end` event. A stopped running worker is ended by the Hermes
  dispatcher about two minutes after the stop (`reap_terminal_workers`), not at once; this is
  not yet observed on a live host. What it spends in those minutes counts against the
  allowance a human gives by unblocking it.
- **Pause timing.** A budget pause, like a preflight pause, unassigns pending cards on the
  next queue-keeper run; a card Hermes itself returns to `ready` (a crash retry keeps its
  assignee) can start again in the minute before that.
- **A board rebuild (C12) gives re-admitted cards new task ids,** so their run and cost
  counts start again from zero.
- `runtime/kanban_events.jsonl` is read in full on each queue-keeper run, and the daily sum
  scans `state.db` `sessions`; both are small today but grow without rotation.

## Integrations

| Document | Covers |
|---|---|
| [`docs/integrations/softmax-ide-bridge.md`](docs/integrations/softmax-ide-bridge.md) | The Softmax IDE bridge as the external agent on `comms/`: the inbox files it writes, the outbox fields it reads, how it treats acknowledgements, and what is not exercised yet |
