# Acceptance and hosting handoff checklist (draft)

Status: DRAFT, written 2026-10-07 at commit `6f0affa` (branch `jboggs/autoresearch-v0-impl`). Plan 03 Task 6.2.

Rule for this file: every item that nobody has checked is marked **UNVERIFIED**. An item without that mark has evidence, and the evidence is named next to it.

Related documents:

- Operations guide (install, `.env`, host config, cron jobs, restore, update): [README.md](../../../README.md). This file links to it and does not repeat it.
- Contracts and amendments A1-A13: [00-overview.md](../2026-10-07-autoresearch-v0/00-overview.md).
- Findings: [hermes-facts.md](hermes-facts.md), [softmax-client.md](softmax-client.md), [player-xp-probe.md](player-xp-probe.md).

## 1. Friday leaderboard measurement

### 1.1 Facts

| Item | Value |
|---|---|
| League | `league_3c60897b-25cf-4b37-9d1a-8554c1198f28` ("Gods of the Arena") |
| League score | "Emmett's Glory": the lifetime XP of the winning heroes, per simulated minute. A loss, a draw, or a timeout scores 0. |
| Live coworld version | 2026.10.7.2 (polyworld `801749e5`), seen 2026-10-07. The version can change. Read it again on Friday with `"$AUTORESEARCH_CLI" softmax game-version`. |
| Test player | Games Bond, `ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1` |
| Do not use | James Botts. Its v13 entry `sub_38be3038` probably fails to load on the live engine (UNVERIFIED: not tested on the live engine). Do not count it as a baseline. |

Which player is measured on Friday is not decided. The checklist assumes Games Bond, because Games Bond is the player that the agent acts as in all live tests. If James names another player, put its id in `AUTORESEARCH_PLAYER_ID` and in the commands below. **UNVERIFIED: the choice.**

### 1.2 Prepare the shell

Use the operator shell from the README ("Operator shell"). `AUTORESEARCH_CLI`, `SOFTMAX_PLAYER_CREDENTIAL` and `AUTORESEARCH_PLAYER_ID` must be set, and the credential must not be expired (it lasts about 24 hours; see section 2.4).

```sh
export PROFILE="${HERMES_HOME:-$HOME/.hermes}/profiles/autoresearch"
set -a; . "$PROFILE/.env"; set +a; export HERMES_HOME="$PROFILE"
"$AUTORESEARCH_CLI" softmax whoami          # must show our player, not a user
"$AUTORESEARCH_CLI" softmax game-version    # record the version in the evidence
```

If `whoami` shows a user and not the player, stop. Do not use `--elevated`.

### 1.3 Read the rank

Primary command (read-only, uses the player credential):

```sh
"$AUTORESEARCH_CLI" softmax standings > standings.json
"$AUTORESEARCH_CLI" softmax leaderboard > leaderboard.json
```

- `standings` prints `{"ok": true, "data": {"league_id", "rows"}}`. Each row has `division_id`, `division_name`, `level`, `rank`, `player_id`, `player_name`, `score`, `rounds_played`. The code reads `coworld results <league>` (divisions) and then `coworld results <division>` (entries). Source: `src/autoresearch/softmax_tool/client.py`, method `standings`.
- `leaderboard` prints our champion policy version (`leaderboard_champion`) and the ledger champion (`current_champion`, `note`). Use it to record which policy version is measured.

Read-only alternative without the autoresearch CLI (project-pinned coworld 0.1.58, not the global one):

```sh
uv run --locked coworld results league_3c60897b-25cf-4b37-9d1a-8554c1198f28 --json   # divisions
uv run --locked coworld results <division_id> --json                                  # entries of one division
```

Steps:

1. In `rows`, find the rows with `player_id` equal to `$AUTORESEARCH_PLAYER_ID`. Note the `division_id`.
2. Take all rows with that `division_id`. This is our division.
3. Remove filler entrants (see 1.4). Count the rest. This count is N.
4. Sort the remaining rows by `rank` (use the platform `rank`; the code falls back to list order only when `rank` is empty). Our rank R is the position of our row in this filtered list.
5. Save the raw files, N, R, the division name, the game version, the time, and the policy version in the evidence record (section 2.5).

