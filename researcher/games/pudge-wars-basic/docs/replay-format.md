<!-- Source: Metta-AI/polyworld-pudge-wars @ cd8111a5ee4f2fdc38c027615b33421f6c7d2536 -->

> **Provenance.** Pudge Wars Basic, Coworld `pudge-wars-basic` 0.1.8
> (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source commit `cd8111a5`, replay game
> version 38. Written 2026-10-26 (PDT) and checked against a hosted league replay
> (`ereq_1f9ac57f-…`, 15,000 ticks, "replay verified: every tick matched") and local native
> runs. Citations are `path:a-b@cd8111a5`. Re-verify when `ReplayGameVersion` or the manifest's
> source commit changes: a tape replays only under the physics that recorded it.

# Pudge Wars Basic — replays, results and logs

## Inventory

| Artifact | Where | Holds | Who can read it |
|---|---|---|---|
| `results.json` | every completed episode | outcome, team kills, per-seat counters ([mechanics](mechanics.md#scoring-and-results)) | anyone who can see the episode |
| replay tape | hosted: public `replay_url`, gzip; local: `replay.pwr`, raw | setup, every action, one state hash per tick | public |
| `player_status.json` | episode dir (league host) | per seat `exit_code` 0 / 1 and `reason` `"Completed"` / `"BASIC VM disabled"` | own runs |
| `player_failure.json` | instead of results | `failed_policy_index` of a seat that failed to load | own runs |
| seat PRINT log | `log_uri` per seat | the seat's PRINT text plus host lines `Player slot N started.`, `BASIC loading error: …`, `BASIC error: …` | **own policies only** |
| seat annotations | `annotations_uri` per seat | `ANNOTATE` records as JSONL | **own policies only** |
| `summary.json`, `events.jsonl` | produced by re-simulating a tape (`replay_inspect`) | positions, hooks, damage, kills, runes, purchases | whoever has the tape |

## The replay tape

- Container: `"POLYWORLDREPLAY"` magic, u16 file version, u16 game version (**38**), u16 name
  length, `"pudge_wars"`, then a Flatty payload (`src/polyworld/tapes.nim:98-115@cd8111a5`,
  `examples/pudge_wars/replays.nim:26-26@cd8111a5`).
- Payload: `{header: {formatVersion 5, gameVersion, createdUnixMs, setup}, actions[], hashes[]}`
  (`src/polyworld/tapes.nim:178-191@cd8111a5`).
- `Setup`: map seed and hash, tick rate 32, grid 128, max ticks, kill goal, bot cadence, aim
  noise, rune period, `pudges[] {id, team, slot, controller, lobbySeat, spawnX, spawnZ}`,
  `seatLabels[]` (`examples/pudge_wars/replays.nim:184-238@cd8111a5`). League tapes carry no
  seat labels, so tools show every seat as `"bot"`; map seats to policies from the episode
  record (`policy_version_ids[i]` is slot i).
- `ReplayAction = {tick u32, pudgeId i32, kind u8, first i32, second i32}`; kinds 1–18 are
  walk, hook, attack, buy upgrade, stop, ping, takeover, fine hook, grapple, fine grapple,
  buy power, blink, fine blink, place wall, place mine, cheat, rot toggle, place ward
  (`examples/pudge_wars/replays.nim:130-164@cd8111a5`). Every action **call** is recorded,
  whether or not the game accepted it.
- **There are no positions, HP or events in the file.** Decoding means re-simulating with a
  binary built from the recording commit; the decoder checks the state hash every tick.
  A tape from another game version is rejected or hash-mismatches.
- Size: about 21 bytes per action; a 15,000-tick 3v3 was 2.95 MB raw, 523 KB gzipped,
  141,509 actions. Hosted copies are gzip; local runs write raw bytes.
- The host encodes the replay before writing results, so results imply a replay exists
  (`examples/pudge_wars/coworld_host.nim:1102-1110@cd8111a5`).

## Decoding: `replay_inspect`

The binding gunzips if needed and runs the headless binary
(`examples/pudge_wars/pw.nim` built `-d:release -d:headless`) as
`pudge_headless --replay R --duellog DIR`, caching the output next to the replay. It
must report every tick matched; any mismatch means a wrong binary, not a game fact. The
duel logger is a pure observer (`examples/pudge_wars/duellog.nim:1-17@cd8111a5`).

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/replay_inspect.py" <ABS_EPISODE_DIR_OR_REPLAY> --seat <N> --json
```

### `summary.json` (per match)

Top level: `seed`, `killGoal`, `ticks`, `outcome`, `reason`, `score {west, east}`,
`players[]`, `kills[]`, `hooks[]`.

- `players[]` (`id` = slot + 1, `seat`, `team`): `kills`, `deaths`, `hooksThrown`,
  `hooksLanded`, `accuracy`, `headshots`, `denies`, `runes`, `upgrades {damage, distance,
  speed, radius}`, `damageDealt` / `damageTaken` by cause (`hook`, `headshot`, `melee`,
  `rot`, `drown`, `fire`, `lightning`, `total`), `timeInRiverSec`, `hitRateByDist`
  (bands `0-15`, `15-30`, `30-45`, `45+` → `[hits, throws]`), `dodge {hooksAimedAtMe, hit,
  dodged, avgMoveDuringFlight}`.
- **`accuracy` is `hooksLanded / hooksThrown`, the same ratio as `results.json`**, so it
  includes ally saves and allied-mine grabs
  (`examples/pudge_wars/duellog.nim:858-866@cd8111a5`). For enemy accuracy, count
  `hooks[]` with `result: "hit"` whose `victim` is on the other team.
- `hitRateByDist` and `dodge` use the logger's `target`: the **nearest enemy at launch**, a
  heuristic, not the policy's intent. A "hit" there means the hook latched anyone
  (`examples/pudge_wars/duellog.nim:551-571@cd8111a5`).
- `hooks[]`: `t`, `owner`, `team`, `from`, `aim`, `result`, `resolveTick`, `flightTicks`,
  `traveledTiles`, `target`, `targetDist`, `targetMoved`; plus `victim`/`damage` on a hit and
  `closest {id, dist, hook, enemy}` on a miss. `result` is one of `hit`, `miss`, `denied`
  (our hook lost a clash), `deny_win` (won a clash), `cancel` (owner died first)
  (`examples/pudge_wars/duellog.nim:585-610@cd8111a5`).
- `kills[]`: `t`, `killer`, `victim`, `cause`, `score`.
- Quirk: `outcome` is `"draw"` at the time limit even when one team leads
  (`examples/pudge_wars/duellog.nim:835-843@cd8111a5`); use `results.json` for the outcome.

### `events.jsonl`

One object per line with `ev`, `t` (tick) and `sec`. Kinds: `match_start` (roster with
`id`, `seat`, `team`, spawn), `action` (`id`, `act`, `x`, `y`), `state` (every 8 ticks: per
butcher `x`, `y`, `hp`, `cd`, `lay`, `vx`, `vy`, `alive`, `buff`, `shield`, `windup`),
`hook_launched` (aim, range, speed, damage, nearest-enemy target snapshot), `hook_landed`
(`victim`, `dist`, `victimHpAfter`), `hook_missed` (`closest`), `hook_resolved`, `damage`
(`victim`, `by`, `amount`, `cause`, `lethal`), `kill`, `respawn`, `river_enter` (`by`),
`river_exit`, `rune_spawn`, `rune_taken`, `buy`, `match_end`. A 15,000-tick 3v3 had about
99k `action`, 1.9k `state`, 864 `hook_launched` and 51 `kill` rows.

Join key: `(episode, slot, tick)`; log `id` = slot + 1. A `state` row is up to 7 ticks stale.

## Counterfactual branches: `branch.py`

`pudge_headless --branch TAPE` replays to a tick, clones the world, hands every other seat to
the stock Base bot, runs each option for the traced seat (`hook_k`, `attack_k`, `rot`,
`retreat`, `none`, …) and reports deltas over a horizon (default 160 ticks)
(`examples/pudge_wars/branch.nim:1-14@cd8111a5`). Output rows: `tick`, `slot`, `option`,
`hp_before/after`, `alive_before/after`, `kills`, `deaths`, `hooks_thrown`, `hooks_landed`,
`gold`, `team_kills`, `enemy_kills`.

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/branch.py" <ABS_REPLAY> --slot <N> --ticks <T1>,<T2> --horizon 160 --json
```

The continuation is played by bots, not the real opponents, so a branch is a key for "was
this choice reasonable", not a prediction of the hosted outcome.

## Policy logs and annotations

- PRINT text reaches only the seat's private log; there are no tick stamps, so print
  `worldTick` in every line (`examples/pudge_wars/fileplayers.nim:51-57@cd8111a5`). Cap 10 MiB.
- `ANNOTATE(worldTick, "kind", "function", "{json}")` writes
  `{"schema_version":1,"time":T,"kind":…,"function":…,"args":{…}}` per line
  (`src/polyworld/annotations.nim:69-99@cd8111a5`). Join to the replay by `time` = tick.
- **The headless binary keeps neither**: PRINT becomes a speech bubble and `ANNOTATE`
  returns "disabled" (`src/polyworld/annotations.nim:113-130@cd8111a5`). Local
  `match.py` runs therefore give no decision trace. The headless binary does echo
  `pudge <id> BASIC error: <message>` to stdout when a VM is disabled
  (`examples/pudge_wars/bots.nim:827-833@cd8111a5`) and ends with
  `scripts: N/M active`.
- Hosted: the core `softmax xp artifacts` command downloads results and replays only. Seat
  logs and annotations exist for our own policies on the platform, but no installed command
  fetches them yet; treat the decision trace as unavailable and say so.

## Local episode output (`match.py`)

Per episode: `results.json` in the hosted schema, `replay.pwr`, `summary.json`,
`events.jsonl` and the stdout log. The headless binary reproduced the league host's final
state hashes for the same seats and seed, so local tapes decode the same way as hosted ones.
It does not produce `player_status.json`; a disabled VM shows in the stdout log.

## Open questions

- Whether the platform compiles a policy at upload or only at episode start (assume the
  latter and validate locally).
- Episode directory names inside an XP download depend on the platform CLI; read the
  download's `manifest.jsonl` rather than assuming a layout.
