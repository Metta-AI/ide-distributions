---
name: pudge-wars-basic-survey
description: Use when surveying Pudge Wars Basic episode batches.
---

# Pudge Wars Basic survey

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-04, D-07, D-17.

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/survey/SKILL.md`, from the GotA `survey` binding in this
repository, and from the upstream mixin's replay survey
(`games/pudge-wars/skills/replay-inspection/README.md` at
`Metta-AI/polyworld-pudge-wars@cd8111a5`). The core process is aggregate, decompose,
flag, narrate, findings first; this skill supplies what those words mean here.

## Worker setup

Follow the core `card` skill first. Run shared commands through the quoted shim.
Resolve tools without changing the worker cwd:

```sh
RESEARCH_HOME="$HERMES_HOME"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
mkdir -p .scratch
```

Use absolute paths for episode directories, replays and output files below. Use the
installed profile environment; do not substitute a global CLI or Python. Run every game
tool from `"$RESEARCH_HOME"` with `"$RESEARCH_PYTHON"` as shown. Read
`games/pudge-wars-basic/GAME.md` and its linked `docs/mechanics.md`,
`docs/replay-format.md` and `docs/game-guide.md` under RESEARCH_HOME. This folder
targets Pudge Wars Basic only: league `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0`,
Coworld `pudge-wars-basic`. The knowledge here was written for version 0.1.8
(`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source `Metta-AI/polyworld-pudge-wars` at
`cd8111a5`, replay game version 38. The tools follow the live league by themselves: every
envelope's `engine` block names the version and commit that ran (binaries for a new commit
are built on first use). If `engine.live` is false the league could not be checked and the
run may be stale: say so in the record. If `knowledge.current` is false, treat
game-specific claims here as possibly stale and propose the maintenance card the
`knowledge.message` names (`kb renew ...`).

## Long commands

Use the core `card` skill's **Long commands** procedure for native builds
(`tools/native.py`), local episodes (`tools/match.py`), `tools/local_check.py`,
`tools/branch.py` and replay inspection of more than a few episodes. A league-length
local 3v3 takes about a second of simulation per episode plus decoding; a first native
build fetches the source and every `nimby.lock` dependency. After exit, read the exit
code, the JSON envelope (`ok`, `error.reason`) and the files the command wrote before
interpreting anything. Exit 1 is a failed check, 2 a rejected input, 3 a tool fault.

## The overview table

One row per policy (aggregate teammate copies per episode first), findings first:

| Column | Source | Note |
|---|---|---|
| policy, version ID | episode record `policy_version_ids[slot]` | labels alone do not identify a version |
| n episodes, n seats | results | |
| win | `evaluate.py` `win` | raw league win rate is compressed toward 50% by team balancing |
| kills, deaths per game | `per_seat` | deaths first: the leaders' signature is 3.6–4.8 deaths |
| team kill margin | `evaluate.py` | |
| landed/thrown | `hook_landed_rate` | includes ally saves; field mean 49.3% (SD 9.6) |
| enemy hit rate, clash W/L | `replay_inspect` `hooks[]` | only for decoded episodes; say how many |
| deaths by cause | `replay_inspect` `damageTaken`, `kill` events | hook, headshot, drown, rot, melee, fire, lightning |
| VM disabled / load failures | `player_status.json`, `player_failure.json`, our seats' `players/<slot>/log.txt` (`BASIC error:` line; hosted via `hosted_logs.py`) | a separate count, never folded into means |

Add the batch context: game version, seeds (distinct?), line-ups, West/East counts.
Missing values stay missing; do not fill zeros.

## Structure worth surfacing

- **Who beats whom.** League episodes mix the same 6–11 policies; matchmaking pairs weak
  policies with strong teammates. Use a teammate-adjusted view (MMR, or a logistic fit
  on team rosters) before calling one policy stronger from raw win rate.
- **Kill economy.** Team kills = enemy deaths (minus uncredited deaths). A team that loses
  despite equal accuracy usually has one seat feeding: look for a seat with deaths far
  above its teammates.
- **Hook economy.** Throws are flat (~20/min); differences live in landed share, clash
  outcomes (`denied` vs `deny_win`) and ally hits.
- **Side and lane.** West vs East should be even; a per-lane (spawn y) pattern in deaths
  points at terrain (trees, low patches) on that seed.
- **Phase.** Upgrades (+4 range, +15 damage per level) and +35 max HP per kill snowball;
  split kills by first half and second half of the game.

## What makes an episode interesting

Cheap flags from results alone, then pick witnesses for `replay-inspection`:

- **Blowout**: margin ≥ 20 (field p75 is 15). Shows what a policy cannot handle.
- **Upset**: our team loses with higher landed/thrown than the enemy (only ~10% of games).
- **Near-win**: lost 28–30 or 29–30; decisive late deaths.
- **Feeding seat**: a seat with deaths ≥ 15 or ≥ 2× its teammates' mean.
- **Uncredited deaths**: Σ deaths > enemy team kills (ally Rot or drowning without a
  credited attacker).
- **Rot suicide**: Σ seat kills < own team kills.
- **Passive game**: `end_reason` `"time_limit"` (none in 192 league games).
- **Accuracy collapse**: a seat's landed/thrown below 35%.
- **Taint cluster**: repeated `BASIC VM disabled` or load failures for one policy version.

## Procedure and outputs

Use core `league-watch`, `opponent-profile` or `meta-recon` for scouting and core
`replay-diagnosis` for a witnessed failure. Survey completed evidence first:

```sh
"$RESEARCH_CLI" records find evidence --where kind=xp-result
"$RESEARCH_CLI" softmax leaderboard
"$RESEARCH_CLI" softmax league-results --league league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0 --limit 50 --dest .scratch/league --replays
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/evaluate.py" --results <ABS_RESULTS_JSON> --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/replay_inspect.py" <ABS_EPISODE_DIR> --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/compare.py" <ABS_BATCH_A> <ABS_BATCH_B> --group seat --json
```

`--replays` saves each episode's public replay beside its results, so `replay_inspect.py
<ABS_EPISODE_DIR>` decodes every seat, ours and the opponents': run it over the whole batch to
fill the enemy-hit-rate and deaths-by-cause columns for every policy in the field (seat →
policy from that folder's `episode.json`). Read `sigma` and `settling` from `softmax
standings` beside each policy's MMR (GAME.md **What predicts league rating**).

Build the table, verify replay hashes ("every tick matched") before using replay
fields, and list sample size and teammate aggregation. A flag chooses a witness; it is
not the cause. Sparse subgroups and a shifting field weaken conclusions; the
2026-10-26 standings in `docs/game-guide.md` are a dated snapshot.

```sh
"$RESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>
"$RESEARCH_CLI" records new lesson --meta-file .scratch/lesson.json --body-file .scratch/lesson.md
```

Use the core claim schema with tiered evidence and `revise_if`; one replay supports a
belief (D-07). For a research mistake use the core `lessons` schema: imperative lesson,
status tentative, scar with incident ref, recurrences. Imported upstream red flags stay
tentative until they recur here (D-17). The survey output names the interesting
episode, why, what remains unknown and the next bounded investigation. It does not
establish competitive improvement from historical summaries.
