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

