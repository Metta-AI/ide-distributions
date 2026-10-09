# Integrating the autoresearch profile with Rohit's Hermes hosting

Status: survey and proposal, 2026-10-08. Nothing here is built yet. Owner on our side: James.
The companion document for Rohit's agents is
[questions-for-rohit.md](questions-for-rohit.md).

**Sources.** Read-only, on the dates given:

- metta `main` at `d123d27d0b` (pulled 2026-10-08 22:23 UTC).
- Rohit's (`djbhindi`) PRs, open and merged, and the diffs of the open ones.
- ide-mvp branch `jboggs/autoresearch-v0-impl` at `4b2db3f`. It is 30 commits ahead of `origin/main`
  and not merged yet. The test shim (`autoresearch-shim/`) is on `origin/main` at `7e9c297`.

When a claim depends on an open PR, it is marked "(open PR)". Those PRs can still change.

**Ownership** (James's decision; see also the goal line in
`docs/plans/2026-10-07-autoresearch-v0/00-overview.md`, "ready to hand to Rohit for hosting"):

- Rohit owns the hosting environment, most of the runtime around Hermes, and credential management.
- We own the Hermes profile distribution (`autoresearch/`). It must work inside his system.
- Our EC2 box and `autoresearch-shim/` are throwaway stand-ins for his work.

## 1. Rohit's architecture in brief

### 1.1 Components and where they live

| Piece | What it does | Where | State |
|---|---|---|---|
| **Bridge** | Connects one player's IDE chat to one Hermes instance. It uses only the public player-agent HTTP API (`wait`, `say`, `changes`, records, `presence`) and the Hermes run API (`v1/runs`). With a workspace path configured, it also relays research: IDE research tasks go to `comms/inbox/`, and `comms/outbox/` messages go to the chat. | `packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py` | On main: #26790 (`d8a6f31e80`), and #27046 (`e76aa6a217`) split it into three coroutines: reader, chat worker, outbox watcher. |
| **Compose runtime** (first provisioning) | `compose.yaml` runs the pinned `nousresearch/hermes-agent@sha256:…` image plus a bridge sidecar on one volume. `provision.py` writes `.env` (binding, `cga_` player token, service key) and `provider.env` (model keys from a file or Secrets Manager). | `compose.yaml`, `provision.py`, `types.py` in the same package | On main (#26790). Operator CLI, one player per directory. |
| **Readiness checks** | The bridge writes `readiness.json` every 20 s: feed reachable, gateway healthy, a random `printf` challenge run through Hermes's terminal tool in the bound profile. The backend accepts evidence that is at most 60 s old. `metta dev doctor` checks the local stack, and its `hermes-ide` profile checks an "optimizer" checkout and a separate policy repo. | `bridge.py` (`report_readiness`, `probe_commands`), `observatory_api/clients/hermes/readiness.py`, `metta-dev/.../doctor.py` | Open PRs #27037, #27023. |
| **Nous Cloud provisioning** | `PUT /v2/cogents/{player_id}/hermes` creates one Nous Cloud instance per owned player through the Nous management MCP, sets dashboard basic-auth, and records player → instance → session bindings in Postgres. Per-instance secrets are stored in S3 with KMS. | `observatory_api/clients/hermes/{cloud,client,service}.py`, `routes/app/v2/cogents/hermes/`, migrations | Open PR #27037. |
| **IDE Start Hermes + gateway plugin** | The IDE button creates or resumes the binding. The backend then uploads `bridge.py` plus `gateway_plugin.py` as a Hermes **platform plugin** named `softmax`, writes `/opt/data/softmax-bridge/config.json`, sets `API_SERVER_*`, enables the plugin, and restarts the gateway. The bridge then runs **inside the gateway process** as a tracked task. | `observatory_api/clients/hermes/bootstrap.py`, `observatory_hosted_worker/gateway_plugin.py`, `web/softmax.com/src/ide/HermesConnection.tsx` | Open PR #27073, stacked on #27037. |
| **EC2 fleet (new default)** | `HERMES_USE_EC2=true` (default) puts each player's Hermes + bridge in one **gVisor sandbox** on a shared EC2 host. Limits: up to 8 sandboxes per host; each gets 2 CPU, 2 GiB RAM, 512 processes, a 10 GiB ext4 disk at `/opt/data`, a read-only root, a 256 MiB `/tmp`, uid `10000`, no capabilities, and egress to public addresses only. A host controller (`fleet.py`) reads encrypted S3 bundles and starts the sandboxes. `runtime.py` supervises `hermes gateway run` and the bridge as a subprocess inside the sandbox. The API issues a capped OpenRouter key per workspace. | `devops/hermes-fleet/`, `devops/tf/hermes-fleet/`, `observatory_hosted_worker/{fleet,runtime}.py`, `Dockerfile`, `routes/app/v2/cogents/workspace/` | Open PR #27068. It includes #27023 and #27037, but not #27073's gateway plugin. |

Superseded or closed: #27031 (replaced by #27046). The ide-mvp side of the bridge contract was
documented by Rohit in ide-mvp PR #4 (`autoresearch/docs/integrations/softmax-ide-bridge.md` on
`main`). Our reply is `softmax-ide-bridge-compat.md` on the impl branch.