If our player is in more than one division, report the highest-level division and list the others. **UNVERIFIED: how a promoted or relegated player appears across divisions.**

### 1.4 Top half

Definition: **top half means R <= ceil(N / 2)**, where N counts the non-filler entrants in our division, and R is our rank among them.

Assumptions, all **UNVERIFIED** until someone checks them on the live standings:

- Our division is the division that holds our player (1.3 step 1). The league-wide list is not used.
- A filler entrant is an entry that the platform marks as filler. Episode participants carry `is_filler` (seen in the 2026-10-07 probe, `player-xp-probe.md` and `softmax-client.md`, "Player read of league episodes"). The standings rows that the CLI keeps do **not** carry this flag. Before Friday, print the raw `coworld results <division_id> --json` once and check whether the entries show filler. If they do not, find fillers by the policy label in episode participants (`softmax league-episodes`, field `is_filler`), or ask James.
- Ties: players with the same `rank` all count. If a tie crosses the ceil(N / 2) line, report it as "borderline" and do not claim a pass.
- A player with `rounds_played` of 0 counts in N only if the platform ranks it. Record how many such entries exist.

### 1.5 Fallback criterion: "plays reasonably"

Use this criterion only if the top-half test fails or cannot be measured (for example, too few rounds). It is **not** a replacement for top half when top half passes.

Both conditions must hold:

1. **No errors.** In the episodes of our player, every episode is `completed` and none has a failed seat. Count: failed episodes must be 0 across the last 20 completed episodes of our player (or all episodes, if fewer).
2. **Glory near the median.** The mean glory of our player over those episodes is within one standard deviation (SD) of the league median glory: `|our_mean - median| <= SD`.

How to compute the median and SD from league episodes:

```sh
"$AUTORESEARCH_CLI" softmax league-episodes --limit 200 --out league-rows.jsonl
```

This reads up to 200 completed episodes from the league (rounds, then episodes, then each result), maps each through the game's evaluate binding, and writes one EpisodeRow per seat as JSONL. Each row has `policy_version`, `status` (`completed` or `failed`), `metrics` and `episode_id`. It also prints `game_versions`; keep only rows with the current game version (older rows are not comparable, because the engine changed).

Then compute with the standard library (replace `glory` with the exact metric name that the GotA evaluate binding writes):

```sh
python3 - <<'PY'
import json, statistics
rows = [json.loads(line) for line in open("league-rows.jsonl")]
current = "<current game version>"          # from `softmax game-version`
ours = "<our policy version label>"         # from `softmax leaderboard`
rows = [r for r in rows if r["game_version"] == current]
glory = [r["metrics"]["glory"] for r in rows if r["status"] == "completed"]
mine = [r for r in rows if r["policy_version"] == ours]
print("seats", len(rows), "failed", sum(r["status"] == "failed" for r in rows))
print("median", statistics.median(glory), "sd", statistics.stdev(glory))
print("our episodes", len(mine), "failed", sum(r["status"] == "failed" for r in mine),
      "our mean", statistics.fmean(r["metrics"]["glory"] for r in mine if r["status"] == "completed"))
PY
```

Notes:

- Glory is 0 for every loss, draw, and timeout, so the glory distribution is lumpy and skewed. The SD is large. The test "within one SD of the median" is therefore weak. State this in the report and show the median, the SD, and our mean next to each other.
- The `stats` command group works on the same rows: `"$AUTORESEARCH_CLI" stats ingest --rows league-rows.jsonl --league <league> --game-version <v> --game gota` cleans them into `stats/rows/<league>.jsonl`, and `stats noise` writes `marginal_sd` per metric (`src/autoresearch/stats/noise.py`). `marginal_sd` is a good check on the SD. It does not give the median, so use the script above for the median. `stats ingest`/`noise` write into a workspace; run them in a scratch workspace (`--root`), not in the live canonical workspace.
- **UNVERIFIED: the GotA metric names.** The GotA game (`games/gota`) is not in this repository yet (Plan 02), so the name `glory` and the shape of the evaluate output are from the plan, not from code. Only `fakegame` exists.
- **UNVERIFIED: whether 200 episodes are enough** and whether other players' seats in other policies' episodes are a fair comparison set. The median here is over all non-filler seats in recent episodes. A different choice (only our division) changes the number. Choose one, write it in the report, and use it each time.

