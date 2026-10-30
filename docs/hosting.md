# Hosting the research distributions

Softmax hosting runs the `orchestrator` and `researcher` profiles in one sandbox per user. The
profiles own the agents' behaviour, records, tools, skills and the watcher; hosting owns
credentials, the image, the Hermes gateway, storage, sleep and the bridge. This page is the
handoff: what the rebuilt distributions need from hosting, and what they guarantee in return.
The design is `docs/designs/ide-profile-refactor-2026-10-28-final.html` (Appendix G lists the
metta changes); the build decisions are in `docs/plans/ide-profile-refactor/00-overview.md`.

## What the image must contain

- The release checkout of this repository, read-only to the sandbox user: `core/` (the shared
  package), the root `pyproject.toml` and `uv.lock` (the workspace environment), `orchestrator/`
  and `researcher/`. The plugin imports `research_core` from `core/src` at run time, the
  workspace environment installs it from `core/`, and `prepare.py` finds both beside the profile
  folder it was given with `--source`. A writable checkout would let an agent change the store
  library or the watcher (design Q24); `workspace.doctor` reports whether the core path is
  writable.
- Hermes v0.21.6 or later with its interpreter at a fixed path. The plugin, `prepare.py` and the
  watcher launcher are stdlib-only Python and run under that interpreter.
- `uv`, `git`, and the toolchains Coworld mixins declare (today: none shipped; a mixin may ask
  for another Python or Nim, which `prepare.py`/setup install under `$IDE_WORKSPACE/tools/`).

## Install and prepare

```
hermes profile install <checkout>/orchestrator --name orchestrator --yes
hermes profile install <checkout>/researcher --name researcher --yes     # one or several
<hermes python> <profile>/scripts/prepare.py --source <checkout>/<folder> --hermes <hermes> [--activate-supervisor] [--check]
```

Run `prepare.py` after every install and every `hermes profile update`, with
`HERMES_HOME=<profile home>` (Hermes sets it for anything it runs for a profile; set it yourself
when you call the script). With `IDE_WORKSPACE` unset the workspace defaults to
`$HERMES_REAL_HOME/workspace`, so set `IDE_WORKSPACE` explicitly. It:

1. writes `<profile>/local/core-path.txt` (where the plugin finds `research_core`);
2. creates or migrates the workspace store and every registered Coworld store;
3. re-applies the config keys the machinery relies on (`approvals.mode: off`, no deny list,
   memory off, `tools.tool_search.enabled: off`, the plugin and toolset, `cron.catch_up_missed`,
   the Orchestrator's compaction settings), removes retired keys, points every profile at the
   active Coworld (`skills.external_dirs`, `skills.trusted_project_dirs`, `terminal.cwd`), and
   applies shipped values only where the owner kept an earlier default;
4. reconciles the Orchestrator's `watcher` cron job (kept paused until `--activate-supervisor`
   on the first preparation);
5. builds the workspace environment with `uv sync --frozen --no-dev --project <checkout>` into
   `$IDE_WORKSPACE/tools/venv`, or verifies a prebuilt one named by `IDE_WORKSPACE_ENV` against
   the lock's sha256;
6. on a fresh Orchestrator workspace with `IDE_DEFAULT_COWORLD` set, registers and activates that
   Coworld so its setup wake runs before the first chat.

`--check` prints what it would change and writes nothing. The 300-second budget hosting gives
the script holds when the workspace environment is prebuilt; a first `uv sync` takes a few
minutes and should happen at image build (then set `IDE_WORKSPACE_ENV`).

## Environment per profile (`.env`)

