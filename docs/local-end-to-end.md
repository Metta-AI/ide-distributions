# Running the research profiles end to end on a Mac

How to run the rebuilt `orchestrator` and `researcher` profiles on your own machine in a setup
that is as close to Softmax hosting as a laptop gets: a real Hermes gateway multiplexing both
profiles with one API key, one shared `IDE_WORKSPACE`, a per-profile `.env`, the seam the
bridge speaks, and the watcher ticking. `docs/hosting.md` says what production provides; this
page says how to stand the same thing up locally and what still differs.

Options A and B run through one harness, `core/tests/hermes/test_seam_real_hermes.py`, which
is also the acceptance scenario ("submit the starter as my champion" ends in a submission). It
passed on October 28, 2026 (Pacific) with Hermes v0.21.6 and `moonshotai/kimi-k3` through
OpenRouter: 7 watcher ticks, 38 seconds after the gateway was ready. Option C runs the same
profiles inside the hosted image through `tools/local_stack.py`; it passed on October 29.

| Option | What you get | When |
|---|---|---|
| A. One command | The acceptance scenario on a real gateway with the fake platform; exits when done | A quick check that the whole chain works |
| B. Interactive session | The same setup kept running: seam URL, `curl` lines, the watcher every minute, Ctrl-C to stop; optionally the real platform | Poking at the profiles by hand |
| C. Inside the hosted image | `tools/local_stack.py`: the same stack inside the Hermes image CI uses, the checkout mounted read-only, the state on a volume, the watcher as a real cron job; stays up until `down` | The closest to production on a laptop |

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
  `moonshotai/kimi-k3` for the Orchestrator and `recursive/flint-v1.0:free` for a Researcher,
  from each profile's `config.yaml`; `GET /models` on `research serve` shows and changes them). The metta token broker vends
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

## A client page

`research serve` also serves a browser client at `GET /` (and `/ui`):
`core/src/research_core/ui/index.html`, one self-contained page (inline CSS and JavaScript, no
build step, nothing fetched from outside the seam) in the Ink & Print house style. It is a
developer's view of the seam, not the IDE: every part of it maps to one route, so it is the
quickest way to watch a Session, the watcher and the stores at once.

Open it against any of the options, or without a model at all:

| Against | URL |
|---|---|
| Option B (`IDE_E2E_KEEP=1`) | The seam URL the banner prints, `http://127.0.0.1:<seam>/` |
| Option C | The seam URL that option prints, reachable the way its "Attaching a client" section says |
| A `research serve` of your own (`IDE_WORKSPACE` and `HERMES_HOME` exported, from the workspace environment) | `http://127.0.0.1:8650/` (or the `--port` given) |
| No gateway and no model: `uv run python tools/ui_dev.py [--port 8650]` | The URL it prints; a temporary workspace on the fake platform, the watcher ticking every 5 seconds on a thread, and `research_core.scripted_agent.ScriptedAgent` playing the Orchestrator (removed on Ctrl-C) |

What each part of the page exercises:

| Part | Routes | What to look for |
|---|---|---|
| Masthead and health strip | `POST /session-open` on load (client `ui`, kind `web-ide`), `GET /health`, `GET /status` every 5 seconds | The session id; profile, active Coworld and contract version; the watcher's heartbeat age, `has_work` with its `waiting_on` reasons, `paused`; whether each stream is open |
| Conversation | `POST /message` with a context block (`mode`, `on_screen`, `view` from the fields under the box), `GET /runs/<id>` every half second until the run is terminal, `POST /ui-action` `reaction` | `delivered` (`start` or `steer`) and the run id on your message; the run's status chip, then its output as the Orchestrator's message; "Helpful" / "Not helpful" write a reaction and show the UI-state version it landed in (no run starts) |
| Now showing, Asks, Panels | The `ui_state` documents of `GET /snapshot?store=workspace`, then `ui.patch` events; `POST /ui-action` `answer` | The last `ui.show`; open asks with their options as buttons (a free-text answer too), answered asks with the answer and who gave it; markdown panels as text, other kinds as JSON |
| Notices and Events | `GET /stream?store=workspace&after=0` and `GET /stream?store=coworld&after=0` (Server-Sent Events; the browser reconnects with `Last-Event-ID`) | `notice` events as a list, newest first; every event as one line (seq, time, store, kind, subject) with a payload summary, the last 300 kept |
| Records | `GET /snapshot?store=coworld` (Goals, Tasks, Versions, Claims, Experiments) and `?store=workspace` (both stores for Directives and settings), refreshed when a record event arrives | Tabs with counts, each record's id, title, status and last update, a Details button with the record as JSON; any `G-1`, `T-3`, `V-1`, `C-2`, `X-1`, `D-1` or `N-1` on the page jumps to its record |
| Edits as user:local | `POST /store/apply` | Add a Directive, rename a Goal, set a Goal's status, change a setting, pause or resume the watcher; the store's record or its refusal with the problems. Creating a Goal is not a user edit (`goals.create` is not in the allow-list): ask the Orchestrator for one |
| Raw request | Any route | Method, path and JSON body; the status and the response. `session_id` is filled in for `/message` and `/ui-action` when the body leaves it empty |

