# Survey findings for the Softmax integration plan (2026-10-09)

This file is the evidence appendix for [plan.md](plan.md). It was checked against metta `62ab977a58` and ide-mvp
`origin/main` `36f5d16`. Paths below are in metta unless they say ide-mvp.

## Rohit's system

- **Merged.** The shared EC2 stack is on metta `main`: #27077, #27078, #27079, #27081, #27082, #27110, #27113, #27114,
  #27146. The only open Hermes PR is #27023 (readiness checks).
- **Production deployment.** `values.yaml:536-542` points at one sandbox host, `hermes-rohit-validation`
  (`m7i.2xlarge`: 8 vCPU, 32 GiB, 200 GiB; `main.tf:181`, `:191-193`).
- **What Rohit reports verified.** Chat, policy upload and a hosted GotA smoke episode. We have not re-checked these.
- **Not tested.** A fresh signup, two users on one host, and host recovery. Promotion and league submission were
  left out on purpose.

## Provisioning and runtime

- **Players.** One owner, one hosted player. A second allocation gets 409 (`service.py:166`). The bridge must
  report within 3 minutes of release (`:32`, `:133`).
- **Credentials and key.** The player credential is a fresh `cga_` token (`ec2.py:33`). The inference key is issued
  once, from one global budget of $10 for 30 days (`ec2.py:69-87`, `hermes/models.py:21-25`). Reconnecting keeps the
  key and does not renew it.
- **Profile install.** The runtime installs `rohit-optimizer@f6508fb` as profile `optimizer` and routes chat to
  `/p/optimizer/`.
- **Research relay.** Switched off: `workspace=None` (`fleet.py:156`).
- **Our env passthrough.** `runtime.py:61` overwrites `terminal.env_passthrough`, which would remove the variables
  our cron jobs need.
- **Updates and image.** The runtime never updates an installed profile (`runtime.py:47-48`). Each workspace's image is
  pinned at first start (`fleet.py:161-162`). `runtime.json` (route, relay path, model) is rewritten only when the
  bundle id changes or the file is missing (`fleet.py:137-163`).
- **Workspace records.** Records live in S3 (`workspaces/<id>.json`, with `status/<id>.json` and
  `credentials/<id>.json`), not on the host (`hermes/store.py:43-94`). They contain the player token, service key and
  inference key (`store.py:68-76`). A replacement host reconciles every record for its host id: a record whose disk,
  `runtime.json` and `image` are missing gets a new empty disk and the host's current image (`fleet.py:118-131,139-163`).
  So a host replacement removes no records.
- **Slots.** Records are sorted by `(created_at, id)` (`store.py:94`); records past `max_workspaces` are stopped and
  report "Waiting for host capacity" (`fleet.py:251-259`).
- **Keys.** `configure` returns early when a record already has a key (`ec2.py:72-73`), so a budget change applies only
  to records created afterwards.
- **No delete path.** The Hermes routes are start, status and bridge report only (`routes/app/v2/cogents/hermes/router.py`);
  one hosted player per owner is enforced (`service.py:166-167`). Removing a workspace needs operator steps (S3 objects,
  the `HermesInstance` row, the `CogentAgent`).

## Sandbox (`fleet.py:167-204`)

- **Container.** runsc (gVisor) as uid 10000, with a read-only root, `--cap-drop=ALL`, `no-new-privileges`
  (`fleet.py:179-182`), so nothing inside can switch user. It runs with `--restart=no`; the controller starts an exited
  container again on its next 15-second pass (`fleet.py:177,205-206,294`), so a deterministic startup failure is a
  restart loop. Docker inter-container traffic is disabled on the host (fleet README, `icc: false`).
- **Limits.** `--cpus 2`, 2048 MB RAM, 512 pids, a 256 MiB `/tmp`, and a 10 GiB ext4 data volume.
- **Host check.** `check_host` checks RAM and disk only (`fleet.py:57-66`).

## Deployment

- **Host replacement.** Terraform sets `user_data_replace_on_change` and `prevent_destroy` (`main.tf:186`, `:210`),
  so an image or user-data change replaces the host.