| Variable | Who sets it | Meaning |
|---|---|---|
| `IDE_WORKSPACE` | hosting | The one workspace root of the sandbox, identical for every profile. It holds the workspace environment, the stores and one folder per Coworld; it must live on the durable, backed-up volume. |
| `IDE_WORKSPACE_ENV` | hosting (optional) | A prebuilt workspace environment. |
| `IDE_DEFAULT_COWORLD` | hosting (optional) | The Coworld a new workspace starts with. |
| `IDE_RESEARCHER_PROFILES` | hosting | Comma-separated Researcher profile names (`researcher`, or `researcher-1,researcher-2`). |
| `SOFTMAX_PLAYER_ID`, `SOFTMAX_PLAYER_CREDENTIAL` | hosting | The Player the workspace acts as, selected at workspace creation, and its credential. Both profiles read them. |
| `SOFTMAX_USER_CREDENTIAL` | hosting, Orchestrator only | The user's User identity token (design B.16). Optional until provisioned; without it User-identity reads and writes are reported unavailable. It is deliberately not passed to terminal commands. |
| `COGAMES_API_URL` | hosting | Softmax API server. |
| `API_SERVER_KEY`, `API_SERVER_HOST`, `API_SERVER_PORT` | hosting | The Hermes API server. The watcher reads them from the Orchestrator profile's `.env` (Hermes strips the key from cron environments). **Every profile must carry the same `API_SERVER_KEY`**: Hermes authenticates `/p/<profile>/v1/runs` with that profile's key. |
| `OPENROUTER_API_KEY` | hosting | The OpenRouter key both profiles use. |
| `OPENROUTER_MODEL` | hosting (optional) | The chat model for this profile, such as `moonshotai/kimi-k3`; `prepare.py` writes it to Hermes's `model.default` on every run, so each profile's `.env` can name a different model. Unset, the shipped default applies (and a model set by hand with `hermes config` survives updates). The fallback model and Hermes's auxiliary models stay as `config.yaml` ships them. |
| `IDE_PLATFORM`, `IDE_FAKE_FAULTS` | tests only | `fake` selects the platform test double. |

## Gateway, sleep and drain

- The gateway must multiplex profiles (`/p/<profile>/…`): the watcher starts Researcher runs and
  wake Sessions through `POST /p/<profile>/v1/runs` and reads `GET /v1/runs/{id}`.
- Turns must be serialised per Session, not per profile (Appendix G). The watcher never starts a
  second run on a Session that is live: it steers instead.
- `sleep_through_jobs` must list `{"orchestrator": ["watcher"]}` so the one-minute tick never
  blocks automatic sleep by itself. The tick writes `has_work` into the workspace store's `meta`
  table (`research watcher status` prints it): a live Researcher run, a Task waiting on XP,
  credits, a submission or a process, an autonomous-submission candidate, or a queued wake.
  `lifecycle.activity` should read it as a reason that counts for automatic sleep only.
