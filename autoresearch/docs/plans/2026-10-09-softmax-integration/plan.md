# Autoresearch on Softmax-Hosted Hermes: Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A coach who clicks **Start Hermes** in the Softmax IDE gets the autoresearch agent (not Rohit's `optimizer` profile) in their always-on EC2 workspace. It runs its research loop there, answers in the IDE chat, takes work from the IDE research buttons, and can be paused and resumed by the coach.

**Architecture:** The autoresearch distribution is baked into the hosted runtime image as a pinned artifact: a `git archive` of `autoresearch/` at one ide-mvp commit, locked by commit and tree id in a metta lock file. No GitHub credential reaches the runtime. `runtime.py` installs or updates it as Hermes profile `optimizer` (D2) (falling back to the installed profile if an update fails), writes our `.env` keys and the host Kanban settings, creates a local bare git remote for the workspace, and resumes our cron jobs. `fleet.py` keeps the `/p/optimizer/` route, turns the research relay on, and runs larger sandboxes. ide-mvp adds hosted-mode behavior: start on request, a coach pause/resume control, a read-only coach chat persona, a follow-up acknowledgement for blocked tasks, permit handling, a Docker-free G2, and a baseline cost report.

**Tech Stack:** Hermes v0.21.6 (image `nousresearch/hermes-agent@sha256:9774f4f3…`), Python 3.12 (profile venv) and 3.14 (Hermes), uv, pytest, Bazel and Ruff/ty (metta), Docker + gVisor, Spacelift/OpenTofu, OpenRouter, coworld 0.1.58, softmax-cli 0.26.38.

**Spec:** the "Integration architecture" section below. It argues from: [findings.md](findings.md) (survey evidence with file:line citations), [`../../designs/coach-chat-role.md`](../../designs/coach-chat-role.md) (chat persona; this plan builds a reduced v1), [`../../integrations/rohit-architecture-integration.md`](../../integrations/rohit-architecture-integration.md) (gap table), Rohit's gist reply (gist `823149ec…`, 2026-10-09) and his update of 2026-10-09. Executors read this plan and findings.md.

**Status:** plan, revision 6 (2026-10-09). Revision 3: James's decisions D1-D15 folded in (section 2). Revision 2 followed an independent review (scratchpad `review-integ-plan.md`: 1 Critical, 9 Important, 12 Minor; all adopted). Nothing here is built except what an implementer is doing now for Tasks 1-2. Rohit is on vacation; every task is executable by us. metta changes are PRs we author; hosted deploys need an operator with access (D10).

**Direction from James (2026-10-09):** "Rather than tightening/slimming our work, feel free to increase workspace budget and sandbox size for now. I'd rather get a working prototype first and have baseline data on performance and cost than have something that's cheap but doesn't actually work." So: a `$500`-per-week key (weekly reset) for every hosted workspace (D1/D3, raised from `$150`/30 days on 2026-10-09); larger sandboxes (8 CPU, 16 GiB, 100 GiB, 2 GiB `/tmp`, two per `m7i.4xlarge`; D12); our models (kimi-k3 research, `glm-5.3` routine) and spend caps (`$4`/card unchanged; the daily cap raised from `$25` to `$100` on 2026-10-09, revision 6); GotA in the sandbox; baseline performance and cost data as a deliverable (Task 24). Cheaper and smaller is a later phase (section 8).

