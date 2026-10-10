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
  C1-C12 and amendments A1-A15. The contract numbers in this file refer to it.
- [Verified Hermes facts](docs/plans/findings/hermes-facts.md): what was checked on Hermes
  v0.21.5.
- [Softmax hosting integration](docs/designs/softmax-integration-plan-2026-10-09.html): how the
  profile goes into the hosted Hermes fleet (milestones M0-M5, decisions D1-D15). The task-level
  plan is [plan.md](docs/plans/2026-10-09-softmax-integration/plan.md), with evidence in
  [findings.md](docs/plans/2026-10-09-softmax-integration/findings.md). This is a plan: none of
  the hosted work is built yet. The coach chat it adds is designed in
  [coach-chat-role.md](docs/designs/coach-chat-role.md).

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
| `AUTORESEARCH_INBOX_WRITERS` | no | Comma-separated uids or user names allowed to write `comms/inbox/` directly (for example the Softmax IDE bridge's own user). When set, queue-keeper adopts only inbox files they own and rejects the rest. An unknown user name stops adoption (not routing) and fails the step `inbox_writers`; a valid but wrong user gets every direct write rejected for good. When unset, it adopts any valid file and logs that direct writes are unauthenticated: any process running as the workspace user could then send inbound messages, which carry the human's authority. Set it whenever an external agent writes the inbox. |
| `AUTORESEARCH_CARD_MODELS` | no | Model per card mode, as `mode=model` pairs separated by commas. Default `planning=z-ai/glm-5.3,scouting=z-ai/glm-5.3,maintenance=z-ai/glm-5.3`: mechanical cards run on the cheaper model, `investigation` and `experimentation` on `model.default`. A mode set to `default` (or the whole value `none`) uses `model.default`. queue-keeper pins the model when it creates the card (`hermes kanban create --model ... --provider openrouter`), so a change applies to new cards only; `hermes kanban --board autoresearch set-model <task> <model>` changes a card already on the board. A bad entry (unknown mode, no model) is skipped, so that mode uses `model.default`; queue-keeper logs it, records the step failure `card_models`, and sends one help request per distinct value. |
| `AUTORESEARCH_MAX_CARD_RUNS` | no | Runs per card. Default 4. queue-keeper stops a card that would start, or has started, one more run: it blocks it (`hermes kanban block --kind capability`, or `needs_input` when its last block was `capability`, so Hermes' unblock-loop breaker does not send it to triage; reason `spend-cap: ...`) and sends one help request. Only `running` and `ready` cards can be blocked; others are skipped. A stop that fails is retried every minute and reported once per failure streak; it grants no new allowance. A stopped card is never requeued or repaired; unblocking it gives it another full allowance. The blocked-card check (queue-keeper and `health`) also keeps a blocked card blocked, without requeue or repair, once it has reached this cap or the cost cap. The caps also run while preflight fails and the agent is paused. `0` turns the cap off. |
| `AUTORESEARCH_MAX_CARD_USD` | no | Estimated model spend per card, in USD. Default 4. Same stop as above. The estimate is Hermes' own (`state.db`, `sessions.estimated_cost_usd`), summed over the sessions of the card's runs and their compression and subagent sessions. `0` turns the cap off. |
| `AUTORESEARCH_DAILY_BUDGET_USD` | no | Estimated model spend per UTC day of every session of this profile. Default 100. At the budget queue-keeper pauses dispatch (the usual pause: pending cards unassigned, no admission) and sends one help request a day; running cards finish under their own caps. It resumes by itself on the next UTC day, or after you raise the value. `0` turns it off. |
| `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD` | no | Credit floor of the OpenRouter key, in USD. Default 10. Preflight reads the key's remaining credit (`limit_remaining` from `GET https://openrouter.ai/api/v1/key`) on every queue-keeper run and pauses the agent with a help request when it is below this floor, because Hermes retries a refused (402) model call forever. A fresh $10 key is not below the default; it pauses after its first spend. Set the floor lower when the key itself is small and should be spent further down. A value that is not a non-negative number uses the default and logs a preflight warning. |
| `AUTORESEARCH_FALLBACK_PRICES` | no | USD per million tokens for a session Hermes could not price (its estimate missing, for example when OpenRouter pricing was unreachable), as `kind=price` pairs. Default `input=3,output=12,cache_read=0.75,cache_write=3`, above kimi-k3's observed rate on purpose. queue-keeper logs each session priced this way once. |
| `AUTORESEARCH_CHAMPION_GRACE_MINUTES` | no | How long the league may take to make our admitted submission the champion (or, when its membership cannot be found, how long after the submit) before queue-keeper pauses the agent for a champion conflict (A9). Default 30. |
| `AUTORESEARCH_RELAUNCH_MIN_CREDITS` | no | Credits an experiment refused for credits needs before xp-poll relaunches it, when the cost per XP episode cannot be estimated from recent requests (A10). Default 100. |
| `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` | no | First wait before an experiment refused for credits is relaunched when the credit balance cannot be read, or when the refusal came with a positive balance (doubles per refusal, at most 8 hours). Default 30. The same wait applies to an experiment the coach's IDE Controls refused (`awaiting-permit`); a refused promotion submit always waits 30 minutes first. |
| `GOTA_G2_RUNNER` | no | GotA G2 gate episode runner. Default `native`: the pinned native match binary, no Docker needed. `docker` runs pinned `coworld run-episode` instead, which needs Docker. See `games/gota/GAME.md`, "G2 episode runner". |

Bootstrap writes `AUTORESEARCH_CLI` (the path of the `autoresearch` command) and
`HERMES_WRITE_SAFE_ROOT` (the canonical workspace) into the same `.env`. Do not set them
by hand.

A game can declare what its bindings need on the host in the `host_requirements` of its
`GAME.md` (GotA: `GOTA_HERMES_PYTHON`, `GOTA_TIRITH_BIN` and `nim` on `PATH`; see
`games/gota/GAME.md`, "Build and upload"). Put those in the profile `.env`, which worker
terminals read (for Nim, a `PATH=/path/to/nim/bin:${PATH}` line extends the PATH workers
start with; the braces are required, Hermes leaves a bare `$PATH` as literal text). The EC2 shim's provisioning writes them for GotA. Each run, queue-keeper
checks them as workers see them and, when one is missing, logs it and sends one help request
per distinct set; it does not pause.

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
| `queue-keeper` | every 1 minute | Bootstrap, preflight, merge of finished cards, publication, admission of new cards, planning, spend caps (per card and per day, see `AUTORESEARCH_MAX_CARD_RUNS` and the rows after it). The only writer of the canonical workspace. A card the runtime did not create is never merged, in any board epoch: a board task with no card body (for example one a model added with `kanban_create`), or a handoff whose request has no committed `cards/requests/<Q-id>.json` (a receipt its worker wrote). It gets one help request that says how to archive it, and the marker `.supervisor/ledger/foreign/<task>.json`; the spend caps still apply to it while it runs. Its card branch is still pushed to `origin` with the others (`refs/heads/card/*`), unmerged. `main` is pushed on its own and never forced; card branches are force-pushed (a retried card rebuilds its branch, so origin's copy is stale), and a card ref origin still refuses is logged once in `keeper.log`. One card that fails (unreadable on the board, or a blocked card the board will not requeue) does not stop the other cards in the same step; the step is still logged as failed. A merge or publication that fails and leaves canonical unchanged is handled the same way; one that leaves it changed stops the step, and the next run repairs it. If the board cannot list its foreign cards, the spend caps still apply to the other cards. A Hermes status the runtime does not know reads as claimed. |
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
[protocol-slice.md](docs/plans/findings/protocol-slice.md)), so v0 has no other queue. The
serial fallback was never built and its queue option was removed (amendment A13).
`FileQueue` remains only as the tests' stand-in for Kanban (`AUTORESEARCH_QUEUE=file` is a
test hook, like `AUTORESEARCH_SOFTMAX=fake`; never set it on a host: preflight warns when the
profile `.env` sets it).

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
| Spend-cap stops of a card (committed) | `$PROFILE/workspace/.supervisor/ledger/health/spend/<task-id>.json` |
| Hermes session usage and cost estimates (read by the spend caps) | `$PROFILE/state.db` (`sessions`) |
| Disposable runtime state | `$PROFILE/workspace/.supervisor/runtime/` |
| Commit provenance journal and its key (not committed) | `$PROFILE/workspace/.git/autoresearch/provenance.jsonl`, `provenance.key` |
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

- **Ask before** (`askBefore`) for XP requests or league submissions. Softmax files the request
  as a permit ask and holds it. The coach gets one help request, "Waiting for your approval in
  the IDE Controls", and approves the ask there. The agent retries the same request, with the
  same body and idempotency key, so the server decides it against the same ask and creates
  nothing twice.
- **The league choice.** The agent plays only the league its coach chose. With no league
  chosen, or another one, the coach gets one help request, "Choose the agent's league in the
  IDE Controls", naming the league to choose (the game's league; to play another one, change
  the game's configuration instead).

An XP request the coach refused leaves its experiment `awaiting-permit` (with
`lifecycle.permit_wait`); xp-poll sends the help request and relaunches the experiment after the
same backoff as a credit denial (30 minutes, doubling, at most 8 hours), so after the coach
answers it can take up to that long to go on. A refused submission leaves its promotion at
`intent` with `waiting` set (`"$AUTORESEARCH_CLI" promotion status` shows it); queue-keeper's
reconcile sends the help request and retries the same submission on the same backoff, and no
other promotion starts meanwhile. There is one help request per refused request or promotion,
however often it is refused again. Each experiment retry is a launch card, so an ask the coach
never answers costs one card per backoff step (at most 3 a day after the first day). If the
coach answers no, Softmax files a new ask at the next retry; to stop the work for good, send the
agent a task that says so.

Only the runtime commits on the canonical workspace's `main` (C1): queue-keeper, xp-poll,
health, bootstrap, and `card finish` (the handoff commit). Each of them records every commit
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
hermes profile update autoresearch -y
```

This replaces the files the distribution owns (code, doctrine, skills, cron job definitions)
and keeps `.env`, `config.yaml`, memories, the workspace, and `local/`. The next
`queue-keeper` run re-applies the configuration rules in contract C9 with
`hermes config set` and re-seeds `AGENTS.md` when `doctrine/VERSION` changed. Hermes has no
automatic update; `updates.check` is set to false so it does not even check.

After an install or update, check once that agent terminals carry the plugin's agent-session
marker (`runtime accept-commits` refuses agents by it; this rests on reading the Hermes
source and is not yet observed live). In an agent session of the profile (a chat with
`hermes -p autoresearch chat`, or a card worker), have the agent run
`"$AUTORESEARCH_CLI" runtime session`. It must print `"agent_session": true`. The same
command in an operator shell prints `false`.

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
- **The coach's answers are not read.** The agent cannot read whether the coach has answered
  a permit ask, so it retries on the backoff instead of right after the answer (see the IDE
  Controls paragraphs in Operate).
- **Gateway turn hooks** of the plugin are unverified (see hermes-facts.md, item 2).
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