### 1.6 Friday report (fill in)

| Field | Value |
|---|---|
| Time and game version | |
| Policy version measured | |
| Division, N (non-filler), R, ceil(N/2) | |
| Top half? (yes / no / borderline) | |
| Fallback: failed episodes, median, SD, our mean | |
| Files saved (standings, leaderboard, league-rows) | |

## 2. Hosting handoff for Rohit

### 2.1 Required environment keys

Source of truth: `distribution.yaml` (`env_requires`). Details and examples: README, "Configure `.env`". Create `$PROFILE/.env` with mode 0600.

| Key | Required | Note |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | Key for the research model. Needs credit (see 2.8). |
| `SOFTMAX_PLAYER_CREDENTIAL` | yes | JSON `{"token": "ply_...", "expires_at": "<ISO 8601>"}`. A bare token is rejected. |
| `AUTORESEARCH_PLAYER_ID` | yes | The player id that the credential belongs to. |
| `AUTORESEARCH_WORKSPACE_REMOTE` | yes | Git remote. The repository must exist before the first run. |
| `OPENROUTER_MODEL` | no | Default `moonshotai/kimi-k3`; fallback `z-ai/glm-5.3`. |
| `AUTORESEARCH_GAME` | no | Default `gota`. `games/gota` does not exist yet (README, "Known gaps"). |
| `AUTORESEARCH_WORKSPACE` | no | Default `$PROFILE/workspace`. |
| `AUTORESEARCH_QUEUE` | no | `file` selects the serial fallback (not shipped, see 2.6). |
| `AUTORESEARCH_SOFTMAX` | no | Tests only (`fake`). Never set in production. |

Bootstrap writes `AUTORESEARCH_CLI` and `HERMES_WRITE_SAFE_ROOT`. Do not set them by hand.

### 2.2 Host configuration (amendment A3)

The host must have: `kanban.max_in_progress 2`, `kanban.dispatch_in_gateway true`, `kanban.default_assignee` unset, and a running gateway. Exact commands: README, "Host configuration". Preflight checks these and pauses the agent with a help request when one is wrong.

Verified: the gateway dispatcher uses the host default profile config (hermes-facts.md, item 5; test `tests/integration/test_kanban_queue_hermes.py`, marker `hermes`). **UNVERIFIED on the real hosted machine:** that the host root config has the three settings. Rohit must run the commands and `hermes gateway status`.

### 2.3 Remote access

Rohit needs:

- Shell access to the host as the user that owns the Hermes install and the profile (to run the operator commands and read `keeper.log`).
- Read and write access to `AUTORESEARCH_WORKSPACE_REMOTE` for that user (SSH key or token). queue-keeper pushes `main` and `card/*` branches on each run.
- Outbound network to OpenRouter, the Softmax API, and GitHub (or the remote host).
- The remote repository is private. It holds the Hermes memory snapshot (README, "Restore on a new host", privacy note).

**UNVERIFIED:** the host, the SSH or token mechanism, and who holds the remote repository. These are not decided in any document in this repository.

### 2.4 Token refresh owner

Facts (verified, `softmax-client.md` and `player-xp-probe.md`):

- A Player session lasts about 24 hours. `/whoami` shows no expiry, so the expiry is stored in the credential JSON (`expires_at`).
- Preflight warns 2 hours before expiry and pauses the agent at expiry. An expired session makes the CLI fail with "Not authenticated"; it does not fall back to a user identity.
- A new credential comes from `coworld player use <ply_id>`. This command needs a logged-in **user** session. It writes the Player session to `~/.softmax/credentials.yaml`. Copy the entry `player_sessions.<server>.cache[<ply_id>]` (a JSON object with `token` and `expires_at`) into `SOFTMAX_PLAYER_CREDENTIAL`.
- After the `.env` change, the next `queue-keeper` run (one minute) reads it; preflight then resumes the agent by itself.

