# Questions and proposals for the Hermes hosting work (from the autoresearch side)

**For:** Rohit Mukherjee and agents working for him in `Metta-AI/metta`.
**From:** James Boggs (autoresearch). Written 2026-10-08.

## Context you need

`Metta-AI/ide-mvp` holds **autoresearch**. It is a Hermes **profile distribution** (root:
`autoresearch/`, manifest `autoresearch/distribution.yaml`). Hermes installs it with
`hermes profile install` as a profile named `autoresearch`. It runs a research loop for one Softmax
player without supervision. The parts:

- Three cron jobs run under the Hermes gateway: `queue-keeper` every 1 min, `xp-poll` every 5 min,
  `health` every 15 min.
- Research cards run on a Hermes Kanban board named `autoresearch`, dispatched by the gateway.
- A git repository is the canonical workspace, at `$PROFILE/workspace` by default.
- Communication goes through the `comms/` folder in that workspace. The research agent writes
  `comms/outbox/<seq:08d>-<M-id>.json`. External agents, such as your bridge, write
  `comms/inbox/<id>.json`.

Your bridge (`packages/observatory-hosted-worker/src/observatory_hosted_worker/bridge.py`, on main
from #26790 and #27046) is the external agent we designed for. The message contract is published
as JSON Schema in `autoresearch/schemas/comms/` (start at `schemas/comms/README.md`). Your bridge's
two message shapes are checked byte for byte there
(`examples/inbound/task/ide-bridge-task.json`, `examples/inbound/steer/ide-bridge-stop.json`;
contract test `autoresearch/tests/protocol/test_bridge_contract.py`).

**Where our files are.** Unless noted, paths are in `Metta-AI/ide-mvp` on branch
`jboggs/autoresearch-v0-impl` (commit `4b2db3f`; not merged to `main` yet). The test harness we
use in place of your system is `autoresearch-shim/` on `main`. It is a throwaway EC2 box. We want
your system to replace it, not copy it. The most useful references:

- `autoresearch/README.md`: operations guide. It has the `.env` keys, host config, cron jobs,
  restore and update.
- `autoresearch/docs/integrations/softmax-ide-bridge-compat.md`: our compatibility review of your
  bridge.
- `autoresearch/docs/integrations/rohit-architecture-integration.md`: our reading of your PRs, and
  a gap table.
- `autoresearch/docs/plans/findings/hermes-facts.md`: Hermes v0.21.5 behaviour we depend on, with
  source citations.

**What we read on your side** (metta `main` at `d123d27d0b`, 2026-10-08): `observatory-hosted-worker`
on main, and the open PRs #27023, #27037, #27068 and #27073. If a PR has changed since then, some
items below may already be answered.

**How to read each item.** Each item has:

- **Q:** the question or proposal;
- **Why:** why it matters;
- **Assumed:** what we assume today;
- **Default:** what we propose if you have no better answer.

**[BLOCKING]** marks an item that blocks the first end-to-end run. That run is defined at the end
of this document. Please answer inline, or in a PR comment that links here. "Do it the default way"
is a fine answer.

---

## 1. Provisioning and profile install

### P1. Who installs our profile, and when? [BLOCKING]

- **Q:** Which component runs `hermes profile install` for autoresearch in a player's runtime? When
  does it run: at sandbox creation, on IDE Start Hermes, or on an explicit "enable research" action?
- **Why:** Nothing in #26790, #27037, #27068 or #27073 installs a profile. `runtime.py` (#27068)
  writes only the host `config.yaml`, `credentials.yaml` and the bridge config. On Nous (#27023
  "Limits"), there is no command-execution transport, so the profile cannot be installed except by
  asking the chat agent.
- **Assumed:** One profile per player runtime, named `autoresearch`, installed at
  `/opt/data/profiles/autoresearch` (host root `/opt/data`).
- **Default:** On EC2, `runtime.py` makes sure the profile exists before it starts the gateway:
  install if missing, update if the staged commit changed (see V1). On Nous, a gateway startup
  hook or the bootstrap step in `observatory_api/clients/hermes/bootstrap.py` does the same, using
  the files API that already uploads `bridge.py`.

### P2. Where does the install source come from? [BLOCKING]

- **Q:** Can provisioning stage the distribution files instead of using `hermes profile install
  <git-url>`?
- **Why:** In Hermes v0.21.5, `hermes profile install <git-url>` shallow-clones the **default
  branch** and requires `distribution.yaml` **at the repository root**
  (`hermes_cli/profile_distribution.py:233-290`). Our distribution is in the `autoresearch/`
  subdirectory of a **private** repo. Also, the sandbox has no GitHub credentials.
- **Assumed:** Provisioning has GitHub read access on the API side.
- **Default:** The API (or the host controller) fetches `git archive <commit> autoresearch/` from
  `Metta-AI/ide-mvp`. It puts the files at a **stable** path, `/opt/data/dist/autoresearch`. Then:
  `hermes profile install /opt/data/dist/autoresearch --name autoresearch`. The path must be stable
  because `hermes profile update` re-reads from the `source:` recorded at install. The commit comes
  from server config (see V1). If you prefer a separate public or private repo with
  `distribution.yaml` at its root, say so and we will publish one.

### P3. What is the full post-install sequence, and who owns each step? [BLOCKING]

- **Q:** Will the installer run these steps, in this order, after the profile install?
  1. Write `$PROFILE/.env` with mode 0600 (keys in C1 to C3 below).
  2. Set the host Kanban config (P4).
  3. Restart the gateway.
  4. Run bootstrap once:
     `HERMES_HOME=$PROFILE python3 $PROFILE/scripts/queue_keeper.py --bootstrap-only`, with the
     profile `.env` loaded and `uv` on `PATH`.
  5. Resume the three cron jobs: `hermes -p autoresearch cron resume queue-keeper|xp-poll|health`.
- **Why:** The cron jobs arrive **paused** on purpose (`autoresearch/README.md` "Resume the cron
  jobs"). Without bootstrap and resume, nothing runs. Our shim does these steps in
  `autoresearch-shim/deploy/bootstrap/bootstrap.sh` and `configure_env.py` (on `main`).
- **Assumed:** The installer, not the research agent, does steps 1 to 5.
- **Default:** As listed. Step 5 can wait until the owner enables research, if you want an explicit
  switch in the IDE. Pausing again is `cron pause` on the same three jobs.

### P4. Host Kanban config in `/opt/data/config.yaml` [BLOCKING]

- **Q:** Can the runtime write these three host-level settings, and keep them across restarts?
  - `kanban.dispatch_in_gateway: true`
  - `kanban.max_in_progress: 1` (see O3 for why 1)
  - `kanban.default_assignee` unset
- **Why:** One gateway per host runs the Kanban dispatcher with the **host root** config, not the
  profile's (amendment A3, hermes-facts item 5). If `default_assignee` is set, our pause stops
  working. `runtime.py` writes `config.yaml` only when it is missing, and only with model and
  terminal keys. Our preflight reads this file on every queue-keeper run, and pauses the agent with
  a help request when it is wrong.
- **Assumed:** Nothing else in the runtime uses Kanban.
- **Default:** `runtime.py` merges these keys on every start (as `hermes config set` does, without
  `-p`), and leaves other keys alone.

### P5. Does a Nous Cloud instance support profile install, cron jobs and the Kanban dispatcher?

- **Q:** On Nous Cloud, can a runtime hold a second profile, run its cron jobs, and dispatch a
  Kanban board from the gateway?
- **Why:** EC2 is now the default (`HERMES_USE_EC2=true`). If Nous does not support this, we target
  EC2 only and say so in our README.
- **Assumed:** Unknown.
- **Default:** We support only the EC2 path for research until Nous is confirmed.

### P6. Our profile versus the "optimizer" setup

- **Q:** Should our profile replace the "optimizer" setup in your docs, or sit beside it?
  - The bridge README names `--workspace /opt/data/profiles/optimizer/workspace` ("the optimizer
    profile's own `workspace/`").
  - `doctor.py`'s `hermes-ide` profile (#27023) expects a pinned "public optimizer" checkout at
    `/opt/data/softmax-ide/optimizer` (commit `730fdfd46d99…`) and a policy repo at
    `/opt/data/softmax-ide/policy`.
  - `runtime.py` (#27068) creates a policy workspace at `/opt/data/workspace`.
- **Why:** Our profile name is `autoresearch`, so the workspace is
  `/opt/data/profiles/autoresearch/workspace`. We could not find commit `730fdfd` in
  `Metta-AI/optimizer-seed` or `Metta-AI/optimizers`, so we do not know which repo is the "public
  optimizer". Our profile has its own policy pipeline: it builds, uploads and submits from its own
  canonical workspace.
- **Assumed:** They are independent. Our profile does not use `/opt/data/workspace` or the
  optimizer checkout.
- **Default:** Make the bridge workspace path a setting (see B1), and make the doctor's
  `hermes-ide` checks optional when the autoresearch profile is installed. Tell us which repo
  `730fdfd` is in.

---

## 2. Credentials and identity

### C1. Can a `cga_` agent token do everything a Player session does? [BLOCKING for milestone 2, not milestone 1]

- **Q:** Can a `cga_…` player agent token do all of these through `coworld` 0.1.58 and
  `softmax-cli` 0.26.38?
  - `whoami`, returning `subject_type: player` and the player id;
  - `coworld xp-request create|get|list --mine|download`;
  - `coworld upload-policy`;
  - `coworld submit --player`;
  - `coworld submissions`;
  - `coworld memberships --champions-only`;
  - league rounds and episode reads.
- **Why:** Our profile acts as one player and checks `whoami` before every mutating call (contract
  C6). Today it requires `SOFTMAX_PLAYER_CREDENTIAL` as the ~24 h Player session
  `{"token": "ply_…", "expires_at": …}` (`autoresearch/README.md`;
  `autoresearch/docs/plans/findings/softmax-client.md`). Your system issues a non-expiring `cga_`
  token per workspace (`routes/app/v2/cogents/workspace/service.py`, #27068). Your README proves
  only upload with it (#26790 local proof). If `cga_` covers the list, we switch to it, and the
  daily refresh our shim does (`autoresearch-shim/deploy/box/refresh-credential.sh`, which also
  restarts the gateway) is no longer needed.
- **Assumed:** It probably works, because the token resolves to a player subject.
- **Default:** We accept a `cga_` token in `SOFTMAX_PLAYER_CREDENTIAL` with no expiry (our change).
  The installer writes the workspace's `cga_` token there. If some route refuses agent tokens,
  please tell us which ones.

### C2. Values the installer must write into the profile `.env` [BLOCKING]

- **Q:** Can the installer write these keys into `/opt/data/profiles/autoresearch/.env` (mode
  0600)?

  | Key | Value in your system |
  |---|---|
  | `SOFTMAX_PLAYER_CREDENTIAL` | the workspace `cga_` token (format per C1) |
  | `AUTORESEARCH_PLAYER_ID` | the workspace player id |
  | `OPENROUTER_API_KEY` | the workspace inference key (see K1) |
  | `AUTORESEARCH_WORKSPACE_REMOTE` | see W3 |
  | `OPENROUTER_MODEL` | optional; our default is `moonshotai/kimi-k3` |
  | `AUTORESEARCH_GAME` | `gota`, or `fakegame` for the first run |

- **Why:** Under the multiplex gateway, a cron script gets only the names on our
  `terminal.env_passthrough` list, read from the **profile** `.env` (A4). The host process env does
  not reach our cron scripts. The full key list is in `autoresearch/distribution.yaml`
  (`env_requires`).
- **Assumed:** Your provisioning has all of these values when it writes `runtime.json`.
- **Default:** Write them at install and on each credential or key rotation. After a rotation,
  restart the gateway, because our workers take identity from the environment when they start.

### C3. The Softmax server URL

- **Q:** Is `COGAMES_API_URL` (set by `runtime.py`) the supported way to tell tools which server
  to use?
- **Why:** Our softmax tool hard-codes `https://softmax.com/api`
  (`autoresearch/src/autoresearch/softmax_tool/platform_cli.py:35`). Your run instructions require
  `--server` because local stacks differ. We will make it configurable.
- **Assumed:** Production is `https://softmax.com/api`, and local stacks set `COGAMES_API_URL`.
- **Default:** We read `COGAMES_API_URL` and fall back to production. Please add `COGAMES_API_URL`
  to the profile `.env` as well, because the host env does not reach our cron scripts (C2).

### C4. The credential is readable by every process in the sandbox

- **Q:** Is it acceptable that the `cga_` token sits in `/opt/data/.softmax/credentials.yaml`,
  where every process can read it, including our research workers' model commands? Or can the
  bridge and chat agent use a location our workers do not share?
- **Why:** Our design gives model commands no direct credential. All platform calls go through a
  trusted tool that checks identity, guarded by a plugin policy. In one uid with `HOME=/opt/data`,
  a model command can run `coworld` as the player directly. That policy is best effort (open risk 1
  in `autoresearch/docs/plans/findings/acceptance-checklist.md` §2.8).
- **Assumed:** Accepted for v0.
- **Default:** Accept it for v0 and record it as a known risk. Revisit with W1.

---

## 3. Workspace, filesystem and users

### W1. Who may write `comms/inbox/`?

- **Q:** Can the bridge run as a separate uid from the gateway and workers, sharing write access to
  `comms/inbox/` only? For example, a second container on the same `/opt/data` mount, with
  `--user 10001` and a shared group.
- **Why:** An inbound message carries the coach's full authority. A `steer` with `urgency: now`
  gets a P0 card. Our workers run as the same uid as everything else in your sandbox. Today,
  nothing below the file layer can tell a bridge message from a forged one. We added
  `AUTORESEARCH_INBOX_WRITERS` (`autoresearch/README.md` `.env` table): queue-keeper then adopts
  only inbox files owned by the listed uids. With #27073 the bridge runs inside the gateway process,
  and with #27068 as a subprocess. Both use uid 10000, so the setting cannot help.
- **Assumed:** Not possible in the current sandbox.
- **Default:** Leave `AUTORESEARCH_INBOX_WRITERS` unset for the first run. queue-keeper logs that
  direct writes are unauthenticated, and the plugin still blocks inbox writes by models, best
  effort. For production, use a separate bridge uid. Inbox directory mode 2770 with a setgid group,
  bridge umask 027. The details are in `softmax-ide-bridge-compat.md` "Trust: who may write the
  inbox". If you set the variable, set it **before** the bridge sends its first message: a wrong
  user gets every write rejected permanently.

### W2. Disk and memory limits

- **Q:** Can research workspaces get a larger tier than 2 CPU / 2 GiB RAM / 10 GiB disk / 256 MiB
  `/tmp` (`fleet.py` `HostConfig`)?
- **Why:** On our test box (4 vCPU, 16 GiB, 200 GiB), one GotA card runs up to 4 native match
  workers, a compiler Hermes child, and Nim builds. With 2 cards at once, 2 GiB is very likely not
  enough. The disk holds the venv, the uv cache, Nim, pinned Polyworld sources, native builds, the
  workspace and its git history, worktrees per card, and `tirith` (32 MB). We have **not**
  measured the peak yet, and will before asking for a number.
- **Assumed:** The fake-game first run fits in the current limits.
- **Default:** Keep the current limits for milestone 1. We measure and propose a research tier
  before GotA. Please set `TMPDIR=/opt/data/tmp` in the runtime env, or tell us to do it in our
  launchers.

### W3. Workspace remote

- **Q:** Where should `AUTORESEARCH_WORKSPACE_REMOTE` point?
- **Why:** The variable is required, and queue-keeper pushes `main` and `card/*` after every
  canonical commit (contract C2). This is our restore path on a new host (README "Restore on a new
  host"). The sandbox has no GitHub credentials, and you list "private policy repository setup" as
  separate work.
- **Assumed:** Nothing exists yet.
- **Default:** For milestone 1, use a local bare repo at `/opt/data/autoresearch-workspace.git`.
  It is covered by your daily EBS snapshots, but it is not an off-host backup. Later, use one
  private repo per player with a deploy key scoped to that repo, provisioned by the API. The same
  mechanism could serve your policy repo.

### W4. Read-only root and installed tools

- **Q:** Will the runtime image include Nim 2.2.10 and a verified `tirith`? Or may our bootstrap
  install them under `/opt/data/tools/`?
- **Why:** The GotA build needs Nim 2.2.10 (`autoresearch/games/gota/GAME.md` "Build and upload"),
  `GOTA_TIRITH_BIN`, and `GOTA_HERMES_PYTHON`. In your image that is `/opt/hermes/.venv/bin/python`.
  The image root is read-only, and the image has only `coworld` and `softmax-cli`.
- **Assumed:** Not needed for milestone 1 (fake game).
- **Default:** Bake Nim 2.2.10 (checksum-verified, as in our shim's `bootstrap.sh`) into the image,
  and set `GOTA_TIRITH_BIN` to the scanner Hermes already downloads to `$HERMES_HOME/bin/tirith`.

### W5. Containers inside the sandbox

- **Q:** Can a research sandbox run containers? We expect the answer is no.
- **Why:** Our G2 build gate runs `coworld run-episode`, which starts Docker containers
  (`autoresearch/games/gota/gates/g2_episode.py`). Your sandbox has no Docker and drops all
  capabilities.
- **Assumed:** No.
- **Default:** We change G2 to a native runner on our side. This is our problem. Please confirm, so
  we do not wait for a container-capable tier.

---

## 4. Bridge contract

### B1. Enable the research relay in the new provisioning paths [BLOCKING]

- **Q:** Can `workspace` in the bridge config be set to `/opt/data/profiles/autoresearch/workspace`
  when our profile is installed?
- **Why:** Today `fleet.py` (#27068) writes `"workspace": None`, and `bootstrap.py` (#27073) writes
  no `workspace` key, so the bridge runs chat only. The IDE then never shows the research buttons.
- **Assumed:** One setting, set at the same time as P1.
- **Default:** Set it from server config when the profile is installed, and restart the bridge so
  that it advertises `state/research`.

### B2. One bridge launch mechanism

- **Q:** Which launch mechanism stays: the `runtime.py` subprocess (#27068) or the gateway platform
  plugin (#27073)?
- **Why:** We do not mind which. Both run as the same uid, which matters for W1. If it is the
  plugin, the bridge shares the gateway process with our cron jobs and dispatcher, so a bridge crash
  goes through the gateway's fatal-error and reconnect path.
- **Default:** Your choice. Please keep `workspace` support in whichever you keep.

### B3. Outbox text is written for an agent, not a person

- **Q:** Can the bridge pass each outbox message through the chat agent before it says it in chat,
  and before it uses it as the IDE task's outcome?
- **Why:** Our outbox messages are written for a Preston-role agent that can read the whole
  workspace. They carry ids, `ar://` refs and terms the coach has not seen. An example task outcome:
  "Card t_6c0a018c (request Q-01M4…) created for inbound task I-01M4…, finished with outcome 'done'
  and was published at f5e50b49…". The runtime acknowledgements are now rewritten in plain words
  (`Done: <task title>` with the card's own summary; ids moved into `refs` and `about`), but
  outbox messages stay agent-facing by design (System Design §7, Runtime §9.1
  "Written for Preston"). Our shim does this translation in `autoresearch-shim/src/ar_shim/preston.py`
  (`main`).
- **Assumed:** Today the bridge says `<title>\n\n<body>` verbatim (`relay_outbox`, `acknowledge`).
- **Default:** For each outbox message, the bridge submits one Hermes run in a **separate session
  id**, so the coach's chat history stays clean. The instruction: "Tell the coach what this means
  and whether they need to do anything. Resolve refs with
  `"$AUTORESEARCH_CLI" comms resolve <uri>`." The bridge says that result, keeps the raw message
  available as a detail (for example in `aside` or a link), and uses the plain text as the task
  outcome. The milestone 1 run can skip this step.

### B4. Not every acknowledgement means success

- **Q:** Can the bridge show a "rejected" outcome when the acknowledging note says the task was
  rejected?
- **Why:** A `note` in reply to a task id is also sent when the task is **rejected** (bad refs,
  admission refused, invalid file). Every runtime message now carries an additive `about` object,
  and an acknowledgement's `about.outcome` is one of `done`, `partial`, `dropped`, `failed`,
  `blocked` or `rejected` (titles read like `Done: <task title>` or `Not started: <task title>`,
  but titles are for reading, not parsing). Today `acknowledge` marks the IDE task `done`/`kept`
  in all cases. Details are in `softmax-ide-bridge-compat.md` "Notes for the bridge".
- **Default:** Close the IDE task with the note as the outcome, as you do now. Do not assume
  success. Map `about.outcome`: `done` → done; `rejected`, `failed` or `dropped` → the status the IDE
  uses for a failed task; `partial` or `blocked` → done with the note visible, or a needs-attention
  status if the IDE has one. Never parse the title. After a stop `steer`, do not reopen a task that the
  coach cancelled.

### B5. Small field fixes in `InboundTask`

- **Q:** Can you make three small field changes in `InboundTask`?
  1. Stop sending `on_behalf_of`. It was removed from the contract on 2026-10-07. We ignore it, so
     there is no hurry.
  2. Fix `why`. The IDE writes `prediction: ""` and a fixed `doneWhen` (`Research.tsx`
     `researchTask`), so every task's `why` is "Done when: The research agent acknowledges this
     task with its result." That tells the agent nothing. Either send the coach's own text as
     `why`, or let the IDE prompt ask for a reason.
  3. Map "Develop policy" (`experimentation`) carefully. Our admission requires a noise
     calibration for the current game version before any experiment (contract C10), so the first
     such task waits behind a calibration card. That is expected, not a failure.
- **Default:** Make changes 1 and 2. For change 3, mention the wait in the IDE prompt text.

### B6. Validate against our schemas at a pinned commit

- **Q:** Can the bridge tests validate `InboundTask`, `InboundSteer` and `Outbound` against
  `autoresearch/schemas/comms/` at a pinned ide-mvp commit, instead of keeping hand-written copies of
  the shapes?
- **Why:** The schemas are now the published contract, and versioning within `ar-comms/0` is
  additive only (`schemas/comms/README.md` "Versioning"). New optional fields and new kinds may
  appear. Readers must ignore unknown fields and unknown kinds. Your `Outbound` model already
  allows extra fields.
- **Default:** Vendor the `schemas/comms/` directory at a pinned commit into the bridge's tests,
  and run the README's validation snippet on the bridge's output.

### B7. Chat-proposed work and answers to questions

- **Q:** Which profile should answer IDE chat, and how should coach answers reach the research
  agent?
- **Why:** The bridge forwards only IDE task records. The research agent also sends `question`
  messages (with options) and expects a `reply`. The coach may also propose work in chat. Neither
  can reach the agent today. The Hermes API URL can select a profile (`/p/<profile>` in
  `probe_commands`).
- **Assumed:** Chat runs in the **default** profile, not in `autoresearch`.
- **Default:**
  - Keep chat in the default profile. Do not point it at our profile: our plugin blocks inbox
    writes and `comms inbound` in every session of our profile.
  - We ship a short guidance file for the chat profile (a skill or `AGENTS.md` fragment). It tells
    the chat agent to read our workspace, explain outbox messages, and send `steer`, `reply` and
    `task` messages with `"$AUTORESEARCH_CLI" comms inbound <kind> --author <name>`. That CLI
    validates, assigns an id and commits.
  - Your installer copies that file into the default profile at install.

### B8. Do not use Kanban comments as a channel

- **Q:** Please confirm that neither the bridge nor the IDE will ever comment on, create, or link
  Kanban cards, and that the Hermes dashboard's Kanban UI is not exposed to IDE users.
- **Why:** Hermes pushes a card comment from any author other than the worker's own profile into
  the running worker as a **user steer, with full authority**. We reproduced this
  (`autoresearch/docs/designs/worker-to-worker-comms.md`, branch
  `jboggs/autoresearch-worker-awareness`). We also block our own workers from `kanban_create` and
  `kanban_link`, so that all work enters through admission. `comms/inbox/` is the only supported
  way in.
- **Default:** Confirmed by you, plus one line in the bridge's `AGENTS.md`.

---

## 5. Inference keys in workers

### K1. The OpenRouter key must reach two trusted code paths [BLOCKING]

- **Q:** Can the workspace key also be written into the profile `.env` as `OPENROUTER_API_KEY`?
- **Why:** `runtime.py` puts the key only in the gateway's process env. Two code paths of ours need
  it, and Hermes **removes provider keys from worker terminals and cron scripts** (hermes-facts
  item 7, A4):
  - Preflight checks the key's remaining credit (`GET https://openrouter.ai/api/v1/key`). It pauses
    the agent below a threshold, because Hermes retries 402 errors forever (A5). Preflight reads
    the key from the environment or else from the profile `.env`
    (`autoresearch/src/autoresearch/runtime/preflight.py`).
  - The GotA build runs a compiler Hermes child that needs the key. The trusted build binding will
    read it from the profile `.env` and give it only to that child. We must not pass it under a
    renamed variable, because model commands could read that. This is open risk 7 in
    `acceptance-checklist.md` on branch `jboggs/autoresearch-gota-build-fixes`.
  Hermes itself reads the profile `.env` for the profile's own model calls.
- **Assumed:** Writing the key to a 0600 file on the workspace disk is acceptable. `runtime.json`
  already holds it on the host.
- **Default:** As asked. Rewrite the `.env` value on key renewal and restart the gateway.

### K2. Budget and expiry values

- **Q:** What values will `HERMES_INFERENCE_BUDGET_USD` and `HERMES_INFERENCE_KEY_DAYS` have for
  research workspaces? What happens at expiry or when the budget runs out? Who is told?
- **Why:** Hermes sends no `max_tokens`, so OpenRouter reserves about $1.97 of headroom per
  kimi-k3 call (A5). Our preflight pauses the agent below $10 remaining. Research runs all day,
  unattended. A chat-sized budget pauses it within hours. When the key expires, research stops
  until an operator renews it (your README: "explicit renewal decision"). Our agent will send a
  `help_request`, which the bridge says in chat.
- **Assumed:** Not decided.
- **Default:** A separate, larger budget for workspaces with research enabled. You decide the
  number. The threshold is configurable on our side. Warn the owner a few days before the key
  expires.

### K3. Model routing

- **Q:** Is it fine for our profile to use its own model through the same key, while the chat
  model is set in your host config?
- **Why:** Your host config sets the chat model (`HostConfig.model = "openai/gpt-6-luna"`). Our
  profile sets its own model in the profile `config.yaml` (`moonshotai/kimi-k3`, fallback
  `z-ai/glm-5.3`). Both use the same key and budget.
- **Default:** Yes. Tell us if the key has a model allowlist.

---

## 6. Scheduling, cron and gateway lifecycle

### S1. The gateway must run all the time while research is enabled [BLOCKING]

- **Q:** Will the gateway run all the time in a workspace with research enabled? And will the
  multiplex gateway (`HERMES_HOME=/opt/data`) run cron jobs for profiles under
  `/opt/data/profiles/`?
- **Why:** Our three cron jobs and the Kanban dispatcher run inside the gateway. Your README
  describes the EC2 sandboxes as always on. Nous mentions "remote wake/sleep" as out of scope. If
  the runtime is ever stopped to save cost, research stops. That is safe, because every protocol
  is crash-safe (C3, C5, C12), but it is silent unless we report it (see O1).
- **Assumed:** Always on, with the multiplex gateway running profile cron jobs. That is the
  topology we verified (hermes-facts items 5 and 7).
- **Default:** Always on. Any future sleep policy skips workspaces with research enabled, or tells
  the owner.

### S2. Restarts and updates while cards run

- **Q:** Can a planned restart (credential rotation, key renewal, image update, profile update)
  wait for running Kanban workers, or at least pause our cron jobs first?
- **Why:** A restart in the middle of a card is safe: the run is reclaimed and retried, and
  handoffs are idempotent. But it wastes model spend. Our shim refuses to update while workers run
  (`autoresearch-shim/deploy/README.md` "Provision the shim").
- **Default:** Pause the three cron jobs, wait until `hermes kanban --board autoresearch list`
  shows no running task (or a timeout passes), then restart. Resume after.

---

## 7. Observability, health and escalation

### O1. Add profile health to readiness

- **Q:** Can `readiness.json` or `doctor` include the autoresearch profile's health when the
  profile is installed?
- **Why:** Your readiness evidence proves the gateway, the terminal and the bridge. It cannot see
  that research is **paused**. Our agent pauses itself on a failed preflight: wrong identity,
  credential problem, low disk, low OpenRouter credit, wrong host Kanban config. Nor can it see
  that queue-keeper has stopped running.
- **Default:** We add `"$AUTORESEARCH_CLI" status --json` (our change). It returns installed
  version and commit, `paused` with a reason, the last queue-keeper run time and result, cron job
  states, and board epoch. It contains no secrets. The bridge's readiness loop runs it in the
  profile and copies the result into `readiness.json` as an optional `research` object.

### O2. Escalation already reaches the coach

- **Note (no action needed):** Our escalations are outbox messages of kind `help_request`. They
  come from failed preflight, health checks (blocked cards, disk, credential), and tool faults:
  rule D-26 in `autoresearch/doctrine/research-agent.md` stops the agent from retrying a broken
  tool and has it ask for help instead. Your bridge already says every outbox message in chat. If
  the IDE ever filters by kind, keep `help_request` visible and prominent. Their schema is
  `schemas/comms/outbound/help_request.json`.

### O3. Logs and how to reach a sandbox

- **Q:** How will James or an agent read our logs in a hosted sandbox?
  - `$PROFILE/workspace/.supervisor/runtime/keeper.log`
  - `.supervisor/runtime/health.log`
  - `$PROFILE/logs/`
  - `$PROFILE/cron/`
- **Why:** Your host has no inbound access, and you use SSM for host operations.
- **Default:** An owner-scoped, read-only "fetch file" through the existing runtime file API
  (Nous) or the host controller (EC2), limited to the paths above. Any shell access stays an
  operator action through SSM.

---

## 8. Versioning and upgrades

### V1. Which ide-mvp commit is installed, and how does it change?

- **Q:** Can the profile source be pinned by a full ide-mvp commit SHA in server config, and
  updated by changing that setting?
- **Why:** `distribution.yaml` has `version: 0.1.0`, but the commit is the real identity. The
  update procedure: replace `/opt/data/dist/autoresearch` with the new commit's files, run
  `hermes profile update autoresearch -y`, and restart the gateway. The update keeps `.env`,
  `config.yaml`, memories, the workspace and `local/`. The next queue-keeper run applies config
  migrations and re-seeds `AGENTS.md` (README "Update"). An update also deletes ignored GotA build
  caches, and bootstrap rebuilds them on the next run.
- **Default:** One server setting (for example `HERMES_AUTORESEARCH_COMMIT`). The bridge's
  readiness report (O1) shows the installed commit. Updates follow S2.

### V2. Which Hermes version is in each image?

- **Q:** Which Hermes version or commit is in `nousresearch/hermes-agent@sha256:9774f4f3…` (the
  #27068 `Dockerfile`) and in `@sha256:c56a1bd1…` (#27023, #27073)? Will there be one digest for
  both hosting paths?
- **Why:** We verified Hermes behaviour on **v0.21.5** only (`hermes-facts.md`; amendments A1 to
  A13). That covers the Kanban dispatcher config, env passthrough, the scrubbing of Kanban env vars
  in worker subprocesses, and plugin hooks. `gateway_plugin.py` says it was verified against
  Hermes `d687605c`, which is `main` on 2026-10-08, after v0.21.6. Our manifest says
  `hermes_requires: ">=0.21.5"`, so it would install on a newer version without our checks.
- **Assumed:** Newer than v0.21.5.
- **Default:** You tell us the digest and commit. We rerun our Hermes integration tests
  (`autoresearch/tests/integration/`, marker `hermes`) against that image before milestone 1, and
  set `hermes_requires` to the range we have verified.

### V3. Contract changes on either side

- **Q:** Can we agree on a rule for contract changes on either side?
- **Why:** Your bridge and our comms contract change separately.
- **Default:**
  - Additive changes within `ar-comms/0` need no coordination.
  - A breaking change bumps the major version and is announced in both repos' integration docs:
    `autoresearch/docs/integrations/` here, and the bridge README there.
  - If a bridge change touches `comms/` behaviour, link an updated example file in
    `schemas/comms/examples/`.

---

## The first end-to-end run (what "blocking" means)

The goal is to prove hosting, install, scheduling and the bridge relay, with no GotA, Nim, Docker
or real Softmax calls. Use one sandbox; your Lima VM from `devops/hermes-fleet/README.md` is
enough. Set it up like this:

1. The profile is installed from staged files at a pinned commit (P1, P2).
2. The `.env` has these values (P3, C2, W3, K1):
   - `AUTORESEARCH_GAME=fakegame`;
   - `AUTORESEARCH_SOFTMAX=fake`;
   - `AUTORESEARCH_WORKSPACE_REMOTE` set to a local bare repo;
   - the workspace OpenRouter key;
   - `OPENROUTER_MODEL=z-ai/glm-5.3-flash`.
3. The host Kanban config is set (P4).
4. Bootstrap has run, and the cron jobs are resumed (P3).
5. The bridge has `workspace=/opt/data/profiles/autoresearch/workspace` (B1).
6. The gateway runs all the time (S1).

The run passes when all of these happen:

- The IDE shows the research buttons.
- **Analyze replay** produces `comms/inbox/<player>:task:<key>.json`, and queue-keeper adopts it
  within about 2 minutes.
- A card runs on the `autoresearch` board.
- A `Done: <task title>` note (with `about.outcome: done`) appears in `comms/outbox/`, the chat shows it, and the IDE task closes.
- Cancelling a task produces a `steer` that a P0 card answers.
- After a sandbox restart, there are no duplicate cards and the bridge cursors resume.

Blocking items: **P1, P2, P3, P4, C2, B1, K1, S1.** C1 blocks the second run (real Softmax). W2,
W4 and W5 block GotA. Everything else is for production.