Against `tools/ui_dev.py`'s scripted Orchestrator (a real one answers as the model sees fit):

- **Submit the starter as my champion.** switches to the fake Coworld; within a few ticks the
  setup wake uploads and submits the starter, and a notice about `V-1` arrives.
- A message containing **ask me** opens a question with two options; answer it under Asks and
  the answer goes into a run of the same Session.
- A message containing **notify** sends a notice.
- A message containing **goal** creates a Goal (once a Coworld is active), brings it into view
  under Now showing and sets a markdown panel about it.
- Anything else is answered with `Noted: <the first 80 characters>`.

The routes are tested in `core/tests/test_serve.py`; the page itself was checked by hand in a
browser at 390, 1000 and 1280 pixels wide, in light and dark mode.

## Option C: inside the hosted Hermes image

CI runs the `hermes`-marked tests inside `nousresearch/hermes-agent@sha256:9774f4f3...` (the
digest in `.github/workflows/ci.yml`), whose Python runs the plugin and `prepare.py` exactly as
hosting does. `tools/local_stack.py` runs the whole stack in that image on your Mac: the checkout
mounted read-only at `/src` like the release checkout, the profile homes, the workspace and the
logs on a Docker volume, the multiplexing gateway with the watcher as a real cron job, and
`research serve` as the seam, both published on this machine's loopback.