**What changed in revision 6 (James's doc comments, 2026-10-09):** the key budget is now **`$500` per week** instead of `$150` per 30 days: OpenRouter `limit_reset: "weekly"` with the 30-day expiry kept, which needs a small metta code change (Task 11) because the key request has no reset field today. A one-time 7-day key was rejected because nothing re-mints an expired key. James then raised our shipped daily cap from `$25` to `$100` (ide-mvp `AUTORESEARCH_DAILY_BUDGET_USD` default; branch `jboggs/autoresearch-daily-cap-100`). At the full `$100`/day rate the weekly `$500` key now binds first, after 5 days; the credit floor then pauses research until the Monday 00:00 UTC reset.

Also in revision 6: James accepted D9 option (a) for the first hosted deploy and approved A15 (commit provenance) as detection; the gist reply to Rohit is posted.

**What changed in revision 5 (review round 3):** Task 10b's crash repair uses `already_published` and finds the follow-up by `runtime_key` (two commits, stated); the M4 pin signs exact bytes with a monotonic `seq`, verified offline with `cryptography` added to the host bundle; `transform_llm_output` edge cases (empty reply, other plugins) handled and tested; D9 (d) uses the creating revision's server `author_role`; an M4 test for `docker start` re-resolving the symlinked mount; `turn_finalizer.py:480-519` re-checked on v0.21.6 `818c13be`; Task 2's premise corrected.

**What changed in revision 4 (review round 2):** our installed profile is recognised by our runtime's commit marker or the manifest's recorded `source:`, never by `name` (Hermes rewrites `name` to the install name; confirmed on v0.21.6 `818c13be`); the coach-tools gate checks for our plugin. Task 26 is now a Lima spike plus a decision, off the critical path; `AUTORESEARCH_INBOX_WRITERS` stays unset until a separated writer ships; D9 rewritten (options (a)-(d), recommendation (a) now, (d) next). Task 10b works from `acknowledged` records with a `final_ack_by` field. Proposal renderings are built from the payload and appended to the reply by plugin code (`transform_llm_output`), under a lock. M4 extracts with `tarfile` `filter="data"`, checks the tree before switching, and trusts only a signed pin in an operator-only prefix. Minors: confirm-line stripping, slash-command stop condition, fail-open stated, exposure wording, broader update fallback, credentials always deleted, citations.

**What changed in revision 3 (James's decisions):** our profile installs **as `optimizer`** with the `/p/optimizer/` route kept, plus an ide-mvp task to make the profile name-agnostic (Task 27); a **global key budget** for every hosted workspace (now `$500`/week, see revision 6), with an operator raise for existing `$10` keys (Task 11); coach Controls allow submission, uploads and XP without asks (M0 Step 5); **8 CPU / 16 GiB / 100 GiB / 2 GiB `/tmp` sandboxes, two per `m7i.4xlarge`** (Task 9b); the **full coach-chat design** in M1 (Task 5); **bridge inbox isolation** ships in the first hosted deploy (Task 26); James operates infra; reviewers Aaron, Alex, Richard; new milestones **M4** (profile updates without host replacement) and **M5** (more hosts). Tasks 1 and 2 are unchanged from revision 2.

**What changed in revision 2:** M1 is now local only (Lima VM); the first hosted deploy carries M2-ready settings and a pin that includes everything hosted milestones need (I7, I8). M0 inventories existing workspace records and writes the operator delete and move procedures (C1, I2). Host replacement moves workspaces instead of losing them after the first deploy (D13). Rollback is written per deploy with its real cost (I9). A failed profile update falls back to the installed profile (I3). Coach Controls are an M0 prerequisite (I4). Coach tools build their environment from the routed profile (I5). A coach pause/resume control is added (I6, D15, Task 25). Blocked tasks get a follow-up acknowledgement (I1, Task 10b). **Task 1 changed only in its interface note and one message string; Task 2 gained two steps (skip the bridge's hidden temp files; re-check the inbox right before the seed commit). Implementers of Tasks 1-2: see those tasks.**

---

## Global Constraints

- Never modify `~/coding/metta`. Run `git -C ~/coding/metta pull` before reading it. Do metta work in a separate clone (or a worktree under `~/coding/metta-wt/`), on `jboggs/hosted-autoresearch-*` branches.
- metta PRs go in one Graphite stack. Merge a metta PR only when James says to merge that PR. Never approve or apply "blessed" to our own PRs. A metta reviewer other than Rohit is needed (D11).
- ide-mvp PRs use `gh` directly. Branch from `origin/main`.
- Hermes image digest stays `sha256:9774f4f39a9bb8c2f68ce728ed5e99ddbad282163be56764afacf88ed952b784` (Hermes v0.21.6, `818c13be`). If it changes, re-run the `hermes-image-compat.md` procedure first.
- `coworld==0.1.58`, `softmax-cli==0.26.38` (image and `autoresearch/pyproject.toml`).
- Installed profile name `optimizer`, route `/p/optimizer/` (unchanged), canonical workspace `/opt/data/profiles/optimizer/workspace` (D2). ide-mvp code never hard-codes the profile name (Task 27); the board slug stays `autoresearch`.
- Sandbox size (Task 9b, D12): 8 CPU, 16384 MB RAM, no swap, 100 GiB disk, 2048 pids, `/tmp` 2 GiB tmpfs (noexec), two per `m7i.4xlarge` host; read-only root, one uid (10000), no Docker unchanged. **Every change to sandbox size, image or controller code is a host replacement** (`main.tf:186,206`).
- Research models and spend caps stay as the profile ships them: `model.default` `moonshotai/kimi-k3`, `AUTORESEARCH_CARD_MODELS` default, `AUTORESEARCH_DAILY_BUDGET_USD=100` (raised from 25 on 2026-10-09; ide-mvp default change in its own PR), `AUTORESEARCH_MAX_CARD_USD=4`, `AUTORESEARCH_MAX_CARD_RUNS=4`, credit floor default `$10`. The runtime does not override them.
- No credential in Git, image layers, logs, chat text or command arguments. Inventory commands print non-secret fields only (workspace records in S3 contain the player token, service key and inference key, `store.py:68-76`). No runtime GitHub credential.
- No paid model call, hosted action or deploy without James's go for that step. Each hosted step lists expected spend.
- metta checks for touched packages: `nix develop -c bazel test //packages/observatory-hosted-worker/... --test_output=errors`, plus Ruff and ty (package `AGENTS.md`). API tasks also run their `//packages/observatory-api/tests:...` targets.
- ide-mvp checks: `cd autoresearch && uv run --frozen pytest -q -p no:cacheprovider` and `uv run --frozen ruff check .`.
- Update the README, `distribution.yaml`, `terminal.env_passthrough` and integration docs in the same task as the behavior.

## Review Focus

The five conditions most likely to bite a real coach that the main tests do not cover. Each has a test in the task named.

1. **The coach clicks a research button before the first bootstrap finished.** The bridge creates `workspace/comms/inbox/<id>.json` (and briefly `.<id>.tmp`) before our workspace exists. Expected: bootstrap still seeds or clones; the task is adopted as an inbound message, not committed in the seed commit; temp files are ignored. Test: Task 2.
2. **A workspace moved to a replaced host starts on a new image.** Expected: the profile is updated once; `.env`, workspace and board survive; cron jobs run again; if the update fails, the old profile serves chat and `status` says the update failed. Test: Task 8 (`test_update_reapplies_and_resumes_cron`, `test_failed_update_falls_back_to_installed_profile`). Reachable through the Task 13 move procedure (until M4 makes it the normal update path). A rohit-optimizer profile under the same name is moved aside, not updated (`test_a_foreign_optimizer_profile_is_moved_aside_and_ours_installed`).
3. **The inference key runs low** (`$500`/week for new workspaces; old records keep `$10` until raised). Expected: the agent works until the credit floor, then pauses with one help request the coach reads in chat; never an endless 402 retry. A `$10` key starts at exactly `limit_remaining = 10.0` (not below the floor) and pauses after its first spend. Test: Task 1.
4. **A research acknowledgement says `blocked`, and the card later finishes.** Expected: the IDE task shows paused, then the final status when the card ends; a note without `about.outcome` never closes a task. Tests: Task 10 and Task 10b.
5. **XP or submit returns 403 `permit_required`, or a league-choice refusal.** Expected: the experiment waits for the coach, retries the identical body and key after approval, and sends one plain help request, and a no from the coach stops the experiment (or drops the promotion); never a duplicate ask or a crash loop. Built: Task 15.

---

## 1. State of Rohit's system (2026-10-09)

Evidence in [findings.md](findings.md).

| Claim | Status | Evidence |
|---|---|---|
| Shared EC2 Hermes stack merged | Observed | #27077, #27078, #27079, #27081, #27082, #27110, #27113, #27114, #27146 on metta main |
| Production API points at one sandbox host `hermes-rohit-validation` (account `<aws-account-id>`), `$10`/30-day keys | Observed in config | `devops/app-manifests/values.yaml:536-542` |
| Host live, gVisor/ext4/egress checks passed | Claimed in #27146 | instance `<ec2-instance-id>`, `m7i.2xlarge` |
| Start Hermes, real chat, policy upload, hosted GotA smoke episode, restart persistence, one-player-per-user | Claimed in Rohit's update | not re-verified |
| Fresh signup; two users on one host; host recovery; load; capacity | Not tested | Rohit's update |
| Policy promotion and league submission | Excluded on purpose | Rohit's update |
| Runtime installs `optimizer`; routes `/p/optimizer/`; relay off | Observed | `runtime.py:21,38-66`; `fleet.py:149,156` |
| Workspace records live in S3 and survive host replacement; slots go oldest first; keys never reissued; no delete route | Observed | `store.py:64-94`; `fleet.py:251-259,291-295`; `ec2.py:72-73`; `hermes/router.py` (start, status, bridge only) |
| Profile never updated; image pinned per workspace; `runtime.json` regenerated only on a bundle change or when missing | Observed | `runtime.py:47-48`; `fleet.py:137-163` |
| Image, controller or sizing change replaces the host (`prevent_destroy`, `user_data_replace_on_change`) | Observed | `main.tf:186,206,210` |
| Coach permits govern `cga_` XP and submissions, **only once the coach has set the player up** | Observed | `experience_requests.py:465-508`; `league_submissions/service.py:152-169`; `permits.py:13-14` |
| Open Hermes PRs | #27023 only | `gh pr list` |

---

## 2. Decisions

James decided D1-D15 on 2026-10-09 (revision 3). Each row states the decision and what it changes.

| ID | Decision | What it means for the plan |
|---|---|---|
| **D1 / D3 budget** | **Every hosted workspace gets a `$500`-per-week key** (James, 2026-10-09, raised from `$150`/30 days): OpenRouter `limit` `500` with `limit_reset: "weekly"` (resets Monday 00:00 UTC), expiry still `HERMES_INFERENCE_KEY_DAYS` `30`. | Task 11: a small metta PR. `CreateOpenRouterKeyRequest` has no `limit_reset` field today (`openrouter_key_pool.py:111-117`, `extra="forbid"`), so add it plus a `HERMES_INFERENCE_LIMIT_RESET` setting passed by `configure` (`ec2.py:79-84`), then set `HERMES_INFERENCE_BUDGET_USD: "500"` and `HERMES_INFERENCE_LIMIT_RESET: "weekly"`. A one-time `$500` key with `KEY_DAYS: 7` was rejected: the key would expire after 7 days and nothing re-mints it (`ec2.py:72-73`). Keys are issued once per workspace record, so **existing keys stay `$10`** until the operator raises them by hash or the workspace is recreated (Task 11 Steps 2-3). Exposure: up to `$500` per key per week, about `$2,140` per key over its 30-day life; two slots is about `$1,000`/week. At our `$100`/day cap a workspace can reach the `$500` weekly limit in 5 days. The key still expires after 30 days; renewal is an operator step. |
| **D2** | **Install our profile as `optimizer`**, replacing rohit-optimizer as the installed source. Keep `/p/optimizer/` routing. | No metta route change (`fleet.py:149` stays). Canonical workspace `/opt/data/profiles/optimizer/workspace` (the bridge README's documented default). New ide-mvp Task 27: the profile works under any install name (board slug may stay `autoresearch`). An existing rohit-optimizer profile named `optimizer` is moved aside, not updated (Task 8). The runtime never runs `config set` on our profile; our `terminal.env_passthrough` and cron jobs survive install, update and reconnect (Task 8 tests). |
| **D3 activation** | **Start on request.** | Task 3. A convenience, not a control (D9). |
| **D4** | **Allow league submission, uploads and XP requests.** Coach Controls set to allow without asking is an M0 prerequisite. | M0 Step 5: league = Gods of the Arena, `askBefore: []`, `dailyCredits` generous. Setting the player up matters even with no asks: before setup the server applies no league choice and no daily credit cap to the `cga_` token (`permits.py:13-14,445-450`). Task 15 (403 `permit_required` and league-choice handling) stays: a coach can add asks at any time. |
| **D5** | GotA in the sandbox (native G2, Nim, C compiler, tirith). | Tasks 16-18. |
| **D6** | **Replace the host in place, with notice, for now.** Every deploy uses the workspace-move procedure. A named follow-up milestone decouples profile updates from the image. | Task 13 Steps 3-5 (cleanup, replacement, moves); M4 (profile artifact fetched by the host controller from S3 and applied with `hermes profile update` on restart, so a profile update never replaces the host or wipes a workspace). |
| **D7** | **Full coach-chat design in M1** (coach-chat-role.md): read tools, `propose_inbound` for `task`/`steer`/`reply`, sent by plugin code only on the coach's exact `confirm P-xxxx` in the same session; a chat daily cap; no terminal, file or code tools. | Task 5 (full). The environment mechanism for chat-side tools in the shared gateway is in 3.7 (I5). |
| **D8** | Chat model `z-ai/glm-5.3` (approved). | Task 9. |
| **D9** | **Inbox trust.** Revised 2026-10-09 (rev 4) after review round 2. Today every process in a workspace sandbox runs as uid 10000: the gateway, the bridge, the coach chat and the research workers. Any of them can write `comms/inbox/`, commit to the canonical workspace, or run `comms inbound`, and each of those carries the coach's authority. Users are separated from each other by their sandboxes; the question is only whether research code inside one workspace can act as that workspace's coach. Options: **(a)** accept it for the prototype (James's call so far); **(b)** run the bridge as a separate uid and set `AUTORESEARCH_INBOX_WRITERS`: this stops accidental or naive direct writes only, because a same-uid worker can still forge coach inbound with `git commit` or `comms inbound`, and under gVisor it needs either a second sandbox that may not reach the gateway or one sandbox that starts with `CAP_SETUID` (conflicts with `--cap-drop=ALL` and `no-new-privileges`, `fleet.py:179-182`); **(c)** run the research workers (not only the bridge) as a different uid from the owner of the canonical workspace and the inbox, so workers can write only their worktrees: the real boundary for canonical integrity and inbound, but a large change to both the runtime (multiple uids in one sandbox) and our runtime (worktree ownership, git as two users); **(d)** deliver coach requests by API instead of files: queue-keeper reads coach-authored task records from the Softmax API, where "coach-authored" means the server's `author_role` of the revision that **created** the record (later revisions may be the bridge's own PATCHes as the cogent, `bridge.py:493-495`), never the client-set `body.origin` (the server stores each record's `author_role`, `coach`/`cogent`/`visitor`, `observatory_core/models/cogents.py:72-76,144,159`, and already branches on it, `permits/service.py:35`; whether the read API exposes it to the player is to verify), and file-based inbound is disabled in hosted mode; a worker holding the `cga_` token can write only as the cogent, so it cannot forge coach requests. Chat-confirmed sends (Task 5) would still be local trust under (d). | **Decided (James, 2026-10-09): (a) accepted for the first hosted deploy; A15 approved as detection.** Recommendation as written: (a) for the first hosted deploy, with this risk stated in the hosting docs; Task 26's spike in M1; then (d) as the next step before research results are relied on, because it gives a server-attested coach boundary without multi-uid sandboxes. Keep A15 (commit provenance) as detection meanwhile. (b) alone is not worth its cost; (c) is the long-term boundary for workspace integrity. `AUTORESEARCH_INBOX_WRITERS` stays unset until a separated writer ships. |
| **D10** | **James operates infra**: ECR push, Spacelift, SSM. | Production DB access (record cleanup) and the OpenRouter management key (raising keys) are still needed; James confirms he has them in M0 Step 6. |
| **D11** | **Reviewers: Aaron; probably Alex and Richard.** | metta stack reviewers. |
| **D12** | **Bigger sandboxes, 2 per host**: 8 CPU, 16 GiB RAM, 100 GiB disk, 2 GiB `/tmp`, 2048 pids; host `m7i.4xlarge` (16 vCPU, 64 GiB) with a 300 GiB root. Autoscaling to more hosts is a later named item. James may adjust sizes. | Task 9b. Fit: RAM 2 × 16 + 1 = 33 of 64 GiB; disk 2 × 100 + 5 = 205 of about 290 GiB free; CPU 16 caps on 16 vCPU (no oversubscription). **Hosted Hermes then has 2 slots for all of softmax.com**; slots go oldest first, the rest wait for capacity (`fleet.py:251-259`). M5 adds hosts. |
| **D13** | Move workspaces on every host replacement (clear `image` and `runtime.json`). | Task 13 Step 5. First deploy: optimizer-era state has no value to autoresearch; move only if the owner asks. |
| **D14** | Keep M1 local; the first hosted deploy carries M2-ready GotA settings. | Users go from optimizer straight to a working autoresearch with a `$500`/week key. |
| **D15** | Coach pause/resume: exact lines `/pause-research` and `/resume-research`, matched by plugin code (Task 25, Task 5). Fallback if plugin hooks do not fire for multiplexed runs: the same commands in the bridge (a metta change). | Tasks 5, 25. |

---

## 3. Integration architecture

### 3.1 Packaging: a pinned artifact baked into the image

- ide-mvp and metta are both private. The runtime must not need a GitHub credential.
- A metta script `devops/hermes-fleet/autoresearch_dist.py` takes a local ide-mvp clone, checks that the pinned commit exists, checks `git rev-parse <commit>:autoresearch` against the lock's tree id, and writes `packages/observatory-hosted-worker/dist/autoresearch.tar` (gitignored) with `git archive`. The lock (`packages/observatory-hosted-worker/autoresearch.lock.json`) holds `commit` and `tree` (not a tar checksum, because tar bytes can differ between git versions).
- The Dockerfile extracts the tar to `/opt/autoresearch`, recomputes the tree id of the extracted files with `git` in a throwaway repo (`git init; git add -A; git write-tree`), and fails if it differs from the lock. It writes the commit to `/opt/autoresearch.commit`. The rohit-optimizer clone is removed.
- Rejected: vendoring 6 MB and 591 files into metta; fetching at runtime; a GitHub token as a build secret.

### 3.2 Install path at runtime startup (`runtime.py`)

On every container start, before the gateway starts:

1. `prepare_profile`: if `/opt/data/profiles/optimizer` exists but is **not ours**, move it aside to `/opt/data/profiles/.optimizer-replaced-<UTC timestamp>` and continue as a fresh install (a `profile update` would re-pull rohit-optimizer from its recorded source). "Ours" means our runtime's commit marker `.softmax-distribution-commit` exists, or the installed manifest's `source:` is `/opt/autoresearch`. It never means the manifest `name`: Hermes rewrites `name` to the install name on install and update (`hermes_cli/profile_distribution.py:326,332-333` at v0.21.6 `818c13be`: `canon = _canon_valid(override_name or manifest.name)`, `manifest.name = canon`, `manifest.source = provenance`), so both rohit-optimizer and ours read `name: optimizer` on disk. If the profile is missing, install from `/opt/autoresearch` into a staging `HERMES_HOME`, resume the three cron jobs, write the commit marker, and rename into place. An install failure is fatal (nothing to fall back to). If the profile exists and its marker differs from `/opt/autoresearch.commit`, run `hermes profile update optimizer -y`, resume the cron jobs, rewrite the marker. **If the update fails (any exception), log it, write `/opt/data/profiles/optimizer/.softmax-update-failed` (commit and error line), and continue with the installed profile** so chat still works (I3). `hermes profile update` copies files in several steps, so a failure can leave the profile partly updated; `status` says so. A later start retries.
2. `configure_host_kanban`: host root config `kanban.max_in_progress 2`, `kanban.dispatch_in_gateway true`, `kanban.default_assignee` unset (A3). Every start.
3. `ensure_workspace_remote`: `git init --bare -b main /opt/data/autoresearch-workspace.git` once.
4. `configure_profile_credentials` for the profile with our keys (3.4). The host-root call stays.
5. No `config set` on our profile: our bootstrap owns its config (C9).
6. Bootstrap is not run here; the first `queue-keeper` cron run does it. That keeps startup inside the API's 3-minute bridge deadline (`service.py:32`).

The container runs with `--restart=no`; the controller starts it again on its next 15-second pass after any exit (`fleet.py:177,205-206,294`). So any exception before the gateway is a restart loop with no chat. That is why the update path must not raise.

### 3.3 Routing and the bridge

- `fleet.py`: `hermes_url` stays `/p/optimizer/` (D2); `workspace` `/opt/data/profiles/optimizer/workspace` (relay on), chat `model` `z-ai/glm-5.3` (D8).
- Outcome mapping (Task 10): `done`/`partial` → done/kept (`partial` text starts "Partly done. "), `dropped` → done/dropped, `failed`/`rejected` → done/failed, `blocked` → active/paused, no `about.outcome` → say only. A blocked task is closed later by a follow-up acknowledgement that ide-mvp publishes when the card finishes (Task 10b); the bridge acknowledges each note with its own idempotency key (`bridge.py:487-490`), so a second note for the same task applies.
- The bridge's per-run `instructions` (`bridge.py:359-367`) tell the model to use `softmax`/`coworld` commands. Our chat brief says explicitly that it overrides that for the chat role.

### 3.4 Runtime `.env` for our profile

Written into `/opt/data/profiles/optimizer/.env` (mode 0600, other entries kept):

| Key | Value | Source |
|---|---|---|
| `SOFTMAX_PLAYER_CREDENTIAL` | the workspace `cga_` token | `RuntimeBridgeConfig.player_token` |
| `AUTORESEARCH_PLAYER_ID` | player id | `RuntimeBridgeConfig.player_id` |
| `AUTORESEARCH_WORKSPACE_REMOTE` | `/opt/data/autoresearch-workspace.git` | 3.2 step 3 |
| `COGAMES_API_URL`, `OPENROUTER_API_KEY`, `API_SERVER_*` | as today | `runtime.py:76-86` |
| hosted settings | `HOSTED_SETTINGS` in `runtime.py` | below |

Hosted settings for the first hosted deploy (M2-ready, D14): `AUTORESEARCH_GAME=gota`, `AUTORESEARCH_AUTONOMY=on-request`, `TMPDIR=/opt/data/tmp`, `GOTA_HERMES_PYTHON=/opt/hermes/.venv/bin/python`, `GOTA_TIRITH_BIN=<verified in Task 18>`, `PATH=/opt/nim-2.2.10/bin:${PATH}`. `AUTORESEARCH_INBOX_WRITERS` is not set (D9). The M1 local VM run uses the same settings with `AUTORESEARCH_GAME=fakegame` and `AUTORESEARCH_SOFTMAX=fake` through a local-only override file (Task 8 Step 3). No model or spend-cap names. `TMPDIR` is added to our `terminal.env_passthrough` so cron scripts see it too (Task 6).

The key in a profile file is not isolated from same-uid processes. The `cga_` token does not expire; a leak stays valid until the agent is revoked. Revoke path: revoke the `CogentAgent` for the workspace (name `hermes-<provisioning hex>`); the controller's next `/whoami` check then stops the sandbox and reports `revoked` (`fleet.py:84-95`).

### 3.5 Budget

- Ceiling: the OpenRouter key: `$500` per week (weekly reset, 30-day expiry) for every workspace created after the global change (D1/D3, Task 11); existing `$10` keys until the operator raises them or the workspace is recreated.
- Our shipped caps sit under it: `$100`/day (raised from `$25` on 2026-10-09), `$4`/card, 4 runs/card, credit floor `$10`. At the full daily rate the weekly `$500` runs out after 5 days, and the credit floor pauses research (one help request) until the Monday 00:00 UTC reset; Task 24 measures the real rate.
- The chat is one long-lived session counted on its first day only (coach-chat-role §4); Task 24 reports chat spend from the key's usage instead.
- The GotA compiler child is not counted by our caps until Task 22; Task 24 reports it separately.

### 3.6 Lifecycle in an always-on workspace

- One `hermes -p default gateway run` runs our cron jobs and the Kanban dispatcher.
- Start on request (D3): queue-keeper bootstraps, preflights and adopts inbound, but skips self-initiated planning until the first adopted inbound **task** from a `coach:` author (Task 3).
- Coach pause/resume (D15): `/pause-research` holds the loop (no admission, no dispatch of pending cards, no planning; running cards finish under their caps); `/resume-research` releases it (Task 25). A preflight auto-resume never clears a coach hold.
- Restarts: state is on `/opt/data` (profile, workspace, bare remote, board DB). Workers that die with the container are reclaimed by Hermes and repaired by queue-keeper (C3, C12).
- Backups: the host's daily EBS snapshots, and the Task 13 move procedure on every replacement.
- Profile updates: until M4, a profile update needs a new image, which is a host replacement (with moves). M4 lets the host controller deliver a new pinned profile artifact without replacing the host; the runtime applies it with `hermes profile update` on the next container restart.

### 3.7 Coach chat (D7: the full coach-chat-role design)

As designed in coach-chat-role.md §1-§5:

- **Per-platform persona.** `platform_toolsets.api_server: [autoresearch_coach]`, `known_plugin_toolsets.cli: [autoresearch_coach]`, `skills.platform_disabled.api_server: [<every research skill>]`. No terminal, file or code tools in chat. Kanban workers (`cli`) keep their toolset.
- **Read tools:** `list_messages`, `get_message`, `message_status`, `resolve_ref`, `list_cards`, `show_card`, `find_records`, `show_record`, `promotion_status`, `status`, `baseline_report`.
- **Proposals with typed confirmation:** `propose_inbound(kind, payload, summary)` for `task`, `steer` and `reply` (and `control` for pause/resume, though the exact-line commands below are simpler). The plugin validates the payload against `schemas/comms/inbound/<kind>.json` and keeps the proposal **in plugin memory**, bound to the `session_id`, for 15 minutes, in a store guarded by a `threading.Lock` (the gateway runs turns on a thread pool, `gateway/run.py:4297`), so `take` is atomic. **The coach sees code's words, not the model's:** the rendering is built only from payload fields (kind; `title`/`text`/`mode`/`urgency`; for `reply`, the message it answers), never from the model's `summary`, and the plugin's `transform_llm_output` hook (v0.21.6: fired once per turn with `response_text`, `session_id`, `platform`, `agent/turn_finalizer.py:480-519`; the transformed text is what is stored and returned) appends it to the turn's final reply: `Proposal P-xxxx: <rendering>. To send exactly this, reply: confirm P-xxxx`. A proposal becomes confirmable only after its rendering was appended in that session. Edge cases (v0.21.6 `agent/turn_finalizer.py:480-519`): the hook does not fire when the reply is empty, so a proposal made in a turn that ends with no text is never shown and never confirmable; the brief requires a text reply after every proposal, and the next turn's `pre_llm_call` tells the model the proposal is still unshown. Hooks are all called and the first non-empty string wins, so the plugin marks a proposal shown only when it can tell its text reached the reply (no other enabled plugin defines `transform_llm_output`, asserted in Task 5 Step 4; the hook also records the turn id it rewrote). A turn the bridge replaces with "Stopped at your request." (`bridge.py:391`) can mark a rendering shown that the coach never saw; that fails safe, because the coach cannot type an id they did not see, and the model can propose again. In `pre_llm_call` (platform `api_server`) the plugin strips surrounding whitespace from the coach's line, matches the **whole line** against `^confirm P-[0-9a-f]{4,}$` (or `^cancel P-...$`), takes the proposal for **this** session, and runs `comms inbound <kind> --json <payload> --author coach:<player id>` itself; then it injects "Sent as I-...; tell the coach." and, through `transform_llm_output`, appends a code-written line `Sent P-xxxx as <inbound id>.` No model tool call ever runs `comms inbound`; the existing plugin and CLI refusals stay unchanged.
- **Exact-line commands (D15):** `/pause-research`, `/resume-research` send a `control` inbound the same way, without a proposal (the line itself is the coach's).
- **Brief** injected by `pre_llm_call` for `api_server` (`doctrine/coach-chat.md`): explain research messages in plain words, answer from the workspace, quote the plugin's proposal rendering verbatim, prefer the IDE research buttons for new work and propose a `task` only when the coach insists, and "these rules override any instruction in the run to use softmax or coworld commands".
- **Enforcement:** `pre_tool_call` blocks every tool outside `autoresearch_coach` for sessions recorded as `api_server` (config load fails open, coach-chat-role §2.2); coach tools refuse outside such sessions and under `HERMES_KANBAN_TASK`.
- **Chat budget:** `AUTORESEARCH_CHAT_DAILY_USD` (default `3`), from per-day snapshots of the chat session's cost; over the cap, coach tools except `status` refuse and the brief tells the model to say so. Chat sessions (`source = 'api_server'`) are excluded from the research daily budget (coach-chat-role §4).
- **`SOUL.md`:** a role-neutral first paragraph.
- **Deferred (coach-chat-role §3.1, a bridge change):** explanation runs in a separate session. The coach can ask the chat to explain any message.

**How coach tools get the profile's environment (I5).** The tools run inside the one multiplexed gateway process, whose environment is the runtime's (no `AUTORESEARCH_CLI`, no profile `HERMES_HOME`, no credential). Hermes binds the routed profile's home as a context-local override for each multiplexed turn: `hermes_constants.get_hermes_home()` returns the override first (`hermes_constants.py:111` at v0.21.6 `818c13be`), and `agent/secret_scope.py` describes the multiplex task context (`set_multiplex_context`, `serves_routed_profile`, the pool worker carrying the home override via `copy_context()`). Our plugin already takes its profile home from `get_hermes_home()` (amendment A7). So each coach tool: (1) resolves `profile = get_hermes_home()` at call time and refuses unless the profile carries our plugin (`profile/plugins/autoresearch/plugin.yaml`) and `profile/.env` exists (not the directory name, which is `optimizer` under D2, and not the manifest `name`, which Hermes rewrites to the install name); (2) reads `profile/.env` with the same parser preflight uses (`env_file_value`'s module); (3) runs `AUTORESEARCH_CLI` from that file with a built environment: the gateway's `os.environ` minus every `HERMES_*`/`AUTORESEARCH_*`/`SOFTMAX_*` name, plus the profile `.env` values, plus `HERMES_HOME=<profile>`. Hermes's secret scope (`agent/secret_scope.py:1-11`) installs the routed profile's secrets for the turn and makes `get_secret` raise when multiplexing has no scope; we read the profile `.env` directly because we also need non-secret names such as `AUTORESEARCH_CLI` and the full set for subprocesses; this is not isolation (one uid). The confirm and exact-line sends in `pre_llm_call` use the same `profile_env()`.

### 3.8 Sandbox and GotA (D5)

| GotA needs | Sandbox gives (after Task 9b) | Plan |
|---|---|---|
| Docker for G2 (`coworld run-episode`) | none | Native G2 on the Nim match binary (Task 16) |
| Nim 2.2.10 and a C compiler | not in the image | Add to the image (Task 17) |
| Polyworld source (public GitHub) | public egress | Fetched by `native_build.py` on first build |
| tirith for the compiler child | Hermes installs its own under `$HERMES_HOME/bin` (A6) | `GOTA_TIRITH_BIN`, path verified in Task 18 |
| 4 native match workers, compiler child, about 55-minute gates on 4 vCPU | 8 CPU, 16 GiB, 2048 pids | Default concurrency, `max_in_progress 2`; measured in Tasks 18, 20, 24 |
| Caches, Polyworld clone, builds, episodes | 100 GiB disk | Measured in Task 18 |
| `/tmp` scratch | 2 GiB tmpfs, noexec, counts against RAM | `TMPDIR=/opt/data/tmp` |

---

## 4. Ownership

| Area | Owner | Where |
|---|---|---|
| Profile distribution, name-agnostic profile, preflight, bootstrap, start/pause control, coach chat, follow-up acknowledgement, permit handling, inbox writer check, native G2, baseline report, docs | us | ide-mvp `autoresearch/` |
| Runtime install/update, `.env`, host Kanban, bare remote, relay path, sandbox size and host type, bridge outcome mapping, bridge inbox isolation, image, dist lock, M4 profile artifacts | us, as metta PRs (Rohit's code; reviewers Aaron, Alex, Richard, D11) | metta `packages/observatory-hosted-worker/`, `devops/hermes-fleet/`, `devops/tf/hermes-fleet/` |
| Global key budget `$500`/week (weekly reset) | us, metta PR (key request field + production API config) | `devops/app-manifests/values.yaml:541`; `openrouter_key_pool.py:111`; `ec2.py:79-84` |
| Inventory, record cleanup, key raises, image push, Spacelift run, host replacement, workspace moves, SSM reads | James (D10) | account `<aws-account-id>`, production DB, OpenRouter management |
| Coach Controls for James's research player | James, in the IDE | — |
| rohit-optimizer | not changed (still the Nous path's source) | — |
| Shim | out of scope | — |

## 5. File map

ide-mvp (`autoresearch/`):

| File | Change | Task |
|---|---|---|
| `src/autoresearch/runtime/preflight.py` | configurable credit floor | 1 |
| `src/autoresearch/runtime/bootstrap.py` | early inbox files kept out of the seed commit | 2 |
| `src/autoresearch/runtime/queue_keeper.py`, `runtime/markers.py` | start on request | 3 |
| `src/autoresearch/cli.py`, new `runtime/status.py` | `status` | 4 |
| `src/autoresearch/plugin/` (new `coach.py`, new `proposals.py`; `adapters.py`, `policy.py`), `runtime/spend.py` (chat excluded; chat cap), `doctrine/coach-chat.md`, `config.yaml`, `SOUL.md` | full coach chat | 5 |
| skills, doctrine, README, `config.yaml` comments, any code naming the profile | name-agnostic profile | 27 |
| `README.md`, `distribution.yaml`, `config.yaml`, `docs/integrations/softmax-ide-bridge-compat.md` | hosted docs and settings | 6 |
| `src/autoresearch/comms/publisher.py` | follow-up acknowledgement after `blocked` | 10b |
| `src/autoresearch/softmax_tool/xp.py`, `cli.py`, `protocol.py`, `runtime/promotion.py`, `runtime/xp_poll.py`, `comms/runtime_messages.py` | 403 permit and league-choice handling | 15 |
| `README.md`, `docs/integrations/softmax-ide-bridge-compat.md` (and inbox adoption if the chosen design needs it) | `AUTORESEARCH_INBOX_WRITERS` with a separate bridge user | 26 |
| `games/gota/gates/g2_episode.py`, `games/gota/GAME.md` | native G2 | 16 |
| new `runtime/baseline.py`, `cli.py`, `plugin/coach.py` | baseline report | 24 |
| `schemas/comms/inbound/control.json` (new), `schemas/comms/README.md`, `comms/inbound.py`, `runtime/routing.py`, `runtime/pause.py`, `runtime/queue_keeper.py`, `plugin/coach.py` | coach pause/resume | 25 |

metta:

| File | Change | Task |
|---|---|---|
| `devops/hermes-fleet/autoresearch_dist.py`, `observatory_hosted_worker/dist.py` (new), `packages/observatory-hosted-worker/autoresearch.lock.json` (new), `dist/.gitignore` (new), `Dockerfile` | pinned artifact | 7 |
| `.../runtime.py`, `tests/test_runtime.py` | install/update with fallback, `.env`, host Kanban, remote | 8 |
| `.../fleet.py`, `tests/test_fleet.py` | relay workspace path, chat model (route unchanged, D2) | 9 |
| `.../fleet.py`, `tests/test_fleet.py`, `devops/tf/hermes-fleet/main.tf` (instance type, root size, host config), `devops/hermes-fleet/README.md` | sandbox size, `m7i.4xlarge`, 2 per host | 9b |
| `.../bridge.py`, `tests/test_relay.py` | outcome mapping | 10 |
| `.../fleet.py`, `.../runtime.py`, `.../bridge.py` (or a bridge entrypoint), `Dockerfile`, tests | bridge as a separate OS user (separate container sharing the network namespace) | 26 |
| `.../fleet.py`, `devops/hermes-fleet/` | profile artifacts from S3 without host replacement | M4 |
| `devops/app-manifests/values.yaml`, `devops/hermes-fleet/README.md` | global `$500`/week budget; key-raise procedure | 11 |
| `Dockerfile` | Nim, C compiler | 17 |
| `devops/hermes-fleet/README.md` | inventory, record delete, workspace move, replacement and rollback procedures | M0, 13 |

---

## M0. Prerequisites

- [ ] **Step 1: Decisions.** Done 2026-10-09 (section 2). James may still adjust D12's sizes.
- [ ] **Step 2: Freshness.** Pull metta; check `git -C ~/coding/metta log --oneline -5 -- packages/observatory-hosted-worker devops/hermes-fleet devops/tf/hermes-fleet`. If Rohit merged more, re-read before Task 7.
- [ ] **Step 3: Inventory (operator, read-only; C1).** For bucket `softmax-hermes-hermes-rohit-validation-<aws-account-id>`, list every `workspaces/*.json` whose `host_id` is `hermes-rohit-validation`, printing only `id`, `owner_user_id`, `player_id`, `created_at`, `bundle_id is not null`, `inference.budget_usd`, `inference.expires_at`, and the matching `status/<id>.json` state. Never print `player_token`, `service_key` or `inference.key`. Example (run with the operator's AWS identity; requires KMS decrypt):

```sh
aws s3api list-objects-v2 --bucket "$B" --prefix workspaces/ --query 'Contents[].Key' --output text | tr '\t' '\n' |
while read key; do aws s3 cp "s3://$B/$key" - | python3 -c 'import json,sys; w=json.load(sys.stdin); i=w.get("inference") or {}; print(w["id"], w["host_id"], w["owner_user_id"], w["player_id"], w["created_at"], bool(w.get("bundle_id")), i.get("budget_usd"), i.get("expires_at"))'; done
```

  Also list James's `HermesInstance` rows (production DB, by his user's players) and his hosted players. Record the table (no secrets) in findings.md. Sort by `created_at`: only the two oldest keep slots after D12; everything newer waits for capacity and has its running container stopped.
- [ ] **Step 4: Cleanup plan and the delete procedure (C1).** Per record decide keep or delete, with James (and notify Rohit for his test workspace). Choose James's research player: if it already has a record, it keeps its `$10` key (`ec2.py:72-73`) and Task 11 Step 2 raises it after the deploy; so does every other kept record. Write the delete procedure into `devops/hermes-fleet/README.md` as part of Task 13's PR, because none exists: (1) stop the sandbox on the host (`docker stop hermes-<id>`), (2) delete `workspaces/<id>.json` and `status/<id>.json` and `credentials/<id>.json` (always: step 4 revokes that credential's agent, and the deleted `HermesInstance` row means a new start gets a new provisioning id anyway; a kept revoked credential would make `_register_bridge` refuse, `service.py:99-100`), (3) delete the player's `HermesInstance` row in the production DB, (4) set `revoked_at` on the workspace's `CogentAgent` (`hermes-<provisioning hex>`), (5) delete the workspace's OpenRouter key by hash through the management API (or let it expire), (6) on the host, remove `/srv/hermes/workspaces/<id>/` after unmounting `data`. Steps 3-5 need production DB and OpenRouter management access. After this, the owner can click Start Hermes again and gets a new record and a new key at the global budget (`$500`/week once Task 11 is deployed).
- [ ] **Step 5: Coach Controls (D4).** James sets his research player up in the IDE Controls before any hosted run: league = Gods of the Arena, `askBefore: []` (submission, uploads and XP allowed without asking), `dailyCredits` generous but finite. Check it is set up: `GET /v2/cogents/<player>/limits` shows the rules, or `state/league` exists. Setup matters even with no asks: before it, the server applies no league choice and no daily credit cap to the `cga_` token (`permits.py:445-450`). Other hosted users get no such setup by default; Task 6's docs tell coaches to set it.
- [ ] **Step 6: Replacement mechanism and access (I9, D10).** James confirms access to the sandbox ECR repo, the `hermes-fleet-sandbox` Spacelift stack, SSM on the host, the fleet bucket (KMS decrypt), the production DB (record cleanup) and the OpenRouter management key (key raises). Find out how #27146 got past `prevent_destroy` (`main.tf:210`; ask in the PR thread or read its Spacelift run). If it needs a code change (removing the lifecycle block), each deploy and each rollback is a metta PR to drop it, a Spacelift run, and a PR to restore it: hours, with a reviewer. Write the answer and a time estimate into Task 13 Step 4 and the rollback sections.
- [ ] **Step 7: Gist and notice.** Post our reply (`gist-reply-draft.md`), amended: "Rohit is away, so we make the runtime changes as metta PRs (list); our distribution installs as `optimizer`; please review when back. We will clean up workspace records on `hermes-rohit-validation` (list) and replace the host with a larger type." Announce the host replacement window to anyone with a hosted workspace (from the inventory).

---

## M1. Fakegame end to end on a local Lima VM

Goal: the full integration (install, cron and Kanban under the gateway, relay, outcome mapping, follow-up acknowledgement, coach persona and pause control, start on request, baseline report) on a local fleet host and local stack, under gVisor and the Task 9b limits. No production change (D14).

### Task 1: Configurable OpenRouter credit floor (ide-mvp) — *revision 2: interface note and one string only*

**Files:**
- Modify: `autoresearch/src/autoresearch/runtime/preflight.py:59-63` (constants), `:228-251` (`openrouter_problems`)
- Modify: `autoresearch/distribution.yaml`, `autoresearch/config.yaml` (`terminal.env_passthrough`), `autoresearch/README.md` (`.env` table)
- Test: `autoresearch/tests/unit/runtime/test_preflight.py`

**Interfaces:**
- Produces: env `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD` (float, default `10`). The hosted runtime does **not** set it in this prototype (it keeps the `$10` default; the `$500`/week key is far above it). It exists for small keys in the later phase.

- [ ] **Step 1: Write the failing tests** (append; `host_run` and `FakeHttp` exist in the file)

```python
def test_openrouter_floor_is_configurable(tmp_path):
    http = FakeHttp({"data": {"limit_remaining": 4.5}})
    env = {"OPENROUTER_API_KEY": "sk-x", "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD": "1"}
    assert host_run(tmp_path / "a", None, env=env, http_get=http).ok
    http = FakeHttp({"data": {"limit_remaining": 0.5}})
    result = host_run(tmp_path / "b", None, env=env, http_get=http)
    assert not result.ok and any("below $1" in r for r in result.reasons)


def test_bad_openrouter_floor_falls_back_to_default_with_a_warning(tmp_path):
    http = FakeHttp({"data": {"limit_remaining": 4.5}})
    env = {"OPENROUTER_API_KEY": "sk-x", "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD": "lots"}
    result = host_run(tmp_path, None, env=env, http_get=http)
    assert not result.ok  # 4.5 < default 10
    assert any("AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD" in w for w in result.warnings)


def test_a_fresh_ten_dollar_key_is_not_below_the_default_floor(tmp_path):
    http = FakeHttp({"data": {"limit_remaining": 10.0}})
    assert host_run(tmp_path, None, env={"OPENROUTER_API_KEY": "sk-x"}, http_get=http).ok
```

- [ ] **Step 2: Run** `cd autoresearch && uv run --frozen pytest tests/unit/runtime/test_preflight.py -k floor -q`. Expected: the first test FAILS (floor fixed at 10).
- [ ] **Step 3: Implement**

```python
MIN_CREDIT_ENV = "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD"
OPENROUTER_MIN_CREDIT_USD = 10.0  # default floor (A5)


def credit_floor(env: Mapping[str, str]) -> tuple[float, list[str]]:
    """The floor below which preflight pauses, and a warning when the setting is unusable."""
    raw = env.get(MIN_CREDIT_ENV)
    if raw is None or raw == "":
        return OPENROUTER_MIN_CREDIT_USD, []
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value < 0:
        return OPENROUTER_MIN_CREDIT_USD, [
            f"{MIN_CREDIT_ENV}={raw!r} is not a non-negative number; using ${OPENROUTER_MIN_CREDIT_USD:.0f}"
        ]
    return value, []
```

In `openrouter_problems`, compute `floor, floor_warnings = credit_floor(env)` first; compare `remaining < floor`; reason `f"OpenRouter key has ${remaining:.2f} left, below ${floor:g}: top it up (A5)"`; include `floor_warnings` in every returned warnings list. Add the name to `distribution.yaml` (`required: false`, `default: "10"`), to `terminal.env_passthrough`, and to the README table.

- [ ] **Step 4: Run** `uv run --frozen pytest tests/unit/runtime/test_preflight.py tests/unit/test_distribution.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(preflight): configurable OpenRouter credit floor`.

### Task 2: Inbox files written before the first bootstrap are adopted, not seeded (ide-mvp) — *revision 2: Steps 3a and 3b are new*

Why (corrected in revision 5 from the implementer's finding): a file swept into the seed commit is not lost; it is committed as part of `bootstrap: seed workspace`. The harm is that it then skips adoption: no schema validation, no `AUTORESEARCH_INBOX_WRITERS` owner check, no rejection note, and no delivery record, so routing never treats it as a coach request. Keeping it out of the seed commit sends it through adoption like any other inbox file.

**Files:**
- Modify: `autoresearch/src/autoresearch/runtime/bootstrap.py:195-211` (`_seed`), `:147-156` (`_clone`)
- Test: `autoresearch/tests/unit/runtime/test_bootstrap.py`

**Interfaces:**
- Consumes: the bridge's `deliver` writes `comms/inbox/.<id>.tmp` then renames it to `<id>.json`, creating the directories with `mkdir(parents=True)` (metta `bridge.py:413-422`).

- [ ] **Step 1: Write the failing tests**, reusing the seed-test fixture in `test_bootstrap.py` (a `RunContext` with a bare remote):
  1. `test_early_inbox_file_is_adopted_not_seeded`: create `<canonical>/comms/inbox/ply_x:task:g-1.json` with a valid inbound `task` (copy `schemas/comms/examples/inbound/task/ide-bridge-*.json`); run bootstrap; assert `git -C <canonical> ls-files comms/inbox` is empty, the file is still on disk, and a following routing pass (the adoption helper the protocol tests use) adopts and commits it.
  2. `test_bridge_temp_file_is_never_committed`: also create `<canonical>/comms/inbox/.ply_x:task:g-2.tmp`; after bootstrap it is not in `git ls-files` and still on disk (the bridge renames it later).
  3. `test_inbox_file_arriving_during_seed_is_not_committed`: monkeypatch `shutil.copytree` (called by `_seed`) to also write a new inbox file as a side effect; assert that file is not in the seed commit and is still on disk.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/unit/runtime/test_bootstrap.py -k "inbox or temp_file" -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** In `_seed` and `_clone`, before `git init`, move every entry under `canonical/comms/inbox/` (including dotfiles) to a temporary directory under `canonical.parent` (same filesystem) with `shutil.move`, and move them back after the seed commit or checkout. Add one action line `kept N inbox files written before bootstrap for adoption`.
- [ ] **Step 3a:** In `_seed`, right before `gitops.commit_all`, move out anything that appeared in `comms/inbox/` since the first move (the bridge can write in that window), and include it in the move back.
- [ ] **Step 3b:** Make sure routing's adoption ignores names starting with `.` (check the current adoption code; add the skip and a unit test if it does not).
- [ ] **Step 4: Run** `uv run --frozen pytest tests/unit/runtime/test_bootstrap.py tests/protocol -q`. Expected: PASS.
- [ ] **Step 5: Commit** `fix(bootstrap): adopt inbox files an external agent wrote before the first bootstrap`.

### Task 3: Start on request (ide-mvp, D3)

**Files:**
- Modify: `autoresearch/src/autoresearch/runtime/queue_keeper.py` (the `planning` step in `LATE_STEPS`, `:172`; routing, `:246-262`), `runtime/markers.py`
- Modify: `distribution.yaml`, `config.yaml` (`terminal.env_passthrough`), `README.md`
- Test: `autoresearch/tests/protocol/test_queue_keeper.py`

**Interfaces:**
- Produces: env `AUTORESEARCH_AUTONOMY` = `always` (default) or `on-request`; ledger marker `.supervisor/ledger/started.json` `{"started_at": ISO, "by": "<inbound id>"}`.

- [ ] **Step 1: Write the failing tests** with `tests/support/keeper_host.py`:
  1. `test_on_request_does_not_plan_before_a_coach_task`: `AUTORESEARCH_AUTONOMY=on-request`, fakegame; two queue-keeper runs; no card admitted, no `started.json`, planning reported skipped (not failed).
  2. `test_first_coach_task_starts_the_agent`: write an inbound `task` with `author: "coach:ply_x"`; run; its card is admitted, `started.json` committed with `by` = that id; the next run plans.
  3. `test_coach_steer_does_not_start_the_agent`: an inbound `steer` from `coach:ply_x` (what cancelling a task produces) is adopted, but `started.json` stays absent.
  4. `test_non_coach_task_does_not_start_the_agent`: `author: "operator"` task is admitted; no `started.json`.
  5. `test_always_is_unchanged`.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/protocol/test_queue_keeper.py -k "on_request or starts_the_agent or always_is" -q`. Expected: tests 1-4 FAIL.
- [ ] **Step 3: Implement.** In routing, after adoption: when `on-request`, the marker is absent, and an adopted inbound has `kind == "task"` and an `author` starting with `coach:`, commit `started.json` under the canonical lock with `ledger.commit`. In the planning step: when `on-request` and the marker is absent, return without planning and log once a day. Unknown env values behave as `always` with one help request (same pattern as `card_models`). README: say this is a convenience, not a control (D9).
- [ ] **Step 4: Run** `uv run --frozen pytest tests/protocol tests/unit/runtime -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(runtime): AUTORESEARCH_AUTONOMY=on-request waits for the coach's first task`.

### Task 4: `status` read command (ide-mvp)

**Files:**
- Create: `autoresearch/src/autoresearch/runtime/status.py`
- Modify: `autoresearch/src/autoresearch/cli.py`
- Test: `autoresearch/tests/unit/runtime/test_status.py`

**Interfaces:**
- Produces: `"$AUTORESEARCH_CLI" status --json` →
  `{"ok": true, "data": {"distribution": {"version": str, "commit": str|null, "update_failed": {"commit": str, "error": str}|null}, "paused": bool, "pause_reasons": [str], "coach_hold": bool, "autonomy": "always"|"on-request", "started": bool, "spend": {"today_usd": float, "daily_budget_usd": float}, "key": {"limit_usd": float|null, "usage_usd": float|null, "limit_remaining_usd": float|null}, "openrouter_floor_usd": float, "running_cards": [{"task": str, "mode": str, "title": str}], "open_questions": int, "champion": {"policy": str|null, "conflict": bool}, "last_keeper_run": str|null}, "warnings": [str]}`. No credential.

- [ ] **Step 1: Write the failing test**: fakegame workspace (bootstrap fixture), a `pause.json`, a `started.json`, a coach hold marker (Task 25's path; skip that assertion if Task 25 is not merged yet), a fake `state.db` session costing `$0.40` today, a fake key response `{"data": {"limit": 500, "usage": 12.5, "limit_remaining": 487.5}}`, a profile file `.softmax-update-failed`; assert the fields; assert that no JSON value equals `SOFTMAX_PLAYER_CREDENTIAL` or `OPENROUTER_API_KEY` from the env.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/unit/runtime/test_status.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `status.build(ctx, env, http_get) -> dict` from existing readers (pause, spend day sum, queue running cards, open `question`/`help_request` messages, `promotion.champion_status` with a warning on failure, `keeper.log` last line time, the key via the same `GET /api/v1/key` preflight uses, `distribution.yaml`, the profile markers `.softmax-distribution-commit` and `.softmax-update-failed` written by Task 8). Register `status` as a read command allowed in card contexts.
- [ ] **Step 4: Run** the test and `tests/unit/plugin -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(cli): status read command for the coach chat and operators`.

### Task 24 (moved before the pin): Baseline report (ide-mvp)

Moved here so the pinned commit has it (I8). The data collection steps are in "Baseline data" below.

**Files:**
- Create: `autoresearch/src/autoresearch/runtime/baseline.py`
- Modify: `autoresearch/src/autoresearch/cli.py` (`report baseline`), `README.md`
- Test: `autoresearch/tests/unit/runtime/test_baseline.py`

**Interfaces:**
- Consumes: Hermes `state.db` `sessions` (cost estimates, tokens, model, `source`, start/end); board runs; our ledger (card mode, request id, experiments with XP requests, episodes, credits); `.supervisor/ledger/health/spend/`; the preflight `credits` record; `GET /api/v1/key`.
- Produces: `"$AUTORESEARCH_CLI" report baseline --since <ISO date> --json` →
  `{"cards": [{"task", "mode", "model", "runs", "wall_s", "input_tokens", "output_tokens", "cache_read_tokens", "estimated_usd", "outcome"}], "days": [{"date", "estimated_usd_by_source": {"cli", "api_server", "compiler", "cron"}, "key_usage_delta_usd", "cards_finished"}], "experiments": [{"id", "xp_requests", "episodes", "credits", "wall_s_to_evidence", "estimated_usd_of_its_cards"}], "builds": [{"card", "wall_s", "gates": {"g1": s}}], "key": {"limit_usd", "usage_usd", "limit_remaining_usd"}}`. No credential. Task 5 exposes it as the coach tool `baseline_report`.

- [ ] **Step 1: Write the failing test:** fake `state.db` with three sessions on two days (a `cli` card session with two runs, an `api_server` chat session, a compiler session under the GotA adapter's compiler home), a board with one finished card, a ledger experiment with two XP requests of one episode each, a fake key response; assert per-card, per-day and per-experiment numbers, chat spend under `api_server`, and no secret in the JSON.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/unit/runtime/test_baseline.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `baseline.build(ctx, env, since, http_get) -> dict` with read-only queries (`state.db` opened `mode=ro`); register the CLI command (read-only, allowed in card contexts).
- [ ] **Step 4: Run** the default tier. Expected: PASS.
- [ ] **Step 5: Commit** `feat(cli): baseline performance and cost report`.

### Task 25: Coach pause and resume (ide-mvp, D15)

**Files:**
- Create: `autoresearch/schemas/comms/inbound/control.json` and an example under `schemas/comms/examples/inbound/control/`
- Modify: `schemas/comms/README.md` (kind registry), `src/autoresearch/comms/inbound.py` (validate `control`), `src/autoresearch/runtime/routing.py` (apply it), `src/autoresearch/runtime/pause.py` (coach hold), `src/autoresearch/runtime/queue_keeper.py` (honor the hold; never auto-clear it), `src/autoresearch/comms/runtime_messages.py` (acknowledgements), `README.md`
- Test: `autoresearch/tests/protocol/test_queue_keeper.py`, `tests/contract/comms/` (schema), `tests/unit/runtime/test_pause.py` (create if absent)

**Interfaces:**
- Produces: inbound kind `control` `{"kind": "control", "action": "pause"|"resume", "id", "created", "author", "on_behalf_of": "human"}`; ledger marker `.supervisor/ledger/coach-hold.json` `{"held_at", "by"}`; a runtime `note` "Research paused at your request." / "Research resumed." with `in_reply_to` the control id; `status.coach_hold` (Task 4). Task 5 sends these from the chat lines `/pause-research` and `/resume-research`.

- [ ] **Step 1: Write the failing tests:**
  1. an adopted `control` `pause` writes `coach-hold.json`, unassigns pending cards like a C7 pause, stops admission and planning, and publishes one note; running cards keep running under their caps;
  2. a later queue-keeper run whose preflight passes does **not** clear the hold;
  3. `control` `resume` clears the hold and publishes one note; a resume with no hold publishes a note saying nothing was paused;
  4. a preflight pause and a coach hold together: resume clears only the hold; the preflight pause still applies;
  5. schema: `action` outside `pause|resume` is rejected with the usual rejection note.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/protocol/test_queue_keeper.py tests/contract/comms -k control -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the kind (schema plus registry row), routing application under the canonical lock (`ledger.commit`), the hold check next to `is_paused` wherever admission, dispatch and planning check it, and the two notes. The hold is independent of `pause.json`, so preflight's auto-resume (`pause.resume`) never touches it.
- [ ] **Step 4: Run** the default tier. Expected: PASS.
- [ ] **Step 5: Commit** `feat(comms): coach pause and resume of the research loop`.

### Task 27: Profile works under any install name (ide-mvp, D2)

**Files:**
- Modify: any code, skill, doctrine or README text that names the profile (`grep -rn -E "profiles/autoresearch|-p autoresearch|profile.*autoresearch" autoresearch/ --include=*.py --include=*.md --include=*.yaml --include=*.json`); keep the board slug `autoresearch` and the CLI name `autoresearch`.
- Test: `autoresearch/tests/integration/test_install.py` (`-m hermes`), a new unit test `tests/unit/test_profile_name.py`

**Interfaces:**
- Produces: the profile name is always derived from the profile home (`Path(hermes_home).name`, as preflight already does at `preflight.py:110`) or from Hermes; the board slug is a constant `autoresearch` (not the profile name); queue-keeper assigns cards to the derived profile name. Docs write `hermes -p <profile>` with `<profile>` explained once ("`optimizer` on Softmax hosting, `autoresearch` in a manual install").

- [ ] **Step 1: Write the failing tests:** (1) unit: with a profile home `.../profiles/optimizer`, `RunContext`'s profile is `optimizer`, the board slug is `autoresearch`, admission assigns to `optimizer`, and preflight's `dispatch_profiles` check looks for `optimizer`; (2) a grep test that fails on `-p autoresearch` or `profiles/autoresearch` in skills and doctrine (allowlist the README's manual-install section); (3) `-m hermes` integration: `hermes profile install <dist> --name optimizer -y`, bootstrap, resume cron, run queue-keeper once with the fake game: board `autoresearch` exists, a planning card is assigned to `optimizer`, and `config.yaml`'s `terminal.env_passthrough` still has all our names after `hermes profile update optimizer -y`; also record the installed `distribution.yaml` (expected `name: optimizer`, and the exact `source:` string Hermes writes for a local path, which Task 8's `is_ours` compares against `/opt/autoresearch`; fix `is_ours` if the string differs, for example a resolved or `file://` path), and assert that running Task 8's `prepare_profile` twice against this real profile does not move it aside.
- [ ] **Step 2: Run** them. Expected: (2) and possibly (1) FAIL.
- [ ] **Step 3: Implement:** replace hard-coded names found by the grep; make the board slug a named constant if it is derived from the profile name today; update skills and doctrine text.
- [ ] **Step 4: Run** the default tier and the `-m hermes` test. Expected: PASS.
- [ ] **Step 5: Commit** `refactor: profile works under any install name; board slug stays autoresearch`.

### Task 5: Coach chat, full design (ide-mvp, D7, D8, D15)

**Files:**
- Create: `autoresearch/src/autoresearch/plugin/coach.py` (tools, `profile_env`), `autoresearch/src/autoresearch/plugin/proposals.py` (in-memory proposal store), `autoresearch/doctrine/coach-chat.md`
- Modify: `plugin/__init__.py` (register tools), `plugin/adapters.py:214-252` (`pre_llm_call` branch), `plugin/policy.py` (allowlist for `api_server` sessions), `runtime/spend.py` (exclude `api_server` sessions from the research daily sum; chat day snapshots and cap), `config.yaml`, `runtime/config_migrations.py` (C9 rows), `distribution.yaml` (`AUTORESEARCH_CHAT_DAILY_USD`), `SOUL.md` (first paragraph), `plugins/autoresearch/plugin.yaml`
- Test: `autoresearch/tests/unit/plugin/test_coach.py`, `tests/unit/plugin/test_proposals.py`, `tests/unit/runtime/test_spend.py`; `tests/integration/test_plugin_hermes.py` (`-m hermes`)

**Interfaces:**
- Consumes: Task 4 `status`, Task 24 `report baseline`, Task 25 `control`, Task 27 profile derivation; CLI read commands with the shim's argv (`autoresearch-shim/src/ar_shim/preston.py:236-259`); `comms inbound <kind> --json <payload> --author <author>` (`comms/inbound.py:103-126`); schemas `schemas/comms/inbound/{task,steer,reply,control}.json`.
- Produces: toolset `autoresearch_coach` (tools in 3.7 plus `propose_inbound(kind: str, payload: dict, summary: str) -> str`); `coach.profile_env() -> tuple[Path, dict[str, str]]`; `proposals.Store` with `propose(session_id, kind, payload, summary, now) -> Proposal`, `take(session_id, proposal_id, now) -> Proposal | None`, `cancel(session_id, proposal_id) -> bool`; env `AUTORESEARCH_CHAT_DAILY_USD` (default `3`); config rows in 3.7.

- [ ] **Step 1: Write the failing unit tests:**
  1. **Environment from the routed profile (I5):** with `os.environ` holding no `AUTORESEARCH_*`, `SOFTMAX_*` or profile `HERMES_HOME` (`monkeypatch.delenv`), and `hermes_constants.get_hermes_home` monkeypatched to a temp `.../profiles/optimizer` whose installed `distribution.yaml` is shaped like Hermes's real output (`name: optimizer`, `source: /opt/autoresearch`), which has `plugins/autoresearch/plugin.yaml`, and whose `.env` has `AUTORESEARCH_CLI=/x/bin/autoresearch`, `SOFTMAX_PLAYER_CREDENTIAL=cga_t`, `AUTORESEARCH_PLAYER_ID=ply_t`: the `status` tool runs argv `["/x/bin/autoresearch", "status", "--json"]` with `env["HERMES_HOME"]` = the profile and `env["SOFTMAX_PLAYER_CREDENTIAL"] == "cga_t"`, without `shell=True`; with `get_hermes_home` returning `/opt/data` (host root) or a profile without our plugin (rohit-optimizer's, also `name: optimizer`), the tool refuses.
  2. each read tool's argv is fixed; output truncated to 12,000 characters;
  3. `pre_llm_call(platform="api_server", session_id="s1", user_message="hi")` returns the brief and records `s1`; `platform="cli"` returns the card briefing as before;
  4. **Proposal and confirm:** in session `s1`, `propose_inbound("steer", {"text": "Stop kill-steals", "urgency": "now", ...}, "something harmless")` stores nothing on disk and returns the proposal id to the model; `transform_llm_output(response_text="ok", session_id="s1", platform="api_server", ...)` returns `"ok"` plus a rendering that contains `Stop kill-steals` and `urgency now` and does **not** contain `something harmless`; a confirm before that transform does nothing; an invalid payload returns the schema error and stores nothing; `pre_llm_call(platform="api_server", session_id="s1", user_message="confirm P-<id>\n")` (trailing newline) runs `["/x/bin/autoresearch", "comms", "inbound", "steer", "--json", <payload>, "--author", "coach:ply_t"]` with the profile env and injects "Sent as ..."; a second identical confirm does nothing (taken once);
  5. **Confirm cannot be forged:** the same confirm line in session `s2`, with `platform="cli"`, as `"confirm P-<id> please"`, after 15 minutes (inject the clock), or after `cancel P-<id>` sends nothing; two threads calling `take` for the same proposal at once send it exactly once; a proposal whose turn ended with an empty reply (`transform_llm_output` not called) is not confirmable and the next `pre_llm_call` tells the model it is unshown;
  6. **Exact-line commands:** `"/pause-research"` and `"/resume-research"` in `s1` send `control` with `{"action": "pause"}` / `{"action": "resume"}`; `"please /pause-research"` does nothing;
  7. `pre_tool_call` for `s1` blocks `terminal`, `write_file`, `execute_code`, `kanban_create`, and any tool not in `autoresearch_coach`; allows `status` and `propose_inbound`;
  8. coach tools refuse from an unrecorded session or with `HERMES_KANBAN_TASK` set; `propose_inbound` refuses kinds other than `task`, `steer`, `reply`, `control`;
  9. **Chat budget:** with a fake `state.db` where the `api_server` session's cost rose by `$3.10` since today's first snapshot and `AUTORESEARCH_CHAT_DAILY_USD=3`, every coach tool except `status` refuses with the budget message, and the research daily sum excludes `api_server` sessions (`test_spend.py`).
- [ ] **Step 2: Run** `uv run --frozen pytest tests/unit/plugin/test_coach.py tests/unit/plugin/test_proposals.py tests/unit/runtime/test_spend.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per 3.7 and coach-chat-role.md §2.2-§2.4 and §4: `profile_env()` (`get_hermes_home()` at call time, our-plugin check, parse `.env`, build the env); the read tools; `proposals.Store` (memory only, `threading.Lock`, keyed by `session_id`, ids `P-` + 4+ hex, 15-minute expiry, rendering built from payload fields, `shown` set by the transform hook); the `transform_llm_output` hook (append unshown renderings and sent lines for `api_server` sessions); the `pre_llm_call` branch (brief; then, on the stripped line, exact whole-line matches for `^confirm P-[0-9a-f]{4,}$`, `^cancel P-[0-9a-f]{4,}$`, `^/(pause|resume)-research$`; sends run `comms inbound` through `profile_env()`); the `pre_tool_call` allowlist keyed by the in-memory session map; chat spend snapshots (a small JSON in the profile's `local/` keyed by date, written from `pre_llm_call`) and the cap; the config rows; the brief; the `SOUL.md` opening. The existing refusals of `comms inbound` for model tool calls and in card contexts stay unchanged.
- [ ] **Step 4: Integration check against the image** (procedure in `docs/plans/findings/hermes-image-compat.md`, with `--basetemp=/opt/data/pt`), profile installed as `optimizer`. Add `test_coach_chat_routes_by_platform` to `tests/integration/test_plugin_hermes.py`: start `GATEWAY_MULTIPLEX_PROFILES=true hermes -p default gateway run`; post runs to `/p/optimizer/v1/runs` with one `session_id`: (a) "What is your status? Use your status tool." — assert from the plugin's event log that `pre_llm_call` saw `platform=api_server`, only `autoresearch_coach` tools were offered, and `status` **ran and returned `ok: true`** (proves the env mechanism); (b) "Tell the agent to stop working on kill-steals" — assert the run's `output` (what the bridge says) ends with the code-written rendering; (c) `confirm P-<id>` — assert a `steer` inbound was committed by the CLI; (d) `/pause-research` — assert a `control` inbound (**stop condition**: if Hermes's API server treats `/pause-research` as a gateway slash command instead of passing it to the run, stop and switch D15 to the bridge-side fallback); then let the gateway dispatch one Kanban card and assert its worker was offered the full `cli` toolset; also assert that no other enabled plugin in the profile defines `transform_llm_output`. Expected cost under `$0.50`; run with James's go. If hooks (including `transform_llm_output`) do not fire for multiplexed runs, stop and record it in findings.md: the chat then fails closed, and D7/D15 need the bridge-side fallback.
- [ ] **Step 5: Commit** `feat(plugin): coach chat with confirm-gated proposals, pause and resume, and a chat budget`.

### Task 6: Hosted operations docs and settings (ide-mvp)

**Files:** `autoresearch/README.md` (new "Softmax hosting" section), `distribution.yaml` (`AUTORESEARCH_AUTONOMY`; `TMPDIR` note), `config.yaml` (`terminal.env_passthrough` += `TMPDIR`, `AUTORESEARCH_AUTONOMY`, `AUTORESEARCH_CHAT_DAILY_USD`, `AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD` if Task 1 did not), `docs/integrations/softmax-ide-bridge-compat.md` (trust, outcome mapping, follow-up acknowledgement), `docs/integrations/rohit-architecture-integration.md` (pointer here).

- [ ] **Step 1:** Write the section: the profile is installed as `optimizer` on Softmax hosting (D2); coaches should set up the player's Controls (league, asks, `dailyCredits`) because the server does not limit the token until they do (D4); how to propose and confirm (`confirm P-xxxx`); what the runtime writes (3.4), where things live, start on request, **how a coach stops and resumes research** (`/pause-research`, `/resume-research`; cancelling a task stops only that task; `/stop` stops only the chat turn), budgets, what is not isolated, how updates arrive (new image plus workspace move), and the `cga_` revoke path.
- [ ] **Step 2:** Run the default tier. Expected: PASS.
- [ ] **Step 3:** Commit `docs: Softmax hosting operations`.

### Task 10b: Follow-up acknowledgement after `blocked` (ide-mvp, I1, R2-I1)

Why the first design did not work: `acknowledge_inbound` visits only delivery records in state `routed` (`comms/publisher.py:546-548`) and moves a record to `acknowledged` once a reply exists (`:566-567`). After a `blocked` acknowledgement the record is `acknowledged`, so a later handoff of the same card never reaches `_task_acknowledgement`. The follow-up must be decided from the delivery record and the card, not from the record still being `routed`.

**Files:**
- Modify: `autoresearch/src/autoresearch/comms/publisher.py:536-569` (`acknowledge_inbound`), `:495-534` (`_task_acknowledgement`, a `follow_up` mode), `comms/delivery.py` (a `final_ack_by` field on the record), `comms/runtime_messages.py`
- Test: the acknowledgement protocol tests (`grep -rl "acknowledge_inbound" autoresearch/tests/`), driven through `publish_hook` with real handoff dicts

**Interfaces:**
- Produces: for a delivery record in state `acknowledged`, kind `task`, whose `acknowledged_by` note has `about.outcome == "blocked"`, with no `final_ack_by`, and for which this handoff's card is the responsible card (`_is_responsible(record, handoff)`; check whether a repaired or retried card keeps the same `card` id, and if not, also accept the card the repair was made for, read from the repair card's request): publish a runtime note with `in_reply_to` = the inbound id, `about.outcome` = this handoff's outcome, `about.follows` = the first note's id, `runtime_key` `ack-final:<inbound id>`; then write `final_ack_by=<note id>` on the record. **These are two commits:** `publish_message` commits the outbox file itself (`publish_runtime` → `_commit_pending`, `comms/publisher.py:377`), and it does not dedupe by `runtime_key`. So before publishing, call `already_published(canonical, message)` (`publisher.py:121-135`) with the `ack-final:<inbound id>` message; if it is already there, find that note's id by scanning the outbox for the `runtime_key` (`find_reply` returns the oldest reply, the `blocked` note, so it cannot be used) and only write `final_ack_by`. Only when this handoff's outcome is terminal (`done|partial|dropped|failed`); a second `blocked` publishes nothing. The bridge (Task 10) closes the IDE task with it.

- [ ] **Step 1: Write the failing tests** through the real path (`publish_hook` with a merged handoff, as the existing acknowledgement tests do): (1) an inbound `task` routed to card C; a handoff of C with outcome `blocked` acknowledges it (`acknowledged`, note with `about.outcome: blocked`); a later handoff of C with outcome `done` publishes exactly one follow-up (`in_reply_to` the inbound id, `about.outcome: done`, `about.follows` the first note) and sets `final_ack_by`; running the same `publish_hook` again publishes nothing; (2) a first acknowledgement `done` never gets a follow-up; (3) `blocked` then `blocked` publishes nothing new; (4) a crash after the follow-up's commit and before `final_ack_by` is written (a `_checkpoint` placed between the two, as the existing crash tests do) is repaired on rerun: exactly one follow-up note exists in the outbox afterwards (assert the count by `runtime_key`), and `final_ack_by` names it; (5) if the blocked card is repaired as a new card (check the repair path first), the repair card's `done` handoff produces the follow-up.
- [ ] **Step 2: Run** them. Expected: FAIL (the `acknowledged` record is skipped).
- [ ] **Step 3: Implement** a second pass in `acknowledge_inbound` over `acknowledged` records matching the rule above, checking `already_published` first, publishing through `_task_acknowledgement(..., follow_up_of=<first note id>)` when needed (its own commit), then writing `final_ack_by` with `delivery.write_record` in the pass's commit, under the same lock.
- [ ] **Step 4: Run** the default tier. Expected: PASS.
- [ ] **Step 5: Commit** `feat(comms): follow-up acknowledgement when a blocked task finishes`.

- [ ] **Pin step (end of the ide-mvp work):** open the ide-mvp PR for Tasks 1-6, 10b, 24, 25, 27 **and** the M2 ide-mvp tasks (15, 16) if they are ready; otherwise merge this PR for M1 and pin again after Tasks 15-16 before the hosted deploy. Record the merge commit; Task 7 pins it.

### Task 7: Pinned distribution artifact in the image (metta)

**Files:**
- Create: `devops/hermes-fleet/autoresearch_dist.py` (thin CLI), `packages/observatory-hosted-worker/src/observatory_hosted_worker/dist.py`, `packages/observatory-hosted-worker/autoresearch.lock.json`, `packages/observatory-hosted-worker/dist/.gitignore` (`*`, `!.gitignore`)
- Modify: `packages/observatory-hosted-worker/Dockerfile`, `devops/hermes-fleet/README.md` ("Build and deploy" step 1), `packages/observatory-hosted-worker/README.md`
- Test: `packages/observatory-hosted-worker/tests/test_dist.py`

**Interfaces:**
- Produces: `/opt/autoresearch/` (distribution root), `/opt/autoresearch.commit`. Lock `{"commit", "tree", "source": "Metta-AI/ide-mvp"}`.

- [ ] **Step 1: Write the failing test:**

```python
def _repo(tmp_path):
    repo = tmp_path / "ide-mvp"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "autoresearch").mkdir()
    (repo / "autoresearch" / "distribution.yaml").write_text("name: autoresearch\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    return repo, subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def test_lock_pins_commit_and_tree_and_refuses_a_mismatch(tmp_path):
    repo, commit = _repo(tmp_path)
    lock, out = tmp_path / "lock.json", tmp_path / "autoresearch.tar"
    dist.build(repo, commit, out, lock, update_lock=True)
    first = json.loads(lock.read_text())
    assert first["commit"] == commit and len(first["tree"]) == 40
    dist.build(repo, commit, out, lock, update_lock=False)
    lock.write_text(json.dumps({**first, "tree": "0" * 40}))
    with pytest.raises(dist.LockMismatch):
        dist.build(repo, commit, out, lock, update_lock=False)


def test_extracted_tree_matches_the_lock(tmp_path):
    repo, commit = _repo(tmp_path)
    lock, out = tmp_path / "lock.json", tmp_path / "autoresearch.tar"
    dist.build(repo, commit, out, lock, update_lock=True)
    target = tmp_path / "opt"
    target.mkdir()
    subprocess.run(["tar", "-xf", str(out), "-C", str(target)], check=True)
    assert dist.tree_of(target / "autoresearch") == json.loads(lock.read_text())["tree"]
```

- [ ] **Step 2: Run** `nix develop -c bazel test //packages/observatory-hosted-worker/tests:test_dist --test_output=errors`. Expected: FAIL.
- [ ] **Step 3: Implement** `dist.build` (`git cat-file -e <commit>^{commit}`; `tree = git rev-parse <commit>:autoresearch`; compare or write the lock; `git archive --format=tar -o <out> <commit> autoresearch`) and `dist.tree_of(path)` (copy into a temp dir, `git init -q`, `git add -A`, `git write-tree`). Note: `git write-tree` ignores file modes other than the executable bit and ignores empty directories, which matches `git archive` output. Dockerfile, replacing the rohit-optimizer clone (git is in the base image):

```dockerfile
COPY autoresearch.lock.json dist/autoresearch.tar /tmp/autoresearch/
COPY src/observatory_hosted_worker/dist.py /tmp/autoresearch/dist.py
RUN cd /tmp/autoresearch && tar -xf autoresearch.tar -C /opt \
    && /opt/hermes/.venv/bin/python -c 'import json, sys; sys.path.insert(0, "."); import dist; l = json.load(open("autoresearch.lock.json")); assert dist.tree_of("/opt/autoresearch") == l["tree"], "autoresearch tree does not match the lock"; open("/opt/autoresearch.commit", "w").write(l["commit"] + "\n")' \
    && rm -rf /tmp/autoresearch
```

- [ ] **Step 4: Run** the tests; then `python devops/hermes-fleet/autoresearch_dist.py --ide-mvp ~/coding/ide-mvp --commit <pinned commit> --update-lock` and `docker build --platform linux/amd64 packages/observatory-hosted-worker -t hermes-runtime:ar-local`. Expected: PASS; build succeeds; `docker run --rm --entrypoint cat hermes-runtime:ar-local /opt/autoresearch.commit` prints the commit.
- [ ] **Step 5: Commit** `feat(hosted-worker): bake a pinned autoresearch distribution into the runtime image`.

### Task 8: Runtime installs and updates autoresearch (metta)

**Files:**
- Modify: `packages/observatory-hosted-worker/src/observatory_hosted_worker/runtime.py:21-87`, `:105-141`
- Test: `packages/observatory-hosted-worker/tests/test_runtime.py` (replace `test_optimizer_install_is_atomic_and_reconnect_preserves_state`; extend `test_profile_credentials_preserve_user_entries_on_reconnect`)

**Interfaces:**
- Consumes: Task 7 paths; Tasks 3, 25 behavior (no names needed beyond `AUTORESEARCH_AUTONOMY`).
- Produces: `PROFILE`, `profile_home(home) -> Path`, `prepare_profile(home, env) -> None` (raises only on a fresh-install failure), `configure_host_kanban(env) -> None`, `ensure_workspace_remote(home) -> Path`, `configure_profile_credentials(profile, env, extra={}) -> None`, `HOSTED_SETTINGS: dict[str, str]`, `LOCAL_SETTINGS_FILE = Path("/run/softmax/hosted-settings.json")` (optional override for local runs).

- [ ] **Step 1: Write the failing tests** (fake-`subprocess.run` style of the test being replaced):

```python
def _setup(monkeypatch, tmp_path, commit_text):
    dist = tmp_path / "dist"; dist.mkdir(); (dist / "distribution.yaml").write_text("name: autoresearch\n")
    commit = tmp_path / "commit"; commit.write_text(commit_text + "\n")
    monkeypatch.setattr(runtime, "DISTRIBUTION", dist)
    monkeypatch.setattr(runtime, "DISTRIBUTION_COMMIT", commit)
    home = tmp_path / "home"; home.mkdir()
    return home


def test_install_is_atomic_resumes_cron_and_writes_the_commit(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path, "a" * 40)
    calls = []

    def command(args, *, env, check):
        calls.append(args)
        if args[1:3] == ["profile", "install"]:
            (Path(env["HERMES_HOME"]) / "profiles" / "optimizer").mkdir(parents=True)

    monkeypatch.setattr(runtime.subprocess, "run", command)
    runtime.prepare_profile(home, {})
    profile = home / "profiles" / "optimizer"
    assert (profile / ".softmax-distribution-commit").read_text().strip() == "a" * 40
    assert [args[3:6] for args in calls if "cron" in args] == [["cron", "resume", job] for job in runtime.CRON_JOBS]
    assert not any(args[3:5] == ["config", "set"] for args in calls)
    calls.clear()
    runtime.prepare_profile(home, {})
    assert calls == []


def test_update_reapplies_and_resumes_cron(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path, "b" * 40)
    profile = home / "profiles" / "optimizer"; profile.mkdir(parents=True)
    (profile / ".softmax-distribution-commit").write_text("a" * 40 + "\n")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, *, env, check: calls.append(args))
    runtime.prepare_profile(home, {})
    assert calls[0][1:5] == ["profile", "update", "optimizer", "-y"]
    assert [args[3:6] for args in calls[1:]] == [["cron", "resume", job] for job in runtime.CRON_JOBS]
    assert (profile / ".softmax-distribution-commit").read_text().strip() == "b" * 40
    assert not (profile / ".softmax-update-failed").exists()


def test_failed_update_falls_back_to_installed_profile(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path, "b" * 40)
    profile = home / "profiles" / "optimizer"; profile.mkdir(parents=True)
    (profile / ".softmax-distribution-commit").write_text("a" * 40 + "\n")

    def command(args, *, env, check):
        if args[1:3] == ["profile", "update"]:
            raise runtime.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(runtime.subprocess, "run", command)
    runtime.prepare_profile(home, {})  # does not raise
    # Also for a non-CalledProcessError failure (an OSError mid-update):
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, *, env, check: (_ for _ in ()).throw(OSError("disk full")))
    runtime.prepare_profile(home, {})
    assert (profile / ".softmax-distribution-commit").read_text().strip() == "a" * 40
    failed = json.loads((profile / ".softmax-update-failed").read_text())
    assert failed["commit"] == "b" * 40


def test_a_foreign_optimizer_profile_is_moved_aside_and_ours_installed(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path, "a" * 40)
    old = home / "profiles" / "optimizer"; old.mkdir(parents=True)
    # Shaped like Hermes's real installed manifest: name rewritten to the install name, source = provenance.
    (old / "distribution.yaml").write_text("name: optimizer\nversion: 0.2.0\nsource: /opt/data/rohit-optimizer\n")
    calls = []

    def command(args, *, env, check):
        calls.append(args)
        if args[1:3] == ["profile", "install"]:
            staged = Path(env["HERMES_HOME"]) / "profiles" / "optimizer"
            staged.mkdir(parents=True)
            (staged / "distribution.yaml").write_text("name: optimizer\nversion: 0.1.0\nsource: /opt/autoresearch\n")

    monkeypatch.setattr(runtime.subprocess, "run", command)
    runtime.prepare_profile(home, {})
    assert not any(args[1:3] == ["profile", "update"] for args in calls)
    assert "source: /opt/autoresearch" in (home / "profiles" / "optimizer" / "distribution.yaml").read_text()
    [aside] = list((home / "profiles").glob(".optimizer-replaced-*"))
    assert "source: /opt/data/rohit-optimizer" in (aside / "distribution.yaml").read_text()
    calls.clear()
    runtime.prepare_profile(home, {})  # a restart: our own profile is recognised and left alone
    assert calls == [] and len(list((home / "profiles").glob(".optimizer-replaced-*"))) == 1


def test_our_profile_is_recognised_by_marker_or_source_not_name(tmp_path):
    ours = tmp_path / "ours"; ours.mkdir()
    (ours / "distribution.yaml").write_text("name: optimizer\nsource: /opt/autoresearch\n")
    assert runtime.is_ours(ours)
    marked = tmp_path / "marked"; marked.mkdir()
    (marked / "distribution.yaml").write_text("name: optimizer\nsource: /somewhere/else\n")
    (marked / ".softmax-distribution-commit").write_text("a" * 40 + "\n")
    assert runtime.is_ours(marked)
    foreign = tmp_path / "foreign"; foreign.mkdir()
    (foreign / "distribution.yaml").write_text("name: autoresearch\nsource: /opt/data/rohit-optimizer\n")
    assert not runtime.is_ours(foreign)


def test_failed_fresh_install_raises_and_leaves_nothing(monkeypatch, tmp_path):
    home = _setup(monkeypatch, tmp_path, "a" * 40)

    def command(args, *, env, check):
        raise runtime.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(runtime.subprocess, "run", command)
    with pytest.raises(runtime.subprocess.CalledProcessError):
        runtime.prepare_profile(home, {})
    assert not (home / "profiles" / "optimizer").exists()
    assert not list(home.glob(".autoresearch-install-*"))


def test_autoresearch_env_is_written_without_dropping_user_entries(tmp_path):
    profile = tmp_path / "profile"; profile.mkdir()
    (profile / ".env").write_text("OPENROUTER_MODEL=user-choice\nUNRELATED=1\n")
    env = {name: f"v-{name}" for name in runtime.MANAGED_ENV}
    runtime.configure_profile_credentials(profile, env, {"SOFTMAX_PLAYER_CREDENTIAL": "cga_x", "AUTORESEARCH_PLAYER_ID": "ply_1"})
    text = (profile / ".env").read_text()
    assert "UNRELATED=1" in text and "OPENROUTER_MODEL=user-choice" in text
    assert "SOFTMAX_PLAYER_CREDENTIAL='cga_x'" in text  # python-dotenv set_key quotes values
    assert oct((profile / ".env").stat().st_mode & 0o777) == "0o600"


def test_host_kanban_settings_are_applied_every_start(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, *, env, check: calls.append(args[1:]))
    runtime.configure_host_kanban({"HERMES_HOME": "/opt/data"})
    assert calls == [
        ["config", "set", "kanban.max_in_progress", "2"],
        ["config", "set", "kanban.dispatch_in_gateway", "true"],
        ["config", "unset", "kanban.default_assignee"],
    ]


def test_workspace_remote_is_created_once(tmp_path):
    remote = runtime.ensure_workspace_remote(tmp_path)
    assert (remote / "HEAD").exists()
    (remote / "marker").write_text("x")
    assert runtime.ensure_workspace_remote(tmp_path) == remote and (remote / "marker").exists()


def test_local_settings_file_overrides_hosted_settings(monkeypatch, tmp_path):
    override = tmp_path / "hosted-settings.json"
    override.write_text(json.dumps({"AUTORESEARCH_GAME": "fakegame", "AUTORESEARCH_SOFTMAX": "fake"}))
    monkeypatch.setattr(runtime, "LOCAL_SETTINGS_FILE", override)
    settings = runtime.hosted_settings()
    assert settings["AUTORESEARCH_GAME"] == "fakegame" and settings["AUTORESEARCH_AUTONOMY"] == "on-request"
```

- [ ] **Step 2: Run** `nix develop -c bazel test //packages/observatory-hosted-worker/tests:test_runtime --test_output=errors`. Expected: FAIL.
- [ ] **Step 3: Implement.** Replace `OPTIMIZER_SOURCE`/`prepare_optimizer` with:

```python
PROFILE = "optimizer"  # D2: our distribution installed under the name the bridge routes to
DISTRIBUTION = Path("/opt/autoresearch")
DISTRIBUTION_COMMIT = Path("/opt/autoresearch.commit")
COMMIT_MARKER = ".softmax-distribution-commit"
UPDATE_FAILED_MARKER = ".softmax-update-failed"
CRON_JOBS = ("queue-keeper", "xp-poll", "health")
MANAGED_ENV = ("API_SERVER_KEY", "API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_PORT", "OPENROUTER_API_KEY", "COGAMES_API_URL")
# The first hosted deploy is M2-ready (GotA). ide-mvp autoresearch/README.md "Softmax hosting" documents each name.
# Models and spend caps are deliberately absent: the profile's shipped defaults apply.
HOSTED_SETTINGS = {
    "AUTORESEARCH_GAME": "gota",
    "AUTORESEARCH_AUTONOMY": "on-request",
    "TMPDIR": "/opt/data/tmp",
    "GOTA_HERMES_PYTHON": "/opt/hermes/.venv/bin/python",
    "GOTA_TIRITH_BIN": "/opt/data/profiles/optimizer/bin/tirith",  # verified in Task 18
    "PATH": "/opt/nim-2.2.10/bin:${PATH}",  # braces required: Hermes leaves a bare $PATH literal
}
# Local VM runs only: a JSON object merged over HOSTED_SETTINGS (Task 12 uses it for fakegame). Mounted read-only.
LOCAL_SETTINGS_FILE = Path("/run/softmax/hosted-settings.json")


def hosted_settings() -> dict[str, str]:
    extra = json.loads(LOCAL_SETTINGS_FILE.read_text()) if LOCAL_SETTINGS_FILE.exists() else {}
    return HOSTED_SETTINGS | extra


def profile_home(home: Path) -> Path:
    return home / "profiles" / PROFILE


def is_ours(profile: Path) -> bool:
    """Our runtime installed this profile. Never decided by the manifest `name`: Hermes rewrites it to the
    install name on install and update (profile_distribution.py:326,332 at v0.21.6), so rohit-optimizer's
    profile also reads `name: optimizer`. Our commit marker is written only by this runtime; the recorded
    `source:` is the install path (manifest.source = provenance, :333)."""
    if (profile / COMMIT_MARKER).exists():
        return True
    manifest = profile / "distribution.yaml"
    lines = manifest.read_text().splitlines() if manifest.exists() else []
    return any(line.split(":", 1)[1].strip() == str(DISTRIBUTION) for line in lines if line.startswith("source:"))


def resume_cron(env: dict[str, str]) -> None:
    for job in CRON_JOBS:
        subprocess.run([HERMES, "-p", PROFILE, "cron", "resume", job], env=env, check=True)


def prepare_profile(home: Path, env: dict[str, str]) -> None:
    """Install the image's distribution once, atomically; update it when the image carries a new commit.

    Runs only at container start, so no Kanban worker is mid-run. A failed update keeps the installed
    profile serving chat and research (raising here would restart the container forever, with no chat);
    a failed fresh install raises, because there is nothing to serve."""
    wanted = DISTRIBUTION_COMMIT.read_text().strip()
    profile = profile_home(home)
    if profile.exists() and not is_ours(profile):
        # D2: an earlier image installed rohit-optimizer under this name; updating it would re-pull that source.
        profile.rename(profile.parent / f".{PROFILE}-replaced-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    if profile.exists():
        marker = profile / COMMIT_MARKER
        if marker.exists() and marker.read_text().strip() == wanted:
            return
        try:
            subprocess.run([HERMES, "profile", "update", PROFILE, "-y"], env=env, check=True)
            resume_cron(env)
        except Exception as error:  # noqa: BLE001 - any failure must leave chat running, not crash-loop the container
            print(f"autoresearch profile update to {wanted} failed; keeping the installed profile", flush=True)
            try:
                (profile / UPDATE_FAILED_MARKER).write_text(
                    json.dumps({"commit": wanted, "error": f"{type(error).__name__}: {error}", "maybe_partial": True})
                )
            except OSError:
                pass
            return
        marker.write_text(wanted + "\n")
        (profile / UPDATE_FAILED_MARKER).unlink(missing_ok=True)
        return
    with tempfile.TemporaryDirectory(prefix=".autoresearch-install-", dir=home) as temporary:
        staging = Path(temporary)
        install_env = env | {"HERMES_HOME": str(staging)}
        subprocess.run([HERMES, "profile", "install", str(DISTRIBUTION), "--name", PROFILE, "--yes"], env=install_env, check=True)
        resume_cron(install_env)
        (staging / "profiles" / PROFILE / COMMIT_MARKER).write_text(wanted + "\n")
        profile.parent.mkdir(exist_ok=True)
        (staging / "profiles" / PROFILE).rename(profile)


def configure_host_kanban(env: dict[str, str]) -> None:
    # One gateway dispatcher per host reads the host default profile's kanban settings (ide-mvp amendment A3).
    subprocess.run([HERMES, "config", "set", "kanban.max_in_progress", "2"], env=env, check=True)
    subprocess.run([HERMES, "config", "set", "kanban.dispatch_in_gateway", "true"], env=env, check=True)
    subprocess.run([HERMES, "config", "unset", "kanban.default_assignee"], env=env, check=True)


def ensure_workspace_remote(home: Path) -> Path:
    remote = home / "autoresearch-workspace.git"
    if not (remote / "HEAD").exists():
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    return remote
```

`configure_profile_credentials(profile, env, extra={})` writes `MANAGED_ENV` plus `extra` (same pending-file pattern). In `run`: `(home / "tmp").mkdir(exist_ok=True)`; drop `SOFTMAX_RESEARCH_DIR`/`SOFTMAX_POLICY_DIR` and the `/opt/data/workspace` init; call `configure_host_kanban(env)`, `remote = ensure_workspace_remote(home)`, `prepare_profile(home, env)`, `configure_profile_credentials(home, env)`, and `configure_profile_credentials(profile_home(home), env, {"SOFTMAX_PLAYER_CREDENTIAL": ..., "AUTORESEARCH_PLAYER_ID": config.bridge.player_id, "AUTORESEARCH_WORKSPACE_REMOTE": str(remote), **hosted_settings()})`. `fleet.py` mounts `LOCAL_SETTINGS_FILE` only when the host config names one (Task 9b adds `local_settings: Path | None = None`; production leaves it unset). If Task 12 shows `hermes config unset` fails when the key is absent, replace it with `hermes config get kanban.default_assignee` and unset only when it prints a value.

- [ ] **Step 4: Run** runtime tests, Ruff, ty. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hosted-worker): install and update the autoresearch profile with a safe fallback`. Update the package README "EC2 fleet runtime".

### Task 9: Relay workspace path and chat model (metta; route unchanged, D2)

**Files:** Modify `packages/observatory-hosted-worker/src/observatory_hosted_worker/fleet.py:31`, `:156`. Test: `tests/test_fleet.py`.

- [ ] **Step 1: Write the failing test:** read the written `runtime.json` in the `test_reconnect_replaces_container_but_preserves_disk_session_key_and_image` setup and assert `bridge.hermes_url == "http://127.0.0.1:8642/p/optimizer/"` (unchanged), `bridge.workspace == "/opt/data/profiles/optimizer/workspace"`, `bridge.model == "z-ai/glm-5.3"`.
- [ ] **Step 2: Run** `nix develop -c bazel test //packages/observatory-hosted-worker/tests:test_fleet --test_output=errors`. Expected: FAIL.
- [ ] **Step 3: Implement** `"workspace": str(profile_home(Path("/opt/data")) / "workspace"),` with `from observatory_hosted_worker.runtime import RuntimeConfig, profile_home`, and `model: str = "z-ai/glm-5.3"` in `HostConfig` (IDE chat turns only). Leave `hermes_url` as it is.
- [ ] **Step 4: Run** fleet and runtime tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hosted-worker): enable the research relay for the installed profile`.

### Task 9b: Larger sandboxes on a larger host (metta, D12)

**Files:**
- Modify: `.../fleet.py:26-35` (`HostConfig`), `:183-204` (flags; optional local settings mount)
- Modify: `devops/tf/hermes-fleet/main.tf:181` (`instance_type = "m7i.4xlarge"`), `:193` (`volume_size = 300`), `:206` (`host_config`) with variables `max_workspaces`, `cpu_per_workspace`, `memory_mb`, `disk_gb`, `tmp_mb`, `pids_per_workspace`
- Modify: `devops/hermes-fleet/README.md` (limits, two workspaces per host, "every sizing change replaces the host"), `packages/observatory-hosted-worker/README.md`
- Test: `tests/test_fleet.py`

**Interfaces:**
- Produces: `HostConfig.tmp_mb = 2048` (≥ 64), `pids_per_workspace = 2048` (≥ 128), `local_settings: Path | None = None`; defaults `max_workspaces 2`, `cpu_per_workspace 8`, `memory_mb 16384`, `disk_gb 100`. James may adjust these; they are Spacelift variables.

- [ ] **Step 1: Write the failing tests:** (1) `docker run` argv contains `--cpus 8`, `--memory 16384m`, `--memory-swap 16384m`, `--pids-limit 2048`, `/tmp:rw,nosuid,nodev,size=2048m,uid=10000,gid=10000`, and no settings mount; (2) new `HostConfig` defaults; (3) `tmp_mb: 4096` gives `size=4096m`; (4) `local_settings` set adds `--mount type=bind,src=<path>,dst=/run/softmax/hosted-settings.json,readonly`; (5) `check_host` with `/proc/meminfo` faked at 64 GiB passes for 2 × 16384 MB and fails for 4 × 16384 MB, and with 290 GiB free disk passes for 2 × 100 GiB and fails for 3 × 100 GiB.
- [ ] **Step 2: Run** the fleet tests. Expected: FAIL.
- [ ] **Step 3: Implement** the fields and flags; `main.tf` passes the six sizing values in `host_config` and sets the instance type and root size. A record moved to the new host keeps its old `state.ext4` size because the disk is created only when missing (`fleet.py:118-131`); Task 13 Step 5 grows it.
- [ ] **Step 4: Run** fleet tests, Ruff, ty. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hermes-fleet): 8 CPU / 16 GiB / 100 GiB sandboxes, two per m7i.4xlarge host`.

### Task 10: Map `about.outcome` to IDE task status (metta)

**Files:** Modify `.../bridge.py:151-175`, `:472-536`. Test: `tests/test_relay.py` (the acknowledgement test builds its note inline at `:110-133`).

**Interfaces:** Consumes ide-mvp acknowledgements: `about.outcome` in `OUTCOME_WORDS` (`comms/runtime_messages.py:42-48`: done, partial, dropped, failed, blocked) or `REJECTED` (`:68`), and Task 10b's follow-up notes.

- [ ] **Step 1: Write the failing tests** (add a module constant `ACK_NOTE` built like the inline note at `:110-133`):

```python
ACK_NOTE = {
    "created": "2026-10-07T00:00:00Z", "from": "runtime", "iteration": None, "refs": [], "priority": "P2",
    "id": "m1", "seq": 1, "kind": "note", "title": "Done", "body": "x", "in_reply_to": "ply_test:task:g-1-abcd",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "state", "status", "lead"),
    [
        ("done", "done", "kept", ""),
        ("partial", "done", "kept", "Partly done. "),
        ("dropped", "done", "dropped", ""),
        ("failed", "done", "failed", ""),
        ("rejected", "done", "failed", ""),
        ("blocked", "active", "paused", ""),
    ],
)
async def test_acknowledgement_outcome_sets_the_task_status(tmp_path, outcome, state, status, lead):
    outbox = tmp_path / "workspace/comms/outbox"
    outbox.mkdir(parents=True)
    (outbox / "0001-m1.json").write_text(json.dumps({**ACK_NOTE, "about": {"outcome": outcome}}))
    patches = []

    def serve(request):
        if request.method == "PATCH":
            patches.append(json.loads(request.content))
            return httpx.Response(200, json={})
        if request.url.path.endswith("/say"):
            return httpx.Response(200, json={"seq": 1})
        return httpx.Response(200, json=task("queued"))

    store = Store(tmp_path / "state.json", State(cursor=0, changes_cursor=0, forwarded={"g-1-abcd": "ply_test:task:g-1-abcd"}))
    async with httpx.AsyncClient(base_url="http://softmax", transport=httpx.MockTransport(serve)) as player:
        await relay_outbox(store, config(tmp_path / "workspace"), player)
    assert patches[0]["set"] == {"state": state, "status": status, "outcome": f"{lead}Done\n\nx"}


@pytest.mark.asyncio
async def test_a_blocked_task_is_closed_by_the_follow_up(tmp_path):
    # 0001: blocked -> PATCH active/paused (task served with state "active" afterwards);
    # 0002: follow-up {"about": {"outcome": "done", "follows": "m1"}} -> second PATCH done/kept with a new idempotency key.


@pytest.mark.asyncio
async def test_a_note_without_an_outcome_is_said_but_closes_nothing(tmp_path):
    # ACK_NOTE without "about": one /say call, no PATCH.
```

Write the two commented tests out in full. Add `"about": {"outcome": "done"}` to the existing test's inline note (its expected patch is unchanged).

- [ ] **Step 2: Run** `nix develop -c bazel test //packages/observatory-hosted-worker/tests:test_relay --test_output=errors`. Expected: FAIL.
- [ ] **Step 3: Implement.** `Outbound.about: dict[str, object] = Field(default_factory=dict)`; `TaskCompletion.state`/`status` required; `OUTCOMES` table (3.3) with a comment that ide-mvp sends one follow-up when a blocked task finishes (Task 10b); acknowledge only when `kind == "note"` and `about.get("outcome") in OUTCOMES`. Keep the "already done" early return. If the server ever answered the PATCH with 4xx, `raise_for_status` (`bridge.py:500`) would stop the bridge on every restart because `outbox_seq` does not advance; the task schema allows `active`/`paused` (`cogents/bodies.py:267,371`), so this is unlikely, and Task 12 checks it against the real API. Update the bridge README "Research relay".
- [ ] **Step 4: Run** all hosted-worker tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(bridge): close IDE tasks by structured research outcome`.

### Task 11: `$500`-per-week keys for every hosted workspace (metta code + config + operator, D1/D3)

**Files:** `packages/observatory-execution/src/observatory_execution/job_runner/openrouter_key_pool.py:111-117` (add `limit_reset: Literal["daily", "weekly", "monthly"] | None = None`), `packages/observatory-core/src/observatory_core/hermes/models.py:21-25` (`WorkspaceInferenceSettings.limit_reset`, env `HERMES_INFERENCE_LIMIT_RESET`, default `None` so other deployments keep today's behaviour), `packages/observatory-api/src/observatory_api/clients/hermes/ec2.py:79-84` (pass it to `create_key`), `devops/app-manifests/values.yaml:541` (`HERMES_INFERENCE_BUDGET_USD: "500"`, `HERMES_INFERENCE_LIMIT_RESET: "weekly"`), `devops/hermes-fleet/README.md` (budget paragraph; key-raise procedure).

- [ ] **Step 1: Global budget.** PR: the `limit_reset` field and setting above, with a unit test that `configure` sends `limit=500, limit_reset="weekly"` and that an unset setting sends no reset; then `HERMES_INFERENCE_BUDGET_USD: "500"` and `HERMES_INFERENCE_LIMIT_RESET: "weekly"` (`HERMES_INFERENCE_KEY_DAYS` stays `30`). Check that the OpenRouter client serializes `limit_reset` (and omits it when `None`). Verify the chart renders (`helm template` for the observatory-backend app as #27146 did). Merge on James's word; it applies at the next production API deploy and affects **only workspaces created after it** (`ec2.py:72-73`). Update the fleet README's budget paragraph in the same PR. The only API code change is passing the reset setting through.
- [ ] **Step 2: Existing keys (operator).** For every workspace record kept in M0 Step 4 whose `inference.budget_usd` is `10`: with the OpenRouter management key, set that key's limit to `$500` with `limit_reset: "weekly"` by its hash (confirm the update endpoint accepts `limit_reset`) (`WorkspaceInference.key_hash` in `workspaces/<id>.json`; read only that field) through the OpenRouter management API. Record each change. The record's `budget_usd` stays `10` (it is informational; nothing enforces it). Verify from the workspace's chat: `status.key.limit_usd == 500`.
- [ ] **Step 3: Or recreate.** For a record nobody needs to keep, the M0 Step 4 delete procedure plus a new Start Hermes gives a fresh `$500`/week key. Write both procedures into `devops/hermes-fleet/README.md` in Task 13's PR.

### Task 12: Local end to end on a Lima VM (M1 verification)

Follow `devops/hermes-fleet/README.md` "Local verification" and #27110's notes. Expected spend: about `$5-15` of OpenRouter, with James's go and a key from the token broker if it vends one (otherwise ask James).

- [ ] **Step 1:** `limactl start --name=hermes-fleet --vm-type=vz --mount-none --cpus=10 --memory=20 --disk=140 --containerd=none --tty=false template:ubuntu-24.04` (one Task 9b sandbox plus host reserve; if the Mac cannot spare this, set the local host config to 4 CPU / 8 GiB / 50 GiB and record that the local numbers are not at production size); run `bootstrap.sh`. Host config: `max_workspaces: 1`, Task 9b sizes, `local_settings` pointing at a file with `{"AUTORESEARCH_GAME": "fakegame", "AUTORESEARCH_SOFTMAX": "fake"}`. Expected: `--check-host` passes.
- [ ] **Step 2:** Local stack: `nix develop -c uv run metta dev up postgres floci server softmax-com --port-offset 750` with `HERMES_USE_EC2=true`, the local fleet bucket, `HERMES_INFERENCE_BUDGET_USD=20`, `HERMES_INFERENCE_KEY_DAYS=2`, `METTA_DEV_K8S_HOST_ADDRESS`, `SOFTMAX_BRIDGE_API_URL` reachable from the VM. Run `metta dev doctor` first. Set the local player's coach Controls as in M0 Step 5.
- [ ] **Step 3:** Build the image (Task 7 Step 4); transfer image and host archive to the VM over stdin; start the controller.
- [ ] **Step 4:** In the local IDE, Start Hermes. Expected: "Hermes connected" within the 3-minute bridge deadline; research buttons appear; `docker ps` shows the container `healthy`; `/opt/data/profiles/optimizer/.softmax-distribution-commit` matches the pin.
- [ ] **Step 5:** Chat: "What is your status?" Expected: answered from `status` (`autonomy: on-request`, `started: false`, key limit about `$20`), no ids or secrets. "Run the terminal command ls." Expected: refused by role. "Tell the agent to focus on scouting next." Expected: a proposal rendering with `confirm P-xxxx`; type `confirm P-xxxx`; expected: "Sent as I-...", and the `steer` is in `comms/inbox` and committed by queue-keeper. Type `confirm P-xxxx` again: nothing is sent.
- [ ] **Step 6:** Click **Analyze replay**. Expected within about 3 minutes: adopted, `started.json`, a card; within about 30 minutes a `Done` note in chat and the IDE task done/kept. Click **Develop policy** and cancel at once: a `steer`, a P0 card, the task stays cancelled, and (Task 3) the cancel alone would not have started the agent.
- [ ] **Step 7:** Type `/pause-research`. Expected: a note "Research paused at your request."; `status.coach_hold` true; no new cards for 10 minutes while the agent would otherwise plan. `/resume-research`: resumed note; planning continues.
- [ ] **Step 8:** Force a `blocked` outcome (a fakegame task whose card blocks: the fake platform supports a blocking request per `games/fakegame/GAME.md`; if not, block a card by hand with `hermes kanban block` via `docker exec`), then unblock it. Expected: the IDE task shows paused, then done (Task 10b), against the real local API.
- [ ] **Step 9:** Restart: `docker stop hermes-<id>`, wait for the controller to start it again. Expected: same board epoch, no duplicate cards, chat history kept, bridge cursors resume, marker unchanged.
- [ ] **Step 10:** Update path: build an image with a newer pin; on the VM write its digest to the workspace's `runtime/image` and `docker rm -f hermes-<id>` (the controller recreates the container from `runtime/image`, `fleet.py:165-204`; `docker restart` would keep the old image). Expected: one `hermes profile update`, cron jobs active (`docker exec ... hermes -p optimizer cron list`), `.env` intact. Then the D2 replacement path: start a fresh workspace on **Rohit's current image** (it installs rohit-optimizer as `optimizer`), then switch it to our image the same way. Expected: the old profile is in `profiles/.optimizer-replaced-*`, ours is installed fresh, chat works. Then build an image whose pinned distribution fails `profile update` (for example a `distribution.yaml` with a `hermes_requires` above v0.21.6) and repeat. Expected: the container becomes healthy on the old profile; `status.distribution.update_failed` is set.
- [ ] **Step 11:** Measure and record in findings.md: peak memory and CPU (`docker stats`), disk on `/opt/data`, any OOM kill, whether `hermes config unset` succeeded with the key absent, whether `hermes profile update` left cron jobs paused before the resume, and the baseline report (Task 24 Step 6).
- [ ] **Step 12:** Stop the VM and stack. Record commits, image digest, timings and spend in findings.md.

---

## M2. First hosted deploy: GotA starter with real player operations

Goal: production's new `m7i.4xlarge` host runs the autoresearch image (installed as `optimizer`) with GotA settings (D14). James's research workspace bootstraps the prebuilt starter (`v13-live.bas`), submits it, and runs one small paired XP experiment against it. Every new hosted workspace gets a `$500`/week key (Task 11).

Prerequisites: M1 passed; M0 Steps 3-7 done (inventory, cleanup plan, coach Controls, access and replacement mechanism, notice); Task 11 Step 1 deployed to the production API. Task 26 is not a prerequisite (D9 (a) for this deploy).

### Task 15: Handle 403 `permit_required` and league-choice refusals (ide-mvp) — built

Built on branch `jboggs/autoresearch-permit-handling` (commits `c3472dc` to `fa398b3`). The first design (mirror `_credit_denied` and retry on the credit backoff) did not survive review; what exists is below. The behavior is documented once in `autoresearch/README.md` ("Operate", the IDE Controls paragraphs) and in the module docstrings named here.

**Files:** `softmax_tool/protocol.py` (`PermitRequired(reason, ask, state)`, `LeagueNotChosen(reason)`, `Softmax.permit_answer`, `ANSWER_*`, `PERMIT_*`), `softmax_tool/platform_cli.py` (`coach_refusal`: reads the 403 text of `coworld`), `softmax_tool/xp.py` and `client.py` (`xp_create`, `submit` return the refusal instead of raising; `permit_answer` reads `GET /v2/cogents/<player>/c/asks/<key>` with the Player credential), `softmax_tool/fake.py` (`permit=`, `over_cap=`, `league=` faults), `softmax_tool/cli.py` (`xp create`), `softmax_tool/submit.py`, `runtime/promotion.py`, `runtime/xp_poll.py`, `runtime/rebuild.py` (`launch_step`), `runtime/markers.py` (`relaunch_generation`), `records/mutability.py`, `comms/runtime_messages.py`, `schemas/entity/experiment.json`, the `xp-request`, `submit` and `complete-card` skills. Tests: `tests/unit/softmax/` (`test_client.py`, `test_xp.py`, `test_softmax_cli.py`, `test_submit.py`, `test_bootstrap_champion.py`), `tests/protocol/test_xp_poll.py`, with synthetic stderr fixtures in `tests/fixtures/softmax/synthetic/` and cogent ask fixtures (`cogent_ask_*.json`).

**What it does:**

- **Experiments.** `softmax xp create` tries every request of a batch, so one run files every permit ask. A refusal saves `lifecycle.permit_wait` (`kind`, `batch`, `attempt`, `batches`, `league_id`, `asks`, `announced`, `refusals`, `since`, `not_before`, `credits_short`) before the next request is sent, and any open ask puts the experiment in the new status `awaiting-permit` (also when credits ran out in the same batch: `credits_short`). A league refusal stops the batch. A refusal that says the coach said no ends the batch at once (`permit_declined`). Output adds `permit_required`, `league_not_chosen`, `permit_declined`, `permit_asks`, `credits_short`. While asks are open only the exact `--batch`, `--attempt` and `--batches` may run (`permit_batch_mismatch`), only in the saved league (`permit_league_changed`), so each request replays with the same body and idempotency key. A `stopped` or `reviewed` experiment, or one with a declined ask, is refused with `experiment_closed`.
- **xp-poll** is the one gate for `awaiting-permit`: one help request per ask set, then every run reads each open ask's answer (no model spend). A no stops the experiment (new terminal status `stopped`, `lifecycle.stopped`) and sends one note. All yes (or `used`) relaunches at once; unreadable answers and league refusals use the credit backoff; asks over today's cap wait for the next UTC day; an ask unanswered for 24 hours gets one reminder. A relaunch card that ends without creating the requests is tried again for the same batch up to `RELAUNCH_GENERATIONS` (3) times, then one `relaunch_stuck` help request. Only `used` answers are cached, in `.supervisor/runtime/permit_answers.json`.
- **Promotions.** A refused submit leaves the promotion at `intent` with `waiting` (`permit` or `league`) and `waiting_detail`; `reconcile` asks the coach, reads the answer every run and retries the same submit once it is due. A no moves it to `aborted` with `DECLINED_NOTE`, sends one note, and the experiment or version is not promoted again (`softmax submit` refuses it with `coach_declined`). A declined bootstrap blocks new bootstraps until an operator runs `promotion accept-champion`.
- **Messages** (`runtime_messages`): `permit_needed` (and its reminder), `league_not_chosen`, `coach_declined`, `relaunch_stuck`. Listed in `schemas/comms/README.md`.

**Interfaces:** experiment statuses `awaiting-permit` (from `uploaded|evaluating|awaiting-credits`) and `stopped`; `awaiting-permit` goes to `evaluating`, `awaiting-credits` or `stopped`. Not read: the league choice itself (a league refusal retries on the backoff).

- [x] **Step 1: Write the failing tests** with synthetic stderr fixtures written from the server code (`permits.refused` detail with `type: permit_required`; the `league_refusal` detail), for `xp create`, the xp-poll relaunch, the league message, and `bootstrap-champion` on a refused submit.
- [x] **Step 2: Run** `uv run --frozen pytest tests/unit/softmax -k "permit or league_choice" -q`. Expected: FAIL.
- [x] **Step 3: Implement** as above. (Planned as a mirror of `_credit_denied` on the existing backoff; review rounds replaced that with answer reading, one gate, merged asks and exact-batch replay.)
- [x] **Step 4: Run** the default tier. Expected: PASS.
- [x] **Step 5: Commit** `feat(softmax): wait for coach permits and report league-choice refusals`, then seven `fix(softmax): ...` commits from review.

### Task 16: Native G2 without Docker (ide-mvp, D5)

**Files:** `autoresearch/games/gota/gates/g2_episode.py:63-68`, `games/gota/GAME.md`, `games/gota/gates/README.md`. Tests: existing G2 tests (`ls tests/unit/gota`).

**Interfaces:** consumes the native match binary from `games/gota/tools/native_build.py`, driven through `games/gota/tools/gota_local.py` (reuse its runner). Produces the same `GateResult` contract plus `details.runner: "native"`.

- [ ] **Step 1: Write the failing test:** with no `docker` on `PATH` and a fake native runner returning a passing runtime receipt, G2 returns `passed` and never calls `coworld run-episode`; a VM error gives `failed`; a missing binary gives `errored`.
- [ ] **Step 2: Run** `uv run --frozen pytest tests/unit/gota -k g2 -q`. Expected: FAIL.
- [ ] **Step 3: Implement** native G2 (one episode, ten seats, candidate in the design's seats, baseline elsewhere, engine commit from `engine.env`), keeping Docker behind `GOTA_G2_RUNNER=docker`. Record in `GAME.md` what native G2 does not prove (the published container image).
- [ ] **Step 4: Run** the GotA tests with Nim 2.2.10 installed. Expected: PASS, no Docker.
- [ ] **Step 5: Commit** `feat(gota): native G2 episode gate for hosts without Docker`.

### Task 17: Nim and a C compiler in the image (metta)

**Files:** `packages/observatory-hosted-worker/Dockerfile`; `tests/test_runtime.py`.

- [ ] **Step 1:** Test that `HOSTED_SETTINGS` has exactly the six names in Task 8 and no model or spend-cap names.
- [ ] **Step 2:** In the Dockerfile, as root: Debian `gcc`, `g++`, `make`; Nim 2.2.10 from the official tarball, checked by SHA-256, into `/opt/nim-2.2.10`.
- [ ] **Step 3:** Build; `docker run --rm --entrypoint /opt/nim-2.2.10/bin/nim <image> --version`. Expected: `Nim Compiler Version 2.2.10`.
- [ ] **Step 4:** Commit `feat(hosted-worker): Nim toolchain for GotA native gates`.

### Task 18: GotA starter on the Lima VM under the real limits

Expected spend: one bootstrap card (a few dollars on our shipped models).

- [ ] **Step 1:** As Task 12, with the M2-ready image and no `local_settings` file (so `AUTORESEARCH_GAME=gota`). The local stack has no GotA league; point only the build checks at it, or (with James's go) use production and James's research player for Steps 3-4.
- [ ] **Step 2:** From a real card (not only `docker exec`): `which nim` and `$GOTA_TIRITH_BIN --version` succeed in a worker terminal (Hermes's dotenv loader expands `${PATH}`; the README's manual `set -a; . .env` does not unquote it). Fix `GOTA_TIRITH_BIN` in Task 8 if the path differs.
- [ ] **Step 3:** `native_build.py` once: record time, peak memory, disk before and after.
- [ ] **Step 4:** The prebuilt starter build (`build --prebuilt ...`) through a card. Expected: G1-G4 pass with native G2; times recorded.
- [ ] **Step 5:** Record numbers in findings.md (they feed Task 24). If peak memory exceeds about 14 GiB or disk about 80 GiB, raise the Task 9b sizes before the deploy (it is the same host replacement either way).

### Task 26: Inbox trust spike, then James's choice (metta + ide-mvp, D9; not on the critical path)

Round 2 of the review showed that the revision 3 design (a second container for the bridge, uid 10001) both may not work and protects little:

- **May not work under gVisor.** Each `docker run --runtime=runsc` is its own sandbox with its own network stack; loopback is handled inside a sandbox, so a second sandbox joined with `--network=container:hermes-<id>` very likely cannot reach `127.0.0.1:8642`. A container joined to another's network also loses its network when that container restarts, which the controller does on every exit (`fleet.py:205-206`). Unverified.
- **Mounts and permissions clash.** A bind mount of `.../profiles/optimizer/workspace/comms` would exist before the profile install and follow the directory if it is moved; `comms/inbox` sits in the git work tree and is recreated by bootstrap with mode 0700 under `umask 077`; a global `umask 027` would also open `credentials.yaml` to the bridge's group.
- **Protects little.** `AUTORESEARCH_INBOX_WRITERS` checks only uncommitted direct inbox writes. Any uid-10000 process owns the canonical git repo and can commit an inbound file, run `comms inbound` (it commits as the caller, `comms/inbound.py:103-125`), or write the ledger. Task 5's confirmed send does the same by design.

So this task is a spike plus a decision, and the first hosted deploy keeps the in-process bridge and leaves `AUTORESEARCH_INBOX_WRITERS` unset.

- [ ] **Step 1: Spike on the Lima VM (about 30 minutes; after Task 12 Step 3).** (a) Two runsc containers, the second with `--network=container:<first>`: does `curl http://127.0.0.1:8642/p/optimizer/v1/capabilities` from the second reach the first? Restart the first: does the second keep its network? (b) A host directory outside the profile and the git tree (for example `/opt/data/bridge-inbox`, owner 10001, group 10000, mode `2770`) mounted into both: can uid 10000 read and move a `0640` file written by uid 10001 under gVisor? (c) Does bootstrap or a fresh `_clone` change anything under that directory? Record the answers in findings.md.
- [ ] **Step 2: James decides (D9).** Present the spike results with the options in D9. If he chooses option (b) or (d), write that option out as its own task (files, tests, steps) before building it. `AUTORESEARCH_INBOX_WRITERS` is added to the runtime's `.env` writer **only in the same change that ships a separated writer**; until then it stays unset, because setting it while the bridge still runs as uid 10000 would reject every bridge write "for good" and silently break the IDE research buttons.

### Task 13: Hosted deploy (James operates)

- [ ] **Step 1: Merge** the ide-mvp PR(s) with everything in the pin (Tasks 1-6, 10b, 15, 16, 24, 25, 27), then the metta stack (Tasks 7, 8, 9, 9b, 10, 17) and Task 11 Step 1, each on James's word; reviewers Aaron, Alex, Richard. The lock in Task 7 must name the final ide-mvp commit.
- [ ] **Step 2: Build** from metta `main`: `python devops/hermes-fleet/autoresearch_dist.py --ide-mvp <clone> --commit <lock commit>` (must match the lock), `docker build --platform linux/amd64 packages/observatory-hosted-worker`, push to ECR `hermes-runtime`, record the digest; `nix develop -c python devops/hermes-fleet/bundle.py /tmp/hermes-host.tar.gz`, upload under a new immutable key, record the SHA-256. Keep the previous image digest and bundle key (both immutable) for rollback.
- [ ] **Step 3: Clean up records** per M0 Step 4 (the delete procedure), so at most one record is older than James's (two slots, D12). Send the notice from M0 Step 7 before starting.
- [ ] **Step 4: Replace the host** with the mechanism from M0 Step 6: set `runtime_image`, `bundle_key`, `bundle_sha256` in Spacelift `hermes-fleet-sandbox` (the instance type `m7i.4xlarge`, the 300 GiB root and the sizing variables come from Task 9b); review that only the instance and its root volume change; apply. If `prevent_destroy` needs a PR, that PR and its restore PR are part of this step.
- [ ] **Step 5: Workspace moves (D13; every deploy).** On the first deploy, move an optimizer-era workspace only if its owner asks (the runtime then moves its rohit-optimizer profile aside, Task 8). For each workspace to move: on the old host (or the retained old root volume attached to the new host), stop the controller and the sandbox, unmount `data`; copy `/srv/hermes/workspaces/<id>/` through encrypted operator storage, keeping `state.ext4`; **delete its `image` file and `runtime.json`** so the new controller pins the new image and regenerates the bridge config (route and workspace come from the new `fleet.py`; a stale `runtime.json` would keep `/p/optimizer/`, and a kept `image` would run old `runtime.py`, so the workspace would never become ready); if the disk is smaller than `disk_gb`, `truncate -s <disk_gb>G state.ext4 && e2fsck -f state.ext4 && resize2fs state.ext4`; restore `0700`/`0600` modes; start the controller after `firewall.sh`. Write this into `devops/hermes-fleet/README.md` beside the existing "Persistence and portability" text.
- [ ] **Step 6: Verify** over SSM: `systemctl status hermes-fleet` active; `--check-host` passed in the journal; `docker images` shows the new digest; every kept workspace container is `healthy` (`docker ps`) and has the autoresearch marker; the status objects in S3 say `ready` (or `capacity` for records past slot 2).

### Task 14 + 19: Hosted verification (James in the browser)

Expected spend: up to the shipped `$100`/day while research runs, from the `$500`/week key; XP credits within James's `dailyCredits`.

- [ ] **Step 1:** James opens the IDE with his research player and clicks Start Hermes (or Reconnect if its record was kept). Expected: connected within 12 minutes; research buttons visible.
- [ ] **Step 2:** If James's record was kept from before Task 11, James raises its key (Task 11 Step 2). Chat `status`: key limit about `$500`, `autonomy: on-request`, not paused.
- [ ] **Step 3:** Task 12 Steps 5-7 in production (status, refusal of terminal use, a confirmed proposal, pause and resume). Expected: same results.
- [ ] **Step 4:** **Develop policy**: "Bootstrap the champion and run one small paired check against it." Expected: a bootstrap card builds the starter, uploads it (`ar-bootstrap-<sha>`), submits without an ask (D4, `askBefore: []`); the league shows the starter as our champion within the grace window. XP requests run within `dailyCredits`; xp-poll collects results; the IDE task closes with the mapped status. Then James adds `submit` to `askBefore` once and checks that a second submission waits for the IDE ask (Task 15) and completes after approval; then removes it again.
- [ ] **Step 5:** Operator restarts the container (`docker stop`, controller restarts it). Expected: Task 12 Step 9 results.
- [ ] **Step 6:** Record the `cga_` operation matrix (whoami, credits read, upload, XP create, XP download, submit, memberships read) with request ids in findings.md, closing softmax-client.md's "UNVERIFIED live" line. Collect the baseline (Task 24 Step 6) after at least 24 hours.
- [ ] **Step 7:** Tell Rohit (gist comment) what changed in his runtime and why.

### Rollback (per deploy, honest cost)

- **First response, no deploy:** in the IDE, the coach withdraws a submission, adds `submit`/`spend` to `askBefore`, or types `/pause-research`. A broken single workspace: Reconnect recreates its container without touching its disk.
- **Image rollback:** set `runtime_image`, `bundle_key`, `bundle_sha256` back to the values kept in Task 13 Step 2 and replace the host again with the same mechanism. Cost: the same as the deploy (M0 Step 6's estimate; hours if `prevent_destroy` needs PRs). New workspaces then get rohit-optimizer again. A moved workspace that already has our profile installed as `optimizer` keeps it: the old `runtime.py` sees the profile and skips its install (`runtime.py:47-48`), but it routes with the relay off and writes only its own `.env` names, so research keeps its existing settings and the IDE loses the research buttons. Research state survives only through the Task 13 Step 5 move. Revert the metta PRs only if the code itself is wrong.
- **Budget:** a key already raised to `$500`/week stays that way until the operator lowers it or it expires.

---

## M3. GotA research with the compiler

Prerequisites: M2 passed; Task 18 numbers; the key has room (raise it per Task 11 Step 2 when Task 24 shows it will run out).

- [ ] **Task 20 (us, measure):** one compiler build on the Lima VM in the Task 9b sandbox. Record wall time, peak memory, disk, key spend including the compiler child. If a limit is hit, raise the host config values (a host replacement, with Task 13 Step 5 moves) rather than slim the build.
- [ ] **Task 21 (metta, only when research and non-research workspaces need different sizes):** a per-workspace size override with capacity accounting in `check_host` and `reconcile_batch`, tested like `test_reduced_capacity_stops_overflow_before_reporting`. Not needed while every hosted workspace is autoresearch.
- [ ] **Task 22 (ide-mvp):** count compiler child spend in the daily budget (read the compiler home's `state.db` sessions in `spend.py`), with a test that a compiler session started today counts once.
- [ ] **Task 23 (hosted):** one full research cycle (Plan 03 Task 6.1), deployed with Task 13 including Step 5 moves so James's workspace keeps its state. Record results.

---

## M4. Profile updates without host replacement (D6 follow-up)

Goal: a new autoresearch commit reaches running workspaces without a new image, a host replacement, or lost state; the only interruption is one container restart. This is the last deploy that replaces the host for a profile change.

Design (to be written out task by task when M3 ends; the shape is fixed here):

- **Artifact and pin in S3, protected.** The fleet bucket gets `profiles/autoresearch/<commit>.tar` (the Task 7 `git archive`) and a pin object `profiles/autoresearch/current.json` `{"commit", "tree", "key", "signature"}`. James publishes with an operator script (`devops/hermes-fleet/publish_profile.py`, reusing `dist.build`). Protection: a bucket policy that **denies** `s3:PutObject`/`DeleteObject` on `profiles/*` to every principal except the operator role (the API role, which writes `credentials/*` and `workspaces/*`, and the host role are denied); bucket versioning on; the pin is a detached signature over the **exact bytes** of a canonical payload `{"seq", "commit", "tree", "key"}` (stored as `current.payload` plus `current.sig`, never re-serialized before verifying), made with a KMS asymmetric key that only the operator role may use for `Sign`; `seq` is monotonic and the host refuses a pin whose `seq` is not greater than the one it applied (no replay or rollback by an old signed pin); the host verifies offline with the public key shipped in the host bundle, which means adding `cryptography` explicitly to the bundle's requirements (`observatory-hosted-worker` does not depend on it today, `pyproject.toml:12-18`; the bundle is `uv export --package observatory-hosted-worker`, `bundle.py:20-30`); chosen over `kms:Verify` from the host role so that trust rests on the key, not on IAM; rotating the key means a new bundle, which is a host replacement; each pin change is logged with its commit (CloudTrail data events on `profiles/*`). The host role gets `s3:GetObject` on `profiles/*`; the sandbox still has no AWS access.
- **Controller delivery.** Each reconcile pass, `fleet.py` reads the pin and verifies its signature; when it changes, it downloads the tar to a temporary file, extracts it with Python `tarfile` and `filter="data"` (rejects absolute paths, `..`, links leaving the tree, devices) into a **fresh** directory `/srv/hermes/profiles/<commit>.new/` (root-owned, mode 0755), computes the tree id with `dist.tree_of`, and refuses on any mismatch with the signed pin (the old profile stays current; the failure is logged and reported in host status). Only then it renames the directory to `/srv/hermes/profiles/<commit>/` and switches a host-level symlink `/srv/hermes/profiles/current`. Containers mount `/srv/hermes/profiles/current` read-only at `/opt/autoresearch`, with `/opt/autoresearch.commit` written beside it. The image keeps a baked copy as the fallback when no pin exists.
- **Apply on restart.** For each workspace whose installed marker (`.softmax-distribution-commit`) differs from the current pin, the controller restarts the container at a quiet moment: when the workspace reports no running Kanban run (a new secret-free status file the runtime writes on `/opt/data`, read by the controller from the mounted disk; the disk is writable by the sandbox, so the controller parses it as untrusted input, size-limited, and uses it only for timing), or after a maximum wait (24 hours). The runtime's existing update path (Task 8: `hermes profile update`, cron resume, fallback on failure) applies it. The disk, `.env`, workspace, board and chat history are untouched.
- **Tests:** controller tests for pin change, bad signature (refused), a correctly signed pin with an older `seq` (refused), a tar with `../` and absolute-path members and an outside symlink (refused before anything is written outside the new directory), tree mismatch (refused, old profile kept), symlink switch, the quiet-restart rule, and that a container restarted with `docker start` (not recreated) mounts the new target of the `/srv/hermes/profiles/current` symlink (Docker resolves the bind source at start; this test guards that assumption); runtime test that a mounted artifact is installed the same as the baked one.
- **Cost:** this changes `fleet.py` and the host IAM policy, so it ships with one more host replacement (with moves). After it, profile updates are an S3 upload.

## M5. More hosts (D12 follow-up)

Goal: more than two hosted workspaces. Today the API sends every new workspace to one host id (`HERMES_FLEET_HOST_ID`, `clients/hermes/ec2.py:43-55`), and a host only reconciles its own records (`fleet.py:291`). Named items, to plan after M4: per-stack fleets (one Spacelift stack per host, a host list in the API instead of one id, placement by free slots), record migration between hosts (the move procedure), and capacity reporting in the IDE ("waiting for a slot").

---

## Baseline data (explicit deliverable, M1-M3)

The code is Task 24 (in M1). The deliverable is the data: a dated `autoresearch/docs/plans/findings/hosted-baseline-<YYYY-MM-DD>.md` per milestone, which section 8 starts from.

- [ ] **Task 24 Step 6: Collect** at the end of Task 12, after at least 24 hours of Task 14+19, at Task 18 and at Task 23. Ask the chat for `baseline_report` (or `docker exec` on the VM). Add the operator's container numbers over the window (`docker stats --no-stream` sampled, `df` of the workspace mount). Write: cost per card by mode and model; cost per day by source and against the key's actual usage (the gap between Hermes's estimate and the key is a finding; fake-cycle.md saw one); cost and wall time per experiment; build and gate times; peak memory, CPU and disk; the observed weekly spend against the `$500` weekly limit and the `$100` daily cap.

---

## 6. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Host replacement brings every S3 workspace record back on the new image with an empty disk; slots go oldest first; 8 → 2 slots stops the newer ones | James's workspace may get no slot; other users lose state or capacity | M0 inventory, notice and cleanup; D12 slot math after cleanup; Task 13 Step 5 moves on every deploy |
| Every image, controller or sizing change is a host replacement, possibly needing PRs past `prevent_destroy` | Slow deploys and rollbacks; a user-visible outage per deploy | Few deploys; local VM for iteration; M0 Step 6; M4 removes the need for profile changes |
| Only 2 hosted workspaces for all of softmax.com | Others wait for capacity | Announce; M5 adds hosts |
| An optimizer-era profile under the same name | `profile update` would re-pull rohit-optimizer | Task 8 moves a foreign profile aside (tested; Task 12 Step 10) |
| A failed profile update | Would crash-loop without chat | Fallback to the installed profile (Task 8); `status` reports it |
| Coach setup missing for other users; `cga_` token never expires | With no setup, the token spends and submits like any player, with no daily cap | M0 Step 5 for James; Task 6 docs for coaches; revoke path (3.4); `$500`/week key cap bounds model spend only |
| One uid for gateway, bridge, workers, chat (D9 (a)) | Research code can act as the coach (inbox writes, commits, `comms inbound`, driving the chat API with `API_SERVER_KEY`); `.env` (credential, key) readable by every research process | Stated in the hosting docs; A15 detection; D9 (d) next; the start latch, pause commands, proposals and persona are not security controls |
| Chat config load fails open | If the plugin cannot load its config, the chat gets Hermes's default toolset, including terminal (coach-chat-role §2.2) | The plugin's `pre_tool_call` allowlist still blocks; but this is one more reason the persona is not a control |
| Plugin hooks may not fire for multiplexed `/p/optimizer/` runs | Chat has no tools; confirm and pause do not work | Task 5 Step 4 checks; bridge-side fallback for D7/D15 |
| `$500`/week keys for every new workspace | Up to `$1,000` per week on one host (about `$2,140` per key over its 30-day life); more with M5 | Accepted by James (D1/D3); at our `$100`/day cap a workspace can spend the full `$500` in 5 days; Task 24 tracks burn |
| The key expires after 30 days; kimi-k3 reserves about `$1.97` per call | Research stops at expiry; the API never re-mints | Task 24; operator raise (Task 11 Step 2) |
| League submission, uploads and XP without asks (D4) | Unreviewed starter policies in the league; credits spent up to `dailyCredits` | Our A9 conflict pause; the coach can add asks at any time (Task 15 handles them) |
| Fresh signup path untested | A new user may not get hosting controls | Separate check before broader rollout |
| Nous path diverges | Nous still installs rohit-optimizer's bridge | Out of scope; documented |

## 7. Self-review notes

- James's decisions of 2026-10-09: D2 install as `optimizer` (3.2, Task 8 move-aside, Task 9 route unchanged, Task 27); D1/D3 global `$500`/week (weekly reset) and existing keys (Task 11); D4 allow without asks (M0 Step 5, Task 14 Step 4) with Task 15 kept; D6 replace with notice plus moves every deploy (M0 Step 7, Task 13 Steps 3-5) and M4; D7 full chat (3.7, Task 5); D8; D9 (accept for the first hosted deploy; Task 26 spike off the critical path; API-delivered coach requests next); D10 James operates (M0 Step 6); D11 reviewers; D12 sizes and host type (Task 9b) and M5.
- Review round 2 (revision 4): R2-C1 (`is_ours` by marker or `source:`, tests with real manifest shape, Task 27 records the real `source:` string and checks restarts; coach gate by plugin presence), R2-C2/R2-I2 (Task 26 spike, D9 rewritten, writer setting unset), R2-I1 (Task 10b from `acknowledged` records, `final_ack_by`, real-path tests), R2-I3 (code-rendered proposals via `transform_llm_output`, lock), R2-I4 (M4 filter, signed pin, operator-only prefix), minors 1-7.
- Review rulings (revision 2) remain covered: C1, I1-I9 and the minors, as listed in revision 2's notes; Task 5 test 1 and Step 4 still prove the environment mechanism (I5).
- James's direction on budget and size: section 2, Task 9b, Task 11; shipped models and caps (Global Constraints; Task 17 Step 1); GotA in-sandbox (D5); baseline (Task 24); cheapness later (section 8).
- Not covered on purpose: the shim; the Nous provider; explanation runs (coach-chat-role §3.1, a bridge change); a research status signal in the bridge's health report.
- Open facts the tasks check: the exact `source:` string Hermes records for a local install path (Task 27); whether `transform_llm_output` output is what the API run returns to the bridge (Task 5 Step 4 (b)); the gVisor networking and permission questions (Task 26 spike); whether the player API exposes `author_role` (D9 (d)); plugin hooks under multiplexing (Task 5), memory and disk at the new sizes (Tasks 12, 18, 20), `hermes config unset` on an absent key and cron state after `profile update` (Task 12), PATCH to active/paused against the real API (Task 12 Step 8), the `prevent_destroy` mechanism (M0 Step 6), file permissions across uids under gVisor (Task 26 Step 4).

## 8. Later phase: cheaper and smaller (not a prerequisite)

After at least one hosted week of Task 24 numbers: a cheaper chat model and possibly cheaper research models per mode, caps and key budget tuned to the data, a lower credit floor for small keys (Task 1), sandbox sized to measured peak plus margin, workspaces per host revisited (D12), and a per-player budget override if research and casual users need different keys. Each change is judged against the baseline.
