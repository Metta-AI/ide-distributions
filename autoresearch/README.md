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
SOFTMAX_PLAYER_CREDENTIAL='{"token": "ply_...", "expires_at": "2026-10-08T12:00:00Z"}'
AUTORESEARCH_PLAYER_ID=ply_...
AUTORESEARCH_WORKSPACE_REMOTE=git@github.com:your-org/autoresearch-workspace.git
```

| Key | Required | Meaning |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Key for the research model. Hermes keeps it from model commands. |
| `SOFTMAX_PLAYER_CREDENTIAL` | yes | The credential of the Softmax player the agent acts as. It is JSON with `token` and `expires_at` (ISO 8601): one entry of `player_sessions.<server>.cache` in the `~/.softmax/credentials.yaml` that `coworld player use <ply_id>` writes. A bare token is rejected. Player tokens last about 24 hours; preflight warns 2 hours before expiry and pauses at expiry. Format details: [softmax-client.md](docs/plans/findings/softmax-client.md). |
| `AUTORESEARCH_PLAYER_ID` | yes | Id of that player. Every Softmax call checks that the credential belongs to it. |
| `AUTORESEARCH_WORKSPACE_REMOTE` | yes | Git remote of the canonical workspace. **The repository must exist before the first run.** An empty repository is fine. A relative path is relative to `$PROFILE`. |
| `OPENROUTER_MODEL` | no | Model id. Default `moonshotai/kimi-k3`, fallback `z-ai/glm-5.3`. When set, bootstrap writes it to `model.default` in the profile `config.yaml` (Hermes workers read the model only from there). |
| `AUTORESEARCH_GAME` | no | Directory name under `games/`. Default `gota`. |
| `AUTORESEARCH_WORKSPACE` | no | Path of the canonical workspace. Default `$PROFILE/workspace`. |
| `AUTORESEARCH_SOFTMAX` | no | `fake` in tests: use the fake Softmax tool. Do not set it in production. |
| `AUTORESEARCH_QUEUE` | no | `file` selects the serial fallback queue (see below). |
| `AUTORESEARCH_INBOX_WRITERS` | no | Comma-separated uids or user names allowed to write `comms/inbox/` directly (for example the Softmax IDE bridge's own user). When set, queue-keeper adopts only inbox files they own and rejects the rest. An unknown user name stops adoption (not routing) and fails the step `inbox_writers`; a valid but wrong user gets every direct write rejected for good. When unset, it adopts any valid file and logs that direct writes are unauthenticated: any process running as the workspace user could then send inbound messages, which carry the human's authority. Set it whenever an external agent writes the inbox. |
| `AUTORESEARCH_CREDIT_BACKOFF_MINUTES` | no | First wait before an experiment refused for credits is relaunched (doubles per refusal, at most 8 hours). Default 30. |

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
| `health` | every 15 minutes | Checks blocked cards (requeued once, then repaired; a `tool-fault:` block is never requeued and gets one help request per blocked run), free disk, and the credential; each problem becomes one help request per day. Log: `.supervisor/runtime/health.log`. |

To start a `queue-keeper` run at the next scheduler tick instead of waiting for the
schedule:

```sh
hermes -p autoresearch cron run queue-keeper
```

`cron run` only marks the job. A running gateway runs it on its next tick, or you can run
`hermes -p autoresearch cron tick` once by hand. To run only bootstrap (no cards). The subshell loads the profile `.env`, because the runtime
reads `AUTORESEARCH_WORKSPACE_REMOTE` and the other keys from the environment. `uv` must be
on `PATH`, because the launcher runs `uv sync`:

```sh
( set -a; . "$PROFILE/.env"; set +a; HERMES_HOME="$PROFILE" python3 "$PROFILE/scripts/queue_keeper.py" --bootstrap-only )
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

### Operator shell

`AUTORESEARCH_CLI` is only in the profile `.env`, and the CLI needs `HERMES_HOME` to be the
profile home. Softmax commands also need `SOFTMAX_PLAYER_CREDENTIAL` and
`AUTORESEARCH_PLAYER_ID`. Prepare each operator shell like this (after `PROFILE` is set):

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

Where things live:

| What | Where |
|---|---|
| Profile home | `$PROFILE/` |
| Canonical workspace (git) | `$PROFILE/workspace/` |
| Durable ledger (committed, pushed) | `$PROFILE/workspace/.supervisor/ledger/` |
| Pause state | `$PROFILE/workspace/.supervisor/ledger/pause.json` (exists only while paused) |
| Disposable runtime state | `$PROFILE/workspace/.supervisor/runtime/` |
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
expired credential, low disk, low OpenRouter credit, wrong host Kanban config). It sends
a help request. It resumes by itself when preflight passes again.

## Restore on a new host

Privacy note: the memory snapshot is committed to the workspace and pushed to
`AUTORESEARCH_WORKSPACE_REMOTE`. Anything the agent writes to memory is visible to everyone
with access to that remote. queue-keeper writes the snapshot only when the content changed.

The canonical workspace is a git repository, and queue-keeper pushes it to
`AUTORESEARCH_WORKSPACE_REMOTE` on each run (`main` and the `card/*` branches). The Hermes
memory is not in that repository by itself: queue-keeper copies it to
`.supervisor/hermes-memory.md` after each merge pass, and that file is pushed with `main`.

1. Install Hermes and the profile as above. Write the same `.env` (new credential if the
   old one expired).
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
- **GotA build and upload bindings are in progress.** `games/gota` is merged (Plan 02), but
  its real build and upload bindings are Plan 02 Task 2.5 (in progress). Nim is needed for
  its replay tools.
- **Curator.** Hermes has no key to exclude single skills from the curator. Shipped skills
  are safe because the curator only manages skills with an agent-created record, and
  `curator.prune_builtins` is set to false. Do not run `hermes curator adopt` on a shipped
  skill.
- **Credits.** A Player credential cannot read credits (HTTP 403). Preflight only warns,
  and experiments that wait for credits are retried with backoff (A10).
- **Gateway turn hooks** of the plugin are unverified (see hermes-facts.md, item 2).
