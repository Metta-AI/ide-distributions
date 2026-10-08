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
| Hermes Agent | v0.21.5 (tag `v2026.9.24`) | runs the cron jobs, the workers and Kanban |
| Nim | 2.2.10 | building the GotA replay tools (needed only for GotA) |

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

This creates the profile `autoresearch` under `$HERMES_HOME/profiles/`. Use that name
everywhere below. The Kanban board has the same name. The three cron jobs arrive paused.

## Configure `.env`

Create `$HERMES_HOME/profiles/autoresearch/.env` (mode 0600). Hermes never overwrites it.

```dotenv
OPENROUTER_API_KEY=sk-or-...
SOFTMAX_PLAYER_CREDENTIAL='{"token": "ply_...", "expires_at": "2026-10-08T12:00:00Z"}'
AUTORESEARCH_PLAYER_ID=ply_...
AUTORESEARCH_WORKSPACE_REMOTE=git@github.com:your-org/autoresearch-workspace.git
```

| Key | Required | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Key for the research model. Hermes keeps it from model commands. |
| `SOFTMAX_PLAYER_CREDENTIAL` | yes | The credential of the Softmax player the agent acts as. It is JSON with `token` and `expires_at` (ISO 8601): one entry of `player_sessions.<server>.cache` in the `~/.softmax/credentials.yaml` that `coworld player use <ply_id>` writes. A bare token is rejected. Player tokens last about 24 hours; preflight warns 2 hours before expiry and pauses at expiry. Format details: [softmax-client.md](docs/plans/findings/softmax-client.md). |
| `AUTORESEARCH_PLAYER_ID` | yes | Id of that player. Every Softmax call checks that the credential belongs to it. |
| `AUTORESEARCH_WORKSPACE_REMOTE` | yes | Git remote of the canonical workspace. **The repository must exist before the first run.** An empty repository is fine. A relative path is relative to `$HERMES_HOME`. |
| `OPENROUTER_MODEL` | no | Model id. Default `moonshotai/kimi-k3`, fallback `z-ai/glm-5.3`. |
| `AUTORESEARCH_GAME` | no | Directory name under `games/`. Default `gota`. |
| `AUTORESEARCH_WORKSPACE` | no | Path of the canonical workspace. Default `$HERMES_HOME/workspace`. |
| `AUTORESEARCH_SOFTMAX` | no | `fake` in tests: use the fake Softmax tool. Do not set it in production. |
| `AUTORESEARCH_QUEUE` | no | `file` selects the serial fallback queue (see below). |

Bootstrap writes `AUTORESEARCH_CLI` (the path of the `autoresearch` command) and
`HERMES_WRITE_SAFE_ROOT` (the canonical workspace) into the same `.env`. Do not set them
by hand.

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
| `queue-keeper` | every 1 minute | Bootstrap, preflight, merge of finished cards, publication, admission of new cards, planning. The only writer of the canonical workspace. |
| `xp-poll` | every 5 minutes | Collects the results of Softmax experience (XP) requests as evidence, and asks for review cards. |
| `health` | every 15 minutes | Checks blocked cards, free disk, and the credential; each problem becomes one help request per day. Log: `.supervisor/runtime/health.log`. |

To start a `queue-keeper` run now, without waiting for the schedule:

```sh
hermes -p autoresearch cron run queue-keeper
```

The first `queue-keeper` run syncs the venv (`uv sync` from `uv.lock`), writes the CLI shim,
creates the canonical workspace, pushes `main` to the remote, and creates the Kanban board.
Later runs skip each step that is already done.

## Kanban go/no-go switch

The default queue is Hermes Kanban. If Kanban does not work on a host, `AUTORESEARCH_QUEUE=file`
selects a serial queue that keeps its cards in the canonical runtime directory and runs one
card at a time:

```dotenv
AUTORESEARCH_QUEUE=file
```

Put it in the profile `.env`. It is on the passthrough list, so cron jobs receive it. Change
it only with the jobs paused. The serial fallback job itself is not shipped yet (see Known
gaps).

## Operate

Read state with the CLI. Use `"$AUTORESEARCH_CLI"` (it is in the profile `.env`):

```sh
"$AUTORESEARCH_CLI" queue list
"$AUTORESEARCH_CLI" comms list
"$AUTORESEARCH_CLI" promotion status
"$AUTORESEARCH_CLI" softmax whoami
```

Look at the Kanban board with `hermes kanban --board autoresearch list`.

Where things live:

| What | Where |
|---|---|
| Profile home | `$HERMES_HOME/profiles/autoresearch/` |
| Canonical workspace (git) | `<profile>/workspace/` |
| Durable ledger (committed, pushed) | `<profile>/workspace/.supervisor/ledger/` |
| Pause state | `<profile>/workspace/.supervisor/ledger/pause.json` (exists only while paused) |
| Disposable runtime state | `<profile>/workspace/.supervisor/runtime/` |
| queue-keeper log | `<profile>/workspace/.supervisor/runtime/keeper.log` |
| Hermes logs, cron output | `<profile>/logs/`, `<profile>/cron/` |
| Card worktrees | `<profile>/workspace/.worktrees/` |
| venv and CLI shim | `<profile>/local/venv/`, `<profile>/bin/autoresearch` |
| Staged skills (not adopted) | `<profile>/local/skills-staging/` |
| Hermes memory | `<profile>/memories/MEMORY.md` |
| Memory snapshot (committed) | `<profile>/workspace/.supervisor/hermes-memory.md` |

A step that fails does not stop the run. It writes a line to `keeper.log` and shows as a
`keeper_step_failed` signal. The agent pauses itself when preflight fails (identity,
expired credential, low disk, low OpenRouter credit, wrong host Kanban config). It sends
a help request. It resumes by itself when preflight passes again.

## Restore on a new host

The canonical workspace is a git repository, and queue-keeper pushes it to
`AUTORESEARCH_WORKSPACE_REMOTE` on each run (`main` and the `card/*` branches). The Hermes
memory is not in that repository by itself: queue-keeper copies it to
`.supervisor/hermes-memory.md` after each merge pass, and that file is pushed with `main`.

1. Install Hermes and the profile as above. Write the same `.env` (new credential if the
   old one expired).
2. Run bootstrap once. When the remote has a `main`, bootstrap clones it into
   `<profile>/workspace/`. It does not seed a new history.
3. Copy the memory snapshot back:

```sh
mkdir -p "$HERMES_HOME/profiles/autoresearch/memories"
cp "$HERMES_HOME/profiles/autoresearch/workspace/.supervisor/hermes-memory.md" \
   "$HERMES_HOME/profiles/autoresearch/memories/MEMORY.md"
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
  but the `research-serial` cron job and its pre-run script (Plan 01 Task 5.2) are built
  only if the Kanban go/no-go selects them.
- **The GotA game is not in `games/` yet** (Plan 02). Only `fakegame` and `_template`
  exist, so `AUTORESEARCH_GAME=gota` cannot run until Plan 02 ships it. Nim is needed
  for its replay tools.
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is set to false. Do not run `hermes curator adopt` on a shipped
  skill.
- **Credits.** A Player credential cannot read credits (HTTP 403). Preflight only warns,
  and experiments that wait for credits are retried with backoff (A10).
- **kimi-k3 tool calling inside Hermes is unverified** (A5). Re-check after the OpenRouter
  key is topped up.
- **Gateway turn hooks** of the plugin are unverified (see hermes-facts.md, item 2).