Not decided (**UNVERIFIED / open**):

- **Who refreshes.** Proposed owner: James (the only person with a user session that can mint a Player token), at least once per 24 hours. Rohit cannot refresh without a user login. If nobody is named, the agent pauses every day.
- **Automation.** No script exists that mints and installs a new credential on the host. Options: (a) a daily manual step; (b) a small scheduled job on a machine with a user session that runs `coworld player use` in an isolated `HOME` and writes the `.env` value on the host; (c) ask Softmax for a longer-lived Player token. Option (c) is the cleanest and has not been requested. This is a decision for James.
- Whether a refresh via the metta token broker is possible. The broker does not vend Player tokens in the scopes checked so far (**UNVERIFIED**: `scopes` was not re-read for this document).

### 2.5 Evidence storage and refetch policy

- Replays and other large artifacts are **not in git**. They go to `.scratch/` in the workspace (ignored by git) and are fetched again when needed.
- The evidence index records references only: XP request id, episode id, artifact name, size, etag, sha256, and the receipt from the download manifest. It does not hold the files. The CLI computes `sha256` while it streams a download and refuses a size mismatch; `xp_artifacts` hashes each completed file again against the latest receipt (`softmax-client.md`, "Artifact hashes").
- Refetch policy: if a file in `.scratch/` is missing or its hash differs from the receipt, fetch it again with `coworld xp-request download <xreq> --include results --include replay -d <dir>`. Exit code 2 means the evidence is still pending: wait and retry. Signed replay URLs expire, so never store them.
- Checked: the references-only rule and the receipts, by unit tests in `tests/unit/softmax/` and `tests/unit/records/`. **UNVERIFIED:** how long the platform keeps XP evidence, so the refetch window is unknown. Also **UNVERIFIED:** disk growth in `.scratch/` on a long run (the health job warns about low disk, but no size limit is set).

### 2.6 Restore procedure and Kanban go/no-go

- Restore: follow README, "Restore on a new host". Summary: install Hermes and the profile, write the same `.env`, run bootstrap once (it clones the workspace from the remote), copy the memory snapshot back, resume the cron jobs. The new board starts a new epoch and queue-keeper rebuilds unfinished work (contract C12).
- Tested locally: rebuild after board loss, `tests/protocol/test_rebuild.py`. **UNVERIFIED:** a full restore on a different machine from a real remote.
- **Kanban go/no-go: GO** (2026-10-08, [protocol-slice.md](protocol-slice.md)). The protocol slice passed on the real gateway dispatcher with real workers, after seven product fixes. The default queue is Kanban (`AUTORESEARCH_QUEUE` unset). The serial fallback (`AUTORESEARCH_QUEUE=file`, Plan 01 Task 5.2) is not built and is not needed for v0.

### 2.7 Known Hermes issues (summary of A1-A8 and A13)

Full text: `00-overview.md` and `hermes-facts.md`.

| Id | Issue | What to do |
|---|---|---|
| A1 | `uv tool install git+...` fails (Hermes `setup.py` refuses wheel builds). | Clone tag `v2026.9.24` and run `uv tool install --python 3.12 -e .`, or use `install.sh`. |
| A2 | `kanban init` fails on a new board slug. | Bootstrap uses `hermes kanban boards create`. No action. Board databases are host-wide. |
| A3 | One gateway dispatcher per host uses the host default profile config. | Set the three host settings (2.2). |
| A4 | Under the gateway, a cron script gets only the names on `terminal.env_passthrough`. Worker terminals see the whole profile `.env` except provider keys. | The plugin, not env filtering, protects the Softmax credential (see 2.8, risk 1). |
| A5 | Hermes sends no `max_tokens`. OpenRouter reserves 131072 output tokens per kimi-k3 call (about 1.97 USD of headroom per call). A 402 becomes `rate_limited` and retries about every 6 minutes without end. | Keep credit above the preflight threshold (default 10 USD). kimi-k3 tool calling inside Hermes **passed** on 2026-10-08 (hermes-facts item 11), as did glm-5.3 and glm-5.3-flash. |
| A6 | `bin/` also holds Hermes's `tirith`. | Never clean `bin/`. |
| A7 | In the gateway process, `HERMES_HOME` is the host root. | Code uses `hermes_constants.get_hermes_home()`. No action. |
| A8 | Kanban run ids are integers per board, not global. | Handled in the handoff paths. No action. |
| A13 | Hermes has no auto-update, and no per-skill curator exclusion. | `updates.check: false` and `curator.prune_builtins: false` are set. Do not run `hermes curator adopt` on a shipped skill. |

