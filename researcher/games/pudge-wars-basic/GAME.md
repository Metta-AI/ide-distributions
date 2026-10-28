---
name: pudge-wars-basic
source: {repo: https://github.com/Metta-AI/polyworld-pudge-wars, pin: coworld_manifest}
bindings:
  build: python3 games/pudge-wars-basic/tools/build.py
  local_check: python3 games/pudge-wars-basic/tools/local_check.py
  upload: python3 games/pudge-wars-basic/tools/upload.py
  evaluate: python3 games/pudge-wars-basic/tools/evaluate.py
  replay_inspect: python3 games/pudge-wars-basic/tools/replay_inspect.py
reference_types:
  replay-segment: games/pudge-wars-basic/tools/resolve_segment.py
outcome_metrics:
  - {name: win, direction: up}
  - {name: team_kill_margin, direction: up}
  - {name: kills, direction: up}
  - {name: deaths, direction: down}
  - {name: hook_landed_rate, direction: up}
performance_guardrails: [win, team_kill_margin]
min_episodes_floor: 100
compiler: basic
league_id: league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0
noise_source: league_episodes
layers: [P,K,S,SK,C,ST]
host_requirements:
  tools: [nim, git]
---
# Pudge Wars Basic

This folder supports **Pudge Wars Basic** only: the Coworld `pudge-wars-basic`
(`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`, version 0.1.8) and its league
`league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0`. Policies are `game-hosted` files: UTF-8 BASIC
source (up to 64 KiB) or a ZIP package with exactly one `.bas` and read-only model resources
(up to 16 MiB, 256 files). No Docker is needed anywhere in this folder. The separate
"Pudge Wars 1v1" Coworld (`pudge-wars`, container players) is a different game that this folder
does not cover.

Read [mechanics](docs/mechanics.md), [policy surface](docs/policy-surface.md),
[strategy guide](docs/game-guide.md) and [replays, results and logs](docs/replay-format.md).
Every fact there is cited `path:a-b@cd8111a5` against
`Metta-AI/polyworld-pudge-wars`. Several upstream guides in that repository are stale (for
example 20k instructions per tick and 80 hook damage); the docs here list them and follow the
code.

## Seats and opponents

An episode of the league variant (`league-3v3`) has six seats: West is slots 0–2 and East is
slots 3–5, contiguous teams. In the league our champion fills **one** seat per episode; the
other five are other players' policies chosen by matchmaking, which balances teams.

- One subject seat (default; mirrors the league): `seats: [N]` for any N in 0–5, and
  `opponent_count: 5` (two teammates and three enemies, sampled from the league per seed).
  Alternate N between West (0–2) and East (3–5) across seeds.
- A whole team (questions about coordination between copies): `seats: [0, 1, 2]` or
  `seats: [3, 4, 5]`, and `opponent_count: 3`. The three seats are one team and one
  episode; their measurements are averaged into one observation.
- A fixed `opponents` set needs an `opponent_rationale` saying why those policies answer
  the question.

The `duel-1v1` variant (two seats, slot 0 West, slot 1 East, first to 10) is useful for local
diagnosis only; the league plays `league-3v3`.

## What predicts league rating

The league rates each player with OpenSkill over `league-3v3` episodes in which the six seats
hold six different players' champions (mixed teams; never two copies of one policy). Three
copies of a candidate against three copies of a baseline, locally or hosted, plays another
game and is not evidence about the rating (a 20–10 copies-against-copies win, 2026-10-27, told
the climb nothing). Use these three reads, in this order of cost:

1. **Standings with uncertainty.** `league_players` (or `"$RESEARCH_CLI" softmax standings`)
   gives each player's `score` (MMR, which is μ − 3σ), `sigma`, `policy_label` (the version
   the rating is for; it can trail a just-promoted champion until that version's first
   round), `version_rounds_played` and `settling`. σ was 1.4–1.7 for versions with 10 or more
   rounds and 2.9 for a 5-round version (2026-10-28). A new version starts low and climbs as
   σ falls, so compare μ = `score` + 3·`sigma`, and treat neighbours whose μ differ by less
   than their σ as unresolved.
2. **Opponent profiles from league replays.** `"$RESEARCH_CLI" softmax league-results
   --league league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0 --limit N --dest DIR --replays`
   writes each episode's `results.json`, `episode.json` (seat → policy, `coworld_id`) and its
   public replay `replay.gz`. `tools/replay_inspect.py DIR/<episode> --json` re-simulates it
   (192 of 192 hosted league scores matched, 2026-10-27) and reports, per seat, damage taken
   and dealt by cause, deaths by cause, hooks aimed at the seat and dodged, river time, runes
   and upgrades. That profiles every opponent, not only our seats; aggregate by
   `policy_version_id` across episodes.
