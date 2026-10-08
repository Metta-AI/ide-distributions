# Finding: real Softmax client facts (Plan 03 Tasks 3.1–3.2)

Verified 2026-10-07 against coworld 0.1.57, softmax-cli 0.26.38 (both pinned in `pyproject.toml`) and the live platform, as Player Games Bond (`ply_f39295d0-…`), read-only. Code: `src/autoresearch/softmax_tool/{platform_cli,identity,xp,client}.py`.

**Versions (2026-10-08):** pinned coworld **0.1.58** (bumped from 0.1.57; `uv lock --upgrade-package coworld`, lock diff touched only coworld), softmax-cli **0.26.38** (still the latest on PyPI). The 0.1.57 → 0.1.58 source diff touches none of the commands below except `upload-policy`: new `--llm-endpoint` / `--llm-api-key-env` options, and the *default* policy name when `--name` is omitted (a Player session now uses its player id). The client always passes `--name`, and `Upload complete: <name>:v<N>` (in the unchanged `coworld/upload.py`) is still the output, so no client or fixture change was needed. The `xp-request`, `submit`, `submissions`, `results`, `rounds`, `episodes`, `episode-results`, `show`, `player` and `leagues`/`memberships` commands are defined in unchanged modules (`tournament_cli.py`, `upload.py`, `submit.py`) or in parts of `cli.py` the diff did not touch (its only hunk is `upload-policy`). Other changes (hosted-LLM runner, `CoworldClient.sql_query`, training-trajectory docs) are outside our path. The findings below were recorded against 0.1.57 and still hold.

## Credential
- `SOFTMAX_PLAYER_CREDENTIAL` is JSON `{"token": "ply_...", "expires_at": "<ISO 8601>"}`: one entry of `player_sessions["https://softmax.com/api"].cache[<ply_id>]` in the `~/.softmax/credentials.yaml` that `coworld player use <ply_id>` writes. A bare token is rejected.
- Why the expiry is in the credential: `/whoami` returns `subject_type`, `subject_id`, `owner_user_id`, `scopes`, … but no expiry. Player tokens last about 24 h.
- The client writes `$HERMES_HOME/local/softmax/home/.softmax/credentials.yaml` (dirs 0700, file 0600) before every CLI call, with `tokens: {}` and the Player token as the active player session (softmax-cli `softmax/auth.py` format). It runs the venv's `softmax`/`coworld` with `HOME` and `cwd` set to that directory and a fresh environment (no host env, no credential variable, `COWORLD_AGENT_GUIDANCE=0`). With no user token in the file, an expired session makes `softmax status` exit 1 ("Not authenticated") rather than fall back to a user identity (checked live).

## Commands used
| Purpose | Command | Output used |
|---|---|---|
| whoami | `softmax status` (no `--json` exists) | `subject_type:` / `subject_id:` lines |
| game version | `coworld leagues <league> --json` → `coworld show <game.canonical_coworld_id> --json` | `version` (`2026.10.6.1` today) |
| XP create | `coworld xp-request create - --json` (body on stdin) | `id` |
| XP list | `coworld xp-request list --mine --limit 200 [--cursor c] --json` | `entries[].title`, `next_cursor` |
| XP status | `coworld xp-request get <xreq> --json` | `status`, `completed_count`, `episode_count` |
| XP artifacts | `coworld xp-request download <xreq> --include results --include replay -d <dest>` | `<dest>/manifest.jsonl` receipts; exit 2 = evidence pending |
| upload | `coworld upload-policy --file <path> --name <name>` (no `--json`) | `Upload complete: <name>:v<N>` |
| submit | `coworld submit <name>:v<N> --league <L> --player <ply> --no-open-browser` (no `--json`) | `Submission: sub_…` |
| submissions | `coworld submissions --player <ply> --json` | `entries` |
| champion | `coworld memberships --league <L> --player <ply> --champions-only --json` | `entries[0].policy_version.label` |

## XP tagging and duplicates
- coworld 0.1.57 has no tag or metadata field on an XP request. The tag `ar:<experiment>:b<batch>:a<attempt>:s<seed>:<arm>` (amendment A12: one request per seed and arm, each with `game_config_overrides: {"seed": <seed>}` and `num_episodes: 1`; older tags without `:s<seed>:<arm>` still parse) goes in `title` (found again by `recover`) and in `idempotency_key`. The server returns the existing request for a repeated key in the same league (observatory-api `v2/experience_requests.py`), checked before credit admission. Experiment ids are ULIDs, so keys do not collide across installs.
- `xp-request list --mine` as a Player lists requests at the owning-user level and also shows rows from `machine-sentinel@softmax.internal`. Filtering is by exact tag only.

## Credits
- `GET /usage/me/credits` and `/usage/me/status` answer 403 "Player credentials are not authorized for this route". No CLI command reads credits. `SoftmaxClient.credits()` raises `CreditsUnavailable`; preflight records it as a warning.
- Denial at create time: HTTP 402. For a Player the detail is "The owning account cannot admit this experience request under its current credit allowance". The CLI prints `Error: Request failed (402) for /v2/experience-requests: <detail>` on stderr and exits 1. The client maps this to `CreditDenied`. HTTP 429 (pending-episode cap) is a plain error.

