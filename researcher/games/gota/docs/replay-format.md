<!-- Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/docs/replay-format.md -->

> Historical source reference. This file describes the pinned September source.
> See [toolkit provenance](../PROVENANCE.md) for verified live replay adaptations.

# Gods of the Arena replay format and expander design

> **Currency.** Format verified against the deployed polyworld `7365e4e9` (coworld
> 2026.9.16.3, 2026-09-16; see the table below). The container and action layout are byte-for-byte
> the same as at `5422fb0c` — the only change to `replays.nim` is `ReplayGameVersion` 33 → 40 — so
> the layer-B decoder still applies. Re-simulation at `7365e4e9` verified on the in-repo
> `replays/demo.replay` (version 40, 105,530 actions, zero hash mismatches); the downloaded league
> episode in the table was recorded at `5422fb0c` (version 33) and re-simulates only there. No
> league episode recorded at `7365e4e9` has been re-simulated yet. **Re-verify when** `tools/deployed_ref.py`
> reports a new commit: the replay format version in `replays.nim`, the action record layout, and
> whether a re-simulation at the new commit still reproduces an episode's hash line. Rendered report:
> [`docs/reports/gota-replay-format-2026-09-15.html`](../../docs/reports/gota-replay-format-2026-09-15.html).

Investigation report (2026-09-15, re-verified 2026-09-16). What a hosted GotA replay actually contains,
how it is produced, what can and cannot be recovered from it, and a proposed
`gota_lab` replay expander in the shape of the Crewrift / Heartleaf / CTF tools.

Everything marked **verified** was checked against the polyworld source at the
commit the live game is built from, against a real league replay downloaded from
the Observatory, or both. **Inferred** items are reasoned from source but not
exercised.

