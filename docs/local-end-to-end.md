# Running the research profiles end to end on a Mac

How to run the rebuilt `orchestrator` and `researcher` profiles on your own machine in a setup
that is as close to Softmax hosting as a laptop gets: a real Hermes gateway multiplexing both
profiles with one API key, one shared `IDE_WORKSPACE`, a per-profile `.env`, the seam the
bridge speaks, and the watcher ticking. `docs/hosting.md` says what production provides; this
page says how to stand the same thing up locally and what still differs.

Everything here runs through one harness, `core/tests/hermes/test_seam_real_hermes.py`, which
is also the acceptance scenario ("submit the starter as my champion" ends in a submission). It
passed on October 28, 2026 (Pacific) with Hermes v0.21.6 and `moonshotai/kimi-k3` through
OpenRouter: 7 watcher ticks, 38 seconds after the gateway was ready.

| Option | What you get | When |
|---|---|---|
| A. One command | The acceptance scenario on a real gateway with the fake platform; exits when done | A quick check that the whole chain works |
| B. Interactive session | The same setup kept running: seam URL, `curl` lines, the watcher every minute, Ctrl-C to stop; optionally the real platform | Poking at the profiles by hand |
| C. Inside the hosted image | Option A or B inside the Hermes image CI uses, with the checkout mounted read-only and the state on a volume | The closest to production on a laptop |

## Prerequisites

- `uv` and `git`. Run everything from the repository root after `uv sync` (the workspace
  environment plus pytest and ruff, Python 3.12).
- A local Hermes v0.21.6. Its own dependencies need Python 3.14, so build it in an isolated
  environment of the Hermes checkout rather than installing it globally:

  ```bash
  cd ~/coding/tools/hermes-agent-v0.21.6
  UV_PROJECT_ENVIRONMENT=~/coding/tools/hermes-env-0.21.6 uv sync --frozen --no-dev --extra messaging
  export HERMES_BIN=~/coding/tools/hermes-env-0.21.6/bin/hermes
  ```

  `--extra messaging` matters: it brings `aiohttp`, without which the gateway starts but its
  API server does not, and nothing in the harness can reach it. The harness runs `prepare.py`
  with the `python` next to `HERMES_BIN`, which is Hermes's own interpreter, as hosting does.
  The tests skip when `HERMES_BIN` is unset or does not name a file.
- An OpenRouter key in `OPENROUTER_API_KEY`. Every turn calls the model (the default is
  `moonshotai/kimi-k3`, from `orchestrator/distribution.yaml`). The metta token broker vends
  one under the scope `openrouter.inference`; the shape, from `docs/spikes/s1-dispatch.md`, is

  ```bash
  cd ~/coding/metta && python3 scripts/token_broker_client.py exec --scope openrouter.inference \
    --reason "local end-to-end run of the IDE research profiles" -- \
    env HERMES_BIN=$HERMES_BIN uv run --project ~/coding/ide-distributions \
      pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s
  ```

  The key reaches only the child command; it is never printed or written outside the run's
  temporary `.env` files.
- For the real platform (Option B with `IDE_E2E_PLATFORM=real`) a **test** Player's
  `SOFTMAX_PLAYER_ID` and `SOFTMAX_PLAYER_CREDENTIAL` (a bare `cga_` token, or a player session
  as JSON; see `researcher/distribution.yaml`), `COGAMES_API_URL` when it is not
  `https://softmax.com/api`, and `IDE_DEFAULT_COWORLD`, the Coworld to start on. The scenario
  makes a real submission as that Player and spends model calls doing it. Check the credential
  first:

  ```bash
  SOFTMAX_PLAYER_CREDENTIAL=... COGAMES_API_URL=... uv run research softmax whoami
  ```

## What every option sets up

The harness builds, in a fresh temporary directory, what hosting builds in a sandbox:

