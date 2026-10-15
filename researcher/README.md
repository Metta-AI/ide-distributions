# autoresearch

An autonomous research agent that improves game policies (first game: Gods of the Arena,
GotA) without human approval, and reports to a "Preston-role" interface agent through a
file-based communication interface. In the Softmax IDE that agent is the profile's own coach
chat ("IDE coach chat" below); elsewhere it is an external agent.

`researcher/` (formerly `autoresearch/`) is the root of a Hermes Agent profile distribution. Install it with
`hermes profile install`. This file is the operations guide: install, configure, run,
restore, update.

Design documents (open the HTML files in a browser):

- [System Design](docs/designs/autoresearch-system-design-2026-10-06.html): what the agent
  does and how it does research. This is the authority for research behavior.
- [Runtime](docs/designs/autoresearch-runtime-2026-10-06.html): how it runs on Hermes.
- [Open questions](docs/designs/autoresearch-open-questions-2026-10-07.html).
- [Implementation plan](docs/plans/2026-10-07-autoresearch-v0/00-overview.md): contracts
  C1-C12 and amendments A1-A15. The contract numbers in this file refer to it.
- [Verified Hermes facts](docs/plans/findings/hermes-facts.md): what was checked on Hermes
  v0.21.5.
- [Softmax hosting integration](docs/designs/softmax-integration-plan-2026-10-09.html): how the
  profile goes into the hosted Hermes fleet (milestones M0-M5, decisions D1-D15). The task-level
  plan is [plan.md](docs/plans/2026-10-09-softmax-integration/plan.md), with evidence in
  [findings.md](docs/plans/2026-10-09-softmax-integration/findings.md). The profile side is
  built ("Softmax hosting" below); the hosting runtime changes are metta work that is not in
  this repository. The coach chat it adds is designed in
  [coach-chat-role.md](docs/designs/coach-chat-role.md) and built ("IDE coach chat" below).

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
export PROFILE_NAME=autoresearch
hermes profile install /path/to/ide-distributions/researcher --name "$PROFILE_NAME"
```

The profile works under any install name. Below, `<profile>` (the shell variable
`$PROFILE_NAME`) is that name: `optimizer` on Softmax hosting, `autoresearch` in a manual
install. This creates the profile `<profile>` under `<host root>/profiles/`. The host root is
`~/.hermes` unless `HERMES_HOME` is set in your shell. Cards are assigned to `<profile>`.
The Kanban board is always named `autoresearch`, whatever the install name, and Kanban boards
are host-wide (`<host root>/kanban/boards/<slug>`). Install **one autoresearch profile per
Hermes root**: two would share the board and requeue, repair and spend-cap each other's
cards. Preflight fails (the agent pauses with one help request) when another profile under
`<host root>/profiles/` has `plugins/autoresearch/plugin.yaml`. The three cron
jobs arrive paused. Shell variables do not survive a new shell: export `PROFILE_NAME` (and
`PROFILE`, below) again in each new shell before running the commands in this guide.

An existing install not named `autoresearch` (an earlier version named its board after the
profile) gets a new `autoresearch` board with a new board epoch on its next queue-keeper run.
Cards on the old profile-named board stay there: let them finish or archive each one with
`hermes kanban --board "$PROFILE_NAME" archive <card>` (`hermes kanban --board "$PROFILE_NAME" list` shows them).

Set `PROFILE` once. Every path below is relative to it:

```sh
export PROFILE="${HERMES_HOME:-$HOME/.hermes}/profiles/$PROFILE_NAME"
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
| `AUTORESEARCH_INBOX_WRITERS` | no | Comma-separated uids or user names allowed to write `comms/inbox/` directly (for example the Softmax IDE bridge's own user). When set, queue-keeper adopts only inbox files they own and rejects the rest. An unknown user name stops adoption (not routing) and fails the step `inbox_writers`; a valid but wrong user gets every direct write rejected for good. When unset, it adopts any valid file and logs that direct writes are unauthenticated: any process running as the workspace user could then send inbound messages, which carry the human's authority. Set it whenever an external agent writes the inbox. |
| `AUTORESEARCH_CARD_MODELS` | no | Model per card mode, as `mode=model` pairs separated by commas. Default `planning=z-ai/glm-5.3,scouting=z-ai/glm-5.3,maintenance=z-ai/glm-5.3`: mechanical cards run on the cheaper model, `investigation` and `experimentation` on `model.default`. A mode set to `default` (or the whole value `none`) uses `model.default`. queue-keeper pins the model when it creates the card (`hermes kanban create --model ... --provider openrouter`), so a change applies to new cards only; `hermes kanban --board autoresearch set-model <task> <model>` changes a card already on the board. A bad entry (unknown mode, no model) is skipped, so that mode uses `model.default`; queue-keeper logs it, records the step failure `card_models`, and sends one help request per distinct value. |
| `AUTORESEARCH_MAX_CARD_RUNS` | no | Runs per card. Default 4. queue-keeper stops a card that would start, or has started, one more run: it blocks it (`hermes kanban block --kind capability`, or `needs_input` when its last block was `capability`, so Hermes' unblock-loop breaker does not send it to triage; reason `spend-cap: ...`) and sends one help request. Only `running` and `ready` cards can be blocked; others are skipped. A stop that fails is retried every minute and reported once per failure streak; it grants no new allowance. A stopped card is never requeued or repaired; unblocking it gives it another full allowance. The blocked-card check (queue-keeper and `health`) also keeps a blocked card blocked, without requeue or repair, once it has reached this cap or the cost cap. The caps also run while preflight fails and the agent is paused. `0` turns the cap off. |
| `AUTORESEARCH_MAX_CARD_USD` | no | Estimated model spend per card, in USD. Default 4. Same stop as above. The estimate is Hermes' own (`state.db`, `sessions.estimated_cost_usd`), summed over the sessions of the card's runs and their compression and subagent sessions. `0` turns the cap off. |
| `AUTORESEARCH_DAILY_BUDGET_USD` | no | Estimated model spend per UTC day of every research session of this profile (all but the IDE coach chat's sessions, Hermes source `api_server`, which have their own cap below). Default 100. At the budget queue-keeper pauses dispatch (the usual pause: pending cards unassigned, no admission) and sends one help request a day; running cards finish under their own caps. It resumes by itself on the next UTC day, or after you raise the value. `0` turns it off. |
| `AUTORESEARCH_CHAT_DAILY_USD` | no | Estimated model spend per UTC day of the IDE coach chat ("IDE coach chat" below). Default 3. Over it, the chat's tools refuse except `status`, and the chat tells the coach its budget is spent; research is not affected. The plugin reads it from the profile `.env`. `0` turns it off. |
| `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD` | no | Credit floor of the OpenRouter key, in USD. Default 10. Preflight reads the key's remaining credit (`limit_remaining` from `GET https://openrouter.ai/api/v1/key`) on every queue-keeper run and pauses the agent with a help request when it is below this floor, because Hermes retries a refused (402) model call forever. A fresh $10 key is not below the default; it pauses after its first spend. Set the floor lower when the key itself is small and should be spent further down. A value that is not a non-negative number uses the default and logs a preflight warning. |
| `AUTORESEARCH_AUTONOMY` | no | `always` (default) or `on-request`. With `on-request` the agent plans nothing (no scheduled, queue-growth or board-rebuild planning card) until the coach's first task arrives: an inbound `task` whose `author` starts with `coach:` and that routing accepted (a task answered with a rejection note, for example because its refs do not resolve, does not start the agent). That task commits `.supervisor/ledger/started.json` (`{"started_at": ..., "by": "<inbound id>"}`), which is permanent; a coach `steer` (what cancelling a task produces) or a task from anyone else does not start the agent, though tasks are still admitted and run. Until then queue-keeper logs once a day that planning waits. This is a convenience, not a security control: everything in the sandbox runs as one uid, so any process there can write a `coach:` task. An unknown value runs as `always`; queue-keeper logs it, records the step failure `autonomy`, and sends one help request per distinct value. A failed commit of `started.json` is the step failure `autonomy_start`. |
| `AUTORESEARCH_FALLBACK_PRICES` | no | USD per million tokens for a session Hermes could not price (its estimate missing, for example when OpenRouter pricing was unreachable), as `kind=price` pairs. Default `input=3,output=12,cache_read=0.75,cache_write=3`, above kimi-k3's observed rate on purpose. queue-keeper logs each session priced this way once. |
| `AUTORESEARCH_CHAMPION_GRACE_MINUTES` | no | How long the league may take to make our admitted submission the champion (or, when its membership cannot be found, how long after the submit) before queue-keeper pauses the agent for a champion conflict (A9). Default 30. |
| `AUTORESEARCH_RELAUNCH_MIN_CREDITS` | no | Credits an experiment refused for credits needs before xp-poll relaunches it, when the cost per XP episode cannot be estimated from recent requests (A10). Default 100. |
| `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` | no | First wait before an experiment refused for credits is relaunched when the credit balance cannot be read, or when the refusal came with a positive balance (doubles per refusal, at most 8 hours). Default 30. The same wait applies to an experiment the coach's IDE Controls refused (`awaiting-permit`) when the coach's answers cannot be read or the league choice refused it; a refused promotion submit then always waits 30 minutes first. |
| `GOTA_G2_RUNNER` | no | GotA G2 gate episode runner. Default `native`: the pinned native match binary, no Docker needed. `docker` runs pinned `coworld run-episode` instead, which needs Docker. See `games/gota/GAME.md`, "G2 episode runner". |

Bootstrap writes `AUTORESEARCH_CLI` (the path of the `autoresearch` command) and
`HERMES_WRITE_SAFE_ROOT` (the canonical workspace) into the same `.env`. Do not set them
by hand.

A game can declare what its bindings need on the host in the `host_requirements` of its
`GAME.md` (GotA: `GOTA_HERMES_PYTHON` and `nim` on `PATH`; see
`games/gota/GAME.md`, "Build and upload"). Put those in the profile `.env`, which worker
terminals read (for Nim, a `PATH=/path/to/nim/bin:${PATH}` line extends the PATH workers
start with; the braces are required, Hermes leaves a bare `$PATH` as literal text). The EC2 shim's provisioning writes them for GotA. Each run, queue-keeper
checks them as workers see them and, when one is missing, logs it and sends one help request
per distinct set; it does not pause.

The GotA `build` binding also needs `GOTA_HERMES_PYTHON` (the Python of the installed
Hermes), plus the GotA native
helpers prepared once on the host. Details: [games/gota/GAME.md](games/gota/GAME.md),
"Build and upload".

## Host configuration

One Hermes gateway runs one Kanban dispatcher for the whole host. It uses the config of the
host default profile, not the config of `<profile>` (amendment A3). The profile
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
hermes -p "$PROFILE_NAME" cron resume queue-keeper
hermes -p "$PROFILE_NAME" cron resume xp-poll
hermes -p "$PROFILE_NAME" cron resume health
hermes -p "$PROFILE_NAME" cron list
```

| Job | Runs | Does |
|---|---|---|
| `queue-keeper` | every 1 minute | Bootstrap, preflight, merge of finished cards, publication, admission of new cards, planning, spend caps (per card and per day, see `AUTORESEARCH_MAX_CARD_RUNS` and the rows after it). The only writer of the canonical workspace. A card the runtime did not create is never merged, in any board epoch: a board task with no card body (for example one a model added with `kanban_create`), or a handoff whose request has no committed `cards/requests/<Q-id>.json` (a receipt its worker wrote). It gets one help request that says how to archive it, and the marker `.supervisor/ledger/foreign/<task>.json`; the spend caps still apply to it while it runs. Its card branch is still pushed to `origin` with the others (`refs/heads/card/*`), unmerged. `main` is pushed on its own and never forced; card branches are force-pushed (a retried card rebuilds its branch, so origin's copy is stale), and a card ref origin still refuses is logged once in `keeper.log`. One card that fails (unreadable on the board, or a blocked card the board will not requeue) does not stop the other cards in the same step; the step is still logged as failed. A merge or publication that fails and leaves canonical unchanged is handled the same way; one that leaves it changed stops the step, and the next run repairs it. If the board cannot list its foreign cards, the spend caps still apply to the other cards. A Hermes status the runtime does not know reads as claimed. |
| `xp-poll` | every 5 minutes | Collects the results of Softmax experience (XP) requests as evidence, and asks for review cards. It also relaunches an experiment that waits for credits (`awaiting-credits`) or for the coach (`awaiting-permit`, see the IDE Controls paragraphs in Operate), reads the coach's answers, and stops an experiment the coach declined. Log: `.supervisor/runtime/xp_poll.log`. |
| `health` | every 15 minutes | Checks blocked cards (requeued once, then repaired; a `tool-fault:` block is never requeued and gets one help request per blocked run; a card whose handoff is already published cannot hand off again, so it is never requeued: when that handoff was `blocked` it is repaired at once, at most 2 repairs deep (then one help request), and a rerun a human starts by unblocking it is closed: dropped while it waits, stopped once the dispatcher claimed it (also when it was claimed between the board listing and the drop). When the handoff was `blocked`, its repair card is requested only after that close, and never while the board still records a worker process for any run of that card (also a run stopped earlier, when the card was unblocked again and then dropped): a stop only ends the run, and Hermes ends the worker 120 s or more later, so the repair of a stopped rerun is requested by a later check, at least 2 minutes after the stop. A rerun that completes before the check sees it is repaired the same way, also once its worker is gone. No spend cap applies to these repairs. A rerun in Hermes `todo` or `scheduled` is left until Hermes makes it `ready`; one in `triage` gets its repair request and is then archived. What this does not cover: a human who unblocks the card again after its repair was requested (the next check closes that rerun), and a worker process Hermes does not record. One bounded exception: Hermes never clears the worker of a run that has no start fingerprint (`worker_started_at`; rows from an older Hermes), so such a worker counts as gone 15 minutes after its run ended, whether or not the process still exists; a card stopped at a spend cap, or blocked at its run or cost cap, is left blocked), free disk, and the credential; each problem becomes one help request per day. Log: `.supervisor/runtime/health.log`. |

To start a `queue-keeper` run now instead of waiting for the schedule:

```sh
hermes -p "$PROFILE_NAME" cron run queue-keeper
```

`cron run` claims the job and runs it in the foreground, then prints `Ran now: succeeded.` or
`Ran now: failed.` This is the same in Hermes v0.21.5 and v0.21.6 (checked in source:
`tools/cronjob_tools.py`, `_action_run`). It does not wait for the gateway. If the scheduler
already holds the job it prints "Already being fired by the scheduler" and does not run it
again; if the CLI inherits a gateway session it may instead report that the job runs in the
background. `hermes -p <profile> cron tick` runs every due job once, by hand.

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
[protocol-slice.md](docs/plans/findings/protocol-slice.md)), so v0 has no other queue. The
serial fallback was never built and its queue option was removed (amendment A13).
`FileQueue` remains only as the tests' stand-in for Kanban (`AUTORESEARCH_QUEUE=file` is a
test hook, like `AUTORESEARCH_SOFTMAX=fake`; never set it on a host: preflight warns when the
profile `.env` sets it).

## Operate

### Operator shell

`AUTORESEARCH_CLI` is only in the profile `.env`, and the CLI needs `HERMES_HOME` to be the
profile home. Softmax commands also need `SOFTMAX_PLAYER_CREDENTIAL` and
`AUTORESEARCH_PLAYER_ID`, and `COGAMES_API_URL` when the server is not softmax.com. Prepare each operator shell like this (after `PROFILE_NAME` and `PROFILE` are set):

```sh
set -a; . "$PROFILE/.env"; set +a; export HERMES_HOME="$PROFILE"
```

Now `HERMES_HOME` is the profile home in this shell. Run `hermes` commands for the host
(`hermes config`, `hermes gateway`) in a different shell, or with `HERMES_HOME` unset.
`hermes -p <profile> ...` commands work in both.

Read state with the CLI:

```sh
"$AUTORESEARCH_CLI" queue list
"$AUTORESEARCH_CLI" comms list
"$AUTORESEARCH_CLI" promotion status
"$AUTORESEARCH_CLI" softmax whoami
"$AUTORESEARCH_CLI" status --json
"$AUTORESEARCH_CLI" report baseline --since 2026-10-09 --json
```

`status --json` is one read-only snapshot, also allowed to cards: the distribution version
and commit (and a failed update, from the profile's `.softmax-update-failed` JSON
`{"commit", "error"}`), pause state and reasons, coach hold, `AUTORESEARCH_AUTONOMY` and
whether the agent started, today's estimated spend against the daily budget, the OpenRouter
key's limit, usage and remaining credit, running cards, open questions, the permit asks the
agent waits on (`permit_asks_open`: the recorded asks of `awaiting-permit` experiments, plus
one for a promotion whose submit waits on an ask; whether the coach already answered is not
read here), the champion and any conflict, and the time of the last queue-keeper run. A source it cannot read becomes a
`warnings` entry. It never prints a credential.

`report baseline --since <YYYY-MM-DD> --json` reports performance and cost from that UTC
day on, read only and allowed to cards (the coach chat's `baseline_report`): per card (mode,
model, runs, wall time, tokens, estimated USD, outcome), per day (estimated USD by session
source, today's key usage from OpenRouter's `usage_daily`, cards finished), per experiment
(XP requests, episodes, time from creation to its latest evidence, the cost of the cards that
ref it; `credits` is null because no per-request credit cost is stored), per uploaded build
(wall time and each gate's seconds, null when the build did not record them), and the key.
`estimated_usd_by_source` always has `cli` (card workers: Hermes sources `kanban` and `cli`),
`api_server` (the coach chat, counted on the day it was spent: the chat is one long-lived
session, so today's figure comes from the chat budget's snapshot
`$PROFILE/local/chat-spend.json`; earlier days on which a chat session existed, and today before
the first coach tool call of the day, are null with a warning, because no history is kept),
`compiler` (GotA compiler copies, below) and `cron`; any other
source seen, such as `delegate`, is an extra key with its own total. Module docstring:
`src/autoresearch/runtime/baseline.py`.

Look at the Kanban board with `hermes kanban --board autoresearch list`.

On a fresh install there is no champion. The first card that needs one bootstraps it by
itself (amendment A14): it builds the game's starter policy (the `## Starter policy`
section of `games/<game>/GAME.md`) and runs
`"$AUTORESEARCH_CLI" softmax bootstrap-champion --build-dir <build dir>`, which uploads it and
promotes it through the normal promotion, without an experiment. So the first league
submission is the game's starter policy, unevaluated. Check it with
`"$AUTORESEARCH_CLI" promotion status` and `"$AUTORESEARCH_CLI" softmax leaderboard`.

Where things live:

| What | Where |
|---|---|
| Profile home | `$PROFILE/` |
| Canonical workspace (git) | `$PROFILE/workspace/` |
| Durable ledger (committed, pushed) | `$PROFILE/workspace/.supervisor/ledger/` |
| Pause state | `$PROFILE/workspace/.supervisor/ledger/pause.json` (exists only while paused) |
| Coach hold | `$PROFILE/workspace/.supervisor/ledger/coach-hold.json` (exists only while the coach holds research) |
| Agent started (`AUTORESEARCH_AUTONOMY=on-request`; permanent once written) | `$PROFILE/workspace/.supervisor/ledger/started.json` |
| Spend-cap stops of a card (committed) | `$PROFILE/workspace/.supervisor/ledger/health/spend/<task-id>.json` |
| Hermes session usage and cost estimates (read by the spend caps) | `$PROFILE/state.db` (`sessions`) |
| IDE coach chat budget: the day's first snapshot of the chat sessions' cost | `$PROFILE/local/chat-spend.json` |
| IDE coach chat event log | `$PROFILE/workspace/.supervisor/runtime/chat_events.jsonl` |
| GotA compiler child sessions, one full copy per build, not pruned yet (read by `report baseline`) | `$PROFILE/gota-compiler/<build id>-<suffix>/state.db` |
| Disposable runtime state | `$PROFILE/workspace/.supervisor/runtime/` |
| Commit provenance journal and its key (not committed) | `$PROFILE/workspace/.git/autoresearch/provenance.jsonl`, `provenance.key` |
| Per-experiment locks (`softmax xp create`, `upload`, `submit`) | `$PROFILE/workspace/.supervisor/runtime/locks/xp-<E-id>.lock` |
| queue-keeper log | `$PROFILE/workspace/.supervisor/runtime/keeper.log` |
| Permit asks whose yes was already used (xp-poll's cache; disposable, one small entry per ask) | `$PROFILE/workspace/.supervisor/runtime/permit_answers.json` |
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
`keeper_step_failed` signal. A card reprioritize or drop request that fails is contained the
same way: it is retried, and after 3 failing runs given up with one help request. The agent pauses itself when preflight fails (identity,
expired or rejected credential, low disk, low OpenRouter credit, wrong host Kanban config). It sends
a help request. It resumes by itself when preflight passes again. When Softmax cannot be
reached to check the credential (network error, timeout, server error), queue-keeper skips
that run instead; only the third such run in a row pauses, with one help request. It also
pauses when today's estimated spend reaches `AUTORESEARCH_DAILY_BUDGET_USD`, and then resumes
only once the spend is under the budget again (the next UTC day). The spend caps still run
while the agent is paused and on a skipped run, because running cards keep running.

Workers wait on long commands without model calls: a build or local check runs with the
terminal tool's `background: true` and one `process_manage` wait of up to 1800 s
(`terminal.timeout` in the profile config, which caps a wait). Hermes also ends any single
tool call after 420 s, which would cut that wait, so the profile sets
`timeouts.tools.sequential_call` and `timeouts.tools.concurrent_batch` to 1900 (the wait plus
margin). The run budget
(`agent.run_budget_seconds`, 5400 s) is long enough for a build card to wait out its gates;
bootstrap raises the earlier shipped defaults (2700 s, Hermes' 180 s for
`terminal.timeout`, Hermes' 420 s for the two tool timeouts) to these values and keeps any other value you set. The raise is one-way:
if you want exactly 2700, 180 or 420, set a nearby value (2701, 181, 421), or bootstrap raises it again. The plugin refuses foreground
`sleep` of 30 s or more and loops that sleep while checking a process.

The champion is whatever policy the Softmax league shows competing for our player
(amendment A9); the promotion ledger only records what the agent submitted and why. When the
league shows something other than what the agent expects (a policy submitted by hand, a
retired or disqualified champion, or our own submission admitted but still not champion
`AUTORESEARCH_CHAMPION_GRACE_MINUTES` after queue-keeper first saw it admitted),
queue-keeper pauses the agent and sends one help request with both policies in its `about`.
A submission still qualifying is not a conflict (one still pending after 24 hours gets a
help request); one the league rejects closes its promotion (stage `rejected`) and is reported
once: a note, or for the first champion (the starter policy) a help request that says what to
run. While a conflict is open, `softmax submit` and `softmax bootstrap-champion` refuse. It stays paused until they
agree: either make the league show the expected policy again, or accept what it shows with
`"$AUTORESEARCH_CLI" promotion accept-champion` (in an operator shell, above; agents cannot
run it). It resumes on the
next queue-keeper run after that.

On Softmax hosting the agent acts with a `cga_` agent token, and the coach's IDE Controls
govern it. Two of their settings can make Softmax refuse what the agent asks (HTTP 403), and
the agent then waits for the coach instead of failing:

- **Ask before** (`askBefore`) for spending credits (XP requests) or league submissions.
  Softmax files a permit ask for each request and holds it. Once the coach has set the agent
  up (chosen a league or saved rules), Softmax asks before every action until the coach saves
  rules. The agent tries every request of an XP batch
  in one run, so all of the batch's asks appear at once, and the coach gets one help request
  per batch, "Waiting for your approval in the IDE Controls", with the number of asks. The
  coach approves them there. To stop the asking per request, remove `spend` from Ask before
  (spends are then allowed up to the daily credits) or replace it with `spend-over-N` (asks
  only for a spend of more than N credits); for submissions, remove `submit`.
- **The league choice.** The agent plays only the league its coach chose. With no league
  chosen, or another one, the coach gets one help request per league and cause, "Choose the
  agent's league in the IDE Controls", naming the league to choose (the game's league; to play
  another one, change the game's configuration instead).

The runtime reads the coach's answers itself, every run, with the agent token
(`GET /v2/cogents/<player>/c/asks/<key>`), so waiting costs no model calls:

- An XP batch the coach refused leaves its experiment `awaiting-permit` (with
  `lifecycle.permit_wait`; `awaiting-credits` is the same wait for a credit refusal, and
  `stopped` is where a no ends it, below). When every ask of the batch is approved, xp-poll requests one
  relaunch card at once; it sends each refused request again with the same body and
  idempotency key (the card names the exact `--batch`, `--attempt` and `--batches`, and
  `xp create` refuses any other batch while the asks wait with `permit_batch_mismatch`, and
  refuses with `permit_league_changed` if the game's league configuration changed meanwhile), so Softmax
  grants it against its yes and creates nothing twice. Every ask stays tracked until its
  request is created: each refusal is saved before the next request is sent, a rerun that
  stops early (credits ran out, or a no) keeps the asks it did not reach, and a new ask
  the server files for a request replaces that request's old one. An experiment with any ask
  open is always `awaiting-permit`: when credits also ran out in the batch, that is recorded in
  the wait (`credits_short`), and the relaunch waits for every yes and then for the credits,
  never for the credits alone (a known balance too low for the rest keeps it waiting). A
  request over today's cap waits for the next UTC day, whatever the coach answers before
  then. A relaunch whose price moved past an earlier yes gets new asks, and with them a new
  help request. A relaunch card that ends without creating the requests and without a new
  refusal is tried again for the same batch, at most 3 times in all; then the coach gets one
  help request and no more cards are made for that wait.
- A refused submission leaves its promotion at `intent` with `waiting` set
  (`"$AUTORESEARCH_CLI" promotion status` shows it). After the yes, queue-keeper's reconcile
  submits the same policy version once; no other promotion starts meanwhile.
- **A no ends it.** An experiment with a declined ask goes `stopped` (its results so far are
  kept, unreviewed, and `softmax xp create` refuses it with `experiment_closed`; the batch
  stops at the first refusal that reports the no); a declined submission drops its
  promotion (`aborted`, the slot is free; no repair card is made for its holder, and
  `softmax submit` refuses that experiment or version again with `coach_declined`). Either way the coach gets one note, and nothing is
  asked or retried again for it. A declined first champion (the starter policy) leaves no
  champion, so research is paused: no new bootstrap runs until an operator runs
  `"$AUTORESEARCH_CLI" promotion accept-champion`, as after a rejected one.
- An ask still unanswered 24 hours after it was first sent gets one reminder help request,
  never more.
- Only when an answer cannot be read, and for a league refusal, does the agent fall back to a
  backoff: `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` (default 30) for experiments, 30 minutes for
  submissions, doubling, at most 8 hours. A retried submission that fails waits for the next
  step too.

To cancel one research task, send an inbound `control` with `{"action": "cancel", "task": "<original inbound task id>"}`.
The runtime closes that request against new admission, recovery, and late result publication, and requests shutdown through the existing queue.
Its reply says **stop requested**: native worker cleanup can take a few minutes. This does not stop the VM or gateway.
It preserves canonical work and unrelated cards. Cancelling a planning-backlog item withdraws that item without stopping a shared planning card.
Cancellation is processed before credit and identity preflight, so those failures do not prevent stopping work.
Canonical provenance checks and inbox-writer validation still apply; pause/resume and new work remain gated by preflight.

A coach pauses and resumes research from the IDE chat with the exact lines
`/pause-research` and `/resume-research`, for which the plugin sends an inbound `control` message
(`{"action": "pause"}` or `{"action": "resume"}`); an operator can send the same with
`"$AUTORESEARCH_CLI" comms inbound control --json '{"action": "pause"}' --author coach:<player id>`.
Queue-keeper applies pause/resume on its next run after preflight passes. A pause/resume control sent while preflight is failing (for example, the key is out of credit) is applied, and answered with its note, only on the first run whose preflight passes. A pause sets the coach hold,
`.supervisor/ledger/coach-hold.json` (`{"held_at", "by", "tasks"}`: `by` is the message's
author, `tasks` the pending cards it unassigned and their assignees): no card is admitted, no
pending card is dispatched, and the agent plans nothing; running cards finish their work
under their spend caps, and a card that blocks meanwhile stays blocked (its one requeue waits for the resume, as it does under the agent's own pause). A resume removes the file and assigns those cards again. Each is
answered with a runtime note in reply ("Research paused at your request." or "Research
resumed."; a resume with no hold says nothing was paused). `status --json` shows the hold as
`coach_hold`. The hold is independent of the agent's own pauses (preflight, daily budget,
champion conflict, `pause.json`): queue-keeper never clears the hold, and it does not lift its
own pause while the hold is set. A resume clears only the hold: if the agent is also paused
for its own reason, it stays paused (the note says so), the cards the hold unassigned wait in
`pause.json`, and it resumes by itself once that cause clears.
While the hold is set, or before the coach's first task starts an agent with
`AUTORESEARCH_AUTONOMY=on-request`, xp-poll makes no relaunch card (for credits or for the
coach's permit asks); everything that spends nothing goes on: evidence and review requests,
the help requests and the reminder, reading the coach's answers, and stopping an experiment
on a no. A relaunch that is due is made on the first xp-poll run after the hold is lifted.

Only the runtime commits on the canonical workspace's `main` (C1): queue-keeper, xp-poll,
health, bootstrap, `card finish` (the handoff commit), and `comms inbound` (also the IDE coach
chat's confirmed send, which runs it). Each of them records every commit
it makes in a journal outside the work tree (`.git/autoresearch/provenance.jsonl`, entries
numbered in sequence and signed with a key in `provenance.key`; a signed entry copied to the
end again, for example to hide a reset, vouches for nothing). Before it acts, every queue-keeper run checks the
first-parent history of `main` since the last checked commit. A commit nobody recorded (for
example one a card worker made itself, working around the plugin), a rewritten history
(reset, rebase, amend), or a missing or unverifiable journal pauses the agent and sends one
help request; that run then only applies the spend caps, and nothing is merged, published or
admitted until a person checks. While the violation stands, no job pushes `main` to
`AUTORESEARCH_WORKSPACE_REMOTE` (`provenance.push_main` checks one commit and pushes exactly
that commit, under the canonical lock), so the unrecorded commits stay on the host; the help request still reaches the coach
because the bridge reads `comms/outbox/` from the local workspace. Pushing resumes after
`accept-commits`. In an operator shell:

```sh
"$AUTORESEARCH_CLI" runtime check-commits
git -C "$PROFILE/workspace" show --stat <commit>
"$AUTORESEARCH_CLI" runtime accept-commits <commit>
```

`check-commits` lists the commits the runtime did not make (id, author, time, files) and the
commit to accept; it changes nothing. Keep the commits, or undo them in the workspace (a
revert is one more commit to accept; do not reset, which is reported as a rewrite), then run
`accept-commits` with the commit `check-commits` names: it trusts that commit and everything
before it, and the next queue-keeper run resumes the agent. It reads its entry back before it
reports success and fails loudly if the journal does not return it (a journal line cut short
by a killed writer is ended before the next entry, so it never swallows that entry). `accept-commits` is refused
for agents (best effort: plugin policy, approvals deny list, and the command refuses in a
card or any agent session; see Known gaps), and every accept is announced: queue-keeper
publishes one note per accept ("Workspace commits accepted"), so an accept nobody ran is
visible. A check that cannot run (an unreadable journal or key, also when bootstrap or one
of its own commits finds it so, or a git error) also pauses the agent and still applies the spend caps.
Every run first checks that the journal can be written (without writing to it), and a journal
failure in any later step of the run pauses the same way; a step that
keeps failing gets its usual help request. The pause unassigns pending cards even when its
own commit fails (a journal that cannot be written fails every canonical commit, so then the
help request cannot be published either and is only logged and retried; the uncommitted
`pause.json` holds the pause until the journal is fixed). A runtime writer
builds each commit before it moves `main` and first records an intent naming that exact
commit, so a crash between moving `main` and the journal entry is recognized, not reported,
and the next queue-keeper run writes the missing entry and sets the index for that commit;
an intent vouches for no other commit (one made on the same parent with the same subject is
reported). The first run of this check on an existing workspace, and a newly
seeded or cloned one, trusts the current `HEAD` once and commits
`.supervisor/ledger/provenance.json`; once that marker has been in history, a lost journal
is reported, not trusted again.

What this check is, and is not. It catches accidental and naive writes to canonical (a worker
committing by hand, as on 2026-10-09). It does not stop a deliberate one: card workers run as
the same OS user as the runtime, so a process that imports the runtime and calls its own commit
function (`ledger.commit`), or reads `provenance.key`, produces commits signed like the
runtime's, with no accept and no announcement. The plugin refuses model tool calls that do this
openly, but that is policy, not a boundary. Truncating the journal (cutting off its newest
entries) is also not detectable from inside the workspace. Real prevention needs card workers
under a different user than the owner of canonical, or coach requests by API (D9 options (c)
or (d) in `docs/plans/2026-10-09-softmax-integration/plan.md`); for the first hosted deploy
James accepted D9 option (a), detection only.

Known gaps of the provenance check:

- **A deliberate same-uid writer defeats it** (above): it can sign commits through the
  runtime's own code or the key, and truncate the journal. Fixing this needs D9 option (c) or (d).
- **The plugin's refusals are best effort.** Paths built at run time, variables, script files
  and other interpreters get past the interpreter-write and journal checks; the provenance
  check is what catches a commit.
- **No help request while the journal cannot be written.** Publishing is a canonical commit,
  and every canonical commit needs the journal. The run still pauses and unassigns pending
  cards, but the help request is only logged (`.supervisor/runtime/keeper.log`) and retried. Check that log
  for `provenance` failures when the agent is paused with no message.
- **A board epoch can be left uncommitted.** If the journal fails exactly when bootstrap
  creates the board, `board.json` gets the new epoch on disk but not in a commit.
- **Seeding fails on a journal error.** A brand new workspace whose first (seed) commit
  cannot be recorded fails bootstrap; there are no cards to pause yet, and the next run
  retries.
- **A crash during a restore gives a false journal-loss pause.** If bootstrap clones a
  workspace from `AUTORESEARCH_WORKSPACE_REMOTE` and dies before the journal is
  initialized, the next run finds the committed marker but no local anchor. It reports
  `journal_lost`, pauses and holds pushes. This fails safe. Remedy, in an operator shell:
  check that the workspace is exactly the remote's `main`
  (`git -C "$PROFILE/workspace" rev-parse HEAD` against
  `git -C "$PROFILE/workspace" ls-remote origin refs/heads/main`), then run
  `"$AUTORESEARCH_CLI" runtime accept-commits <that commit>`.

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
   not seed a new history. The commit provenance journal is local to the host, so the new
   host trusts the cloned history once (Operate, above): whatever is on the remote's `main`
   is trusted, so check the remote's recent history before a restore if the old host may
   have been tampered with.
3. Copy the memory snapshot back:

```sh
mkdir -p "$PROFILE/memories"
cp "$PROFILE/workspace/.supervisor/hermes-memory.md" "$PROFILE/memories/MEMORY.md"
```

4. Resume the cron jobs. The board is new on a new host, so queue-keeper starts a new board
   epoch and rebuilds the cards that were admitted but not finished (C12).

## Update

```sh
hermes profile update "$PROFILE_NAME" -y
```

This replaces the files the distribution owns (code, doctrine, skills, cron job definitions)
and keeps `.env`, `config.yaml`, memories, the workspace, and `local/`. The next
`queue-keeper` run re-applies the configuration rules in contract C9 with
`hermes config set` and re-seeds `AGENTS.md` when `doctrine/VERSION` changed. Hermes has no
automatic update; `updates.check` is set to false so it does not even check.

After an install or update, check once that agent terminals carry the plugin's agent-session
marker (`runtime accept-commits` refuses agents by it; this rests on reading the Hermes
source and is not yet observed live). In an agent session of the profile (a chat with
`hermes -p "$PROFILE_NAME" chat`, or a card worker), have the agent run
`"$AUTORESEARCH_CLI" runtime session`. It must print `"agent_session": true`. The same
command in an operator shell prints `false`.

### IDE coach chat

On Softmax hosting each coach line in the IDE chat is one Hermes run on the API server
(platform `api_server`), in this profile. The plugin turns those runs into the coach's
interface to the research (design: [coach-chat-role.md](docs/designs/coach-chat-role.md);
code: `src/autoresearch/plugin/coach.py`, `plugin/proposals.py`). Kanban workers (platform
`cli`) are unchanged.

- **Persona.** For an `api_server` turn the plugin's `pre_llm_call` injects the brief
  `doctrine/coach-chat.md` (once per session: Hermes keeps injected context in the history).
  `SOUL.md` names both roles.
- **Tools.** Config (C9 rows, re-applied by bootstrap) gives `api_server` only the toolset
  `autoresearch_coach` and none of the shipped skills, and keeps that toolset off for `cli`
  (`platform_toolsets.api_server`, `known_plugin_toolsets.cli`,
  `skills.platform_disabled.api_server`; bootstrap adds the coach toolset to
  `known_plugin_toolsets.cli` and keeps the other entries Hermes wrote there). The tools are fixed-argv read commands of the
  profile CLI, with no shell (`list_messages`, `get_message`, `message_status`,
  `resolve_ref`, `list_cards`, `show_card`, `find_records`, `show_record`,
  `promotion_status`, `status`, `baseline_report`; output cut to 12,000 characters), and
  `propose_inbound`. Because Hermes's config load fails open to the full default toolset, the
  plugin's `pre_tool_call` also blocks every other tool in a chat session (Hermes's
  tool-search bridge passes only as far as it reaches coach tools), and the coach tools refuse
  outside one or under `HERMES_KANBAN_TASK`. A chat turn gets at most 8 tool rounds (model
  calls with tools). The tool names are the design's (`status`, `list_cards`, ...); since they
  are generic, the plugin checks that Hermes's tool registry maps each to its own handler, and
  if another plugin owns any of them, every coach tool is refused and the chat says so.
- **Profile environment.** The tools run inside the one multiplexed gateway process. They
  find the routed profile with Hermes's `get_hermes_home()` at call time, refuse unless it
  carries this plugin (`plugins/autoresearch/plugin.yaml`) and a `.env`, and run
  `AUTORESEARCH_CLI` with the gateway's environment minus `HERMES_*`, `AUTORESEARCH_*` and
  `SOFTMAX_*` names, plus the profile `.env`, plus `HERMES_HOME=<profile>`.
- **Sending needs the coach's own line.** No model tool call can run `comms inbound`.
  `propose_inbound(kind, payload, summary)` (`task`, `steer`, `reply` or `control`; only the
  kind's schema fields) validates the payload with `comms inbound --dry-run` and keeps the
  proposal in plugin memory for 15 minutes, bound to the session. The plugin's
  `transform_llm_output` hook puts its own rendering first in the reply: the payload exactly as
  it will be sent, as JSON in a fenced code block (so Markdown or HTML in a value shows as
  text), with control and format characters such as bidi overrides and zero-width spaces shown
  as `\uXXXX` escapes, never the model's summary, ending `To send exactly this, reply: confirm
  P-xxxx`. Unicode default-ignorable characters (variation selectors, tag characters, Hangul
  fillers) are escaped too, as JSON escapes, so the block parses back to the exact payload.
  The plugin's block comes first in the reply, then a horizontal rule, then the model's text,
  so nothing the model writes can hide it. All model text in a reply that carries a plugin
  block is escaped (line endings normalized, `&` and `<` escaped on every line, so inline
  code shows entities such as `&lt;` literally). Only after that is the proposal
  confirmable, and it is shown again in every reply while it is pending, so the plugin's
  rendering is shown first, before the model's text, for that id. When the coach's whole line (surrounding
  whitespace stripped) is `confirm P-xxxx` in that session, the plugin runs `comms inbound
  <kind> --json <payload> --author coach:<AUTORESEARCH_PLAYER_ID>` itself, once. The send runs
  on a plugin thread (it can wait for the canonical lock longer than Hermes's 30 s hook
  timeout). Sends to one profile run one at a time, in the order the coach typed them (one
  queue per profile), so a slow pause can never land after a later resume; the turn says "Sending P-xxxx", and the next reply carries `Sent P-xxxx as I-...`
  or the failure. The send runs in its own process group, killed whole after 300 s; a send that
  timed out or broke unexpectedly is reported as "may or may not have been sent", never as
  "nothing was sent"; only a validation answer or a bad-arguments exit from the CLI is reported
  as "Nothing was sent". A send killed at the timeout can leave a stale
  `workspace/.git/index.lock`: if later commits fail with "index.lock exists", check that no
  `git` process is running in the workspace, then remove the file. `cancel P-xxxx` drops it. Any other line, another session, an
  expired, cancelled or never-shown proposal sends nothing. State is keyed by profile and
  session. A gateway restart drops all proposals and any send outcome not yet shown. A context
  compression inside a turn starts a new session id: the coach tools refuse for the rest of
  that turn (fails closed) and the next turn records the new id and injects the brief again.
- **Budget.** `AUTORESEARCH_CHAT_DAILY_USD` (default 3) per UTC day, from the rise of the chat
  sessions' Hermes cost since the day's first snapshot (`$PROFILE/local/chat-spend.json`,
  module `runtime/state_db.py`). Over it, every coach tool but `status` refuses. A spend that
  cannot be read refuses too.
- **Log.** `workspace/.supervisor/runtime/chat_events.jsonl`: chat turns, read tools (name and `ok`), proposals (id and
  kind), shown renderings, sends (proposal id and inbound id). Payload text is not logged.

## Softmax hosting

How the profile runs when Softmax hosts it for a coach (plan:
[plan.md](docs/plans/2026-10-09-softmax-integration/plan.md), sections 3.4-3.7 and decisions D1-D15). The
profile side described here is built on this branch. The hosting runtime (image, installer, key budget, bridge) is
metta code that the plan describes and that is not part of this repository; where a sentence below
depends on it, it says "per the plan", and you should check the deployed metta version before relying on it.

### For coaches

- **Set up your player's Controls first.** Before you do, the server applies no league choice and no
  daily credit cap to the hosted agent's `cga_` token. In the IDE Controls set the league (Gods of
  the Arena), what the agent must ask before (submission, uploads and XP requests are allowed without
  asking when the list is empty), and `dailyCredits` (the most XP credits it may use per day). Add
  asks whenever you want to approve more.
- **Research starts when you ask.** With `AUTORESEARCH_AUTONOMY=on-request` (the hosted setting) the agent plans
  nothing until your first accepted `task`. After that it plans on its own, permanently
  (`.supervisor/ledger/started.json`). A `steer` or task-cancellation control does not start it.
- **Ask the chat what is going on.** The chat reads the workspace (`status`, `baseline_report`,
  cards, records, messages; see "IDE coach chat" above). It cannot change anything by itself.
- **Send work with a proposal.** Ask the chat for a `task`, `steer`, `reply` or `control`. The plugin shows
  the exact message first in its reply, ending `To send exactly this, reply: confirm P-xxxx`. Nothing is
  sent until your whole line is `confirm P-xxxx` in the same chat session, within 15 minutes. `cancel P-xxxx`
  drops it. The send runs in the background, so the reply to `confirm` says "Sending P-xxxx" and the
  next reply tells you whether it was sent.
- **Stop and resume research.** Type `/pause-research` (hold: no new cards are admitted or dispatched, nothing
  is planned; running cards finish under their caps) and `/resume-research`. A resume does not override
  the agent's own pause (credit floor, daily budget, champion conflict); it says so and the agent
  resumes when that cause clears. Cancelling one task in the IDE stops only that task: it arrives as a
  `steer`. The chat's stop button (`/stop`) stops only the chat turn that is running. Both are bridge
  behavior, per the plan, and neither pauses research.
- **Budgets you will meet.** The chat has its own cap, `AUTORESEARCH_CHAT_DAILY_USD` (default $3 per UTC
  day): over it the chat's tools refuse except `status`, and research is not affected. Research has the
  caps in the table under "Configure `.env`".

### Budgets on hosting

| Limit | Value | Who enforces it | What happens at the limit |
|---|---|---|---|
| OpenRouter key | $500 per week, resets Monday 00:00 UTC, expires after 30 days (per the plan; existing keys of $10 stay until the operator raises them) | OpenRouter | Model calls are refused. The credit floor (below) pauses research first. |
| Credit floor | `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD`, default $10 | preflight, every queue-keeper run | The agent pauses with one help request and resumes by itself once the key's remaining credit is back above the floor (for example after the weekly reset). A coach hold is never cleared by this. |
| Research per day | `AUTORESEARCH_DAILY_BUDGET_USD`, default $100 | queue-keeper | Dispatch pauses with one help request; resumes the next UTC day. At $100 a day the weekly $500 lasts about 5 days. |
| Per card | 4 runs and $4 | queue-keeper | The card is blocked with a help request. |
| IDE coach chat | `AUTORESEARCH_CHAT_DAILY_USD`, default $3 | the plugin | Chat tools refuse except `status`. |
| XP credits | the player's `dailyCredits` | the Softmax server, once Controls are set | Experiments are refused for credits and relaunched later (A10). |

The spend figures are Hermes's estimates for this profile's own sessions. GotA compiler child runs are not counted
by the daily and card caps; `report baseline` shows them separately, and the key's real usage is in `status`.

### What the runtime writes and where things live

Per the plan (3.4), the hosting runtime writes the profile `.env` (mode 0600): `SOFTMAX_PLAYER_CREDENTIAL` (the
workspace's `cga_` token), `AUTORESEARCH_PLAYER_ID`, `AUTORESEARCH_WORKSPACE_REMOTE`
(`/opt/data/autoresearch-workspace.git`), `COGAMES_API_URL`, `OPENROUTER_API_KEY`, and the hosted settings
`AUTORESEARCH_GAME=gota`, `AUTORESEARCH_AUTONOMY=on-request`, `TMPDIR=/opt/data/tmp` and the GotA tool paths.
It does not set `AUTORESEARCH_INBOX_WRITERS` or any spend-cap name. The three variables the plugin
and cron scripts read (`AUTORESEARCH_AUTONOMY`, `AUTORESEARCH_CHAT_DAILY_USD`, `TMPDIR`) are on
`terminal.env_passthrough` in `config.yaml`, which bootstrap keeps in place.

| What | Where (hosted) |
|---|---|
| Profile | `/opt/data/profiles/optimizer` (install name `optimizer`; the Kanban board is still named `autoresearch`) |
| Canonical workspace and the relay path the bridge reads and writes | `/opt/data/profiles/optimizer/workspace` |
| Everything else | the table under "Operate", relative to that profile |

### Looking at the agent

The chat's `status` and `baseline_report` tools, or in an operator shell `"$AUTORESEARCH_CLI" status --json` and
`"$AUTORESEARCH_CLI" report baseline --since <YYYY-MM-DD> --json` (see "Operate"). `status` shows the
installed version and commit, whether the last profile update failed (`distribution.update_failed`), pause
state, the coach hold, whether the agent has started, spend against the budget, the key's remaining credit,
running cards and the champion.

### Updates

Per the plan: the profile is baked into the hosting image and installed as `optimizer` when a workspace
starts. A workspace that starts on a newer image gets `hermes profile update`, after which `.env`, the workspace and
the board stay and the cron jobs run again. Until the plan's later milestone (M4) a new profile version needs a
new image, which is a host replacement with workspace moves. Two things to know:

- **A failed update keeps serving.** The runtime logs it, leaves a `.softmax-update-failed` marker and carries on with the
  installed profile; `status` reports it. `hermes profile update` copies files in steps, so a failed update
  can leave the profile partly updated. That is a known gap until M4.
- **Replacing the earlier `rohit-optimizer` profile moves its Hermes state aside.** This is the behavior in the
  hosting runtime PRs (Metta-AI/metta #27216), not yet deployed. Ours is installed fresh into a staging directory first. Only when that install succeeds is the old profile moved to
  `/opt/data/profiles/.optimizer-replaced-<UTC time>` and ours renamed into its place. The agent therefore
  starts without the earlier chat context, while the IDE can still show the old transcript. If the install
  fails, the runtime logs it and the `rohit-optimizer` profile stays in place and keeps serving chat; the next
  start tries again. With no earlier profile, a failed install stops the start, since there is nothing to serve.

### What is not isolated

Everything in the sandbox runs as one uid (plan D9): the gateway, the bridge, the coach chat and the research
workers. `AUTORESEARCH_INBOX_WRITERS` is not set on hosting, because there is no second user to name. The
commit provenance check (A15, merged in PR #24; "Operate" above) pauses the agent on a commit to canonical
`main` that the runtime did not make, but it is detection of accidental writes only (see its Known gaps): a research
process can still write the inbox, sign a commit through the runtime's own code, or run `comms inbound`, and the
inbound messages carry the coach's authority. The `OPENROUTER_API_KEY` and `cga_` token in the profile `.env` are readable by
the same processes. See [softmax-ide-bridge-compat.md](docs/integrations/softmax-ide-bridge-compat.md), "Trust: who
may write the inbox".

### If the token leaks or must stop

The `cga_` token does not expire. Revoke the workspace's agent (the `CogentAgent` named
`hermes-<provisioning hex>`) in Softmax. Per the plan, the host controller's next `/whoami` check then stops the
sandbox and reports `revoked`; until it does, the agent pauses itself with a help request when Softmax rejects the token. Revoking the
OpenRouter key stops model calls the same way the credit floor does.

## Known gaps

- **GotA is bound but not yet proven end to end.** `games/gota` has real `build`,
  `local_check`, `evaluate` and `upload` bindings (Plan 02 Task 2.5). The build follows the
  live engine. The compiler passed M1 on 2026.10.7.2 only; a newer engine has no semantic
  qualification yet, and no real research cycle has run on GotA
  ([acceptance-checklist.md](docs/plans/findings/acceptance-checklist.md), section 3.2).
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is set to false. Do not run `hermes curator adopt` on a shipped
  skill.
- **Credits.** The Player reads the XP credit balance over HTTP
  (`GET /observatory/usage/me/credits`), because coworld 0.1.58 has no `credits` command yet.
  Preflight records it and warns at 0. An experiment refused for credits relaunches when the
  balance covers its remaining requests (the recent cost per episode, or
  `AUTORESEARCH_RELAUNCH_MIN_CREDITS`), at most one experiment per xp-poll run; otherwise the
  backoff decides (A10).
- **The league choice is not read.** The runtime reads the coach's answers to permit asks,
  but not the league choice, so after a league refusal it retries on the backoff (see the IDE
  Controls paragraphs in Operate).
- **Gateway turn hooks** of the plugin are verified for multiplexed API-server runs on the
  pinned image (the coach chat, [findings.md](docs/plans/2026-10-09-softmax-integration/findings.md)
  "Verified in Task 5 Step 4"); other gateway paths are not (hermes-facts.md, item 2).
- **The coach chat's proposal text reaches the coach through the reply.** The plugin shows
  its rendering first (again on every reply while pending), but the model's own words below it could
  still misdescribe it, and a reply
  the bridge replaces ("Stopped at your request.") marks a rendering shown that the coach
  never saw (safe: the coach cannot type an id they did not see). A deterministic bridge-side
  rendering is a later bridge change (coach-chat-role.md §3.1). Everything in the sandbox runs
  as one uid, so a research process could still write the inbox or call the API server
  directly; the chat design does not change that (plan D9).
- **One OS user, so no guard inside the profile is a security boundary.** Card workers,
  queue-keeper and the other jobs all run as the workspace user. The plugin refuses canonical
  writes from the shell, file tools and literal interpreter code (`python -c`, `python3 -
  <<EOF`, `execute_code`), but a constructed path, a script file run by name or another
  interpreter gets past it, and `canonical.lock` is advisory. The commit provenance check
  (Operate, above) detects a commit on canonical `main` that the runtime did not make, after
  the fact. It is detection, not prevention, and it can be defeated on purpose. The OS
  cannot hide the journal key from workers (same user, and `card finish` in the worker writes
  the journal); the plugin refuses model tool calls that name the journal, its key or the
  provenance module, and `runtime accept-commits` refuses in any agent session, but both are
  policy, so a process that gets the key anyway can write valid entries, and a deliberate
  agent can assemble the command and clear its environment. Each accept is announced by a
  runtime note, so a forged one is visible but not prevented. Deleting the journal
  is reported, not trusted again, once the ledger marker has been in history; only a history
  rewrite that also removes the marker resets the trust, and that is itself loud. The check
  sees commits, not files: a file a worker writes into a directory that a runtime commit
  picks up whole (`comms/outbox`, the receipts directory, `comms/inbox` by design) arrives
  in a recorded commit. Doctrine D-27 forbids working around any refusal. The real fix is to
  run card workers as a separate OS user without write access to canonical (Rohit's
  sandbox).
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
- **A card worker can still write the canonical workspace through interpreter code.** On
  2026-10-09 a worker committed its own receipt to canonical `main` from a `python3 - <<EOF`
  script (`admit Q-... (manual admission repair ...)`), so `card finish` took the handoff of a
  card that skipped admission. Since then: the plugin refuses literal interpreter writes to
  canonical (best effort), `card finish` refuses a receipt whose request was never committed,
  foreign cards are decided from the ledger and never merged (above), and the commit
  provenance check pauses the agent on any commit the runtime did not make (Operate, and the
  one-OS-user bullet above). A worker that writes canonical another way can still forge
  files, but its commit is detected.
- **A malformed ledger file stops a step.** Routing, handoff processing and rebuild parse every
  committed receipt and handoff; one file that is not valid JSON (only a direct write as above
  can produce one) fails that step every run until a human fixes or removes it.
- **A board rebuild (C12) gives re-admitted cards new task ids,** so their run and cost
  counts start again from zero.
- `runtime/kanban_events.jsonl` is read in full on each queue-keeper run, and the daily sum
  scans `state.db` `sessions`; both are small today but grow without rotation.

## Integrations

| Document | Covers |
|---|---|
| [`docs/integrations/softmax-ide-bridge.md`](docs/integrations/softmax-ide-bridge.md) | The Softmax IDE bridge as the external agent on `comms/`: the inbox files it writes, the outbox fields it reads, how it treats acknowledgements, and what is not exercised yet |
| [`docs/integrations/softmax-ide-bridge-compat.md`](docs/integrations/softmax-ide-bridge-compat.md) | What the distribution guarantees the bridge, notes for the bridge (acknowledgement and follow-up rules), and the trust model for who may write the inbox |