- **Unknown procedure.** #27146 replaced the host "with explicit approval". The exact procedure isn't written down.

## Bridge (`bridge.py`)

- **Task outcomes.** Every replying note closes the IDE task as done/kept (`:172-175`, `:524`). `about.outcome` is
  not read.
- **Approvals.** A Hermes run waiting for approval is stopped (`:393`).
- **Inbox creation.** `deliver` writes `.<id>.tmp` then renames it, creating `comms/inbox` with `mkdir(parents=True)`
  (`bridge.py:413-422`), so it can create the workspace directory before our bootstrap does.
- **Acknowledgements.** The bridge acknowledges each note with an idempotency key built from its outbox seq and the
  task revision (`bridge.py:487-490`), so a second note for the same task applies. ide-mvp publishes only one
  acknowledgement per inbound task today (`comms/publisher.py:503-505`, `find_reply` at `:404-419`), so a task
  acknowledged `blocked` is never followed up.

## Permits

- **Gates.** An XP request or league submission made with a `cga_` credential must pass the coach's league choice and
  then a permit check (`experience_requests.py:465-508`, `league_submissions/service.py:152-169`; rules in
  `permits.py:1-14`).
- **Only after setup.** These gates apply only once the coach has set the player up (a live `state/league` or
  `profile/coach`, `permits.py:13-14`, `:445-450`). Before that, the `cga_` token acts like any player's token: no league
  choice and no daily credit cap. The token has no expiry; revoking the `CogentAgent` makes the controller stop the
  sandbox (`fleet.py:84-95`).
- **Our gap.** Our Softmax tool handles 402 only (ide-mvp `xp.py:47`). A 403 `permit_required` is not handled.

## rohit-optimizer

- **Access.** Public repo.
- **Policies.** GotA policies are BASIC file uploads.
- **Autonomy.** Its SOUL says "No background loop is enabled by installing this profile". That differs from our
  always-on cron loop.

## Mismatches on our side (ide-mvp)

- **Credit floor.** Hard-coded at $10 (`preflight.py:63`). The check is `remaining < floor` (`preflight.py:245`), so a
  fresh $10 key (`limit_remaining = 10.0`) passes and pauses after its first spend.
- **Seeding.** `_seed` copies into the existing directory and runs `commit_all` (`bootstrap.py:195-211`). Files the
  bridge already wrote to the inbox would be swept into the seed commit.
- **G2 gate.** Calls `coworld run-episode`, which needs Docker (`g2_episode.py:64-67`). The sandbox has no Docker.
- **Native helpers.** G1, G3 and G4 need the Nim native helpers. Polyworld is public, so building them needs no
  credential.

## Cost reference

- **Fakegame.** One cycle on glm-5.3 cost about $1 (ide-mvp `findings/fake-cycle.md:53-66`).
- **EC2 test box.** Real GotA on kimi-k3 spent about $67 in about a day and a half, with $25/day caps reached.

## Multiplexed gateway and the profile environment

- The runtime runs one `hermes -p default gateway run` (`runtime.py:146`) whose environment has no profile `HERMES_HOME`,
  `AUTORESEARCH_CLI` or player credential (`runtime.py:123-138`).
- Hermes v0.21.6 binds the routed profile's home as a context-local override per multiplexed turn:
  `hermes_constants.get_hermes_home()` returns the override first (`hermes_constants.py:111-118`), and
  `agent/secret_scope.py` handles the multiplex context (`set_multiplex_active`, `set_multiplex_context`,
  `serves_routed_profile`; plugin secret lookups fail closed under multiplexing). Our plugin already resolves its profile
  home with `get_hermes_home()` (amendment A7). So plugin code in a chat turn can find the profile's `.env` and build the
  environment for CLI subprocesses itself (plan 3.7).

## Host sizing

- Current host `m7i.2xlarge` (8 vCPU, 32 GiB), 200 GiB gp3 root, retained on termination (`main.tf:181,191-196`).
  `check_host` reserves 1 GiB RAM and 5 GiB disk and checks RAM and disk, not CPU (`fleet.py:57-66`).