Also **UNVERIFIED** (hermes-facts.md, item 2): that plugin hooks fire in a gateway messaging turn. Worker sessions fire all hooks (verified).

### 2.8 Open risks

1. **Worker terminal HOME isolation for Softmax credentials: not decided.** Worker terminals see the profile `.env`, including `SOFTMAX_PLAYER_CREDENTIAL` (A4). The softmax tool writes a private `HOME` under `$HERMES_HOME/local/softmax/home` for its own CLI calls, but a model command in a worker terminal can still read the credential from the environment. The only guard is the plugin policy. Decision needed: strip the credential from worker terminals, or run workers with an isolated `HOME`. Until then, a model that finds a way past the plugin can act as the player.
2. **Plugin residual risk.** The policy parser is best-effort and stops a model that reaches for a blocked command. It does not stop an adversary. Known unhandled forms (from `src/autoresearch/plugin/policy.py`):
   - a script file written inside the worktree and then run;
   - positional-parameter and array tricks, indirect expansion, and variables set in an earlier tool call;
   - aliases, earlier shell functions, renamed or copied binaries;
   - Python code that calls coworld through a library or a built string.

   Other layers: the `approvals.deny` globs in `config.yaml` (coarse) and the `whoami` identity check before every platform change (C6).
3. **OpenRouter credit preflight.** The broker key `openrouter.inference` had about 0.95 USD left on 2026-10-07 (hermes-facts.md, item 11). Preflight pauses the agent when `limit_remaining` is below a threshold (default 10 USD). A Player credential cannot read Softmax credits (HTTP 403); preflight only warns, and XP requests that hit a credit denial (HTTP 402) retry with backoff (A10). **UNVERIFIED:** the credit level on the hosted key. Top up before the protocol slice.
4. **Credential expiry** every 24 hours (2.4). The agent pauses at expiry.
5. **GotA is not in `games/` yet** (Plan 02). The compiler M1 baseline has not been shown here. **UNVERIFIED.**
6. **Live platform drift.** The coworld version changed from 2026.10.5.1 to 2026.10.6.1 to 2026.10.7.2 within three days. Noise records and designs are tied to a game version (C10); a version change triggers calibration again.

## 3. Local readiness versus hosted run

"Verified locally" means a test or a recorded probe passed on James's Mac. Test command for all rows marked `pytest`: `uv run --locked pytest -q` in `autoresearch/`. Last run for this document: **1349 passed, 9 deselected** (the 9 are marker `hermes`, excluded by default) at commit `6f0affa`, 2026-10-07.

### 3.1 Verified locally