- A planned pause runs `research workspace drain` first (from the workspace environment with the
  Orchestrator's `.env`): it stops Researcher runs, marks their Sessions to resume, and returns
  when quiet. After a wake, Hermes's cron catch-up fires the watcher at once and its first tick
  resumes the drained Sessions before starting new work.
- Container health stays as it is: the watcher reports through its heartbeat and
  `workspace.doctor`, never through container liveness, so a broken tool cannot crash-loop.

### Background activity and autosleep

The host may run `scripts/activity.py` in the installed Orchestrator profile with the
profile's environment and Hermes's Python. It prints one JSON line, `{"waiting_on": [reason,
...]}`, from the watcher's last tick without polling the platform or writing a record: a live
Researcher run (`session:<id>:running`), a Task waiting on XP, credits, a submission or a
process (`task:T-n:waiting:<kind>`), an autonomous-submission candidate
(`submission:candidate`) or a queued wake (`wake:queued`). A non-empty list prevents automatic
sleep; an empty list permits it, and so does an explicitly paused workspace. A workspace that
was never prepared or has no store yet is empty. A non-zero exit or unreadable output must
prevent automatic sleep. The same list is `waiting_on` in `research watcher status`.

## The bridge (the seam)

The bridge does transport only. For each user message it POSTs `{"input": <message + "\n\nContext
v1:\n" + json>, "session_id"}` to the Orchestrator's runs route, where the JSON block is the
client's typed context validated against `research context-schema` and stamped with the client's
identity, the active Coworld and `contract_version: 1`. If the Session has a live run, it steers
instead of starting. It streams each store's `events` table to clients as Server-Sent Events
(`research store events --after <seq>` and `--follow` show the rows; one cursor per store), and
applies user edits through `research store apply --actor user:<id> <op> <json>`. Notices
(`events` rows of kind `notice`) must reach every client. `research serve` is the reference
implementation of all of this against a local Hermes. The full contract layer (`session-open`,
capabilities, panels, the agent-context panel) ships later in its own stack.

## Storage and backup

The durable state is `$IDE_WORKSPACE` (stores, code repositories, worktrees, documents) and each
profile's Hermes `state.db`. SQLite runs in WAL mode with `synchronous=FULL` on every writable
connection, so a committed record survives a crash of the process or the machine; the bridge may
read the stores with a read-only connection (check S3: WAL reads under gVisor from a second
process). Nothing on the volume survives losing the volume, so backups are part of the contract:

- **Hourly encrypted snapshots of the volume are the required minimum**, keeping the daily
  history as it is (a longer retention, not a shorter one). This bounds the loss at one hour of
  records, chat and action receipts. The design's accepted daily bound (R6) is replaced by this.
- `research store backup <dest-dir>` is a database-aware copy hosting may schedule more often
  than the snapshots, into separate storage. It copies the workspace store, every registered
  Coworld store and, with `HERMES_HOME` set, that profile's Hermes `state.db` with SQLite's
  backup API (never a raw copy of a live database and its WAL) into `<dest-dir>/<timestamp>/`
  with a `manifest.json` (sources, sizes, sha256, the workspace store's `last_seq`). Run it once
  per profile whose transcripts should be covered, or copy the other profiles' `state.db` files
  the same way.
- `research store restore-check <backup-dir>` is the restore test: it opens every copy
  read-only, runs `PRAGMA integrity_check`, compares the manifest's checksums and checks each
  store's schema against the running release, and prints a JSON verdict (exit 1 on any
  failure). Run it against a stored backup, not the one just written, as part of the hosting
  checks.
- A backup must cover the workspace store, every Coworld store, the Hermes transcript databases
  of every profile, and the volume's repositories, worktrees and documents (the volume snapshot
  covers the files; the records refer to them by path).
- Monitor the age of the latest restorable backup (the newest folder whose `restore-check`
  passed) and alert when it exceeds the loss window.

Before initiating a volume snapshot, run `research store quiesce --then <snapshot command>`, or
run `research store quiesce` in the background and initiate the snapshot when its first JSON
line appears. It checkpoints each database's WAL into its file, takes and holds the write lock
on the workspace store, every Coworld store and (with `HERMES_HOME` set) that profile's Hermes
`state.db`, fsyncs each file and its directory, and holds for at most 30 seconds (`--max-hold`;
a `--then` command still running is killed). Readers continue and writers wait, so the snapshot
is one consistent recovery point across the databases and the ordinary files on the volume.
Keep the hold short: a connection opened before the hold began (the seam server's, Hermes's own
to `state.db`) waits only its usual five seconds before reporting "database is locked", so
initiate the snapshot within that time; stores opened during the hold wait for the whole of
it. One
bounded risk remains: a repository may have a git write in flight from a tool process at that
moment. Git writes objects and refs through temporary files and renames, so such a snapshot
still restores to a readable repository, at worst with a stale `.lock` file or an unfinished
temporary object to remove.

The watcher's schema repair never replaces the store file: it rewrites the schema inside the
file in one transaction and first saves a copy next to it (`store.before-repair-<timestamp>.db`),
named in the notice. A store written by a newer release than the running image is left
untouched and the workspace is paused until the matching release runs again.

## Releases

A push to `main` touching `core/`, `orchestrator/`, `researcher/`, `pyproject.toml` or
`uv.lock` dispatches Metta's image release. A release ships only if the acceptance scenario
("submit the starter as my champion" on a new workspace ends in a submission) passes on the fake
platform in CI and on the release canary. The nightly read-only platform check
(`research platform check --read-only`) blocks a release when a route the tools use fails. The
daily lock refresh keeps `coworld` and `softmax-cli` current.

## Checks hosting should run once (design H "Validation")

- S1: several Researcher runs in one profile stay isolated; a Session resumes after a gateway
  restart (`core/tests/hermes/test_spike_s1.py`, needs a model key).
- S3: the bridge reads a store with WAL under gVisor.
- S4: whether the profile venv is writable (not needed any more: the workspace environment is
  the one `prepare.py` manages).