3. **Hosted A/B against the live field, with a measured noise floor** (a screen, not the
   verdict). What the platform holds fixed: the seed fixes terrain, trees and spawn lanes
   (`docs/mechanics.md` **Randomness**), the rune schedule is fixed, BASIC has no `RND`, and
   the roster pins each seat. An A/A control (the same policy in both arms) matched on 54 of
   54 pairs (2026-10-27). Nothing is re-randomized, but every policy reacts to what it sees,
   so one changed action in our seat changes the whole match: a candidate and its baseline
   play different games on the same seed. Measured on 150-pair sets (2026-10-27/28):
   - 124–129 of 150 pairs had the same win outcome for both arms (the line-up decides most
     games), so a result rests on the 20–25 pairs that flip.
   - The baseline arm is one draw. On set 3 the v4 baseline won 56.0%; four different
     candidates won 48.0–50.7%, and their losing flips overlapped on 7–10 pairs per
     candidate pair (about 3 expected if independent). The shared baseline's luck, not the
     candidates, explains much of that common −5 to −8 points.
   - Per arm, the standard error of a win rate is about 0.48/√n: 3.9 points at 150
     episodes, so a difference of two arms has a 95% interval of about ±11 points. Resolving
     5 points takes about 1,500 episodes per arm.

   Method:
   - The field is the latest completed round's champions:
     `"$RESEARCH_CLI" softmax exec -- coworld rounds --league <league> --status completed
     --limit 1 --json`, `entries[0].round_config.entrant_policy_version_ids`, minus our own
     versions. For pair *i*, a seeded RNG draws 5 field policies and seed *s_i*; our policy
     sits in one fixed subject seat for the whole batch (run a second batch on the other
     side, e.g. seat 0 then seat 3).
   - Arms on the same pairs: `base` (the current champion, `player_champion`), at least
     three **neutral replicates** of it (one threshold moved by its smallest step, e.g.
     `holdBack = 16` to `17`; check locally with `tools/match.py` that its results differ from
     the champion's on some seeds, or it is an A/A copy that measures nothing), and each
     candidate.
   - One request per arm and pair, created with `"$RESEARCH_CLI" softmax exec -- coworld
     xp-request create body.json`:

     ```json
     {"idempotency_key": "<tag>-<i>-<arm>", "private": true,
      "coworld_id": "<game_support league.game.coworld_id>", "variant_id": "league-3v3",
      "game_config_overrides": {"seed": 1001}, "num_episodes": 1,
      "roster": [{"slot": 0, "player": {"policy_ref": "<policy version UUID or name:vN>"}}, "... slots 1-5"],
      "title": "<tag> <arm> pair <i>"}
     ```

   - Read each request (each through `"$RESEARCH_CLI" softmax exec --`) with `coworld
     xp-request get <xreq> --json` until `completed`, then `coworld xp-request episodes <xreq>
     --json` and `coworld episode-results <ereq> > <ARM_DIR>/<i>.json`, one directory per arm.
   - Compare unpaired, with the floor: `tools/compare.py <BASE_DIR> <CAND_DIR> --seats <seat>
     --group all --replicate <NF1> --replicate <NF2> --replicate <NF3> --json`. Report
     `difference_vs_replicate_mean` and `noise_floor`; a candidate inside the floor
     (`within_noise_floor`) is not better or worse. Never report one candidate's paired win
     flips as its strength, and do not count several candidates against one baseline arm as
     independent confirmations.
   - Cost and limits: 0.5 credits per episode. At most 300 undispatched XP episodes per user
     (`429 Too many pending experience-request episodes`): create in batches and retry after
     about 30 s. A `403 permit_required` means the coach must approve the spend: report the ask
     to the user and stop; do not retry around it.

   **League episodes decide.** A candidate that clears the floor is a submission candidate,
   not a result. After it competes, its rating (μ, σ falling over rounds) and its league
   episodes (`league-results` and `replay_inspect.py` per seat, read 1 and 2 above) are the
   evidence of strength.

   A research card runs the same line-ups through its frozen design (one subject seat,
   `opponent_count: 5` sampled from the league per seed, `"$RESEARCH_CLI" softmax xp create`).
   Its paired `stats evaluate` takes the one baseline draw as given, so run the replicate
   arms too and check its verdict with `compare.py --replicate` on the downloaded arms. The
   steps above are for a conversation that has no experiment record.

## Commands

Run every tool from `"$RESEARCH_HOME"` (the installed profile directory) with
`"$RESEARCH_PYTHON"` in place of `python3`, as the core `card` skill sets them, with absolute
worktree paths. Each tool has `--help`; with `--json` it prints one envelope
`{"ok": true, "data": ...}` or `{"ok": false, "error": {"reason", "message"}}`. Exit 0 passed,
1 failed, 2 rejected input, 3 errored (a tool fault: work around it and report it, doctrine D-26).

| Tool | Use |
|---|---|
| `tools/native.py resolve\|prepare\|build\|path [--target pudge_league\|pudge_headless]` | `resolve` reads the live league engine (below). The others fetch the live commit's game source and `nimby.lock` dependencies and build, with receipts, the league host `pudge_league` (runs episodes with seat logs) and the headless simulator `pudge_headless` (decodes replays, scores branches). Other tools build both on first use. |
| `tools/match.py --seat SPEC ... [--variant] [--seeds A..B] [--out DIR]` | Run local episodes. `SPEC` is `PATH[:COUNT]` or a starter (`base`, `aggressive`, `sniper`, `neural`), in slot order. Writes `results.json` (hosted schema), replay, `summary.json`, `events.jsonl` per episode. |
| `tools/build.py --source F --strategy S --out D` / `--starter NAME --out D` | The `build` binding (below). |
| `tools/local_check.py ARTIFACT --baseline B --work-dir W [--full] [--gate G]` | The `local_check` binding: G1 static upload rules, G2 one league-length episode in all six seats, G3 robustness over seeds × sides, G4 advisory screen (with `--full`). |
| `tools/evaluate.py --results R --json` / `R --seat N` | The `evaluate` binding (see Metrics). |
| `tools/replay_inspect.py REPLAY [--seat N] [--events K,...] [--ticks A:B]` | The `replay_inspect` binding: re-simulates a replay (raw or gzip; hosted or local) with every tick's hash verified, and reports per seat enemy hook accuracy, kills, deaths, damage by cause, hit rate by distance, dodges, runes and upgrades, plus filtered events. |
| `tools/branch.py TAPE --slot N --ticks T,T` | Counterfactual scoring of the options a seat had at chosen ticks (bots continue the match; evidence about that state, not proof). |
| `tools/compare.py BASE_DIR CAND_DIR [--group team\|seat] [--seats ...]` | A/B diagnostics over two sets of results (local or downloaded XP). Needs scipy: use `"$RESEARCH_PYTHON"`. The decision still comes from `stats evaluate`. |
| `tools/hosted_logs.py --xp XREQ\|--episode EREQ --dest DIR` | Fetch our own seats' PRINT logs, annotations and artifacts from hosted episodes as the Player (`"$RESEARCH_CLI" softmax exec -- coworld ...`), laid out like a local episode (`players/<slot>/log.txt`, `annotations.jsonl`). Other players' logs are not readable. |
| `tools/upload.py` | The `upload` binding: `"$RESEARCH_CLI" softmax upload` (the coordinator uploads). |

Local episodes run natively on the host through the game's own league host
(`examples/pudge_wars/coworld_host.nim`, `-d:basicLeague`) at the live commit: the same program
the league runs, with the same per-seat `players/<slot>/log.txt` (BASIC `PRINT`),
`annotations.jsonl` (`ANNOTATE`) and `player_status.json`. A league-length 3v3 takes about 3
seconds. `match.py --runner headless` uses the headless simulator instead (slightly faster, no seat
logs); both give byte-identical `results.json` and state hashes. Both binaries need only libc
(libcurl is stubbed, `tools/curl_stub.c`; no SSL) and were checked on macOS and Linux. Neither
reports per-decision instruction or work use, so a policy near the 100,000-instruction budget is
not detected locally.

### Staying current with the league

Nothing here is pinned to one game version. Every tool reads the live engine from the public
API (league → `game.canonical_coworld_id` → the Coworld's version, manifest `source_url` commit
and starter files), re-checked at least hourly, and builds binaries for that commit on first use.
Engine records, source checkouts, dependencies and binaries live under
`$HERMES_HOME/cache/pudge-wars-basic/` (`~/.hermes/cache/pudge-wars-basic/` outside a profile).
This writable profile cache survives task cleanup and leaves the installed game folder read-only.
`tools/engine.env` is only a last-known fallback when the API cannot be reached; every envelope
then says `engine.live: false`, `stale_risk: true` and warns. `COGAMES_API_URL` selects the API
server; `PUDGE_LIVE_ENGINE` overrides the live lookup (tests and offline hosts).

Every envelope also carries `knowledge {cited_commit, live_commit, current}` and `starters`. The
docs, skills and seed claims cite `cd8111a5`; when `knowledge.current` is false, treat
game-specific claims as possibly stale and propose a maintenance card that runs
`kb renew --game-repo <clone> --old cd8111a5ee4f2fdc38c027615b33421f6c7d2536 --new <live commit>
--version <live version>` and re-checks the docs. A `starters` mismatch means the league's
starter files changed and `baselines/` needs refreshing.

## Local validation

`local_check` and `build --full-evaluation` show that the policy is accepted and survives play;
they do not show it is stronger in the league.

- G1: the file obeys the upload rules (encoding, size, ZIP layout, `tensors.json` shape).
- G2: it compiles and plays a full league-length episode (seed 2026) in all six seats without
  a VM being disabled, and the replay re-verifies.
- G3: seeds 1..N (default 10), each from both sides, against `--baseline`: no candidate VM is
  disabled. Per-decision instruction and work use is not measured by the binary, so G3 cannot
  show how close a policy runs to the 100,000-instruction / 250,000-work budget.
- G4 (advisory): candidate against baseline over the G3 episodes: win, kill margin, kills,
  deaths, enemy hook accuracy. The bots here are the starters, not the league field; use
  hosted Experience Requests for evidence.

## Metrics

`evaluate.py` validates a results file against the game's results schema and the host's own
formulas (`examples/pudge_wars/coworld_host.nim:1059-1100@cd8111a5`) before computing
anything; a missing or inconsistent field is an error, never a guessed zero. Hosted results
must carry the real integer `seed` (the platform injects a seed per episode). One entry per
seat:

- `win`: 1 if the seat's team won, 0 if it lost, 0.5 for a draw.
- `team_kill_margin`: own team kills minus enemy team kills.
- `kills`, `deaths`: the seat's counters.
- `hook_landed_rate`: `hooks_landed / hooks_thrown`, 0.0 when no hook was thrown. The host's
  `hooks_landed` also counts latching an ally or an allied Hooktrap mine, so this is not enemy
  accuracy; `replay_inspect`'s `enemy_hook_accuracy` is.

Guardrails are `win` and `team_kill_margin`. League noise (192 episodes, 2026-10-26): per seat
per episode the spread is about 3.7 kills, 3.4 deaths and 0.48 for win/loss, so the floor is 100
episodes per arm and about 200 to resolve a win-rate gap under 15 points. In the league, the
team with higher hook accuracy won 90% of episodes, and the leaders win by dying less
(3.6–4.8 deaths against 8.3 for the field), so read `deaths` and enemy accuracy next to `win`.

## Build and upload

`build.py --source <worktree>/strategy/candidates/<E>/policy.bas --strategy
<worktree>/strategy/candidates/<E>/STRATEGY.md --out <new dir>` freezes the candidate's exact
bytes (there is no compiler: `compiler: basic`). A neural candidate is a package directory
(packed deterministically into `policy.zip`) or a ready `.zip`. It writes
`policy.bas|policy.zip`, `version.json`, `report.json`, `report.md` and `gates/`. By default it
runs G1 and G2 (seconds once the simulator is built); `--full-evaluation` adds G3 and G4.
Upload requires every non-advisory gate to pass.

The build gates against the live engine and records it in `version.json.engine` (version,
Coworld id, commit, source URL, manifest hash, `live`, knowledge and starter status, binaries).
`--offline` skips the API and uses the last-known engine, marked `live: false`.

The coordinator uploads a completed build with `upload_build`; the file reaches the platform
through `coworld upload-policy --file`. Researchers never upload.

## Starter policy

The starter is the package's `base` player (SHA-256
`f38ba2929c91f985d73afa5a54ec3e5da582e5e71122069cbc2847b19fc48511`), shipped as
`baselines/base.bas` and as the seed's `strategy/policy.bas` (byte-identical, checked by the
build). The seed's `strategy/STRATEGY.md` describes it. Build it for a bootstrap card with:

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/build.py" --starter base --out <ABS_WORKTREE>/.scratch/build/champion
```

`--starter` excludes `--source` and `--strategy`. For `base`, the build records the shipped
`seed/strategy/STRATEGY.md` hash as `strategy_sha256`, which the bootstrap requires. The other
starters (`aggressive`, `sniper`, `neural`) build the same way for comparison; they have no
STRATEGY.md.

## Qualification scope

Checked on 2026-10-26 against Coworld version 0.1.8 and source commit `cd8111a5`: the headless
simulator's results and replays match the league host (a Docker `coworld run-episode` duel, a
native 3v3, and a hosted league replay re-simulated against its hosted results), and the league
host and headless runners give byte-identical results; the starter
build passes the shared upload check. Not checked: how the platform validates a file at upload,
and per-decision budget use.

### Rejected build inputs

`build.py` exits 2 for: no input or both `--starter` and `--source`/`--strategy`; a missing or
non-UTF-8 BASIC file; a file over 64 KiB; a ZIP over 16 MiB or 256 entries, without exactly one
`.bas`, or with unsafe names; an existing `--out` directory.
