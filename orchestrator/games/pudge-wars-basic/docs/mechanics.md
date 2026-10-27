<!-- Source: Metta-AI/polyworld-pudge-wars @ cd8111a5ee4f2fdc38c027615b33421f6c7d2536 -->

> **Provenance.** Pudge Wars Basic: Coworld `pudge-wars-basic` 0.1.8
> (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), whose manifest runnable `source_url` pins
> `Metta-AI/polyworld-pudge-wars` at `cd8111a5ee4f2fdc38c027615b33421f6c7d2536`. Written
> 2026-10-26 (PDT) from that source. Citations are `path:a-b@cd8111a5`. Where the upstream
> prose and the code disagree, the code wins; see [Stale upstream docs](#stale-upstream-docs).
> Re-verify when the manifest's `source_url` commit changes (`kb renew`).

# Pudge Wars Basic — mechanics

Units: 1 tile = 60,000 world units (`WorldScale`); 1 s = 32 ticks
(`examples/pudge_wars/content.nim:11-11@cd8111a5`, `examples/pudge_wars/content.nim:41-41@cd8111a5`).
Values in tiles or seconds below are derived from the raw constants by those factors.

## What the league actually runs

- The hosted image runs `pwleague`, which is `examples/pudge_wars/coworld_host.nim` built with
  `-d:basicLeague` (`coworld/Dockerfile:35-37@cd8111a5`). In that mode the JSON `/player`
  WebSocket route is not registered (`examples/pudge_wars/coworld_host.nim:1352-1353@cd8111a5`):
  every seat is a runner-staged BASIC file (or ZIP package) loaded into its own Bassy VM
  (`examples/pudge_wars/fileplayers.nim:154-180@cd8111a5`, `examples/pudge_wars/bots.nim:698-714@cd8111a5`).
- A separate, platform-hosted Coworld called `pudge-wars` ("Pudge Wars 1v1", Docker/JSON
  players) shares this repository. It is a different game; this folder does not cover it, and
  its JSON surface (`walk_to`, `hook_to`, …) is unreachable in `pwleague`.
- The match loop is `tickWorld(game, runBotDecisions)` until `tick >= max_ticks` or game over,
  with no wall-clock pacing (`examples/pudge_wars/coworld_host.nim:1366-1383@cd8111a5`).
- The league config validator requires `tokens`, `players`, an int32 `seed`, `max_ticks` in
  1..40000, `kill_goal` in 1..40, and `fog` not false (`examples/pudge_wars/coworld_host.nim:1242-1256@cd8111a5`);
  seat count must be 2, 4, 6, 8 or 10 (`examples/pudge_wars/coworld_host.nim:1276-1282@cd8111a5`).
- Authoritative rules: `examples/pudge_wars/sim.nim` (tick and rules), `content.nim`
  (constants), `maps.nim` (terrain). BASIC host API: `bots.nim`.

## Variants

| Variant | Seats | kill_goal | max_ticks | Notes |
|---|---|---|---|---|
| `league-3v3` (the league) | 6: slots 0–2 West, 3–5 East | 30 | 38,400 (20 min) | fog on |
| `duel-1v1` | 2: slot 0 West, slot 1 East | 10 | 15,360 (8 min) | same 3-lane map; duelists use team slot 1 |

Source: `coworld/coworld_manifest_template.json:282-335@cd8111a5`,
`examples/pudge_wars/sim.nim:1588-1611@cd8111a5`, `examples/pudge_wars/coworld_host.nim:1283-1290@cd8111a5`.
Variants default to `seed: 2026`, but hosted league episodes receive a platform-injected
per-episode seed (192 of 192 sampled league episodes on 2026-10-26 had distinct seeds).

## Time and decision order

- `DecisionTicks` is 1 and the league never changes `botEvery` from 1: **every living seat's
  BASIC program runs every tick** (`examples/pudge_wars/content.nim:39-39@cd8111a5`,
  `examples/pudge_wars/sim.nim:5256-5266@cd8111a5`, `examples/pudge_wars/bots.nim:839-854@cd8111a5`).
  `botAimNoise` is 0, so there is no aim scatter (`examples/pudge_wars/bots.nim:260-283@cd8111a5`).
- Seats decide in rotation: the first seat advances by one each tick. Each action call
  (`walkTo`, `hookTo`, `blinkTo`, …) is applied to the world immediately inside the call
  (`examples/pudge_wars/bots.nim:245-310@cd8111a5`), so seats later in a tick's rotation see the
  effects of earlier seats. Dead (`Dying`) seats and seats whose VM failed do not run
  (`examples/pudge_wars/bots.nim:716-724@cd8111a5`).
- Tick order (`examples/pudge_wars/sim.nim:5138-5203@cd8111a5`): decisions → movement → hook
  windups release → hooks fly, latch, clash and reel → drowning → Rot → Lightning fire →
  runes → fountains → melee → props → cooldowns, buffs, respawns → `checkWinner`.
- The hook cooldown is 65 ticks, not 64, on purpose: a 64-tick cycle phase-locked the rotation
  to one bank, which then won every same-cycle hook or rune race
  (`examples/pudge_wars/content.nim:32-37@cd8111a5`).

## Map

- 128 × 128 tiles. x runs west→east; y (sim z) runs north→south.
- River centre column x = 64. Side-lane water is x 53..75; the banks pinch in near the middle
  (|y−64| < 16), so at y = 64 the water is only x 59..69 (`examples/pudge_wars/maps.nim:29-42@cd8111a5`,
  `examples/pudge_wars/maps.nim:121-139@cd8111a5`).
- Playable West bank x 14..52, East bank x 76..114, y 16..111 (`examples/pudge_wars/maps.nim:43-51@cd8111a5`).
  BASIC reads `riverWest` = 52 and `riverEast` = 76, the side-lane lips
  (`examples/pudge_wars/bots.nim:786-787@cd8111a5`).
- Fountain island at (64,64), radius 3, unstandable; hooks bounce off a 3.5-tile cylinder
  (`examples/pudge_wars/maps.nim:57-59@cd8111a5`, `examples/pudge_wars/sim.nim:3922-3932@cd8111a5`).
- Shop pads West (22,64), East (106,64), reach Chebyshev ≤ 4 (`examples/pudge_wars/maps.nim:60-65@cd8111a5`).
- **Walking cannot cross the waterline.** A bank walker stands only on its own layer's bank
  tiles; a river walker only on river tiles. You enter or leave the river only by Blink, by
  being hooked or dumped (`examples/pudge_wars/sim.nim:900-911@cd8111a5`, `examples/pudge_wars/sim.nim:3499-3504@cd8111a5`).
  A walk target your layer cannot stand on snaps to the nearest standable tile within
  Chebyshev 24 (`examples/pudge_wars/sim.nim:1847-1893@cd8111a5`).
- An enemy hooked onto **your** bank takes your bank's layer and can walk there
  (`examples/pudge_wars/sim.nim:1498-1522@cd8111a5`).
- 8 seeded trees per bank (mirrored), plus seeded impassable low patches; hooks bounce off
  trees and blocked land (`examples/pudge_wars/maps.nim:557-612@cd8111a5`, `examples/pudge_wars/maps.nim:533-534@cd8111a5`).
- Bodies are solid circles of radius 2.6 tiles: living, unhooked butchers on the same layer
  stay ≥ 5.2 tiles apart (`examples/pudge_wars/content.nim:237-243@cd8111a5`).

## Teams, seats and spawns

- For 2N seats, slots 0..N−1 are West and N..2N−1 East; butcher id = slot + 1
  (`examples/pudge_wars/sim.nim:1588-1611@cd8111a5`).
- Spawn lanes {40, 64, 88} are shuffled per seed; each West pad gets x ∈ [18, 36], y = lane ± 10;
  East pads mirror West (x = 128 − x). Slot i and slot i+N share a lane
  (`examples/pudge_wars/maps.nim:262-310@cd8111a5`). Read `homeX`/`homeY`; never hard-code pads.
- Respawn after 128 ticks (4 s) at the home pad with full HP and a 64-tick spawn shield
  (`examples/pudge_wars/content.nim:31-31@cd8111a5`, `examples/pudge_wars/content.nim:48-50@cd8111a5`).
  The shield blocks hooks, melee, Rot, fire and Lightning but **not mines or drowning**, and
  any act (walk, hook, blink, Rot toggle, attack order, prop) breaks it
  (`examples/pudge_wars/sim.nim:1818-1836@cd8111a5`).

## Butcher

| Stat | Value | Source |
|---|---|---|
| HP | 1,200 base; +35 max HP per credited enemy kill | `examples/pudge_wars/content.nim:232-235@cd8111a5` |
| Walk speed | 32,400 units/tick ≈ 17.3 tiles/s; ~8 ticks to top speed, ~6 to stop | `examples/pudge_wars/content.nim:246-255@cd8111a5` |
| Melee | 28 damage every 19 ticks (~47 DPS), reach ≈ 5.45 tiles; BASIC must order it with `attackTarget` | `examples/pudge_wars/content.nim:236-236@cd8111a5`, `examples/pudge_wars/sim.nim:3752-3788@cd8111a5` |
| Upgrade points | +1 per credited enemy kill | `examples/pudge_wars/sim.nim:1760-1763@cd8111a5` |

Speed modifiers: +8% per River Treads rank, +40% Haste, −40% inside any Rot disk (does not
stack) (`examples/pudge_wars/sim.nim:1376-1382@cd8111a5`).

## Meat Hook

| Property | Level 0 | Per upgrade level (cap 10) | Source |
|---|---|---|---|
| Cooldown | 65 ticks, from cast acceptance | — | `examples/pudge_wars/content.nim:32-37@cd8111a5` |
| Windup | 9 ticks; the caster **coasts**, not rooted | — | `examples/pudge_wars/content.nim:42-47@cd8111a5`, `examples/pudge_wars/sim.nim:3471-3497@cd8111a5` |
| Damage | 200 | +15 (Damage) | `examples/pudge_wars/content.nim:256-259@cd8111a5` |
| Range | 60 tiles | +4 (Distance) | `examples/pudge_wars/content.nim:260-266@cd8111a5` |
| Launch speed | 0.60 tiles/tick, +1,800 units every flying tick, cap 1.8 tiles/tick | +1,800 units (Speed) | `examples/pudge_wars/content.nim:267-276@cd8111a5` |
| Grab radius | 2.75 tiles (latch = radius + 2.6 body) | +0.125 (Radius) | `examples/pudge_wars/content.nim:277-281@cd8111a5` |

- `buyUpgrade(k)` (0 Damage, 1 Distance, 2 Speed, 3 Radius) costs one point and works
  anywhere (`examples/pudge_wars/sim.nim:2520-2534@cd8111a5`).
- A cast is rejected when dead, on cooldown, winding up, with a hook already out, off-map, or
  at your own position. On acceptance it breaks the spawn shield, starts the cooldown, drops
  the walk order and **increments `hooksThrown`** (`examples/pudge_wars/sim.nim:2588-2627@cd8111a5`).
- Hooks fly over water and **bounce** off map edges, blocked land, palisades, trees and the
  fountain without regaining range (`examples/pudge_wars/sim.nim:4032-4259@cd8111a5`).
- The first body the sweep touches is latched: any butcher except the caster, alive and
  unshielded, **ally or enemy** (`examples/pudge_wars/sim.nim:4376-4482@cd8111a5`).
  Enemy: damage at latch time, the victim is reeled to the caster. Enemy already held by
  another hook: **headshot**, an instant kill credited to you. Ally: +120 HP and you reel them
  (a save) (`examples/pudge_wars/content.nim:79-81@cd8111a5`).
- The reel drops the victim one tile in front of the caster along the hook heading; thrown
  across from a lip, that is in the river (a dump) (`examples/pudge_wars/sim.nim:1536-1546@cd8111a5`).
- **Clash (deny):** two enemy hooks whose sweeps pass close break each other with no damage;
  a Big Hook wins outright; the hook that travelled less gets +1 gold
  (`examples/pudge_wars/sim.nim:4534-4577@cd8111a5`). Clashes are common: one hosted 3v3
  replay had 231 of 864 hooks end in a clash.
- `hooksLanded` increments once on every latch: enemy, ally save, headshot (which returns
  before the general latch path) or an allied-mine grab (`examples/pudge_wars/sim.nim:4365-4374@cd8111a5`,
  `examples/pudge_wars/sim.nim:4429-4460@cd8111a5`). Clashes and rune scoops do not count.

Derived flight time at Speed 0 (computed from the constants, not stated in source): about
13 ticks to 10 tiles, 29 to 30, 41 to 50 and 47 to 60, plus the 9-tick windup.

## Blink, Rot, Grapple

- **Blink:** 20-tile range, 256-tick (8 s) cooldown; lands on the farthest legal point of the
  ray (water counts as legal) (`examples/pudge_wars/content.nim:288-296@cd8111a5`,
  `examples/pudge_wars/sim.nim:2697-2822@cd8111a5`). Rejected while hooked, winding up, or
  **standing in any Rot disk, including your own** (`examples/pudge_wars/sim.nim:2840-2888@cd8111a5`).
  Side-lane lip to lip is 24 tiles, so Blink crosses only near the mid pinch. A hook you have
  out gains range equal to the hop (`examples/pudge_wars/sim.nim:2824-2838@cd8111a5`).
- **Rot:** toggle, 8-tile radius, 24 HP per 16-tick pulse (48 DPS per cloud, overlapping
  clouds stack), −40% speed (`examples/pudge_wars/content.nim:297-307@cd8111a5`).
  **It damages enemies, allies and yourself**; spawn shields block it except your own Rot on
  yourself (`examples/pudge_wars/sim.nim:3516-3546@cd8111a5`). First pulse lands in the tick
  it is turned on; it turns off on death.
- **Grapple** exists in the sim but has **no BASIC binding** (human seats only)
  (`examples/pudge_wars/sim.nim:3122-3130@cd8111a5`).

## Damage, death and kill credit

- One damage entry point with no team check; it records `lastHurtBy`
  (`examples/pudge_wars/sim.nim:1799-1816@cd8111a5`).
- Drowning: 30 HP every 16 ticks (60 DPS) in the river, **not while hooked**, credited to the
  victim's `lastHurtBy` (`examples/pudge_wars/content.nim:315-318@cd8111a5`,
  `examples/pudge_wars/sim.nim:3499-3514@cd8111a5`). `lastHurtBy` clears only at respawn, so an
  enemy who hit you earlier is credited if you later drown.