| In the harness | In hosting (`docs/hosting.md`) |
|---|---|
| `host/`, the gateway's `HERMES_HOME`, with `.env` and `profiles/orchestrator`, `profiles/researcher` installed with `hermes profile install <checkout>/<role> --name <role> --yes` | One sandbox, one profile home per role, installed from the release checkout |
| One `API_SERVER_KEY` (32 hex characters) in the host's and both profiles' `.env`, with `API_SERVER_ENABLED`, `API_SERVER_HOST=127.0.0.1` and a free `API_SERVER_PORT` | The same key in every profile (Hermes authenticates `/p/<profile>/v1/runs` with that profile's key) |
| `IDE_WORKSPACE=<root>/workspace` in every `.env`, plus `IDE_PLATFORM`, `SOFTMAX_PLAYER_ID`, `IDE_RESEARCHER_PROFILES=researcher` and `OPENROUTER_API_KEY` | The per-profile `.env` hosting writes |
| `prepare.py --source <checkout>/<role> --hermes $HERMES_BIN` for each role, which records the core path, creates the stores, applies the protected config keys, installs the watcher job paused and builds the workspace environment (`uv sync`) under `workspace/tools/venv` | The same script, run after every install and update; the environment is prebuilt at image build |
| `hermes gateway run` with `GATEWAY_MULTIPLEX_PROFILES=true`, ready when `GET /v1/capabilities` answers 200; its output in `gateway.log` | The multiplexing gateway |
| `SeamServer` (`research serve`'s class) in the pytest process, bound to a free loopback port | Metta's bridge |
| Watcher ticks run in process: every 5 seconds during the scenario, every 60 seconds in an interactive session; the Hermes cron job stays paused | The `watcher` cron job every minute inside Hermes |

The gateway enables its API server from the host home's `.env`, not from the process
environment, and under multiplexing each profile's `.env` reaches the plugin through Hermes's
secret scope (`research_core.plugin.executor.profile_env`), which is exactly what the
"submission was made as the profile's Player" assertion checks.

## Option A: the one-command run

```bash
cd ~/coding/ide-distributions
HERMES_BIN=$HERMES_BIN OPENROUTER_API_KEY=... \
  uv run pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s
```

`-s` shows the harness's output as it happens; without it you see nothing until the end. The
first run builds the workspace environment with `uv sync`, which takes a few minutes; later runs
reuse uv's cache but still build into the new temporary workspace. Install and preparation of
both profiles took 67 seconds and the gateway was ready 3 seconds after start on October 28
(`docs/spikes/s1-dispatch.md`).

What it prints:

```
gateway log: <root>/gateway.log
profiles: <root>/host/profiles
workspace: <root>/workspace
chat run run_<hex> (start)
tick 1: {"paused": false, "has_work": false, "errors": [], "steps": {...}}
tick 7: ...
7 ticks, 38 s
active Coworld: cow_fake
notices: [...]
submissions by Player: {'ply_canary': 1}
```

`<root>` is pytest's temporary directory for the run (something like
`/private/var/folders/.../pytest-of-<you>/pytest-<n>/acceptance0`); pytest keeps the last three,
so the paths stay readable after the run. `--basetemp=<dir>` puts the run where you choose
(pytest removes that directory first if it exists). The `tick` lines show every sixth watcher
report. A second test in the module, `test_transcripts_are_where_the_output_says`, then checks
that the gateway log and the Orchestrator's `state.db` exist.

How to read a failure:

- `gateway exited <code>; see <root>/gateway.log` or `gateway not ready within 180 s`: open the
  log. The usual cause is a Hermes environment without `--extra messaging` (the gateway runs,
  the API server does not).
- `prepare <role>:` followed by the script's output: preparation failed, most often `uv sync`
  (network, or a lock that does not match).
- `no submission after <n> s; gateway log <path>, notices [...]`: the chat run or the setup wake
  did not get to a submission within `SCENARIO_TIMEOUT_S` (600 seconds). Read the gateway log
  around the run id printed as `chat run run_...`, the notices, both stores' events
  (`research store events --after 0`, below) and the fake platform's state file
  `<root>/workspace/.state/fake_platform.json` (`counts`, `policies`, `submissions`).
- `the submission was not made as the profile's Player ply_canary: {'ply_fake': 1}`: the
  tools ran without the profile's `.env` (the fake's default Player is `ply_fake`); that is the
  secret-scope path in `executor.profile_env`.
- `assert active["coworld_id"] == "cow_fake"`: the Orchestrator never switched to the fake
  Coworld, or switched away.

## Option B: an interactive session that stays up

Four environment variables turn the harness into a session you can talk to. All are off by
default, so CI runs the acceptance scenario exactly as before.

| Variable | Effect |
|---|---|
| `IDE_E2E_KEEP=1` | After the scenario (pass or fail) the gateway and the seam server stay up. The harness prints the URLs, where the API key is, ready-to-paste `curl` lines and the `research` commands for this run, opens a chat Session if the scenario did not, ticks the watcher every 60 seconds like the cron job, and stops the seam server and the gateway on Ctrl-C. |
| `IDE_E2E_SCENARIO=0` | Sends no request: an empty prepared workspace to talk to. Only useful with `IDE_E2E_KEEP=1` (alone, the module skips). |
| `IDE_E2E_PLATFORM=real` | The real Softmax platform instead of the fake; see below. |
| `IDE_E2E_TIMEOUT_S=<seconds>` | Replaces the 600-second scenario timeout (a real build takes longer than the fake). |

