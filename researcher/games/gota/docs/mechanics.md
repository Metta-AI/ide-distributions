<!-- Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/docs/wiki/mechanics.md -->

> Historical source reference. This file describes the pinned September source.
> See [toolkit provenance](../PROVENANCE.md) for verified live replay adaptations.

# Gods of the Arena — mechanics and reference

See [overview](https://softmax.com/gods-of-the-arena/wiki/overview), [illustrated kits and items](https://softmax.com/gods-of-the-arena/wiki/game-guide), and
[policy and host surface](https://softmax.com/gods-of-the-arena/wiki/policy-and-host-surface).

## Map, teams and objects

Default map size is 116×116, with even sizes 64–256 accepted by map generation.
Map seed 54 is distinct from match randomness. Read `mapWidth`, `mapHeight`,
`mapLayers` and `selfLayer`. `walkTo(x,y)` clamps coordinates to the map and
uses engine pathfinding; `castPoint(slot,x,y)` rejects coordinates outside the map,
then limits valid ground aim to spell range. Neither call accepts a destination layer.
Static terrain queries have explicit-layer variants, work through fog, and describe
terrain rather than guaranteed path reachability or occupancy by units/towers.

Teams are Red = 0, Blue = 1. Seats 0–4 are Red, seats 5–9 Blue.

| Class ID | Hero | Team |
| --- | --- | --- |
| 0 | Vanguard Knight | Blue |
| 1 | Ranger | Blue |
| 2 | Arcanist | Blue |
| 3 | Druid Warden | Blue |
| 4 | Demon Hunter | Blue |
| 5 | Death Knight | Red |
| 6 | Crossbowman | Red |
| 7 | Lich | Red |
| 8 | Warlock | Red |
| 9 | Berserker | Red |

Object kinds: 1 fort, 2 hero, 3 footman, 4 tower, 5 barracks. Non-heroes have class -1.
Allied objects are included; enemy objects require team visibility. Destroyed
towers and barracks leave the object list. Object-list indexes are temporary;
actions use stable object IDs.
Tower `objectAlive` requires positive HP and exposure: outer → inner → gate in
each lane. A barracks is exposed only when all three of its lane's towers are
down. The fort becomes exposed when any one lane has no surviving towers.
Its alive flag includes that exposure condition. This is a targeting constraint,
not evidence that a protected structure has died.

Each team has two barracks per lane (six in total, `generation/maps.nim`
`placeBarracks`). Every 480 ticks (20 s, `DefaultSpawnIntervalTicks`) each
surviving barracks spawns three footmen (`CreepsPerBarracks`), so a lane sends
six per team per wave. Barracks do not attack. Destroying one stops its footmen.

## Economy and recovery

Heroes start at level 1 with 150 gold. Maximum level is 20; the XP requirement for
each next level is `100 + (level - 1) * 75`. Hero-attributed killing hits award:

| Victim | XP | Gold |
| --- | --- | --- |
| Footman | 25 | 15 |
| Hero | 150 | 100 |
| Tower or barracks | 100 | 75 |

Footmen have 60 HP and 12 base damage; tower HP is 950/1,300/1,950 for outer/inner/gate
(`TowerHitPoints`, sim.nim) with tower damage 18/24/30; barracks HP is 950 (the outer-tower
value); fort HP is 400.
These rewards are not an automatic whole-team payout for every kill.

Inventory has six slots (0–5). Consumables stack to eight; a full existing stack
rejects another purchase, even with an empty slot. Equipment duplicates are rejected.
Buying requires a living hero, gold and inventory capacity, with **no shop-distance
or minimum-level condition**. Held equipment applies immediately; `useItem` is for
consumables and rejects equipment. Increasing maximum HP/mana also increases the
current value by the same delta in `refreshHeroStats`.

Consumables: ration (ID 1) heals 40 for 30 gold; elixir (2) heals 90 for 50;
mana potion (3) restores 60 for 45; poison (4) deals 35 for 40.
Healing/restoration caps at maximum and rejects a full resource. Poison requires
the hero's selected enemy to be hittable within basic-attack range, including
visibility and structure exposure. See the [guide](https://softmax.com/gods-of-the-arena/wiki/game-guide) for equipment IDs' names
and bonuses; the enum in content source gives IDs 5–20.

Respawn retains level, inventory, gold and script memory; restores HP/mana and
clears movement/attack state. Abilities start the new life fully charged. The death
sequence waits 24 animation ticks plus 192 respawn ticks (9 simulated seconds
counted after entering Dying). Living hero updates regenerate one mana every six
ticks, capped at maximum.

## Combat and spells

Basic attacks cost no mana and use no spell charges. They are separate from four
ability slots: 0 passive/Q, 1 primary/W, 2 secondary/E, 3 ultimate/R. The names do
not prohibit explicit casting of slot 0. `castTarget` and `castPoint` expose all four.
BASIC bots also use automatic casting: the combat path tries the passive, then the
ultimate, secondary and primary until one of the latter three succeeds.
Human-control mode disables that automatic spell path; the BASIC host exposes no
toggle for it.

Ability availability depends on mana, charges and cooldown, not level unlocks.
Cooldown and recharge queries return **ticks**, while guide tables show seconds.
Charges recharge one at a time; spending another charge does not reset a running
recharge timer. Cast acceptance consumes resources; delayed impact checks eligible
targets later. Valid ground shots can miss. An accepted target order does not prove
that a hit occurred. Replay command records likewise capture attempts before acceptance.

Use effective `abilitySpec` results, not only `BaseAbilitySpecs`: primary overrides
set three charges, 2-second cooldown and 12-second recharge for nine listed attacks;
Molten Fist's effective mana cost is zero.

## Movement and scoring

Living towers and barracks occupy their footprint tiles, which block pathing for
every unit on that layer; a destroyed building frees its tiles at once. A unit
whose ordered move makes no progress for 24 ticks (one second) has its path
recomputed. `terrainWalkable` also reports building footprints, but only as your
team last saw them: an enemy building destroyed out of your sight still reads as
blocked until your team sees it (`knownWalkable`).
Footmen skip route waypoints already passed after a combat detour.

The game returns score 1 to winning-team seats and 0 otherwise. An unfinished fort
contest at the time limit returns ten zeros. `total_xp` is separate output.
Consult the active league for its rating and ranking rules.

Sources: [simulation](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/sim.nim), [effective content](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/content.nim),
[host](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/bots.nim), [terrain](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/terrains.nim),
[map configuration](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/generation/configs.nim).

---
Maintained by Codex, an automated agent working for James Boggs.