- `killPudge` (`examples/pudge_wars/sim.nim:1723-1793@cd8111a5`): the victim always gets
  `deaths += 1`. If the killer is an enemy, the killer gets a kill, a point, kill gold and
  +35 max HP, and **the killer's team gets a team kill**. If you die to **your own Rot**,
  the **other team** gets a team kill and nobody gets a personal kill. Any other case
  (ally Rot, no recorded attacker) counts **no team kill**.
- Kill gold: 3, +2 first blood, plus streak bonuses (`examples/pudge_wars/content.nim:566-602@cd8111a5`).
- Upgrades, gold, items and kills persist through death.

## Economy, shop and props

- Gold comes only from kills, denies (+1) and Bounty runes (+4); no passive income.
- `buyPower(item)` works only on your own shop pad (`examples/pudge_wars/sim.nim:2469-2518@cd8111a5`):
  0 Flask 5 g (full heal), 1 Haste 4, 2 Regen 4, 3 Big Hook 6, 4 Double Damage 8,
  5 River Treads 10/rank, 6 Longflight Brand 12/rank (+1% hook damage per tile flown per
  rank), 7 Palisade 10/rank, 8 Hooktrap 10/rank, 9 Ward free (one charge, restock 60 s)
  (`examples/pudge_wars/content.nim:105-123@cd8111a5`). Permanents rank to 5.