### 1.2 How a player's instance comes up (EC2 path, open PR #27068)

1. The signed-in owner clicks **Start Hermes** in the IDE. The API calls `ensure_workspace`
   (`workspace/service.py`). It generates a `cga_` token, registers it as agent `hermes-hosted` of
   the player, and stores an encrypted workspace bundle in S3. Then it mints an OpenRouter key with
   `limit=HERMES_INFERENCE_BUDGET_USD` and `expires_at=now+HERMES_INFERENCE_KEY_DAYS`, through the
   tournament OpenRouter management client.
2. `fleet.py reconcile` on the host checks the token against `/observatory/whoami`. It creates and
   mounts the 10 GiB disk, writes `runtime.json`, and runs the image under `runsc`.
3. `runtime.py` sets `HOME=HERMES_HOME=/opt/data` and `OPENROUTER_API_KEY` in the process env.
   It writes `/opt/data/.softmax/credentials.yaml` (`{"tokens": {server: cga_…}}`), `git init`s
   `/opt/data/workspace` (the policy workspace), and writes the host `config.yaml` once (model,
   `terminal.cwd`, `env_passthrough: [COGAMES_API_URL]`). Then it starts `hermes gateway run`,
   waits for the API with durable run idempotency, and starts the bridge.
4. The bridge config has **`"workspace": None`**, so the research relay is off. The IDE shows
   research buttons only when the bridge advertises `state/research` with `available: true`.