- James's sizing (D12): 8 CPU / 16 GiB / 100 GiB / 2 GiB `/tmp` per sandbox, two per `m7i.4xlarge` (16 vCPU, 64 GiB)
  with a 300 GiB root: RAM 33 of 64 GiB, disk 205 of about 290 GiB free, CPU 16 caps on 16 vCPU.

## Unverified (checked by plan tasks)

- **Plugin hooks:** verified 2026-10-09 (Task 5 Step 4, below).
- **Memory:** use under gVisor at the proposed sizes.
- **Task status:** whether a cogent may set an IDE task to active/paused.
- **Config:** whether `hermes config unset` fails on a missing key.
- **Profile update:** whether `hermes profile update` resets cron jobs to paused.
- **Replacement mechanism:** how #27146 got past `prevent_destroy`.
- **Cross-uid files under gVisor:** group-readable outbox and a setgid inbox for a separate bridge user (plan Task 26).
- **Installed name:** whether anything in our profile assumes the install name `autoresearch` (plan Task 27).

## Added in plan revision 4 (review round 2)

- **Installed manifest name.** Hermes rewrites a distribution's `name` to the install name and records the install path
  as `source` on install and update: `hermes_cli/profile_distribution.py:326` (`canon = _canon_valid(override_name or
  manifest.name)`), `:332-333` (`manifest.name = canon`, `manifest.source = provenance`), checked at v0.21.6 `818c13be`.
  So rohit-optimizer and our profile both read `name: optimizer` when installed under that name. Identify ours by our
  runtime's marker or by `source:`.
- **Output hook.** v0.21.6 has a `transform_llm_output` plugin hook, fired once per turn with `response_text`,
  `session_id`, `model`, `platform`, `turn_id`, before the reply is stored; the first non-empty string returned replaces
  the reply (`agent/turn_finalizer.py:480-519`, re-checked at v0.21.6 `818c13be`; `hermes_cli/plugins.py:111-112`). It returns early on an empty reply, and with several plugins the first non-empty string wins. Plugin code can append text the coach
  sees without relying on the model.
- **Gateway threads.** The gateway runs turns on a `ThreadPoolExecutor` (`gateway/run.py:4297`), so plugin state shared
  across turns needs a lock.
- **Acknowledgement state.** `acknowledge_inbound` visits only `routed` records (`comms/publisher.py:546-548`) and moves
  them to `acknowledged` once (`:566-567`); a follow-up must start from `acknowledged` records.
- **Record authorship.** Cogent records store `author_role` (`coach`, `cogent`, `visitor`;
  `observatory_core/models/cogents.py:72-76,144,159`), and the permit route branches on the writer role
  (`permits/service.py:35`). Whether the player-facing read API returns it is unverified.
- **gVisor networking (unverified).** Each `runsc` container is its own sandbox with its own network stack, so a second
  container joined with `--network=container:` very likely does not share the first's loopback. Task 26's spike checks it.

## Verified in Task 5 Step 4 (coach chat on the pinned image, 2026-10-09)

`tests/integration/test_plugin_hermes.py::test_coach_chat_routes_by_platform`, run in an image
built from `packages/observatory-hosted-worker/Dockerfile` lines 1-4 and 8-10 (base
`sha256:9774f4f3…`, Hermes v0.21.6 `818c13be`) with the `fleet.py` flags Docker Desktop allows
(no runsc), the profile installed as `optimizer`, one `GATEWAY_MULTIPLEX_PROFILES=true hermes -p
default gateway run`, runs posted to `/p/optimizer/v1/runs` the way the bridge posts them, model
`z-ai/glm-5.3`. Passed. Model spend for the passing run about $0.014 (OpenRouter key usage).

- **Plugin hooks fire for multiplexed API-server runs.** `pre_llm_call` got `platform=api_server`
  and the bridge's `session_id`; `pre_tool_call`, `post_tool_call` and `transform_llm_output`
  fired in the same turns. The coach tools ran with the routed profile's `.env` (`status`
  returned `ok: true`), so `get_hermes_home()` is the routed profile inside tool handlers too.
- **Config per platform is read from the routed profile.** Hermes resolved `api_server` to
  `{autoresearch_coach}` only and `cli` to the full research list without the coach toolset;
  only our plugin registers `transform_llm_output`.