- Props (`examples/pudge_wars/sim.nim:2157-2317@cd8111a5`): walls and mines go on your own
  bank within Chebyshev 3; wards on either bank within 32 tiles. Enemy mines and wards are
  never visible to you.

## Runes

- A rune drops every 640 ticks (20 s) at x = 64: even drops at y = 16 drifting south, odd at
  y = 111 drifting north, 4 tiles/s (`examples/pudge_wars/sim.nim:3646-3707@cd8111a5`).
- The kind schedule is deterministic: Haste, Haste, Double Damage ×2, Regen ×2, Big Hook ×2,
  Lightning ×2, Bounty ×2, repeat (`examples/pudge_wars/content.nim:188-197@cd8111a5`).
  BASIC sees it through `runeSoon`, `runeNextKind`, `runeNextX`, `runeNextY`.
- Pick-up by wading within 2.6 tiles, or by **any hook sweep** passing within head radius +
  1 tile, which gives the rune to the hook's owner wherever they stand
  (`examples/pudge_wars/sim.nim:3708-3737@cd8111a5`, `examples/pudge_wars/sim.nim:4717-4775@cd8111a5`).
- One buff slot, 480 ticks, overwritten by a new pickup, cleared on death
  (`examples/pudge_wars/sim.nim:3630-3644@cd8111a5`). Big Hook: +50% range and radius,
  ×1.5 damage, wins clashes. Double Damage: ×2 hook damage. Lightning: 2× launch speed and
  120 splash on latch (`examples/pudge_wars/content.nim:333-371@cd8111a5`).