The Nous path (#27037 + #27073) differs in mechanism but ends in the same state. The bridge config
that `bootstrap.py` writes has no `workspace` key either.

### 1.3 The IDE's model of chat and tasks

- **Chat.** Coach lines are read with `GET /v2/cogents/{player}/wait`. Each one becomes one Hermes
  run (`POST v1/runs`, the same `session_id` every time). The reply is posted with `POST …/say`.
  A line of exactly `/stop` stops the run in progress. The run instructions name the player and the
  server, and tell the agent to pass `--server` to `softmax`/`coworld`.
- **Research tasks.** The IDE research buttons (`web/softmax.com/src/ide/Research.tsx`: "Analyze
  replay" = `investigation`, "Develop policy" = `experimentation`, "Scout opponent" = `scouting`)
  write a `tasks` record with `origin: coach`, `area: <mode>`, `prediction: ""` and
  `doneWhen: "The research agent acknowledges this task with its result."`. The bridge turns it
  into `comms/inbox/<player>:task:<key>.json`. A cancelled task becomes a `steer` with
  `urgency: now`.
- **Answers.** Every outbox file is said in chat once, as `<title>\n\n<body>`. A `note` with
  `in_reply_to` set to a forwarded task id closes that IDE task (`state: done`, `status: kept`,
  outcome = the note's title and body).

### 1.4 Credentials in his system

| Credential | Issued by | Lifetime | Reaches |
|---|---|---|---|
| Player agent token `cga_…` | API: one per workspace, agent `hermes-hosted` | No expiry. It is revocable; a revoked token stops the sandbox. | Bridge config, and `~/.softmax/credentials.yaml` in `tokens` format (so every process in the sandbox can use it) |
| OpenRouter key | API, through the tournament management key | `HERMES_INFERENCE_KEY_DAYS`, cap `HERMES_INFERENCE_BUDGET_USD`. Renewal is an explicit operator decision. | `OPENROUTER_API_KEY` in the gateway process env |
| Hermes API service key | Generated at provisioning | Instance lifetime | Bridge, and the gateway's `API_SERVER_KEY` |
| Nous management client id/secret | Backend only | n/a | Never reaches the runtime |

`provision.py` refuses anything except a `cga_` token ("never an owner credential").

## 2. How our profile fits

The fit at the architecture level is good. The details differ in about a dozen places.

**What already lines up:**

- The bridge is exactly the Runtime design §10 "external agent". The IDE is the Preston-role
  surface. Chat answers come from a Hermes session, and research goes through `comms/`.
- The bridge's message shapes are validated byte for byte against our published schemas
  (`schemas/comms/examples/inbound/{task,steer}/ide-bridge-*.json`,
  `tests/protocol/test_bridge_contract.py`). queue-keeper adopts direct inbox writes. The bridge
  already treats `in_reply_to` as optional (`Outbound.in_reply_to: str | None = None` on main).
- Both hosting paths run `hermes gateway run` with `HERMES_HOME=/opt/data` as the host root. That
  is the multiplex topology our cron jobs and the Kanban dispatcher were verified under
  (hermes-facts items 5 and 7). If our profile is installed at `/opt/data/profiles/autoresearch`,
  the gateway runs its cron jobs and dispatches its board.
- `uv` and `git` are in the EC2 image (`Dockerfile` copies `uv`; `runtime.py` runs `git init`). Our
  launchers need both.
- Egress is allowed to public HTTPS (OpenRouter, softmax.com, PyPI, GitHub). Our first
  queue-keeper run needs PyPI for `uv sync`.
- Help requests (`help_request` outbox messages: preflight failures, tool faults under D-26, the
  health job) reach the coach through the bridge's outbox relay with no extra work.

**Where our profile would sit:**

```
sandbox (uid 10000, /opt/data = 10 GiB volume)
├── hermes gateway run              HERMES_HOME=/opt/data (host root)
│   ├── default profile             IDE chat runs here (session per player); bridge readiness probe
│   ├── cron: autoresearch jobs     queue-keeper 1 min, xp-poll 5 min, health 15 min
│   └── Kanban dispatcher           host config /opt/data/config.yaml (A3)
├── bridge                          subprocess (EC2) or gateway plugin (Nous)
└── /opt/data/
    ├── profiles/autoresearch/      our distribution, .env, local/venv, bin/
    │   └── workspace/              canonical workspace (AUTORESEARCH_WORKSPACE)
    │       └── comms/{inbox,outbox}/   bridge <-> research agent
    ├── workspace/                  Rohit's "policy workspace" (git init by runtime.py)
    └── .softmax/credentials.yaml   cga_ token, readable by every process
```

## 3. Gap table: what the shim does today, and who owns it in his system

"Shim" means `autoresearch-shim/deploy/` on `origin/main`. Status values: **exists** (on metta
main), **in flight** (open PR), **missing** (no code anywhere).

| # | Shim does today | Owner in Rohit's system | Status | Notes |
|---|---|---|---|---|
| 1 | Creates the host (`metta box new`, m7i.xlarge, 4 vCPU, 16 GiB, 200 GiB) | EC2 fleet host + per-player gVisor sandbox; or a Nous Cloud instance | in flight #27068, #27037 | Sandbox has **2 CPU, 2 GiB RAM, 10 GiB disk**. See gap 17. |
| 2 | Installs Hermes v0.21.5 editable (`bootstrap.sh`, `/opt/autoresearch-shim/hermes-agent`) | Pinned official image by digest | exists (compose), in flight (Dockerfile) | Digests differ between PRs (`c56a1bd1…` in #27023/#27073, `9774f4f3…` in #27068's `Dockerfile`). The gateway plugin was verified against Hermes `d687605c` (main, 2026-10-08, after v0.21.6). We verified only v0.21.5. |
| 3 | Installs and updates the profile from a stable source (`provision.sh` uploads a `git archive`, `hermes profile install`/`update`) | nobody | **missing** | `hermes profile install <git-url>` needs `distribution.yaml` at the repo root and clones the default branch only (`hermes_cli/profile_distribution.py:233-290`, v0.21.5). Ours is in `autoresearch/` of a **private** repo. |
| 4 | Writes the profile `.env` (`configure_env.py`, `profile.env.template`) | nobody | **missing** | His runtime writes bridge config and `credentials.yaml`, not our `.env` keys. |
| 5 | Sets host Kanban config (A3: `max_in_progress`, `dispatch_in_gateway`, no `default_assignee`) | `runtime.py` writes host `config.yaml` once | **missing** | It writes model and terminal keys only. If those settings are wrong, our preflight pauses the agent. |
| 6 | Runs `queue_keeper.py --bootstrap-only` and resumes cron jobs | nobody | **missing** | Cron jobs arrive paused by design. |
| 7 | Runs the gateway as a systemd service (`Restart=on-failure`, `UMask=0077`) | `runtime.py` supervisor (EC2); Nous-managed gateway | in flight #27068, #27073 | Equivalent. |
| 8 | Mints a Player session and refreshes it daily (`mint_player_credential.py`, `refresh-credential.sh`, which restarts the gateway) | API mints a `cga_` agent token per workspace | in flight #27068, #27073 | **Different credential type.** Our profile accepts only `{"token": "ply_…", "expires_at": …}`. A `cga_` token does not expire, so the daily refresh problem may go away. Our side must change (§4.1). |
| 9 | Copies an OpenRouter key file to the box | API-issued capped key per workspace in gateway env | in flight #27068 | It is not in our profile `.env`. Our preflight and our GotA build path look for it there (§4.2). Budget and expiry values are not set. |
| 10 | Runs research as its own user `autoresearch`; recommends a separate bridge user and `AUTORESEARCH_INBOX_WRITERS` | One uid (10000) for gateway, bridge, chat agent and workers | **missing** (structural) | In-process plugin (Nous) or subprocess (EC2): no OS-level separation is possible in the current sandbox. |
| 11 | Local bare workspace remote (`/var/lib/autoresearch/workspace.git`) | nobody; daily EBS snapshots (#27068) | **missing** | `AUTORESEARCH_WORKSPACE_REMOTE` is required. Rohit lists "private policy repository setup" as separate work. |
| 12 | Nim 2.2.10 at `/opt/nim-2.2.10`, Docker, pinned Polyworld sources, native builds, `tirith` | nobody; image has only `coworld==0.1.58`, `softmax-cli==0.26.38` | **missing** | G2 runs `coworld run-episode`, which needs **Docker**. The sandbox has no Docker and drops all capabilities. Root is read-only, so Nim must be in the image or on `/opt/data`. |
| 13 | Test console: chat, feed, steer, and Preston rewriting outbox messages into plain language (`ar_shim/preston.py`) | IDE chat + bridge outbox relay | exists (#26790, #27046) | The bridge says outbox text **verbatim** and uses it as the task outcome. There is no translation step. |
| 14 | Writes the inbox through `comms inbound` (console) | Bridge direct writes | exists (main), but **disabled** in the new paths | `workspace` is `None` in `fleet.py` and absent in `bootstrap.py`. The documented default path `/opt/data/profiles/optimizer/workspace` does not match our profile name `autoresearch`. |
| 15 | Observability: `journalctl`, `keeper.log`, SSH tunnel to the console and Hermes dashboard | `readiness.json` (commands + bridge), host status every 15 s with a 90 s expiry, `metta dev doctor` | in flight #27037, #27068, #27023 | Nothing reports **profile** health: paused, last queue-keeper run, cron state. |
| 16 | Backups: none (box-local remote) | Daily EBS snapshots, 7 kept; manual workspace move | in flight #27068 | Better than the shim. Still crash-consistent, daily. |
| 17 | Sizing: 4 vCPU, 16 GiB, 200 GiB | 2 CPU, 2 GiB, 10 GiB, 512 pids, 256 MiB `/tmp` | in flight #27068 | With `max_in_progress 2`, GotA's 4 native match workers, the compiler's Hermes child, and Nim builds, 2 GiB and 10 GiB are very likely too small. Not measured. |

## 4. What we must change on our side

Ordered by when the integration needs it.

1. **Accept the `cga_` agent credential.** Today `SOFTMAX_PLAYER_CREDENTIAL` must be
   `{"token": "ply_…", "expires_at": …}` and a bare token is rejected (`README.md`,
   `softmax_tool/identity.py`, `docs/plans/findings/softmax-client.md`). Proposed: also accept a
   `cga_` token with no expiry. Keep the `whoami` check against `AUTORESEARCH_PLAYER_ID`. Skip the
   expiry warning and pause for tokens that do not expire, and write the client's private
   `credentials.yaml` in the `tokens` format for them. **First verify** that a `cga_` token can
   create XP requests, upload and submit. Only upload is proven (#26790 local proof). If it works,
   the daily refresh and the gateway restart (`refresh-credential.sh`) are not needed when hosted.
2. **Make the Softmax server configurable.** `softmax_tool/platform_cli.py:35` hard-codes
   `SERVER = "https://softmax.com/api"`. Rohit's run instructions require `--server` because local
   stacks use another URL. Proposed: read `COGAMES_API_URL` (his runtime already sets it and
   passes it through) or a new `AUTORESEARCH_SOFTMAX_SERVER`, defaulting to production. Pass it as
   `--server` on every CLI call.
3. **Get the OpenRouter key to the trusted paths without exposing it to model terminals.** Hermes
   strips provider keys from worker terminals (hermes-facts item 7, A4). The GotA build calls a
   Hermes child that needs the key (`games/gota/GAME.md` "Build and upload"; risk 7 in
   `acceptance-checklist.md` on branch `jboggs/autoresearch-gota-build-fixes`). Preflight falls back
   to the profile `.env`, but Rohit's runtime puts the key only in process env. Proposed: the
   installer also writes the key into `$PROFILE/.env`. The build binding (a trusted path) then reads
   it from there, as preflight does, and passes it to the compiler child only. Never use a renamed
   variable that a model command could read.
4. **A Docker-free G2, or a host tier with containers.** G2 (`games/gota/gates/g2_episode.py`) runs
   `coworld run-episode`, which starts Docker containers. This cannot run in the gVisor sandbox.
   This is James's decision (it changes the build gates). Options: a native-runner G2 (the GotA
   toolkit already has native match workers in `tools/gota_local.py`), or a platform-side check.
5. **A small-host configuration.** For 2 CPU / 2 GiB: `kanban.max_in_progress 1`, GotA match
   concurrency 1, and `TMPDIR` on `/opt/data`, because `/tmp` is a 256 MiB tmpfs. Measure our disk
   footprint (venv, uv cache, Nim, Polyworld sources, native builds, workspace, `tirith` at 32 MB)
   on the shim box before we ask for a size.
6. **A machine-readable profile status for his readiness checks.** Proposed:
   `"$AUTORESEARCH_CLI" status --json` returns installed version and commit, paused (from
   `.supervisor/ledger/pause.json`), last queue-keeper run time and result, cron job states, and
   board epoch. It must contain no credentials. His `readiness.json` or doctor can include it.
7. **Packaging for a non-root install.** Either Rohit stages a `git archive` of `autoresearch/` at a
   pinned ide-mvp commit (as the shim does), or we publish the distribution as its own repository
   with `distribution.yaml` at the root. Staging is smaller and keeps one repo. Note that
   `hermes profile update` re-pulls from the `source:` recorded at install, so the staged path must
   be stable (`/opt/data/dist/autoresearch`). Replace its contents, then run
   `hermes profile update autoresearch -y`.
8. **Chat-agent guidance (Preston role).** The IDE chat runs in the Hermes **default** profile.
   That agent does not know our workspace or CLI. Proposed: we ship a small skill or `AGENTS.md`
   fragment for the chat profile. It covers how to read the canonical workspace, how to resolve
   `ar://` refs with `comms resolve`, how to explain outbox messages in plain words, and how to send
   a chat-proposed `steer` or a `reply` to a `question` with `"$AUTORESEARCH_CLI" comms inbound`.
   The L1 path commits directly, so it is not affected by `AUTORESEARCH_INBOX_WRITERS`. The IDE has
   no surface for `reply` or `seen` today, so this is also how coach answers would reach the agent.
9. **Re-verify Hermes facts on his image.** Our facts (A3, A4, A7, Kanban env scrubbing, the plugin
   hooks) were checked on v0.21.5 only. Rerun `tests/integration/*` (marker `hermes`) against the
   exact image digest he ships. `hermes_requires: ">=0.21.5"` admits newer versions that we have not
   tested.
10. **Inbox trust in a single-uid sandbox.** The compat doc's "run the bridge as its own user" is
    not possible there. For the first end-to-end run, leave `AUTORESEARCH_INBOX_WRITERS` unset and
    accept that direct writes are unauthenticated: queue-keeper logs it, and the plugin still blocks
    model inbox writes, best effort. For production, ask for a separate bridge container with its
    own uid that shares only `comms/inbox/` (see the questions doc, W1). Update
    `softmax-ide-bridge-compat.md` when this is decided.

These items are not built. Items 1, 2 and 7 are small. Items 3 to 6 need James's decisions.

## 5. Proposed integration sequence

### Milestone 1: the smallest end-to-end run (fake game, fake Softmax, real hosting)

Goal: prove provisioning, profile install, cron and Kanban under his gateway, the bridge relay, and
the inference key. Use no GotA, no Nim, no Docker and no real Softmax calls.

Setup in one of Rohit's sandboxes. His local Lima VM from `devops/hermes-fleet/README.md` is enough:

1. Stage `autoresearch/` from a pinned ide-mvp commit at `/opt/data/dist/autoresearch`. Run
   `hermes profile install /opt/data/dist/autoresearch --name autoresearch`.
2. Write `/opt/data/profiles/autoresearch/.env`:
   - `AUTORESEARCH_GAME=fakegame` and `AUTORESEARCH_SOFTMAX=fake`;
   - `AUTORESEARCH_PLAYER_ID=<player>` and the credential (any well-formed value works with the
     fake Softmax);
   - `AUTORESEARCH_WORKSPACE_REMOTE=/opt/data/autoresearch-workspace.git` (a local bare repo);
   - `OPENROUTER_API_KEY` (the workspace key) and `OPENROUTER_MODEL=z-ai/glm-5.3-flash`.
3. Host config: `kanban.max_in_progress 1`, `kanban.dispatch_in_gateway true`,
   `kanban.default_assignee` unset. Restart the gateway.
4. Run `queue_keeper.py --bootstrap-only`, then resume `queue-keeper`, `xp-poll` and `health`.
5. Start the bridge with
   `workspace=/opt/data/profiles/autoresearch/workspace`.

Pass criteria, all observed in the IDE or the workspace:

- The IDE shows research controls (`state/research` is available).
- Clicking **Analyze replay** writes `comms/inbox/<player>:task:<key>.json`. Within about 2
  minutes queue-keeper adopts and commits it, and a card appears on the `autoresearch` board.
- The card finishes. A `Done: <task title>` note (`about.outcome: done`) appears in `comms/outbox/`. The bridge says it in
  chat, and the IDE task closes with that note as the outcome.
- Cancelling a second task produces a `steer` and a P0 card that answers it.
- Restarting the sandbox loses nothing: same board epoch, no duplicate cards, bridge cursors
  resume.
- `keeper.log` shows no failing step except the expected "direct writes are unauthenticated"
  line.

This milestone needs answers to the questions marked **blocking** in `questions-for-rohit.md`,
and change 7 from §4. It needs nothing else from §4.

### Milestone 2: real Softmax, still no GotA build

Changes 1 and 2 (`cga_` credential, configurable server). Run preflight `whoami`,
`softmax game-version`, league episode reads, and one XP create with a small request against the
real platform as the workspace player. This proves the credential path and the 402/credits
behaviour (A10) in his environment.

### Milestone 3: GotA in the sandbox

Changes 3 to 5: the key in the build path, a Docker-free G2 or a container-capable tier, and the
small-host configuration. Nim 2.2.10 and `tirith` must be in the image or installed on `/opt/data`.
Then run the real GotA cycle (Plan 03 Task 6.1) hosted.

### Milestone 4: production hardening

Inbox trust (a separate bridge uid), the outbox translation step in the bridge or the chat agent
(change 8), the upgrade procedure, an off-host workspace remote, inference key renewal, and profile
health in readiness (change 6).

## 6. Risks worth naming now

- **Two bridge launch mechanisms are in flight.** #27068 starts the bridge as a subprocess from
  `runtime.py`. #27073 runs it as a gateway platform plugin and says "No detached bridge process is
  launched". We only need one, as long as it carries `workspace`.
- **The credential sits in the standard store at `HOME=/opt/data`.** Any process in the sandbox,
  including our research workers' model commands, can act as the player without going through our
  softmax tool. This makes acceptance-checklist risk 1 concrete. In a single-uid sandbox the plugin
  policy is the only guard.
- **Kanban comments carry user authority.** Hermes pushes a card comment from any author other than
  the worker's profile into the running worker as a user steer
  (`docs/designs/worker-to-worker-comms.md` on `jboggs/autoresearch-worker-awareness`). If the
  Hermes dashboard's Kanban UI is ever exposed to IDE users, or the bridge comments on cards, that
  bypasses `comms/`. Neither happens today.
- **Inference budget versus kimi-k3.** Hermes sends no `max_tokens`. OpenRouter then reserves
  about $1.97 per kimi-k3 call (A5), and our preflight pauses below $10 of remaining credit. A
  small `HERMES_INFERENCE_BUDGET_USD` pauses research early. When the key expires, the agent stops
  until an operator renews it.
- **The "optimizer" naming.** Rohit's docs and doctor assume an "optimizer" profile
  (`/opt/data/profiles/optimizer/workspace`) and a pinned "public optimizer" checkout
  (`/opt/data/softmax-ide/optimizer`, commit `730fdfd`). That commit is in neither
  `Metta-AI/optimizer-seed` nor `Metta-AI/optimizers`. It is not clear whether our profile replaces
  that setup or sits beside it.

## Update 2026-10-09: Rohit's reply and James's decisions

Rohit's agent replied on the gist (checked against metta main `6c01816c`). Full text is kept with the session notes;
the points that change this document:

- **PR baseline moved.** #27077 replaced #27037/#27073. The shared EC2 stack is merged: API #27078, runtime #27079,
  deployment #27081, IDE #27082. EC2 is the default host. Merged means code exists, not a verified production deploy or
  an autoresearch end-to-end run.
- **Profile.** The EC2 image installs `Metta-AI/rohit-optimizer@f6508fb1` as profile `optimizer` through Hermes's
  profile installer; chat and capability checks use `/p/optimizer/`. Paths: research source `/opt/data/rohit-optimizer`,
  workspace `/opt/data/workspace`, profile state `/opt/data/profiles/optimizer`. No profile auto-update; no host Kanban
  config; no drain before upgrades. **James's decision (2026-10-09): autoresearch replaces `optimizer`.** Consequences:
  our distribution must be shipped as a pinned artifact installed by the runtime startup path (no runtime GitHub
  credential); the runtime's `.env` writer must be extended for our settings; and our profile needs a coach-facing chat
  role (today played by the shim's Preston), because the IDE chat will talk to our profile directly.
- **Credentials.** `cga_` is a PLAYER principal, no expiry, XP allowed. We support it (keep the whoami check, drop daily
  renewal) and use the configured `COGAMES_API_URL` with explicit `--server` — in progress on branch
  `jboggs/autoresearch-cga-server`. Real upload/XP/submit with `cga_` stays milestone 2.
- **Inference key.** Issued with an explicit budget and expiry (`HERMES_INFERENCE_BUDGET_USD`, `HERMES_INFERENCE_KEY_DAYS`);
  reconnect keeps it and does not renew it. A key in a profile file is not isolated from same-UID processes.
- **Bridge.** Still `workspace=None` (relay off). Every replying `note` closes the IDE task as done/kept; `about.outcome`
  is not read yet (agreed gap). Bridge stays deterministic transport; explanation belongs to the chat agent, without an
  extra model call being a delivery requirement.
- **Sandbox.** One UID, 2 CPU, 2 GiB, 10 GiB disk, 256 MiB tmp, no nested Docker. No separate bridge user, so
  `AUTORESEARCH_INBOX_WRITERS` cannot be used there; first runs accept unauthenticated direct writes. Native G2 fits;
  GotA sizing unmeasured. No upstream workspace remote; a local bare remote is fine for the first smoke test.
- **Health.** Bridge callbacks plus a health socket and Hermes durable-run check; they do not show research progress. A
  secret-free research status signal would be welcome as a separate capability.
- **Image.** Hermes pinned by digest `sha256:9774f4f3…`, with coworld 0.1.58 and softmax-cli 0.26.38. We are re-running
  our Hermes tests against that exact image (findings: `docs/plans/findings/hermes-image-compat.md`, pending).
- **Milestones** unchanged (fake game → real player operations with `cga_` → GotA), plus structured outcome handling and
  profile routing in the first integration contract.