- **The API server passes `/pause-research` to the run** (not a gateway slash command): the
  plugin matched the line and committed a `control` inbound. No bridge-side fallback is needed
  for D15.
- **`transform_llm_output` output is the run's `output`:** the bridge would say the reply ending
  in the plugin's `To send exactly this, reply: confirm P-xxxx`.
- **Tool search.** v0.21.6 may hide plugin tools behind the bridge tools `tool_search`,
  `tool_describe` and `tool_call` (`tools/tool_search.py`); `tool_call` is unwrapped so
  `pre_tool_call` sees each real tool name. The model called `tool_describe` in the first run.
  The chat allowlist lets the two catalog reads through and a `tool_call` only when every call
  it names is a coach tool (`plugin/policy.py` `chat_tool_policy`).
- **API server key.** The adapter refuses to start with an `API_SERVER_KEY` under 16 characters
  (`gateway/config.py` `_has_usable_api_server_key`); the gateway then logs only "No messaging
  platforms enabled".


## Task 12 local end-to-end (2026-10-10)

**Status: finished on 2026-10-12. Steps 1-12 passed, with four real bugs found.** Two were fixed and verified on the way (the literal `${PATH}`, 3f7e0237e0; the login-shell PATH, 35b61b447b). One is open: the bridge ends the whole runtime on any API error (metta). The fourth, the rerun close racing the dispatcher (ide-mvp), is fixed on branch `jboggs/autoresearch-task12-fixes` (8ef7bf0, af31ae0), by tests only: it was not run again end to end. Spend $10.11 of the $20 key. VM and dev stack stopped, state kept. Running log: `.superpowers/sdd/plan/task-12-log.md`. (Earlier stops: Step 2, the broker approval reached nobody; Step 7, the Mac's free disk fell to 1.2 GiB from activity outside the run. The session was also suspended from 23:28 PDT Oct 10 to 11:32 PDT Oct 12; the workspace ran unattended in that window and paused itself on the credit floor after 13 minutes.)

Code under test: metta-wt `jboggs/hosted-autoresearch-start-pause` 59817c2f49 (on main 89fbbaa247); pin ide-mvp
fc85e37 (tree 2069e73a). Host: Lima vz VM, aarch64, 10 CPU / 20 GiB / 60 GiB sparse disk.

- **Workspace disk not at production size.** Host config uses the Task 9b CPU/RAM/pids/tmp sizes
  (8 CPU, 16384 MB, 2048 pids, 2048 MB /tmp) but `disk_gb: 8`, not 100, for Mac disk reasons.
- **Step 1 passed.** `bootstrap.sh` (gVisor 20261005.0 aarch64, runsc registered), host archive installed per
  user-data (sha256 269d3a22…), `fleet --check-host` passed. `--check-host` still needs
  `HERMES_FLEET_BUCKET`/`HERMES_FLEET_KMS_KEY_ID` set even though it never touches S3 (it builds the store client
  first); minor.
- **Images (arm64, built inside the VM from piped contexts).** Ours at the pin: sha256:bd4b71d3…; Rohit's origin/main
  image: sha256:f886be4f…; newer pin A (fc85e37 + one README line): sha256:20d98298…; failing pin B
  (`hermes_requires: ">=0.99.0"`): sha256:830cd322…. The VM's Docker has no buildx; `bootstrap.sh` does not need it
  for production (images are pulled), but local image builds in the VM need `apt-get install docker-buildx`.
- **Finding: the Dockerfile builds on arm64 with an unusable Nim.** The x86_64 Nim tarball unpacks fine, the build
  succeeds, and `/opt/nim-2.2.10/bin/nim` fails with "Exec format error" at run time. Harmless for fakegame and for
  the amd64 production host. But nothing fails the build or says the image is amd64-only. A GotA run on an arm64 host
  would fail late, in the native gates.
- **Finding (docs): `metta dev doctor` does not exist** in this checkout (`No such command 'doctor'`). The fleet
  README's "Local verification" section tells operators to run it.
- **Finding (docs): the hosted-worker README's distribution paragraph is stale.** It says "The current pin predates
  `AUTORESEARCH_AUTONOMY`, so the image build fails until the lock is re-pinned". The lock is fc85e37, which declares
  it, and the build passed. The fleet README is correct.
- **Blocker: the approval could not reach James.** The token broker now notifies through Slack, and it reported "nobody
  was notified: james@softmax.com has no Slack binding". The README and the task brief both expect a Discord DM.
  Request a56b9734-753f-4615-83fd-128fe05b5180 (scope `openrouter.manage`, grant once) expires 18:30:47 PDT. Fix
  this with `AWS_PROFILE=softmax python scripts/token_broker_client.py set-slack`, or approve from the CLI with
  `uv run python scripts/token_broker_client.py approve <id>`. Every hosted-key operation in the M2 runbook depends on
  this approval path too.
- **Approval path, resolved.** After James's Slack was bound (james@softmax.com -> U0C58T83Q7P), a new request
  (88ceea68-…) reported "owner notified via: slack" and was approved in about 15 s. Owner commands (`approve`,
  `set-slack`, …) need an AWS SSO session in the infrastructure account. James has ReadonlyAccess there, which is
  enough; his `softmax` profile asks for PowerUserAccess and is refused, so docs that say `AWS_PROFILE=softmax` for
  owner commands are wrong for him.
- **Local stack fixes (environment, not code).** Docker Desktop had run out of network address pools ("all predefined
  address pools have been fully subnetted"; 36 empty leftover `metta-dev-*` networks), and `floci.sh` hides that
  error (`docker network create ... 2> /dev/null`), so floci failed later with "network ... not found". The
  worktree also had no `node_modules` (`prisma: command not found` in softmax-com-db-migrate). The seeded user
  needs `tosVersion = "2026-02-v1"` as well as `profileCompleted`, or /ide redirects to /complete-profile.
- **Steps 2-4 passed.** The first /ide visit created player ply_dbe05244-… with hosting controls. Start Hermes →
  container `healthy` about 45 s after the click; IDE showed "Hermes connected" and the Analyze replay / Develop
  policy buttons; `.softmax-distribution-commit` = fc85e378… (the pin); runsc, 8 CPU, 16 GiB, 2048 pids; the three
  cron jobs installed and resumed ([active]).
- **Coach Controls gap.** The IDE Controls page sets League (set to Gods of the Arena), Practice, Hosting and Do next
  only. There is no control for `askBefore` or `dailyCredits`; `/limits` shows the default
  `askBefore: [submit, spend, join-team, public-post, share]`, `dailyCredits: null`. So M0 Step 5's
  "`askBefore: []`, finite `dailyCredits`" cannot be done in the IDE today.
- **Hermes warns about every SQLite database** that `/opt/data` is "a cross-VM filesystem (virtiofs/9p)" with
  non-coherent WAL shared memory. This is gVisor's gofer mount of the ext4 volume. Only one sandbox uses it, so it is
  probably harmless. Check what Hermes changes when it detects this, because production uses the same mount.
- **BUG (blocks M1 and M2): routed cron jobs get a literal `${PATH}`, so the profile never bootstraps.** `runtime.py`
  `HOSTED_SETTINGS` writes `PATH='/opt/nim-2.2.10/bin:${PATH}'` to the profile `.env`. Under
  `GATEWAY_MULTIPLEX_PROFILES`, Hermes builds a routed profile's environment with `agent/secret_scope.py`
  `load_env_file`, which does not expand variables. In the container,
  `build_profile_secret_scope(Path("/opt/data/profiles/optimizer"))["PATH"] == '/opt/nim-2.2.10/bin:${PATH}'`
  (python-dotenv does expand it, which is presumably what the "braces required" comment was tested against). The
  cron runner (`cron/scheduler_script.py` `_run_job_script` → `build_subprocess_env`) uses that scope, so
  queue-keeper fails with `autoresearch launcher: uv not found on PATH` on its first run. `AUTORESEARCH_CLI` is
  never written, and the coach correctly answers that its tools are not set up. Every hosted workspace hits this,
  GotA included. Likely fix: write an absolute PATH (the image's `ENV PATH` with `/opt/nim-2.2.10/bin` first), and
  add a test that the routed scope's PATH contains `/usr/local/bin`. Introduced in e6343f1be8.
- **PATH fix verified (3f7e0237e0).** The workspace was moved to the rebuilt image the Step 10 way (new id written to
  `runtime/image`, `docker rm -f`; the controller recreated it, `healthy` in 35 s). The routed scope PATH is now
  absolute; queue-keeper's next run printed `exit_code 0, pushed true`; `AUTORESEARCH_CLI` was written and the
  workspace was seeded.
- **Steps 5 and 6 passed.** Chat `status` reply (on request, key $20, no ids); `ls` refused; steer proposal →
  confirm → one inbox file, committed by queue-keeper; a second confirm sent nothing. The "Sent P-… as I-…" line
  arrives with the next reply, not the confirm's own reply (the plugin reports results one turn late). Analyze
  replay was adopted in 30 s, wrote `started.json`, and its card finished: Done note in chat, IDE task done/kept,
  about 5 minutes end to end. A steer before any coach task stayed in the planning backlog and did not start the
  agent. Develop policy then cancel: urgent stop steer, P0 card, the develop card dropped, the IDE task stayed
  cancelled.
- **IDE gap: no control to cancel a queued coach task.** Focus "Remove" is for intentions; "Turn it down" appears
  only on ideas the cogent suggested; Controls "Stop" ends the active task. Step 6 used the same records write that
  "Turn it down" makes.
- **`/pause-research` works.** The control reached the inbox in 3 s and queue-keeper applied the coach hold about
  3.5 minutes later (`status.coach_hold` true).
- **BUG (blocks M1 and M2): card terminals use a login shell that resets PATH.** The image sets PATH only through
  Dockerfile `ENV`. Hermes runs terminal commands from a login-shell snapshot, and the image's `/etc/profile` resets
  PATH to `/usr/local/bin:/usr/bin:/bin:...`. So `/opt/hermes/.venv/bin` (`hermes`), `/opt/softmax-tools/bin`
  (`softmax`, `coworld`) and Nim are missing in every card terminal; only `uv` (/usr/local/bin) survives. On
  fakegame, `softmax bootstrap-champion` failed with `KanbanError: ... hermes not found: hermes` (ide-mvp
  `queue/kanban_queue.py` runs `hermes` by name) and the card blocked as a tool fault. GotA would also lose the
  Softmax CLIs and Nim. Fix in the image: symlink those binaries into `/usr/local/bin`, or add an
  `/etc/profile.d` script that restores the image PATH. Add an image test that `bash -lc 'command -v hermes
  softmax coworld uv'` succeeds.
- **Resource use so far** (fakegame, two experiment cards running): container 90.8% CPU, 1017 MiB of 16 GiB, 183
  pids; `/opt/data` 610 MB.
- **The hold stops new work.** Under the coach hold (04:39-04:48Z, about 9 of the 10 minutes) no card was admitted. The
  card already running finished, as `blocked` with the same `hermes not found` tool fault, and its follow-up request
  waited. `/resume-research` was not exercised.
- **Measurements so far.** Peak seen: container 90.8% CPU and 1017 MiB of 16 GiB with two fakegame experiment cards;
  at rest: 1.5% CPU and 532 MiB; 183 pids; `/opt/data` 613 MB; no OOM. Workspace disk 8 GB (not production size).

### Resume on 2026-10-10 22:52 PDT to 2026-10-12 (Steps 7-12)

Code under test: metta-wt top 80b9db87b1 (fix 35b61b447b); pin ide-mvp fc85e378. Final image
`hermes-runtime:ar-fc85e37-fix2` sha256:56e50cb82f5df0227d56715222dbe21083facc34f2cec83b97811356a25e59a2 (arm64, built
in the VM; nothing was built on the Mac).

- **Login-shell fix verified (35b61b447b).** In the rebuilt image and in the running workspace,
  `bash -lc 'command -v hermes softmax coworld nim uv'` resolves all five in `/usr/local/bin`. In a real card:
  after the unblock, `softmax bootstrap-champion` ran, the promotion was submitted and the first champion exists.
- **Step 7 passed.** Pause: no card admitted for about 9 minutes under the hold. `/resume-research`: the reply said
  "Sent /resume-research as I-…" at once; queue-keeper committed "coach resume" 100 s later; chat showed "Research
  resumed."; held requests were admitted within 25 s.
- **Unblocking a tool-fault card works as documented, with one race (BUG, ide-mvp, fixed in 8ef7bf0 and af31ae0).** `hermes kanban unblock`
  put both cards in `ready` and the dispatcher claimed them within seconds (a human unblock is not an admission, so
  the coach hold does not stop it). For one card, health wrote the repair request and stopped the rerun, as
  documented. For the other, `runtime/blocked.py` `_close_rerun` saw the card `pending`, wrote the repair request,
  then `queue.drop` re-read the card, found it claimed, and raised `ValueError: cannot drop a claimed card; only
  pending cards` (queue-keeper: "step blocked failed: RuntimeError: blocked card(s) not handled"). The rerun kept
  running and did the real work; once its promotion existed, `promotion.covers_task` made later passes skip it. So
  the original card and its repair card both ran. No duplicate submission happened, only because the repair agent
  checked first ("Nothing needed redoing"). Proposed fix: when `drop` fails because the card was claimed, fall
  through to `queue.stop` in the same pass (or use `stop` for `ready` cards, which `STOPPABLE_STATUSES` allows), and
  do not leave a repair request behind for a card that was not closed.
  **Fixed in 8ef7bf0 and af31ae0** (branch `jboggs/autoresearch-task12-fixes`): `_close_rerun` drops a waiting rerun
  and stops a claimed one, also one claimed between the listing and the drop. The repair request of a stopped rerun
  is written by a later pass, only when the board no longer records a worker for the stopped run: `queue.stop` does
  not signal the worker, and Hermes ends it 120 s or more later. A `todo` or `scheduled` rerun is left until Hermes
  makes it `ready`. Not covered: a human who unblocks the card again after its repair was requested (the next pass
  closes that rerun).
- **BUG (minor, ide-mvp): `status.last_keeper_run` shows `"RuntimeError:"`** after a keeper step fails. It reads the
  first token of the last `keeper.log` line, which is then the last line of a traceback. Use the last line that
  starts with a timestamp. **Fixed in 1912308** (same branch).
- **Step 8 passed (blocked, then done).** fakegame has no blocking request, and a hand `hermes kanban block` publishes
  no `blocked` handoff, so the IDE would not show paused. The test used a coach task (the same records write the IDE
  buttons make) whose text made the agent hand off `blocked` until a test file existed. Variant 1: the agent handed
  off `blocked` and completed the card; the IDE task went active/paused in about 8 minutes and stays paused (the
  documented no-follow-up case in softmax-ide-bridge-compat.md). Variant 2: the agent left the card blocked on the
  board; IDE paused after 9.5 minutes; health opened repair request `repair-blocked:t_d356e402` 2.5 minutes later;
  the repair card finished `done`; the follow-up acknowledgement closed the IDE task as done/kept 10 minutes after
  the pause. Task 10b works against the real local API. No human unblock was needed.
- **Finding: the credit floor halves a $20 key.** The distribution pauses research when the key's remaining credit
  is below $10 (`AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD` default). With `HERMES_INFERENCE_BUDGET_USD=20` the agent
  paused itself after $10.05 ("preflight failed: OpenRouter key has $9.95 left, below $10: top it up (A5)") and
  stayed paused for 35 hours with no further spend. The guard works. The hosted budget and this floor must be chosen
  together: either set the floor in the hosted settings or size the key as budget plus floor.
- **Finding: spend rate.** Cards on fakegame (kimi-k3 for experiments and investigation, glm-5.3 for the rest, up to
  two at once) spent about $7.80 in 47 minutes, roughly $10 per hour, once the agent had started. On-request
  autonomy does not bound this after the first coach task.
- **BUG (metta, open, blocks M2): one failed API call in the bridge ends the whole runtime.** Seen twice.
  (1) The local API answered 500 while its database was down (an outside restart of the dev stack's postgres).
  `bridge.py` `chat_worker` (`response.raise_for_status()` on `/wait`, line 663) raised out of the TaskGroup, the
  runtime exited 1, and the container stayed down for 47 minutes, until the API was healthy; gateway, cron and any
  running card die with it. (2) After a state disk was restored whose bridge cursor was behind the server, the
  bridge answered an already answered chat message again with the same idempotency key; the API refused with 422;
  `say` (line 268) raised; the pending reply is persisted in `softmax-bridge/state.json`, so every restart retried
  it and died: a permanent crash loop that needed a hand edit of `state.json`. Case 2 is what the M0 move and
  restore procedures can produce. Proposed fix: retry 5xx and transport errors with backoff inside the workers; treat
  a 4xx on publishing a reply as final for that message (log it, drop the pending reply, advance the cursor); never
  let a bridge worker's exception stop Hermes.
- **Step 9 passed.** `docker stop`; the controller started the same container 14 s later; healthy after 30 s. Same
  board epoch (e1), same 28 cards and no duplicates, same workspace HEAD, bridge cursors resumed (54/54/27), marker
  and `.env` unchanged, chat history kept, chat answered.
- **Step 10 passed.** Newer pin: one `hermes profile update`, cron active, `.env` intact, marker moved, 40 s to
  healthy. Failing pin (`hermes_requires: ">=0.99.0"`): healthy in 25 s on the old profile, marker unchanged,
  `status.distribution.update_failed` = {commit, error}; going back to a good image cleared it. D2: a fresh state
  disk started on Rohit's origin/main image (it installed rohit-optimizer v0.2.0 as `optimizer`), then switched to
  our image: the old profile moved to `profiles/.optimizer-replaced-20261012T184305Z`, ours installed fresh with the
  pin's marker, chat worked, and queue-keeper bootstrapped on its first run (about 90 s after healthy; a status
  question before that gets a plain "setup has not run yet" answer). `/opt/data/rohit-optimizer` (1.4 MB) stays on
  the disk. The fresh "workspace" was a fresh disk for the same workspace record, because the local stack has one
  hosted player per account and one slot.
- **Step 11 answers.** `hermes config unset` on an absent key exits 1 (v0.21.6); the runtime's `config get` guard is
  needed and works. `hermes profile update` leaves cron jobs active (they are paused only after `profile install`),
  so the resume after an update is a no-op. Hermes's SQLite warning ends "so journal_mode=DELETE is used instead":
  under gVisor every database on `/opt/data` runs without WAL, in production too.
- **Measurements.** At rest 1.8% CPU, 374 MiB of 16 GiB, 79 pids. Peak sampled 90.8% CPU, 1017 MiB, 183 pids with
  two experiment cards (the busiest 47 minutes were not sampled). No OOM kill. `/opt/data` 685 MB after 37 card runs;
  the 8 GiB state file uses 1.1 GB on the host. Image 5.17 GB. Start to healthy: 20-40 s on every restart or image
  change.
- **Baseline (fakegame, 27 cards, `report baseline`).** Experimentation on kimi-k3: 11 cards, 13 runs, $7.35, 43
  minutes of card time. Investigation on kimi-k3: 6 cards, $2.03. Planning, scouting and maintenance on glm-5.3: 10
  cards, $0.82 together. Hermes's estimate ($10.195) is within 1% of the key's usage ($10.105). One experiment
  reached evidence in 466 s with 80 episodes; its cards cost $0.71. Report gaps: the day with all the spend has
  `key_usage_delta_usd: null` and `api_server: null`; build `wall_s` and gate times are null on fakegame.
- **Step 12.** Stack and VM stopped 11:49 PDT Oct 12. Spend $10.105 of $20. The test key was not deleted (it expires
  2026-10-13T03:57Z). Left: the VM disk (workspace state, four images), the postgres volume and floci data of port
  offset 750, and one IDE task still paused by design.
- **Local notes.** `docker build -` in the VM needs a gzip context from macOS tar (a plain bsdtar stream is read as
  a Dockerfile). `/etc/hermes-fleet/host.json` on the VM still names the first, deleted image; set it before
  starting a new workspace there. The controller was a transient `systemd-run` unit and must be started again after
  each VM start.