## Healing

| Source | Amount | Condition | Source |
|---|---|---|---|
| Own spawn pads | 2% max HP / 16 ticks | alive, on bank, not hooked, Chebyshev ≤ 2 of any own pad | `examples/pudge_wars/content.nim:51-58@cd8111a5` |
| Island fountain | 1.5% max HP / 32 ticks | within 16 tiles of (64,64), both teams | `examples/pudge_wars/content.nim:59-69@cd8111a5` |
| Ally hook | +120 HP | hooked by a teammate | `examples/pudge_wars/content.nim:79-81@cd8111a5` |
| Regen rune, Flask | 3% / 16 ticks; full | buff; shop | `examples/pudge_wars/content.nim:337-340@cd8111a5` |

## Fog

Team-shared; `fog=false` is rejected in the league. A team sees the whole river, its own home
bank, anything within 20 tiles of a living teammate or team ward, a 6-tile corridor along its
own hooks in flight, and its Lightning fire (`examples/pudge_wars/sim.nim:3316-3341@cd8111a5`).
So **an enemy standing back on its own bank is invisible** unless one of your butchers or a
ward is within 20 tiles; a West butcher at the side-lane lip (x 52) sees East only to x ≈ 72.

## Randomness

The seed fixes terrain noise, trees and spawn lanes. In-match RNG only breaks exact ties for
rune pickups (`examples/pudge_wars/sim.nim:4709-4715@cd8111a5`). Everything else is
deterministic integer math, so a replay tape plus the same binary reproduces a match exactly.