## Artifact hashes
- The download manifest gives `size` and `etag` per file but no content hash (`version` is not the sha256 of the content). The CLI computes `sha256` while streaming and refuses a size mismatch. `xp_artifacts` re-hashes every completed file on disk against the latest receipt for it.

## Policy identifiers
- `upload-policy` and `submit` have no `--json` and work with `<name>:v<N>` labels, so the real client uses the label as `policy_version_id` (upload returns it, submit takes it, `leaderboard_champion` returns it). Rows from `submissions` carry both `policy_version.id` (UUID) and `policy_version.label`.
- `submit` without `--player` lets the server pick the account's default player, so the client always passes `--player $AUTORESEARCH_PLAYER_ID`.

## Player read of league episodes (2026-10-07)

Probe of whether a Player-only credential (Games Bond, `ply_f39295d0-…`, no `--elevated`) can read league episodes of GotA (`league_3c60897b-…`). Pinned `uv run --locked coworld` (0.1.57) with an isolated temp HOME holding only the user token; `coworld player use` then `softmax status` showed `subject_type: player`, `subject_id: ply_f39295d0-…`. The real `~/.softmax/credentials.yaml` sha256 was identical before and after; the temp HOME was deleted. Read-only commands only.

| # | Command | Exit | Data? | Fields seen (redacted) |
|---|---|---|---|---|
| 1 | `coworld rounds --league <L> --status completed --limit 3 --json` | 0 | yes | `entries[]` with `id` (`round_…`), `round_number` (1320), `round_config.entrant_attributions[]` (subject_id, policy_version_id) |
| 2 | `coworld episodes --round <round_id> --limit 5 --json` | 0 | yes, 5 rows, all `completed` | `id` (`ereq_…`), `status`, `coworld_id`, `round_id`, `policy_version_ids`, `replay_url` (signed, not recorded) |
| 3 | `coworld episodes <ereq_id> --json` | 0 | yes | `coworld_version` (2026.10.7.2), `variant_name` (Competition), `game_config.seed` (2026, config value), top-level `seed` null, `participants[]` (position, `label` such as `pawgod:v27`, `policy_version_id`, `player_name`, `is_filler`), `scores[]` and `participant_scores[]` per seat |
| 4 | `coworld episode-results <ereq_id>` (no `--json`; prints JSON) | 0 | yes | `scores` (10 ints), `ticks`, `seed` (per-episode, e.g. 1450878938), `outcome` (`BlueTeam`), `banked_gold`, `returned`, `total_xp` (10 ints) |
| 5 | `coworld episode-stats <ereq_id> --json` | 0 | yes | `policy_stats[]` with position, policy_version_id, policy_name/version, `avg_reward`; `game_stats` empty |

Notes:
- The episodes were not owned by Games Bond (other players' ladder round), so the COOKBOOK claim that `episode-results` is team-only and per-episode lookup is owner-only does not hold for coworld 0.1.57 against the live platform: no 403 anywhere.
- Two seeds exist: `game_config.seed` (2026) is a config value; the real per-episode seed is `results.json` `seed`. Use the latter for noise calibration. The detail row's top-level `seed` was null in this sample.
- Seat order is `participants[].position`, which lines up with the 10-element arrays in `episode-results`.

**Verdict: PASS.** A Player-only credential can list rounds, list episodes per round, and read per-episode detail, results.json (seed, scores, outcome, total_xp) and per-seat policy ids and labels for league episodes. The `league_episodes` path in `client.py` is viable as a Player.

## League episode read latency (EC2 box, 2026-10-08)

Measured from the box's Hermes session log (read-only; no platform calls made for this note):

- `--limit 20` ran 41 s and then failed on the first evaluate (the old missing `--seat` bug). The client then fetched every episode before evaluating any, so about 2 s per episode is the fetch alone: two coworld processes per episode (`episodes <ereq>`, `episode-results <ereq>`), mostly CLI start-up plus one HTTP call each.
- `--limit 200` hit the 300 s terminal timeout; `--limit 500` ran over 10 minutes with no output. A background `--limit 200` later finished (200 episodes, 2000 seat rows).

Change (this branch): the client yields episodes in listing order while 4 threads (`LEAGUE_FETCH_WORKERS`) fetch details ahead (window 2 x 4), and the CLI evaluates each as it arrives, prints progress to stderr every 10 episodes, and writes `--out` once at the end. The estimate for 200 episodes is about 100 s of fetching (2 s x 200 / 4), overlapped with evaluation. That is a design estimate from the fixture-timed tests and the numbers above, not a live measurement; confirm it on the next box run. Concurrency is safe on our side because `PlatformCli.run` rewrites the same credential with an atomic, uniquely named temp file. The calibrate-noise skill keeps `--limit 200` (400 at most) with a 600 s foreground timeout.