| Area | Evidence |
|---|---|
| Distribution manifest, env keys | `tests/unit/test_distribution.py` (pytest) |
| README commands lint | `tests/unit/test_readme.py` (pytest) |
| Install with real Hermes v0.21.5 in a throwaway home | `tests/integration/test_install.py` (marker `hermes`; run with `pytest -m hermes`; **not re-run for this document: UNVERIFIED today**) |
| Config migrations, plugin loading, Kanban queue on real Hermes | `tests/integration/test_config_migrations.py`, `test_plugin_hermes.py`, `test_kanban_queue_hermes.py` (marker `hermes`; **not re-run for this document**) |
| Hermes facts (11 items) | `findings/hermes-facts.md`: 9 PASS, 1 FAIL (config location, became A3), kimi-k3 PASS on 2026-10-08 (was PARTIAL) |
| Records, queue, runtime protocols: admission, handoff, merge, publication, promotion, pause, rebuild | `tests/unit/records`, `tests/unit/queue`, `tests/unit/runtime`, `tests/protocol/` (pytest, fake board) |
| Plugin policy (block, inject) | `tests/unit/plugin/` (pytest); live hook behavior in workers: `hermes-facts.md` item 2 |
| Comms interface | `tests/unit/comms`, `tests/contract/comms` (pytest) |
| KB maintenance, code-citation renewal | `tests/unit/kb` (pytest) |
| Statistics, noise, calibration, decision rule | `tests/unit/stats` (pytest) |
| Softmax tool against the fake | `tests/unit/softmax`, `tests/fixtures/softmax` (pytest) |
| Softmax tool against the live platform, as a Player | `findings/player-xp-probe.md` (create, get, list, episodes, download as Games Bond: PASS) and `findings/softmax-client.md` (read-only probes: PASS; league episodes readable by a Player) |
| Skills and doctrine consistency | `tests/unit/test_skills.py`, `tests/unit/test_doctrine.py` (pytest) |
| Models glm-5.3, glm-5.3-flash tool calling in Hermes | `hermes-facts.md` item 11 |

### 3.2 Needs the hosted or live run

| Item | Plan task | Status |
|---|---|---|
| Protocol slice on the real Kanban dispatcher with recovery tests | Plan 01 Task 5.1 | **PASS: GO** (2026-10-08, [protocol-slice.md](protocol-slice.md)). |
| Serial fallback queue | Plan 01 Task 5.2 | **NOT SHIPPED.** Built only if 5.1 fails. |
| kimi-k3 tool calling inside Hermes | A5 | **PASS** (2026-10-08, hermes-facts item 11). |
| Full research cycle on the fake game, real cheap model, 90 minutes, with `credits=0` injection | Plan 03 Task 6.0 (`tests/integration/test_fake_cycle.py`) | **PASS** (2026-10-08, glm-5.3; runs 1 and 3 passed, about 30 min each; [fake-cycle.md](fake-cycle.md)). |
| Real research cycle on GotA (calibration, planning, investigation, experiment, XP, review, promotion) | Plan 03 Task 6.1 | **NOT RUN.** Needs `games/gota` and the compiler build binding (Plan 02). Record in `findings/first-real-cycle.md`. |
| Compiler M1 baseline (semantic to policy, with goal tracing) | Plan 02 | **UNVERIFIED.** Not shown in this repository at this commit. |
| Upload and submit as a Player | Plan 03 Task 3.x | Documented as working in the coworld-player-swap skill; the submit path was **not re-tested live** (`player-xp-probe.md`). **UNVERIFIED.** |
| Gateway cron jobs (`queue-keeper`, `xp-poll`, `health`) running for hours on a host | README | **UNVERIFIED.** Only short runs under test. |
| Token refresh in practice (2.4) | - | **UNVERIFIED.** No owner. |
| Restore on a second machine from a real remote | README | **UNVERIFIED.** |
| Friday leaderboard measurement (section 1) | - | **NOT RUN.** Needs a real submission in the league first. |

### 3.3 Go and no-go before handing over

Hand over to Rohit only when all of these are true. Today none of the live items is true.

- [ ] Plan 01 Task 5.1 passed, or the serial fallback is built and passed (Kanban go/no-go recorded here with the date and evidence).
- [ ] OpenRouter key has enough credit for the model in use, and kimi-k3 tool calling is checked (or the fallback model is chosen on purpose).
- [ ] Token refresh owner and method named (2.4).
- [ ] Decision on worker-terminal credential isolation (2.8, risk 1).
- [ ] Fake cycle passed (Task 6.0). Real cycle run, with each step ticked and evidence recorded (Task 6.1), or its failure recorded as a finding.
- [ ] Host config applied and `hermes gateway status` shows the dispatcher on (2.2).
- [ ] Friday measurement done (section 1).