**Verified on October 29, 2026 (Pacific)** with OrbStack (Docker server 29.8.2, linux/arm64; the
digest is a multi-arch manifest, so the image runs natively on Apple silicon) and the Hermes the
image ships (v0.21.6, 2026.9.24). `up --reset` on a fresh volume had the gateway and the seam
answering 90 seconds after the container started and then ran the acceptance scenario through
the seam, which ended in a submission by `ply_canary` 35 seconds after the request, inside the
chat run itself (seq 12 of the Coworld store, before the cron job's first tick); a second run
of the same scenario on another fresh volume took 33 seconds. The exact output is below.

### Prerequisites

- OrbStack (or another Docker) running: `open -a OrbStack`. The first `up` pulls the image when
  it is missing (4.5 GB).
- `OPENROUTER_API_KEY` in the environment of the first `up` on a volume. The broker shape from
  the prerequisites above works unchanged:

  ```bash
  cd ~/coding/metta && python3 scripts/token_broker_client.py exec --scope openrouter.inference \
    --reason "local stack of the IDE research profiles in the Hermes image" -- \
    uv run --project ~/coding/ide-distributions python ~/coding/ide-distributions/tools/local_stack.py up
  ```

  The key goes into the container only, into the `.env` files on the volume (that is how Hermes
  reads it); the tool never prints it and writes nothing on this machine. A later `up` without
  the key in the environment keeps the one already in the `.env` files.
- For the real platform, the same variables as Option B: `SOFTMAX_PLAYER_ID`,
  `SOFTMAX_PLAYER_CREDENTIAL`, optionally `COGAMES_API_URL` and `IDE_DEFAULT_COWORLD`.

### Commands

```bash
cd ~/coding/ide-distributions
uv run python tools/local_stack.py up                 # real platform as your Player (see below)
uv run python tools/local_stack.py up --platform fake # no credentials, no network; runs the scenario once
uv run python tools/local_stack.py status             # container, seam /health, gateway /v1/capabilities, watcher status
uv run python tools/local_stack.py scenario           # fake: send "Submit the starter as my champion." and wait (real: add --really)
uv run python tools/local_stack.py logs [--follow]    # tail /opt/data/gateway.log and /opt/data/serve.log
uv run python tools/local_stack.py shell              # a shell in the container with the Orchestrator's environment
uv run python tools/local_stack.py down               # stop the services and the container; the volume stays
uv run python tools/local_stack.py down --reset       # also remove the container and the volume
```

`up` on an existing container restarts the gateway and the seam without reinstalling (70 to 90
seconds, most of it `prepare.py` re-checking the config through `hermes config get`); `up
--reset` starts over. `--platform`, `--seam-port` (default 8650), `--gateway-port` (default
8642) and `--name` (default `ide-local`; the volume is `<name>-stack`) are fixed when the
container is created and read back from its labels afterwards. The scenario is written for a
new workspace: run it once per volume, or `up --reset` first.

### What `up` sets up

One container from the image digest, as a GitHub Actions job container runs it (`--user 0`,
the entrypoint replaced, here by `sleep` under `--init`), with `~/coding/ide-distributions` at
`/src` read-only, the volume at `/opt/data`, both ports published on `127.0.0.1` only and the
`GIT_*` identity CI sets. It writes two scripts into the volume and runs the first with `docker
exec`:

| Step inside the container (`/opt/data/local_stack/bootstrap.sh up`) | In hosting |
|---|---|
| Install `uv` into `/opt/data/.local/bin` (the image ships none, CI uses `astral-sh/setup-uv`) with a link from `/usr/local/bin/uv`, because the agents' terminal is a login shell whose `PATH` lacks the volume's directory; mark `/src` safe for git (it belongs to your Mac user) | The image has `uv` |
| `hermes profile install /src/<role> --name <role> --yes` into `HERMES_HOME=/opt/data/host` for both roles; skipped when the profile exists | The same, from the release checkout |
| Write `/opt/data/host/.env` and both profiles' `.env`: one random `API_SERVER_KEY` per volume (kept in `/opt/data/local_stack/api_key`), `API_SERVER_HOST=0.0.0.0` in the host home (the gateway binds from there, so the published port reaches it) and `127.0.0.1` in the profiles (where the watcher and the seam read the gateway's address), `GATEWAY_MULTIPLEX_PROFILES=true`, `IDE_WORKSPACE=/opt/data/workspace`, `IDE_PLATFORM`, `IDE_RESEARCHER_PROFILES=researcher`, `SOFTMAX_PLAYER_ID` (`ply_canary` for the fake), `OPENROUTER_API_KEY`, and for the real platform the Player credential, `COGAMES_API_URL` and `IDE_DEFAULT_COWORLD` | The per-profile `.env` hosting writes |
| `prepare.py --source /src/<role> --hermes /opt/hermes/.venv/bin/hermes` with the image's Python, `--activate-supervisor` for the Orchestrator, each with `HERMES_HOME=<profile home>` and that profile's `.env` exported (`as-profile.sh`); the first run builds the workspace environment with `uv sync` into `/opt/data/workspace/tools/venv` (uv's cache and the CPython 3.12 it fetches stay on the volume) | The same script; the environment is prebuilt at image build |
| Fake platform: build the fakegame fixture repository at `/opt/data/upstream/fakegame_repo` and point the fake's manifest at it (`tools/local_stack/configure_fake_platform.py`, as the real-Hermes test does) | n/a |
| `hermes gateway run` with the host home, in the background, log `/opt/data/gateway.log`; ready when `GET /v1/capabilities` answers 200 with the key. The watcher cron job is on, so the gateway ticks it every minute for real | The multiplexing gateway |
| `research serve --host 0.0.0.0 --port 8650` from the workspace environment with the Orchestrator's environment, log `/opt/data/serve.log`; ready when `GET /health` answers | Metta's bridge |

Timings on October 29, 2026 (Apple silicon, fast network), fresh volume: `uv` install 1 s,
both profile installs 3 s, Orchestrator preparation 46 s including the `uv sync` (the CPython
3.12 download included), Researcher preparation 33 s, fixture under a second, gateway ready 6 s
after start, seam under a second: 90 s in all. With the environment already on the volume a
re-`up` takes 70 to 90 seconds, almost all of it the two preparations.

### Where things live in the volume (`ide-local-stack`, mounted at `/opt/data`)

| Path | What |
|---|---|
| `host/` | The gateway's `HERMES_HOME`: `.env` and `profiles/orchestrator`, `profiles/researcher` |
| `host/profiles/<role>/.env`, `config.yaml`, `local/core-path.txt` (`/src/core/src`), `state.db`, the Orchestrator's `cron/jobs.json` | What install, `prepare.py` and the gateway wrote; the watcher job reads `enabled: true, state: scheduled` |
| `workspace/` | `IDE_WORKSPACE`: `tools/venv`, `.state/store.db`, `.state/fake_platform.json`, one folder per Coworld |
| `upstream/fakegame_repo` | The fake Coworld's source repository (fake platform only) |
| `local_stack/` | `bootstrap.sh`, `as-profile.sh`, `api_key`, the services' pid files |
| `gateway.log`, `serve.log` | The gateway's and the seam's output, appended across restarts |
| `uv-cache/`, `uv-python/`, `.local/bin/uv` | uv, its cache and the managed CPython 3.12 |

`docker exec ide-local cat /opt/data/local_stack/api_key` prints the key. `shell` drops you
into the container with `HERMES_HOME=/opt/data/host/profiles/orchestrator`, the profile's
`.env` exported and the workspace environment first on `PATH`, so the commands under "What to
look at" work as written with `RESEARCH=research`.

### The output

`up --reset` on October 29 (the `hermes profile install` banners and the `uv` installer's lines
are left out; `prepare.py`'s JSON summary lines are shortened):

```
removing container ide-local
removing volume ide-local-stack
creating container ide-local from nousresearch/hermes-agent@sha256:9774f4f3... (volume ide-local-stack)
[local-stack 0s] installing uv into /opt/data/.local/bin (the image ships none; CI uses astral-sh/setup-uv)
[local-stack 1s] installing profile orchestrator from /src/orchestrator
[local-stack 3s] installing profile researcher from /src/researcher
[local-stack 4s] writing /opt/data/host/.env and both profiles' .env (one API_SERVER_KEY everywhere)
[local-stack 4s] preparing profile orchestrator (the first run builds the workspace environment with uv sync)
{"role": "orchestrator", ..., "actions": ["wrote local/core-path.txt", "workspace store ready", "config: set skills.external_dirs", "cron: watcher job unchanged", "cron: resumed the watcher job", "workspace environment synced"]}
[local-stack 50s] preparing profile researcher (the first run builds the workspace environment with uv sync)
{"role": "researcher", ..., "actions": ["wrote local/core-path.txt", "workspace store ready", "config: set skills.external_dirs", "workspace environment up to date"]}
[local-stack 83s] pointing the fake platform at the fakegame fixture under /opt/data/upstream
{"source_url": "file:///opt/data/upstream/fakegame_repo", "source_commit": "13170d61...", "state": "/opt/data/workspace/.state/fake_platform.json"}
[local-stack 83s] starting the gateway on 0.0.0.0:8642 (log /opt/data/gateway.log)
[local-stack 89s] the gateway is ready (pid 653)
[local-stack 89s] starting research serve on 0.0.0.0:8650 (log /opt/data/serve.log)
[local-stack 89s] the seam server is ready (pid 836)
[local-stack 89s] up: seam http://127.0.0.1:8650, gateway http://127.0.0.1:8642
bootstrap took 90 s

=== local stack ide-local (platform fake) ===
seam (research serve routes):  http://127.0.0.1:8650
gateway (Hermes API server):   http://127.0.0.1:8642  routes under /p/<profile>/
API key:                       /opt/data/local_stack/api_key in volume ide-local-stack (API_SERVER_KEY, same in every .env)
                               docker exec ide-local cat /opt/data/local_stack/api_key
gateway log:                   /opt/data/gateway.log      (tools/local_stack.py logs)
seam log:                      /opt/data/serve.log
profile homes:                 /opt/data/host/profiles
workspace (IDE_WORKSPACE):     /opt/data/workspace
checkout (read-only):          /src

talk to it:
  curl -s -X POST http://127.0.0.1:8650/session-open -H 'Content-Type: application/json' -d '{"client": {"id": "curl", "kind": "web-ide"}, "actor": "user:local"}'
  curl -s -X POST http://127.0.0.1:8650/message -H 'Content-Type: application/json' -d '{"session_id": "<session_id>", "message": "What is the state of the workspace?", "actor": "user:local", "context": {"contract_version": 1, "on_screen": []}}'
  curl -s 'http://127.0.0.1:8650/snapshot?store=workspace'
  curl -sN 'http://127.0.0.1:8650/stream?store=workspace&after=0'
  uv run python tools/local_stack.py scenario | status | logs | shell | down

fresh workspace: running the acceptance scenario (skip with --no-scenario)
scenario on http://127.0.0.1:8650 (platform fake): 'Submit the starter as my champion.'
session api_1793300437_df403177; chat run run_6cf635fe546d4167bb08c29ba02f28a1 (start)
[   0 s] no active Coworld yet; watcher: has_work=False waiting_on=[] heartbeat_age_s=None
version.submitted after 35 s (seq 12): {"store": "coworld", "seq": 12, "at": "2026-10-29T19:01:13.419698Z", "actor": "session:api_1793300437_df403177", "kind": "version.submitted", "subject": "V-1", "payload": {"auto_champion": "always", "autonomous": false, "cites": "User: \"Submit the starter as my champion.\"", "league": "league_fake", ...
[  35 s] Coworld cow_fake: 0 tasks, 1 versions, seq 12; watcher: has_work=False waiting_on=[] heartbeat_age_s=None
submissions by Player: {'ply_canary': 1}
```

`heartbeat_age_s=None` there is the cron job not having ticked yet: the gateway was 40 seconds
old, and the job runs every minute.

`status`, a minute later on an earlier stack:

```
container ide-local: running
platform: fake; volume: ide-local-stack
seam http://127.0.0.1:8650/health: 200 {"ok": true, "profile": "orchestrator", "coworld": null, "contract_version": 1}
gateway http://127.0.0.1:8642/v1/capabilities: 200
watcher: {"ok": true, "heartbeat": "2026-10-29T18:53:55.478290Z", "heartbeat_age_s": 10.7, "has_work": false, "waiting_on": [], "paused": false, "paused_drained": false}
```

`scenario` run by hand, on a stack brought up with `--no-scenario`: it opens a Session, sends
the request, then reads the active Coworld's event stream through the seam until a
`version.submitted` event arrives, with a progress line every 30 seconds (the Coworld's record
counts and `research watcher status` from inside the container).

```
scenario on http://127.0.0.1:8650 (platform fake): 'Submit the starter as my champion.'
session api_1793300060_8892c812; chat run run_a1277fdd02bf48ac96625021eb6c479a (start)
[   0 s] no active Coworld yet; watcher: has_work=False waiting_on=[] heartbeat_age_s=25.6
version.submitted after 33 s (seq 12): {"store": "coworld", "seq": 12, "at": "2026-10-29T18:54:50.456767Z", "actor": "session:api_1793300060_8892c812", "kind": "version.submitted", "subject": "V-1", "payload": {"auto_champion": "always", "autonomous": false, "cites": "user: \"Submit the starter as my champion.\"", "league": "league_fake", "league_name": "Fake League", "policy_version_id": "pv_14cd6cc64c32", ...
[  33 s] Coworld cow_fake: 0 tasks, 1 versions, seq 12; watcher: has_work=False waiting_on=[] heartbeat_age_s=58.5
submissions by Player: {'ply_canary': 1}
```

The Orchestrator switched to `cow_fake`, made Version `V-1` from the starter, uploaded and
submitted it in one turn; the Oct 28 run of Option A took 38 seconds for the same request, so
the "7 ticks" there were the harness polling every 5 seconds, not watcher work. The setup
wake that followed ran under the cron job a minute later (`logs` shows its tool calls).

How to read a failure: `the gateway exited; see /opt/data/gateway.log` or `not ready within 180
s` come with the log's last 40 lines (a gateway that refuses to start prints why there);
`prepare.py` failures print the script's output (most often `uv sync`: network, or a lock that
does not match); `no version.submitted after <n> s` means the chat run or the wakes did not get
to a submission in time: `logs`, then from `shell` the commands under "What to look at"
(`research store events --after 0`, the fake platform's state file).

### Attaching a client

The seam is `research serve` on `http://127.0.0.1:8650` with the routes of Option B (`POST
/session-open`, `/message`, `/ui-action`, `/store/apply`; `GET /snapshot`, `/stream`,
`/runs/<id>`, `/health`), so the `curl` lines above, `ScriptedClient("http://127.0.0.1:8650")`
from `core/src/research_core/client.py`, and any client of the seam contract talk to it from
the Mac. The gateway is `http://127.0.0.1:8642` with the routes under `/p/<profile>/` and the
key from the volume:

```bash
KEY=$(docker exec ide-local cat /opt/data/local_stack/api_key)
curl -s http://127.0.0.1:8642/v1/capabilities -H "Authorization: Bearer $KEY"
curl -s -X POST http://127.0.0.1:8642/p/orchestrator/v1/runs -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{"input": "Reply with READY and stop.", "session_id": "api_local_1"}'
```

Both ports are bound to `127.0.0.1` on the Mac; inside the container the two servers listen on
`0.0.0.0` of a network namespace in which nothing else runs.

### The real platform

The default. The stack talks to `https://softmax.com/api` (or `COGAMES_API_URL`) as one of
your Players:

```bash
softmax player list                        # your Players; `softmax player use <id>` picks one
OPENROUTER_API_KEY=... uv run python tools/local_stack.py up --reset            # the active Player
OPENROUTER_API_KEY=... uv run python tools/local_stack.py up --reset --player ply_...
```

Without `SOFTMAX_PLAYER_ID` and `SOFTMAX_PLAYER_CREDENTIAL` in the environment, `up` takes the
Player from this machine's `softmax` login: the active Player's session (a 24-hour token
`softmax player use` minted, kept in the CLI's `credentials.yaml`), or the `--player` id. It
prints which Player and when the session expires; an expired session is refused with the
command that mints a new one. The token goes into the `.env` files on the volume, as hosting
would write it, and stays there until `down --reset` or until it expires, after which `up`
again reads a fresh one. Set the two variables yourself to use a Player this machine is not
logged in as.

On the real platform `up` never runs the submission scenario on its own, and `scenario` refuses
without `--really`, because the scenario uploads and submits the starter policy to a real
league as your Player. Ask the Orchestrator for what you want through the seam instead; it
finds Coworlds with `platform.leagues` and `platform.coworld`, and a switch clones the
Coworld's source repository from the manifest (or from a `source` URL you name). With
`IDE_DEFAULT_COWORLD=cow_...` in the environment, `prepare.py` registers and activates that
Coworld on the fresh workspace before the first chat.

### What still differs from production

- **gVisor.** Docker's default runtime (OrbStack's), not gVisor; the S3 check (a second process
  reading a WAL store under gVisor) is not exercised.
- **Sleep and drain.** Nothing puts the container to sleep: `sleep_through_jobs`,
  `lifecycle.activity`, the cron catch-up after a wake and the first tick's resume of drained
  Sessions do not run. From `shell`, `research workspace drain` drains by hand, and the host's
  autosleep probe can be tried as hosting would run it, with the image's Python in the
  Orchestrator profile: `/opt/hermes/.venv/bin/python $HERMES_HOME/scripts/activity.py` prints
  `{"waiting_on": [...]}` from the watcher's last tick (an empty list permits sleep;
  `docs/hosting.md`, "Background activity and autosleep").
- **The bridge.** `research serve` plays metta's bridge; the clients are `curl` and
  `ScriptedClient`, not the IDE.
- **Player provisioning.** Hosting selects the Player at workspace creation and mints and
  refreshes `SOFTMAX_PLAYER_CREDENTIAL` (a player session lasts 24 hours); here you paste a test
  Player's credential and nothing refreshes it. `SOFTMAX_USER_CREDENTIAL` is absent, so
  User-identity reads report themselves unavailable.
- **Root.** Production runs the image's s6 init, which drops to the unprivileged `hermes` user
  before the gateway; this runs everything as root, as CI's job container does (`--user 0`), and
  the gateway starts as root only with `HERMES_ALLOW_ROOT_GATEWAY=1`, which the bootstrap sets
  (without it the gateway prints "Refusing to run the Hermes gateway as root inside the official
  Docker image" and exits). Every file on the volume is root's.
- **The workspace environment** is built by `prepare.py` on the first `up`, not prebuilt at image
  build (`IDE_WORKSPACE_ENV`).
- **Hermes's warnings.** The gateway logs that its API server is network-accessible with the
  unsandboxed `local` terminal backend, and that it ingested `OPENROUTER_API_KEY` from the
  environment; both are expected here.

What is as in production here and was not in Options A and B: the image and the Hermes it ships,
the read-only checkout, and the watcher as a Hermes cron job every minute (enabled by
`prepare.py --activate-supervisor`; `research watcher status` shows its heartbeat).

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