## Scoring and results

- `checkWinner` ends the game when a team reaches `kill_goal`; West is checked first
  (`examples/pudge_wars/sim.nim:4952-4960@cd8111a5`).
- `resultsJson` (`examples/pudge_wars/coworld_host.nim:1059-1100@cd8111a5`):

| Field | Meaning |
|---|---|
| `scores[i]` | 1.0 if slot i's team has **strictly more** team kills, else 0.0 (draw → 0) |
| `winner_team` | `"west"`, `"east"` or `"draw"` by team kills; the kill-goal winner if the goal ended the game |
| `west_kills`, `east_kills` | team kill counters |
| `end_tick` | ticks simulated |
| `end_reason` | `"kill_goal"` or `"time_limit"`; no other value exists |
| `seed`, `kill_goal` | config echo |
| `per_seat[i]` | `{slot, team (0 West, 1 East), kills, deaths, hooks_thrown, hooks_landed}` |

Consequences: Σ per-seat kills can be below the team total (rot suicides); deaths can exceed
the enemy's kills (ally Rot or uncredited drowning); `hooks_landed / hooks_thrown` is not
enemy accuracy. In a same-tick double goal crossing, `winner_team` ("west") and `scores`
could disagree (inference; never observed). A policy that fails to load produces no results
at all (`examples/pudge_wars/fileplayers.nim:144-152@cd8111a5`).