| Evidence | Value |
| --- | --- |
| Live game version | `2026.9.16.3` (`GET /v2/coworlds/cow_54d6f449-6d63-464d-b742-ff720a9ce803` → `version`) |
| Live source commit | [`7365e4e9`](https://github.com/Metta-AI/polyworld/tree/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena) (`manifest.game.runnable.source_url` on the same endpoint; `tools/deployed_ref.py` resolves it) |
| Sample league episode | `ereq_9aa9fcd9-b288-42aa-bc37-70f9b736e9d9`, Competition division, completed 2026-09-15T19:46Z at `5422fb0c` (replay version 33), time-limit draw, 291,325 recorded actions |
| Sample at the live commit | `examples/gods_of_the_arena/replays/demo.replay` at `7365e4e9` (version 40, 2,222,059 bytes, 105,530 actions, 10,911 ticks) |

All polyworld links below point at `7365e4e9`. Line numbers are for that commit;
re-check with `grep -n` when the source moves.

---

## 1. Where replays come from and the exact format

### 1.1 Producer (verified)

The hosted game runs the headless polyworld binary compiled with `-d:coworld`.
The runner hands it `COGAME_SAVE_REPLAY_URI`; the game records an in-memory
**action tape** during the match and writes it once at the end:

- Path plumbing: [`src/polyworld/coworld.nim:280`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/coworld.nim#L280)
  (`replayPath = localPath(getEnv("COGAME_SAVE_REPLAY_URI"))`).
- Recorder created per match: [`examples/gods_of_the_arena/game.nim:144`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/game.nim#L144)
  (`initReplayRecorder(currentSetup(...))`) and the hosted `GotaConfig` is copied
  into the tape at `game.nim:147-149`.
- Written at the end: [`game.nim:229` `saveRecording`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/game.nim#L229)
  → [`replays.nim:285` `saveReplay`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/replays.nim#L285).
- `finishCoworld` refuses to publish results if the replay file does not exist
  ([`coworld.nim:329`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/coworld.nim#L329)),
  so every completed episode has a replay.

The Observatory serves it two ways (verified on the sample episode):

- `episode.json.replay_url` → `https://softmax-public.s3.amazonaws.com/replays/<job>.replay`
  (public S3, raw bytes). The manifest declares `replay_viewer.replay_compression: "gzip"`,
  and polyworld's own inspector tolerates a gzip prefix
  ([`tools/herostats.nim:162`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/tools/herostats.nim#L162)),
  but the bytes we received were **uncompressed**.
- `/v2/episode-requests/{id}/artifacts/replay`, which is what
  `.claude/skills/coworld-episode-artifacts/scripts/fetch_artifacts.py` uses. It
  saved 6,119,872 identical bytes as both `replay.json.z` and `replay.json`
  (its zlib attempt fails silently and falls back to the raw blob). **The file
  named `replay.json` is not JSON**; treat it as `replay.bin`.

### 1.2 Container format (verified)

A GotA replay is the generic polyworld tape container
([`src/polyworld/tapes.nim`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/tapes.nim)):
a tiny hand-written header followed by a [Flatty](https://github.com/treeform/flatty)
(`0.4.0`, `-d:flatty64`) serialization of one Nim object. There is no
compression, no per-tick state, and no event log.

```
offset  size  field                              value in sample
0       15    magic                              "POLYWORLDREPLAY"          tapes.nim:11
15      u16   file format version                1                          tapes.nim:12
17      u16   game version                       40 (33 before 2026-09-16)  replays.nim:15
19      u16   game name length                   17
21      n     game name                          "gods_of_the_arena"        replays.nim:11
21+n    …     Flatty payload: ActionTape[Setup, ReplayAction, ReplayMetrics, GotaConfig]
```

Payload layout (Flatty writes object fields in declaration order; `int` and
`seq` lengths are 8-byte little-endian; enums are 8 bytes; bool is 1 byte;
**a `seq` of a plain-data object is a raw `memcpy` of the Nim struct, padding
included** — `flatty.nim:464-472`):

| Field | Type | Bytes / notes | Source |
| --- | --- | --- | --- |
| `header.formatVersion` | u16 | 5 | [`replays.nim:12`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/replays.nim#L12) |
| `header.gameVersion` | u16 | 40 | `replays.nim:15` |
| `header.createdUnixMs` | i64 | | [`tapes.nim:187`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/tapes.nim#L187) |
| `header.setup` | `Setup` | `mapSeed i32, mapHash u64, tickRate u16, gridTiles u16, spawnIntervalTicks u32, maximumTicks u32, heroes seq[ReplayHero]` | [`replays.nim:37`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/replays.nim#L37) |
| `ReplayHero` | 8 bytes each | `id i32, team u8, slot u8, lane u8, class u8` | `replays.nim:30` |
| `config` | `GotaConfig` = `MatchConfig[MapConfig]` | `players seq[{name string}], seed i32, maxTicks i32, spawnIntervalTicks i32 (default 480 = 20 s), playerSlot i32, dayCount i32, mapPreset` | [`configs.nim:18`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/configs.nim#L18), [`presets.nim:10`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/presets.nim#L10) |
| `MapConfig` | | `mapSize i64, seed i64, lakeCrossings i64, jungleRoads i64, 9 × f32, campsTouchRoads u8` | [`generation/configs.nim:10`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/generation/configs.nim#L10) |
| `actions` | `seq[ReplayAction]` | count i64, then **20 bytes each**: `tick u32 @0, heroId i32 @4, kind u8 @8, 3 pad, first i32 @12, second i32 @16` | [`replays.nim:46`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/replays.nim#L46) |
| `hashes` | `seq[uint64]` | one canonical state hash per completed tick | `tapes.nim:190-198` |
| `metrics` | `ReplayMetrics` | `tickRate i32, interval i32, frames seq[{tick i32, rows seq[{cpu i32}]}], final seq[{cpu i32}]` | [`metrics.nim:27`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/metrics.nim#L27) |

Action `kind` codes ([`replays.nim:16-23`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/replays.nim#L16)):

| kind | meaning | `first`, `second` |
| --- | --- | --- |
| 1 | `walkTo` | tile x, tile y |
| 2 | `attackTarget` | object id, – |
| 3 | `buyItem` | item id (`Item` enum ordinal, [`content.nim:83`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/content.nim#L83)), – |
| 4 | `useItem` | inventory slot 0–5, – |
| 5 | `attackMove` | tile x, tile y (human/graphical client only; never emitted by BASIC) |
| 6–9 | `castTarget` slot 0–3 | object id, – |
| 10–13 | `castPoint` slot 0–3 | tile x, tile y |
| 14 | `manualSpells` | 1 = human seat (only written when a human plays) |

A pure-Python decoder of this layout parses the sample replay and the in-repo
`examples/gods_of_the_arena/replays/demo.replay` to the exact last byte, and the
last entry of `hashes` (`0x320ad325`) equals the `hash:` line in the episode's
`game_logs.log`. (Prototype: `gota_replay_probe.py` in this session's scratchpad;
about 60 lines of `struct` calls, worth lifting into the lab as-is.)

Decoded sample header for reference (the league episode, recorded at `5422fb0c`; matches
recorded at `7365e4e9` carry `spawn 480` because `DefaultSpawnIntervalTicks` is now 20 s):

```
setup:   map_seed 223226943, map_hash 0x29424656, tick_rate 24, grid 116, spawn 240, max 28800
heroes:  id 100-104 team 0 (Red) classes DeathKnight, Crossbowman, Lich, Warlock, Berserker (lanes 0,0,1,2,2)
         id 105-109 team 1 (Blue) classes VanguardKnight, Ranger, Arcanist, DruidWarden, DemonHunter
players: ['Andrew Brower', 'richard', 'Games Bond', 'daveey-2', 'docxology', 'Andrew Brower B', 'relh', 'Andre von Auto', 'daveey', 'Andre von Houck']
actions: 291,325  (attackTarget 180,166 · walkTo 85,632 · useItem 15,371 · buyItem 10,156 · 0 casts)
hashes:  28,800   metrics: 1,201 CPU frames at 24-tick interval + 10 finals
```

Note `config.players[].name` is the **public player display name**, so the
replay itself tells you who sat where (hero id 100 + slot = seat 0..4 Red, 105 +
slot = seat 5..9 Blue; `episode.json.participants[].position` is the same order).

### 1.3 What "replay" means here (verified)

The tape stores only **requests** (each BASIC action call, recorded *before* the
engine accepts or rejects it — [`bots.nim:284-427`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/bots.nim#L284):
`recordWalkTo` 285, `recordAttackTarget` 310, `recordBuyItem` 350, `recordUseItem` 374, `recordCast` 400/418)
plus one hash per tick. Playback **re-runs the full simulation** and re-applies
the recorded requests at their ticks through the same validators
([`sim.nim:2689` `applyReplayAction`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L2689),
called from [`tickWorld` `sim.nim:3242`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L3242) at line 3270),
checking `stateHash` against the recorded hash every tick
([`sim.nim:3374-3376`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L3374) → `checkReplayHash` at 3233).
This is the same design as the bitworld `.bitreplay` used by Crewrift/Heartleaf,
so everything in [`crewrift-replays.md`](../../crewrift_lab/docs/crewrift-replays.md)
about version coupling applies.

### 1.4 Version coupling — and how to find the right build (verified)

`ReplayGameVersion` (40 at `7365e4e9`) is **not** sufficient to pick a compatible sim.
The sample league replay (version 33, recorded at `5422fb0c`) diverges at tick 132
(28,669 mismatches) when built at a later commit that also declared version 33 —
sim-affecting PRs (tower collision, creep lane resume, creep upscale) merged without
a version bump. Built at the recording commit, the replay re-simulates with **zero
mismatches** on this arm64 Mac, and the headless summary reproduces the hosted game
log exactly:

```
result: time limit
forts: red 400 hp, blue 400 hp / towers: red 4, blue 5
hash: 00000000320AD325 map 0000000029424656
heroes: red L13 L13 L8 L3 L12, blue L10 L10 L9 L10 L9
economy: red 21200 XP / 635 gold, blue 18950 XP / 680 gold
replay: 291325/291325 actions          (24 s wall clock for 28,800 ticks)
```

The same build at `7365e4e9` re-simulates the in-repo `demo.replay` (version 40) with
zero mismatches (`hash: 000000005DF98735 map 00000000482F35B6`, `replay: 105530/105530
actions`, 4.7 s for 10,911 ticks). At `7365e4e9` the hash also covers hero and footman
`velocity`, `attacksLanded`, `movePath`, `movePathIndex`, `moveRevision`, `stuckTicks`
and `targetBuildingId` (`sim.nim:3139-3204`), so any pathing or movement change in a
later commit diverges on the first tick a unit moves.

Two consequences:

1. **The correct ref is discoverable, not guessed.** `GET /v2/coworlds/{coworld_id}`
   returns `manifest.game.runnable.source_url` =
   `https://github.com/Metta-AI/polyworld/tree/<sha>/coworld/gota`. The build
   script should resolve `GOTA_REF` from that (with `episode.json.coworld_version`
   as the cache key), the way `CREWRIFT_REF`/`HEARTLEAF_REF` are pinned today but
   automatically. Polyworld's own policy is "older replays use their archived
   client; never add compatibility branches" (`replays.nim:13-14`), and the
   `replays.nim` history shows 12 commits in five days, so expect frequent
   re-pins.
2. **arm64 host builds are deterministic enough.** Map generation uses `float32`
   presets, yet the map hash and all 28,800 tick hashes matched. The
   `validateReplayWorld` check ([`sim.nim:1314`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L1314))
   also passed, so no Docker/amd64 is needed for expansion. (One sample; keep the
   hash check as the guard.)

Build recipe that worked (no credentials; `nim` 2.2.6 and `nimby` already in `~/.local/bin`):

```sh
curl -fsSL https://github.com/Metta-AI/polyworld/archive/$REF.tar.gz | tar xz -C src --strip-components=1
cd src && nimby --global sync nimby.lock          # writes nim.cfg with dependency paths
nim c -d:headless -d:release --out:../gota_headless examples/gods_of_the_arena/gota.nim
../gota_headless --replay replay.bin              # hash-checked; non-zero exit on divergence
```

Without writing into the source tree: the repo's `config.nims` reads `POLYWORLD_DEPS`
and adds every package named in `coworld/dependencies.lock` from that directory, so
`POLYWORLD_DEPS=~/.nimby/pkgs nim c -d:headless -d:release --nimcache:<scratch> --out:<scratch>/gota_headless <src>/examples/gods_of_the_arena/gota.nim`
builds from a read-only checkout (used for the `7365e4e9` verification above).

### 1.5 The other artifacts of an episode (verified)

| File | Content |
| --- | --- |
| `results.json` | `{"scores":[10 × 0/1],"ticks":28800,"seed":…,"outcome":"time_limit" \| "RedTeam" \| "BlueTeam","banked_gold":[],"returned":[],"total_xp":[10 ints]}` — [`gota.nim:9-14`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/gota.nim#L9), [`coworld.nim:41`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/coworld.nim#L41). `banked_gold`/`returned` are CTA fields, always empty for GotA. |
| `game_logs.log` | Container stdout. The `game` section is the headless summary: `replay saved … (N actions)`, `result`, `forts`, `towers` (standing `TowerBuilding`s only — barracks are not counted, `game.nim:201-206`), `hash`, `heroes` (levels), `economy` (team XP / unspent gold), `scripts: 10/10 active, N decisions` ([`game.nim:243`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/game.nim#L243)). Cheap end-of-game signal without touching the replay; note `scripts: 10/10 active` tells you whether any VM died. |
| Per-seat private log | Only for seats you own. Contains `Player slot N started.`, every BASIC `PRINT`, `BASIC error: …` on VM failure, `Player slot N completed.`; capped at 10 MiB ([`coworld.nim:141-175`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/coworld.nim#L141)). No tick stamps unless the policy prints `worldTick` itself. Fetch: `/v2/episode-requests/{id}/{policy_version_id}/policy-logs/{position}`. On the sample episode the `games-bond-gota` seat returned `403 You do not own this policy` under the current login — that policy belongs to the "Games Bond" player identity (see the `coworld-player-swap` skill). |
| Player artifact ZIP | Not produced by GotA (`integration.md`: "No player artifact ZIP is produced"). |
| `episode.json` | Discovery row: `participants[] {position, policy_name, version, policy_version_id, player_name, is_filler}`, `participant_scores`, `coworld_version`, `replay_url`, `inspect_url`. |

---

## 2. Inventory: what is recorded and what is recoverable

### 2.1 Directly in the file (no re-simulation)

- **Per action** (every BASIC action call, accepted or not): tick, hero id,
  kind, arguments. That gives command rates, target choices (object ids only —
  no positions), shop requests, item-use requests, cast requests, and the tick
  each hero's VM was still issuing calls (a VM that died stops appearing).
- **Per second, per seat**: CPU% (instructions used / 20,000 budget) in
  `metrics.frames`; lifetime average in `metrics.final`. `-1` = no VM (human seat).
- **Per episode**: seed, map preset, hero roster (id/team/slot/lane/class), player
  display names, recorded duration (`len(hashes)`), creation time.
- **Not in the file**: acceptance of any action, positions, HP, gold, XP, level,
  kills, deaths, damage, item inventories, ability charges, structure HP, fog.

### 2.2 Recoverable by re-simulation (verified reachable in `World`)

Re-running the sim gives complete authoritative state at every tick:

| State | Where | Notes |
| --- | --- | --- |
| Hero position (world units, `WorldScale = 60,000` per tile), facing, velocity, nav layer | [`Hero` `sim.nim:97`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L97) | `mapCoordinate(pos.x)` = tile x as BASIC sees it |
| HP/maxHp, mana/maxMana, level, xp, totalXp, gold, `attacksLanded` | `Hero` | `gold` is unspent; earned gold is `stats.values[slot][GoldMetric]` |
| State (`Marching/Fighting/Dying`), current targets (`targetFootmanId/HeroId/BuildingId`, `attackingFort`), `attackObjectId`, `attackMoving` | `Hero` | "what the engine is actually doing" vs. what the script asked |
| Movement: `movePath` (world-unit waypoints) + `movePathLayers`, `movePathIndex`, `moveTileX/Y`, `hasMoveTarget`, `moveRevision` (matches `world.navigationRevision` when the path is current), `stuckTicks` (path is dropped after `TickRate` stuck ticks, `sim.nim:1775-1778`) | `Hero` `sim.nim:133-140` | tells you whether a hero is on a valid path, waiting on a re-route, or stuck |
| Inventory (6 × item enum + counts), ability cooldowns/charges/recharges | `Hero` | |
| Footmen (id, team, lane, position, velocity, HP, state, target ids, `movePath`/`moveRevision`/`stuckTicks`) | `Footman` `sim.nim:67`; `UnitCap = 120 × CreepsPerBarracks = 360` (`sim.nim:404`) | |
| Buildings — towers **and barracks** (`kind`, id, team, lane, tier, position, HP/maxHp, target, `footprint` tiles, `occupied`, `knownAlive[team]`), forts (HP 400 at start of siege) | [`Building` `sim.nim:155`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L155), `Fort` `sim.nim:149`, `world.buildings` / `world.forts` | destroyed buildings stay in `world.buildings` with `hp ≤ 0`; `syncBuildings` (`sim.nim:972`) frees their footprint from `world.occupancy` and bumps `navigationRevision`, and the script-visible object list reports them dead (`buildingExposed`, `sim.nim:655`) |
| Live spell casts (`ability, heroId, targetId, origin, position, direction, started/impact/ends, resolved`) | [`SpellCast` `sim.nim:192`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L192), `world.casts` | includes the engine's **automatic** casts, which are never in the tape; what a given bot could see of them is `observations.nim` `spellVisible` (only while pending, `started ≤ tick ≤ impact`; own team's casts, or enemy casts whose `position` is in team vision) |
| Team vision / explored tiles | `world.teamVisible`, `teamExplored` `sim.nim:226-227`, rebuilt each tick ([`rebuildVision` `sim.nim:542`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L542)) | lets you say "was the target visible when the script attacked it" |
| Team-known building state / footprint walkability | `Building.knownAlive`, `knownWalkable` `sim.nim:1015` | `terrainWalkable` as BASIC sees it keeps a building's footprint blocked until that team has seen the building destroyed (`knownAlive[team]`), so a script may path around a barracks that is already gone |
| Combat counters per seat: gold earned, kills, deaths (`LossesMetric`), assists (10-second damage window) | `world.stats` (`CombatStats`, [`metrics.nim:81` `hitHero`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/src/polyworld/metrics.nim#L81)) | hashed with the world, so guaranteed to match the live game |
| `teamHeroKills`, `teamHeroDeaths`, `gameOver`, `winner` | `World` | |
| Accepted commands per seat (APM) | `game.metrics.command` in `tickWorld` | acceptance is recomputed on playback |

Reward accounting is exact and event-attributable at the hit site
([`applyHeroHit` `sim.nim:2237`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L2237)):
footman kill +25 XP/+15 gold, hero kill +150/+100, building kill +100/+75 — the
same `TowerXpReward`/`TowerGoldReward` for a tower **or a barracks**, since both are
`Building`s (`sim.nim:387-392`, `2264-2269`); fort damage awards nothing. Spell strikes go through the
same proc ([`hitSpellTarget` `sim.nim:2414`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/sim.nim#L2414)),
so spell kills are credited. Tower and footman damage to heroes calls
`hitHero(-1, …)` (`sim.nim:2017`, `2161`) → a death with no killer seat.

### 2.3 Existing prior art in polyworld (verified)

Polyworld already ships a minimal re-sim extractor for its tournament tool:
[`tools/herostats.nim:157` `inspectReplay`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/tools/herostats.nim#L157)
+ [`tools/inspect_players.nim`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/tools/inspect_players.nim).
It replays the tape, refuses any hash mismatch, cross-checks `scores()` against
`participant_scores`, and emits **end-of-game** per-seat JSON: `hero, class,
team, win, draw, level, xp, xp_progress, gold (earned), banked_gold, kills,
deaths, assists`, then derives `tower_kills` and `last_hits` from the exact
reward arithmetic ([`tournaments.nim:274` `objectiveCounts`](https://github.com/Metta-AI/polyworld/blob/7365e4e9390e97bbc8b7722b41a2ae88ae008c2b/examples/gods_of_the_arena/tools/tournaments.nim#L274));
since barracks pay the tower reward, `tower_kills` counts barracks too.
`inspect_players.nim:9-11` accepts replay versions 26–33 and 40.
It is end-state only (no timeline, no positions, no events), and it imports
`curly`/`zippy`/`jsony` for the tournament runner. It is the right thing to
**copy the loop from**, not to depend on.

---

## 3. What is not recorded (gaps that limit analysis)

1. **No per-tick state in the file.** Anything beyond command counts needs the
   version-matched re-sim (§1.4). If a ref cannot be built (e.g. a source commit
   that no longer exists, or a private dependency change), the replay is opaque
   apart from the action stream.
2. **Requests, not effects.** `attackTarget(id)` in the tape does not mean the
   hero hit, or even approached, that target. Acceptance must be recomputed
   (the re-sim does this — `applyReplayAction` returns the accepted flag), and
   *effect* must be read from world state afterwards.
3. **No damage or healing totals.** GotA never writes `DamageMetric`,
   `HealingMetric`, `ArmyMetric`, `BankedMetric` or `StructuresMetric` (grep of
   `examples/gods_of_the_arena/*.nim`; polyworld's own report says "Damage and
   healing totals are not tracked … and are not reported as zero"). They are
   trivially recoverable by hooking `applyHeroHit` in the expander, but nothing
   in the file or the stock tools has them.
4. **No per-hit attribution for tower/creep damage.** `hitHero(-1, …)` records a
   hero death by a non-hero source without saying tower vs footman; the expander
   sees the call site, so it can tag it.
5. **Automatic ability casts are invisible in the tape** (they happen inside
   `updateHero` → `tryCastAbility`). Recoverable from `world.casts` during
   re-sim only.
6. **No policy-side reasoning.** PRINT output is private to the seat owner and
   only for seats you own; other entrants' logic is opaque. For our own seats,
   the private log is the only "why" channel and it has no tick stamps unless
   the policy prints `worldTick`.
7. **No wall-clock or scheduling data** beyond `createdUnixMs`.
8. **Visibility is derivable but not recorded.** Whether an enemy was visible to
   a hero at a tick has to be recomputed from `teamVisible`; nothing in the tape
   says what the script saw.
9. **Tooling mismatch in the shared downloader.** `fetch_artifacts.py` labels the
   binary tape `replay.json`; anything that assumes JSON breaks. A `replay_bytes`
   sniff (magic `POLYWORLDREPLAY`) or a game adapter hook is needed.

---

## 4. Replay tools: current contract

This section describes the implemented tools, verified against polyworld
`f2ab9598d8f8001b6beae3e66404e341770c803f`, league release `2026.9.16.5`, replay game
version **41**. The earlier investigation sections describe older releases; this
section defines the tools' current output. No simulator patches are used.

### 4.1 Tools and commands

| Tool | Purpose |
| --- | --- |
| `tools/build_expand_replay.sh [COMMIT]` | Fetch the recording commit, run its dependency sync, compile the Nim expander inside the clone; print `tools/bin/expand_replay-<sha8>`. An omitted commit resolves the currently deployed release, which may be wrong for an older recording. |
| `tools/expand_replay.nim` | Layer A: authoritative replay, per-tick hash checks, events, optional sampled state, final seat counters. |
| `tools/replay_actions.py` | Layer B: stdlib Python tape/CPU decoder that does not require a simulator build. |
| `tools/replay_stats.py` | Layer C: episode/batch expansion, verified cache, per-seat/per-team Markdown and JSON tables. |
| `tools/viz_replay.py` | Layer C: sampled hero paths or occupancy heatmaps on the tile grid; Pillow is its only non-stdlib Python dependency. |
| `tools/test_replays.py` | Focused decoder, hosted action parity, statistics, and cache tests. |

Run from the repository root. Select the **recording commit**, not automatically the
latest engine. Replays are version-coupled even when the declared game version does
not change. The build cache rebuilds when the expander source or build script changes.

```sh
# This commit records league release 2026.9.16.5.
gods_of_the_arena_lab/tools/build_expand_replay.sh f2ab9598d8f8001b6beae3e66404e341770c803f

gods_of_the_arena_lab/tools/bin/expand_replay-f2ab9598 EPISODE/replay.json expanded.jsonl \
  --episode EPISODE/episode.json --snapshot-every 240

# No engine needed. Emits meta, action and CPU JSONL to a file or stdout.
uv run python gods_of_the_arena_lab/tools/replay_actions.py EPISODE/replay.json --out actions.jsonl

# Fast reward-only analysis is the default. Accepts one episode or a batch root.
uv run python gods_of_the_arena_lab/tools/replay_stats.py BATCH \
  --ref f2ab9598d8f8001b6beae3e66404e341770c803f --json stats.json
# Alternatively: --binary gods_of_the_arena_lab/tools/bin/expand_replay-f2ab9598
# --json without a filename prints JSON instead of Markdown to stdout.

# Generate spatial samples, then render them.
uv run python gods_of_the_arena_lab/tools/replay_stats.py EPISODE \
  --ref f2ab9598d8f8001b6beae3e66404e341770c803f --snapshot-every 240
uv run python gods_of_the_arena_lab/tools/viz_replay.py EPISODE/expanded-replay.jsonl paths.png
uv run python gods_of_the_arena_lab/tools/viz_replay.py EPISODE/expanded-replay.jsonl heatmap.png \
  --mode heatmap --slot 3
```

The file named `replay.json` may contain raw `POLYWORLDREPLAY` bytes. Both decoders
also accept gzip-wrapped bytes. Layer B explicitly supports the verified Flatty64
container-1/payload-5 layouts for game versions 33, 40 and 41; unknown versions fail
rather than assuming an unchanged layout. It is engine-independent, **not** an
unconditionally future-compatible format reader.

### 4.2 Layer A rows and attribution

One JSON object per line. Every row has `type` and `tick`; seat-tagged rows carry
platform `slot` (0–9) and `hero_id`. `type` is `meta`, `action`, `event`, `state` or
`summary`. Event/state rows use string `kind`; action rows use the numeric tape kind.
Positions contain tile `x`, `y` (`mapCoordinate`) and raw `world_x`, `world_y`,
`world_z`. Raw vertical height is `world_y`; tile `y` corresponds to world `z`.

| Row | Fields and meaning |
| --- | --- |
| `meta` | `schema_version=2`, source path, game version, seed, recorded ticks, tile-grid size, tick rate, world scale, roster, `actions_enabled`, and damage availability. |
| `action` | Every consumed tape request in tape order: `action_index`, numeric `kind`, two `args`, `accepted`. |
| `hero_death` / `hero_respawn` | Seat, position and `killer_slot`; `-1` means no hero credited, null means ambiguous. Death also includes `heroes_before`: every seat's HP and position immediately before the death tick. Respawn retains the preceding death's killer. |
| `hero_kill` / `assist` | One row per credited kill/assist. Kill rows carry victim seat/hero ID when known, otherwise null plus candidate victim seats. |
| `last_hit` | One row per footman last hit, from exact XP/earned-gold reward decomposition. |
| `building_kill` | One row per credited building reward. `building_kind=tower|barracks`, target team, lane, tier, object ID and god-guard status where known. Ambiguous victim fields are null and candidates are retained. |
| `building_destroyed` | The destroyed building's actual ID, target team, lane, kind, tier, HP and position, including buildings killed by nonheroes. Barracks tier is null. |
| `fort_damaged` / `fort_destroyed` | Fort ID/team/position, previous and current HP, net HP loss. Forts do not have lane/tier. |
| `level_up` | Previous and resulting level; a multi-level gain is one row. |
| `gold_spent` / `item_bought` / `item_used` | Accepted transactions, item ID/name, cost, and inventory slot for use. Use costs zero additional gold. Inventory reconstruction is checked against the engine. |
| `cast` | Ability ID/name, ability slot, target/point, origin, complementary `explicit`/`auto` booleans when actions are enabled. Both are null in reward-only mode. |
| `ambiguous_attribution` | Subject, seat or victim, candidate killers/victims and exact count constraints. Building candidates include kind/team/lane/tier/ID. Candidate lists are possibilities, not independent credited kills. |
| `damage` | Reserved, **no rows emitted**. `meta.damage.available=false`: per-attacker damage/healing awaits [engine change request 14](requested-game-changes.md). Net sampled HP changes are not an attribution substitute. |
| `state`, `kind=objective_state` | All heroes (resources, state, target IDs, inventory/counts, cooldowns/charges/recharges, path length/index, stuck ticks), buildings/forts and living footman counts by team/lane. |
| `state`, `kind=visibility` | One row per seat: visibility-filtered object list, team-visible tile grid, visible enemy hero/structure counts, and the engine's `visibleSpellCount`. |
| `summary` | Final tick/outcome, recorded action count, hash/result verification, attribution completeness and the `heroes` array described below. |

`--snapshot-every N` emits state at positive multiples of N. Default 0 disables
samples; negative N is rejected. Visibility samples are taken at the end of the tick
using the engine's existing visibility map; they are not exact decision-time traces.

**Action acceptance.** `tickWorld` increments the command metric iff its private
`applyReplayAction` returns true. All-accepted/all-rejected per-seat deltas identify
individual results directly. Mixed outcomes use successive action-prefix replays
from the same checkpoint through unchanged `tickWorld`. Adjacent prefix command
counts give exact booleans. The full prefix must match the authoritative tick's state
hash and command counts. Diagnostic prefixes have no recorded hashes; the actual
playback verifies every recorded hash. `manualSpells` can change its flag while
returning false; the tool reports that engine return value faithfully.

`--actions` is Layer A's default. `--no-actions` skips acceptance probes and omits
action/item-transaction rows; casts remain but their explicit/auto classification is
null. Reward counts, structural/lifecycle events, snapshots and hash checks remain
available. `replay_stats.py` chooses `--no-actions` unless passed `--actions`.

**Ambiguity never discards a hash-verified replay.** Reward decomposition gives exact
per-seat footman, hero and total building kills. Multiple same-tick kills can obscure
which victim belongs to which seat. Those identities are null, candidates are emitted,
and `attribution_complete=false`; `verified` still reflects only successful replay
hash verification. Same-kind building candidates preserve exact tower/barracks counts.
If both kinds are candidates and the split cannot be recovered, the affected split is
null with `tower_kills_min/max` and `barracks_kills_min/max`. These are exact feasible
bounds, not point estimates. `building_kills` always retains the exact combined count.

### 4.3 Summaries, tables and caches

Each `summary.heroes` row has `slot`, `hero_id`, team/class, `last_hits`, `hero_kills`,
`building_kills`, `tower_kills`, `barracks_kills`, deaths, assists, XP, `gold_earned`,
`banked_gold`, level and `win` (the actual engine seat score, not inferred from XP).
`--episode episode.json` joins policy/player names by participant position; without
it, player names come from the tape and policy names are null. Heroes start with 150
gold, excluded from earned gold: `150 + gold_earned - gold_spent = banked_gold`.

The expander checks:

```text
25*last_hits + 150*hero_kills + 100*building_kills == xp
building_kills == tower_kills + barracks_kills   # whenever the split is known
```

A sibling `results.json`, when available, is checked for ticks, seat count, scores
and XP; `results_verified` records whether that external check occurred. The stats
reader requires it. Hash mismatches exit nonzero with a failed summary. Incomplete
outputs are not evidence: direct binary runs can leave partial JSONL, so require a
final `verified=true` summary. Reward/inventory/result inconsistencies also fail;
ambiguous pairings alone do not.

`replay_stats.py` reports per-seat and per-team tables for every episode. Columns
include last hits, hero/building/tower/barracks kills, deaths, level, XP and XP per
1,000 final ticks, with policy names. Seat rows additionally have their share of the
team's footman last hits and rank by last hits within that team. Rank uses competition
ties (1, 1, 3); a team with zero footman last hits has null shares. Team level is the
mean seat level. Unknown splits remain null/`unknown`, never zero. JSON also retains
bounds and all summary seat fields. Team bounds sum seat bounds and can be conservative
because seat assignments are correlated.

With event-time `heroes_before` data, seat rows also report the fraction of enemy
deaths within 12 tiles of that living seat, and the fraction of those deaths credited
to that seat. Tile distance is Euclidean on the map's integer coordinates. Missing
event positions or ambiguous nearby killer attribution leave the corresponding
fraction null. Old expansions must be regenerated with the rebuilt binary for this
measurement. Comparison averages per-episode fractions, so a pooled event fraction
can differ.

Reports use `episode.json.id` for episode identity, not the artifact-directory name.
`compare.py --baseline-replay-stats BASE.json --candidate-replay-stats CAND.json`
and `miner_rows.py --replay-stats REPORT.json` join exact combat counts by episode
ID and seat position. The join checks outcome, duration, policy name and XP; duplicate
episodes/seats or mismatches fail. Exact comparison mode leaves unavailable combat
metrics missing rather than falling back to telemetry. The miner excludes seats
without exact counts when a replay report is requested. Private logs remain the
source of policy activation counters and first-occurrence estimates.

Artifact discovery accepts one directory or recursively finds episode directories
under a batch. It recognizes `replay.bin`, `replay.json`, `replay`, then `replay.json.z`.
Missing or failed artifacts appear in an explicit `errors` list and cause exit 1;
available episode tables are still returned. No failed episode silently disappears.

Caches are `expanded-replay.jsonl` and `expanded-replay.cache.json` beside the replay.
Their signature covers replay, results, optional episode metadata, binary contents,
action mode and snapshot interval. The receipt also checksums the expanded output.
Changed inputs/options or incomplete/corrupt caches trigger regeneration; `--refresh`
forces it. Only a verified expansion is atomically promoted to the cache. The JSON
report exposes `cache_hit` for each episode. Earlier files named `expanded.jsonl` are
not overwritten or treated as validated caches.

### 4.4 Visualization and downstream integration

`viz_replay.py` accepts expanded JSONL and a PNG output. `--mode paths|heatmap`,
repeatable `--slot N`, and `--scale` select the display. It requires both sampled
objective state and a verified summary. Path lines break at death/respawn events.
Heatmaps count living hero samples per tile (square-root color scale), not exact
continuous dwell time. Coordinates increase right/down. Buildings are marked T/B/F
for tower/barracks/fort. The background is a tile grid, not recovered terrain.

The existing `compare.py` and `features.py` adapters still read their existing hosted
results/log/LH inputs. They have **not** been wired to the expander in this task. A
future integration can join `(episode_id, slot)` while preserving attribution-null
fields and coverage failures. Damage-dealt/healing features remain unavailable.

### 4.5 Validation and operational limits

Run the focused checks, optionally including actual Layer A action parity:

```sh
GOTA_TEST_REPLAY=EPISODE/replay.json GOTA_TEST_EXPANDED=FULL_EXPANSION.jsonl \
  uv run python -m unittest discover -s gods_of_the_arena_lab/tools -p test_replays.py
```

Layer B validates container/layout boundaries and preserves action order and padding;
its `accepted=null` and `hash_verified=false` are intentional. Stored hashes and CPU
telemetry are decoded, not recomputed. Layer A must use the recording engine; passing
Layer B cannot certify replay determinism. Unsupported layouts fail explicitly.

Expansion is synchronous and uses one process per replay; engine module globals make
multiple games in one process unsafe. Batch statistics run sequentially and benefit
from cache reuse. Action probes can materially increase expansion time; use the fast
reward-only path unless action acceptance or item/cast classification is needed.

---

## 5. Open questions

1. **Ref resolution for old episodes.** `/v2/coworlds/{id}` gives the *current*
   version's source commit. For an episode with an earlier `coworld_version`
   we need a version→commit map; polyworld keeps receipts in
   `coworld/releases/*.json` (`source_commit`), but only for some releases — at
   `7365e4e9` the latest is `2026-09-14-gota.json` (`2026.9.14.1`); neither
   `2026.9.15.1` (`5422fb0c`) nor `2026.9.16.3` (`7365e4e9`) has one. Cache
   `(coworld_version → sha)` locally as we observe them and fail loudly otherwise.
2. **Damage attribution** is blocked on [requested game change 14](requested-game-changes.md): damage and heal events recorded by the engine. Still open at the deployed `7365e4e9`: the tape holds only `actions`, `hashes` and CPU `metrics` (`tapes.nim:190-198`), nothing under `examples/gods_of_the_arena/` writes `DamageMetric` or `HealingMetric`, and `herostats.nim:428` still reports "healing totals are not tracked … and are not reported as zero". The observation expansion (`e127989`, in this build) added observations, not event recording. Until then `damage` is a stub and `features.py` must not build on "damage dealt".
3. **The Nim expander is required for the first iteration** (decided 2026-09-15). Layer B alone gives aggregates and unaccepted action requests, which is not enough. The expander must also emit, per tick and per bot, what that bot could observe under fog of war (the team visibility map and the visibility-filtered object list) so we can tell what each policy knew when it acted.
4. **Own-seat private logs**: the `games-bond-gota` seat is owned by the
   "Games Bond" player identity; the current `softmax login` is not that
   identity (403 "You do not own this policy"). Confirm which identity owns the
   policy we will iterate on before relying on PRINT traces.
5. **Downloader naming.** Fix `fetch_artifacts.py` to sniff the
   `POLYWORLDREPLAY` magic and save as `replay.bin` (or leave `replay.json` and
   document it). Small, shared-skill change — needs a go-ahead because it
   touches the root tooling.
6. **arm64 determinism** is verified on one episode. Keep the hash check
   mandatory; if a mismatch ever appears with the correct ref, that is the
   signal to fall back to a `linux/amd64` container build.