Keep the session after the scenario:

```bash
IDE_E2E_KEEP=1 HERMES_BIN=$HERMES_BIN OPENROUTER_API_KEY=... \
  uv run pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s
```

An empty workspace, no request sent:

```bash
IDE_E2E_KEEP=1 IDE_E2E_SCENARIO=0 HERMES_BIN=$HERMES_BIN OPENROUTER_API_KEY=... \
  uv run pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s
```

The banner the harness prints (paths and ports are those of the run; the key itself is never
printed, the shell reads it from the profile's `.env`):

```
=== interactive session (IDE_E2E_KEEP=1); Ctrl-C stops the gateway ===
seam (research serve routes):  http://127.0.0.1:<port>
gateway (Hermes API server):   http://127.0.0.1:<port>  routes under /p/<profile>/
API key:                       <root>/host/profiles/orchestrator/.env  (API_SERVER_KEY, same in every profile)
gateway log:                   <root>/gateway.log
profile homes:                 <root>/host/profiles
workspace (IDE_WORKSPACE):     <root>/workspace
workspace environment:         <root>/workspace/tools/venv
chat session:                  api_<epoch>_<hex>

research CLI from the workspace environment ...:
  export HERMES_HOME=<root>/host/profiles/orchestrator IDE_WORKSPACE=<root>/workspace IDE_PLATFORM=fake SOFTMAX_PLAYER_ID=ply_canary IDE_RESEARCHER_PROFILES=researcher
  <root>/workspace/tools/venv/bin/research watcher status
  ...

curl against the seam:
  curl -s -X POST http://127.0.0.1:<seam>/session-open -H 'Content-Type: application/json' -d '{"client": {"id": "curl", "kind": "web-ide"}, "actor": "user:local"}'
  curl -s -X POST http://127.0.0.1:<seam>/message -H 'Content-Type: application/json' -d '{"session_id": "api_...", "message": "What is the state of the workspace?", "actor": "user:local", "context": {"contract_version": 1, "on_screen": []}}'
  curl -s http://127.0.0.1:<seam>/runs/<run_id>
  curl -s 'http://127.0.0.1:<seam>/snapshot?store=workspace'
  curl -sN 'http://127.0.0.1:<seam>/stream?store=workspace&after=0'

curl against the gateway (a fresh session id, so it never races the chat Session):
  KEY=$(grep '^API_SERVER_KEY=' <root>/host/profiles/orchestrator/.env | cut -d= -f2-)
  curl -s -X POST http://127.0.0.1:<gateway>/p/orchestrator/v1/runs -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d '{"input": "Reply with READY and stop.", "session_id": "api_..."}'
  curl -s http://127.0.0.1:<gateway>/p/orchestrator/v1/runs/<run_id> -H "Authorization: Bearer $KEY"

The watcher ticks every 60 s below, as the cron job would.
[10:54:37 PDT] watcher tick 1: has_work=False paused=False errors=none
```

The seam's routes are those of `research serve` (`core/src/research_core/serve.py`): `POST
/session-open`, `/message`, `/ui-action`, `/store/apply`; `GET /snapshot`, `/stream`,
`/runs/<id>`, `/health`. `/message` answers `{session_id, run_id, delivered, problems, seq}`;
`delivered` is `start` for a new run and `steer` when the Session already had a live one (the
bridge's per-Session rule). `/stream` is Server-Sent Events with `id: <seq>` and the event JSON
in `data:`; `?store=coworld` streams the active Coworld's store. `core/src/research_core/client.py`
(`ScriptedClient`) is the same conversation in Python.

Ctrl-C ends the loop, the seam server shuts down, the module's fixture terminates the gateway,
and the second test runs. If the scenario had failed, pytest reports that failure after the
session ends; the run directory stays for reading.

### The real platform

```bash
IDE_E2E_KEEP=1 IDE_E2E_PLATFORM=real IDE_E2E_TIMEOUT_S=1800 \
  SOFTMAX_PLAYER_ID=ply_... SOFTMAX_PLAYER_CREDENTIAL=... COGAMES_API_URL=https://softmax.com/api \
  IDE_DEFAULT_COWORLD=cow_... \
  HERMES_BIN=$HERMES_BIN OPENROUTER_API_KEY=... \
  uv run pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s
```

With `IDE_E2E_PLATFORM=real` the harness does not configure the fake platform and writes
`IDE_PLATFORM=real` plus `SOFTMAX_PLAYER_ID`, `SOFTMAX_PLAYER_CREDENTIAL`, `COGAMES_API_URL` and
`IDE_DEFAULT_COWORLD` (those that are set) from your environment into the host's and both
profiles' `.env`; `prepare.py` then registers and activates the default Coworld on the fresh
workspace, as hosting does, so the fixed request applies to it. Without `IDE_DEFAULT_COWORLD`
the request "Submit the starter as my champion." names no Coworld; use `IDE_E2E_SCENARIO=0` and
send a request that does.

The scenario's assertions were kept, read through the real client: it records the Player's
submissions on the ladder when it starts and passes when a submission that was not there
before appears (`RealPlatform.submissions(player_id=...)`), and the active Coworld must be
`IDE_DEFAULT_COWORLD` when that is set. The per-Player check is implied by the ladder's Player
filter (the real `Submission` record carries no Player id), so a run with the real platform
proves the chain ends in a submission as the test Player, not that the fake's `.env` isolation
holds; the fake run checks that. The harness never prints the credential; it is in the run's
`.env` files, so delete the run directory (or use `--basetemp` somewhere private) when done.
`SOFTMAX_USER_CREDENTIAL` is not written: User-identity reads report themselves unavailable,
as in a sandbox before provisioning.

## Option C: inside the hosted Hermes image

CI runs the `hermes`-marked tests inside `nousresearch/hermes-agent@sha256:9774f4f3...` (the
digest in `.github/workflows/ci.yml`), whose Python runs the plugin and `prepare.py` exactly as
hosting does. The same harness runs there on your Mac, with the checkout mounted read-only like
the release checkout and the homes, the workspace and the gateway log on a volume.

**Not verified on this machine.** The Docker daemon was not running when this page was written,
so the steps below are assembled from `ci.yml` and the image's Dockerfile, not from a run. What
is known: the digest is a multi-arch manifest list with `linux/amd64` and `linux/arm64` images,
so it runs natively on Apple silicon; CI runs `prepare.py` end to end in it
(`test_prepare_real.py`), but CI has no model key, so the gateway part of the harness has only
run on the Mac. Expect to adjust.

```bash
docker volume create ide-e2e-data
docker run --rm -it --name ide-e2e --user 0 --entrypoint /bin/bash \
  -v "$HOME/coding/ide-distributions:/src:ro" \
  -v ide-e2e-data:/opt/data \
  -e HERMES_BIN=/opt/hermes/.venv/bin/hermes \
  -e OPENROUTER_API_KEY \
  -e IDE_E2E_KEEP=1 \
  -e UV_PROJECT_ENVIRONMENT=/opt/data/dev-venv -e UV_CACHE_DIR=/opt/data/uv-cache \
  -e GIT_AUTHOR_NAME="IDE local" -e GIT_AUTHOR_EMAIL=ide-local@example.invalid \
  -e GIT_COMMITTER_NAME="IDE local" -e GIT_COMMITTER_EMAIL=ide-local@example.invalid \
  nousresearch/hermes-agent@sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784
```

Then, inside the container:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH=/root/.local/bin:$PATH
git config --global --add safe.directory /src
cd /src && uv sync --frozen
uv run pytest -q -m hermes core/tests/hermes/test_seam_real_hermes.py -s \
  -p no:cacheprovider --basetemp=/opt/data/e2e/$(date +%Y%m%d-%H%M%S)
```

What each piece is for:

- `--user 0 --entrypoint /bin/bash`: what a GitHub Actions job container does with this image
  (CI passes `--user 0` and does not run the image's s6 init). Everything runs as root and
  `HERMES_BIN` is the venv binary directly, as in CI, not the `hermes` shim on `PATH` that drops
  to the image's `hermes` user.
- `/src:ro`: the checkout the way hosting mounts the release checkout; `workspace.doctor` then
  reports `core_path.writable: false` as in production. `prepare.py` writes nothing there; it
  records the core path in the profile and builds the workspace environment under the
  workspace. The S1 spike (`test_spike_s1.py`) writes its report into `docs/spikes/`, so it
  cannot run against a read-only mount.
- `UV_PROJECT_ENVIRONMENT=/opt/data/dev-venv`: `uv sync` for the dev environment cannot write
  `/src/.venv`. `prepare.py` sets its own `UV_PROJECT_ENVIRONMENT` for the workspace
  environment, so this does not interfere. `UV_CACHE_DIR` on the volume keeps the downloads
  (including the CPython 3.12 uv fetches for the workspace environment) across containers.
- `uv` is not on the image's `PATH`; CI installs it with `astral-sh/setup-uv`, the installer
  line does the same for the container.
- `GIT_*`: the Orchestrator commits in the workspace's code repository; the container has no git
  identity of its own (CI sets the same variables).
- `-p no:cacheprovider`: pytest cannot write `.pytest_cache` under a read-only root directory.
- `--basetemp` on the volume: the run's `host/`, `workspace/` and `gateway.log` survive the
  container. Inspect a running session from a second shell with `docker exec -it ide-e2e bash`
  and the commands the banner printed; the seam and the gateway listen on the container's
  loopback only.

What still differs from production, even here:

- **gVisor.** Docker's default runtime, not gVisor; the S3 check (a second process reading a
  WAL store under gVisor) is not exercised.
- **Sleep and drain.** Nothing puts the container to sleep: `sleep_through_jobs`,
  `lifecycle.activity`, the cron catch-up after a wake and the first tick's resume of drained
  Sessions do not run. `research workspace drain` can be run by hand from the workspace
  environment with the Orchestrator's variables exported.
- **The bridge.** `research serve`'s `SeamServer` in the pytest process plays metta's bridge;
  the clients are `curl` and `ScriptedClient`, not the IDE.
- **Player provisioning.** Hosting selects the Player at workspace creation and mints and
  refreshes `SOFTMAX_PLAYER_CREDENTIAL` (a player session lasts 24 hours); here you paste a test
  Player's credential and nothing refreshes it. `SOFTMAX_USER_CREDENTIAL` is absent.
- **The watcher.** Production runs it as a Hermes cron job every minute (enabled by
  `prepare.py --activate-supervisor`); the harness leaves the job paused and ticks in process.
- **The process model.** Production runs the image's s6 init with the supervised gateway as
  the unprivileged `hermes` user and a prebuilt workspace environment (`IDE_WORKSPACE_ENV`);
  this runs as root with the entrypoint replaced and builds the environment at prepare time.
- **The release.** Options A and B use your checkout and the Hermes v0.21.6 environment you
  built; Option C uses the checkout read-only and the Hermes the image ships.

## What to look at

With the variables the banner printed exported (`HERMES_HOME` is the Orchestrator profile home,
`IDE_WORKSPACE` the workspace; in a real-platform run `SOFTMAX_PLAYER_CREDENTIAL` is already in
your shell), from the workspace environment:

```bash
RESEARCH=$IDE_WORKSPACE/tools/venv/bin/research
$RESEARCH watcher status                                # {"heartbeat", "heartbeat_age_s", "has_work", "paused", "paused_drained"}
$RESEARCH tool workspace --args '{"action": "doctor"}'  # commits, watcher heartbeat, both stores' schema, capabilities, CLIs, core_path.writable, api_keys, problems
$RESEARCH store snapshot --store workspace              # coworlds, sessions, notices, directives, settings, ui_state, and seq
$RESEARCH store snapshot                                # the active Coworld's store: goals, tasks, claims, versions, champion, ...
$RESEARCH store events --after 0 --store workspace      # the workspace store's events as JSON lines; --follow tails
$RESEARCH store events --after 0                        # the active Coworld's events
$RESEARCH workspace drain                               # stop Researcher runs and mark their Sessions to resume
```

`research store snapshot` without `--store` refuses until a Coworld is active ("no active
Coworld; pass --store workspace, or switch to a Coworld first"). The same `research watcher
tick` the cron job runs can be run by hand, but in an interactive session the harness is already
ticking.

On disk:

- `<root>/gateway.log`: everything the gateway wrote, the chat run, the wake runs and the
  plugin's tool calls included.
- `<root>/host/profiles/orchestrator/state.db` (and the Researcher's): Hermes's own session
  store; `sqlite3 <path> .tables` to look around. `GET /p/<profile>/api/sessions/<id>/messages`
  on the gateway is the same history over HTTP.
- `<root>/host/profiles/<role>/.env`, `config.yaml`, `local/core-path.txt` and the
  Orchestrator's `cron/jobs.json` (the `watcher` job, paused): what `prepare.py` wrote.
- `<root>/workspace/.state/store.db` and `<root>/workspace/<coworld>/.state/store.db`: the
  stores the `research store` commands read; `<root>/workspace/.state/fake_platform.json`: the
  fake platform's state in a fake run.