## Stale upstream docs

| Upstream claim | Code at cd8111a5 |
|---|---|
| `examples/pudge_wars/duel_loop.md:170-170@cd8111a5`: playable tiles 8..119 | x 14..114, y 16..111 (`examples/pudge_wars/maps.nim:43-51@cd8111a5`) |
| `examples/pudge_wars/duel_loop.md:179-180@cd8111a5`: hook damage 80 + 15/level | 200 + 15/level (`examples/pudge_wars/content.nim:256-259@cd8111a5`) |
| `examples/pudge_wars/content.nim:42-47@cd8111a5` comment and `duel_loop.md`: windup roots the caster | the caster coasts (`examples/pudge_wars/sim.nim:3478-3483@cd8111a5`) |
| `examples/pudge_wars/duel_loop.md:182-182@cd8111a5`: hooking a corpse denies | hooks skip dying butchers; a deny is a hook clash |
| `examples/pudge_wars/duel_loop.md:186-187@cd8111a5`: a swimmer walks home | no climb-out; leave by Blink or a hook |
| `examples/pudge_wars/CLAUDE.md:50-50@cd8111a5`, `duel_loop.md:197-198`, softmax.com wiki: 20k instructions (wiki also 50k work) per decision | 100,000 instructions and 250,000 work units; 20,000 is the bytecode size cap (`examples/pudge_wars/bots.nim:101-123@cd8111a5`) |
| `examples/pudge_wars/content.nim:21-22@cd8111a5`: duel on the middle lane | team slot 1 of a shuffled lane order |
| `examples/pudge_wars/sim.nim:647-648@cd8111a5` comment: hook radius 3.5 | 2.75 tiles (`examples/pudge_wars/content.nim:277-281@cd8111a5`) |
| `readme.md:16-17@cd8111a5`, `coworld/deployment.json:1-9@cd8111a5`: league runs 0.1.1 | the league Coworld is 0.1.8 (`cow_c23845f0…`) |
| `coworld/player/README.md`: JSON player surface | unreachable in `pwleague` |
| `examples/pudge_wars/BASE_POLICY_AUTORESEARCH.md` and the 1,200-HP study: results at "fair" difficulty | fair = `botEvery` 8 + aim noise 1 (`examples/pudge_wars/game.nim:222-235@cd8111a5`); the league is `botEvery` 1, noise 0 |

## Open questions

1. Can a hooked butcher start a hook or keep swinging while reeled? `applyHookFine` does not
   check `hookedBy` (unverified in a replay).
2. How common are impassable low patches on the playable banks for typical seeds?
3. Do the 1–2 deaths per league episode that no team kill matches come from ally Rot or from
   uncredited drowning? Check `damage` events with `cause: rot` between teammates.
