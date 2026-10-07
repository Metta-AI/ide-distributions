# Gods of the Arena

**New GotA week: everyone needs to update their bot.** Handle the draft, spend ability points, buy only in your own keep, and review the new BASIC number semantics and lane rewards; start from the updated `players/base.bas`.

Two teams of five BASIC heroes battle to slay the enemy god. The official
Softmax ladder uses OpenSkill with rating-based matchmaking and a mean-score
column. Open Draft allows duplicate hero picks. Game results report
**Emmett's Glory**: each winning hero's lifetime XP divided by
elapsed simulated minutes, rounded down to whole points. Fractional minutes
and drafting time count. Losing teams, draws, and timeouts score zero.
There is no fixed XP penalty per minute. Zero-duration games score zero.

Destroying the enemy god grants every hero on your team a flat 1,000 XP,
including dead heroes and heroes elsewhere on the map, regardless of who lands
the last hit. This is awarded once on the final tick and included in lifetime XP
before calculating Emmett's Glory. Finishing sooner increases the XP-per-minute
score. Timeouts grant no god reward.
Destroying a tower or barracks grants its killer 200 XP and 75 gold.
Towers fire once per second, and reload continues when they lose or switch
targets. Their homing fireballs deal damage on arrival and follow the original
target beyond range and into fog, even if the tower is destroyed. Shots disappear
if their target dies before impact.

The gods are the objectives: Hades for Red and Zeus for Blue. Each god has two level-3 guard towers. Clearing all three towers in any one lane exposes the guards. The god cannot take damage from attacks or spells until both of its guards are destroyed. Guards have the same 3900 HP and 60 damage as level-3 lane towers.

Slots 0–4 are Red and slots 5–9 are Blue. Platform slots are zero-based. Upload a `.bas` file containing BASIC source. The game reads the staged file directly, with no player container or network connection.

Start with the bundled `players/base.bas`. The [game documentation](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/docs/index.html) describes observations and available BASIC commands. The same source is available under `examples/gods_of_the_arena/bots.nim` and `content.nim`.

The baseline is a playable reference using structured GotA observations. It drafts
missing roles, farms lanes, prioritizes last hits, pushes exposed buildings and
the enemy god, upgrades and explicitly casts spells, leads area shots, dodges
visible warnings, and uses enemy stats and equipment to judge fights. It also
shops, stacks and uses recovery items, returns to spawn, uses portal scrolls,
and buys back when affordable. Actions are conditional on a useful opportunity,
so one match need not exercise every mechanic. Its observation scans are bounded
and its main decisions run every six ticks. Abilities and items require
explicit policy commands; the engine never casts or uses them automatically.
Spell lead times and targeting preferences use a small policy table; ranges,
health, damage, costs, and ranks use live observations. This is an editable starting point, not an optimal policy.

Every hero has a free single-target melee or ranged basic attack in addition
to four abilities. Basic damage grows each level and includes equipment bonuses.
Idle heroes automatically acquire the nearest visible, attackable enemy,
including enemy creeps, heroes, and exposed buildings. `attackTarget(objectId)`
takes priority; melee and ranged heroes both move into their own attack range
and repeat basic attacks. `walkTo(x, y)` cancels the attack and suppresses
automatic acquisition while walking. Basic attacks do not spend mana or spell
charges.

`attackMove(x, y)` uses the same attack-move order as the player controls. It follows a path toward that tile, stops for enemies in the hero's normal acquisition range, and resumes afterward. Like other actions, it returns 1 when accepted and 0 when rejected, and is recorded in replays.

The bundled `players/rusher.bas` sends all five heroes down mid together. It regroups toward the living team's center when any pair is more than 10 tiles apart, closing to 8 tiles before resuming. It attacks visible, vulnerable enemies within 20 tiles, favoring the enemy closest to the group's center. Otherwise it attack-moves through the middle and toward the opposing god. Dead allies are ignored until they respawn. This policy uses basic attacks only; add explicit casting commands to use abilities.

## Structured observations

Existing policies keep working without changes. To use the additional API,
put this marker on the first nonblank line of your BASIC source:

```basic
' @gota-structures

if draft.active then
  if draft.turnId = self.id then
    accepted = draftHero(Ranger)
  end if
  end
end if

for i = 0 to match.objectCount - 1
  if objects(i).team <> self.team and objects(i).alive then
    accepted = attackTarget(objects(i).id)
    exit for
  end if
next i
```

The host supplies the types and declarations in
[`structures.bas`](https://github.com/Metta-AI/polyworld/blob/main/examples/gods_of_the_arena/structures.bas).
Do not redeclare these names. Additional policy-defined types are allowed.

| Record | Contents |
| --- | --- |
| `self` | Own health, mana, economy, level/XP, role, motion, combat, controls, respawn, shop and buyback state. |
| `objects(i)` | Visible objects, including health, level, mana, motion, target, controls and neutral-camp metadata. |
| `abilities(slot)` | Four ability slots with ranks, upgrade eligibility, effects, costs, range/radius, casting kind, charges and timers. |
| `items(slot)` | Six inventory slots with ID, count and cooldown. |
| `objectItems(i * 6 + slot)` | Visible object inventory IDs and stack counts. |
| `spells(i)` | Visible spell warnings, positions, ability/caster IDs, impact tick, hostile and support flags. |
| `camps(i)` | Public camp positions and difficulty tiers. |
| `match` | Match/battle ticks, limits, seed, wave timers, observation counts and team-known building counts. |
| `map` | Dimensions, layer count, world-to-map origin and enemy god position. |
| `draft`, `players(i)`, `heroChoices(class)` | Draft state, public roster selections, roles and hero availability. |
| `lastAction` | The latest gameplay command's `accepted` flag and `error` code. |
| `tile` | Terrain kind, walkability, height and water depth after `readTile(x, y, layer)`. |

Positions use FIXED tile-center map coordinates, the same frame accepted by
`walkTo`, `attackMove`, `castPoint` and `useItemAt`. They are not team-relative.
`position.y` is the map's second horizontal axis. Facing is a unit vector;
velocity and `self.moveSpeed` are tiles per simulation tick. Attack and ability
ranges are tiles. Durations end in `Ticks`; `spells(i).impactTick` is an absolute
world tick. Flags, counts, IDs and health are INTEGER. Tile height/water depth
retain the legacy terrain query units. `readTile` requires integer tile indices;
use `floor(position.x + .5)` to select the containing tile.

The visible-object and spell collections are a snapshot of the decision frame.
They use the existing team LOS filter. Hidden target/caster identities remain
zero, and enemy building counts use team-known state. `objects(i).alive`
preserves `objectAlive(i)` semantics, including building vulnerability.
Camp records contain static geometry, not hidden respawn or population data.
Use the corresponding count: `match.objectCount`, `match.spellCount`,
`match.campCount`, or `draft.playerCount`. Indices can change between decisions;
remember stable IDs instead. Slots vacated by a shrinking collection are cleared.

Own state, inventory, abilities, draft state and action feedback refresh after
each gameplay command, including rejected commands. This lets a policy upgrade
several abilities or buy several items in one decision. World snapshots stay
fixed until the next decision. Writes to records only change the policy's copy;
they cannot heal a hero, reveal an enemy, or alter the simulation.

The API reserves space for 4,096 objects, 2,048 warnings, 64 camps, 10 players,
and 10 hero choices. Exceeding a collection capacity raises a BASIC error rather
than silently omitting observations. Structured policies have limits of 512
scalar fields/globals, 128 array fields/arrays, 262,144 array cells and 8 MiB
logical VM storage. Snapshot fields count toward those limits. Existing policies
retain their original limits, and instruction/work budgets are unchanged.

The host prepares referenced scalar fields and loads array columns on first
access, avoiding work for observations a policy does not use. Loading keeps
the same decision snapshot and does not change instruction or work budgets.

The baseline now uses these records. The legacy scalar variables, functions,
math helpers, neural functions, and chat/LLM APIs remain available.

## BASIC-defined neural networks

Policies can implement their neural architecture in BASIC using packed int32,
Q16.16 or float32 tensors loaded from their ZIP. Float32 stays inside tensors;
BASIC scalar arithmetic keeps its existing deterministic behavior. Each player's
VM owns its weights, scratch buffers and recurrent state.

Generic operations include dense and sparse CSR multiplication, activations,
elementwise math, slicing and gather/scatter. Richard, David, Andre and Fly
architectures have BASIC implementations using these operations. Existing custom
runners remain available. Example packages contain synthetic weights only.

See the [tensor API and package format](https://github.com/Metta-AI/polyworld/blob/main/docs/neural-tensors.md)
for all functions, resource limits and conversion commands.

## Drafting

Every live match starts with a draft. A seeded random team picks first.
Teams alternate, and each team's players pick in spawn order. Combat,
waves, and the battle clock wait until all ten players have drafted.

The match setting `draft_mode` selects the hero availability rules:

| Mode | Setting | Picks allowed |
| --- | --- | --- |
| Unique Draft (default) | `unique` | Each hero class once across both teams. |
| Team Draft | `team` | Each hero class once per team. Enemy teams may mirror picks. |
| Open Draft | `open` | Any duplicates, including ten players using the same hero. |

The Softmax Competition league uses Open Draft. Every player may pick any
hero, including a hero already picked by a teammate or an opponent. Local
games keep Unique Draft unless a different mode is selected.

Local games accept `--draft-mode team` or `--draft-mode open`. The web
player accepts `?draft-mode=team` or `?draft-mode=open`. JSON configs use
`"draftMode": "team"` (or `draft_mode`). The replay saves this setting.

Only the active player's BASIC script runs during drafting, once every
half second. Every player, including humans, has ten simulation seconds to
pick. At the deadline, the game picks a random available hero if the player
has not chosen one. The bundled base and rusher scripts try to fill
five roles: frontline, carry, mage, support, and fighter. This is a policy
preference, not a draft rule. Any combination of available heroes is allowed.
Their normal movement and combat logic runs after drafting.

| Data or command | Meaning |
| --- | --- |
| `draftMode` | Unique Draft 0, Team Draft 1, Open Draft 2. |
| `drafting` | 1 during drafting, 0 during battle. |
| `draftTurnId` | ID of the player picking now, or 0 after drafting. |
| `draftPlayerCount()` | Number of players in the public roster. |
| `draftPlayerId(i)` | Player ID at zero-based spawn index `i`, or 0 if invalid. |
| `draftPlayerTeam(i)` | Team at spawn index `i`: Red 0, Blue 1, invalid -1. |
| `draftedClass(id)` | Hero class picked by player ID, or -1 if unpicked or invalid. Both teams' picks are public. |
| `heroAvailable(class)` | 1 if the caller's team may pick this class under the current draft mode, otherwise 0. This checks availability, not whose turn it is. |
| `heroRole(class)` | Frontline 0, carry 1, mage 2, support 3, fighter 4, invalid -1. |
| `draftHero(class)` | Selects a hero on your turn. Returns 1 on success, 0 on rejection. |

`selfClass` is -1 until your pick. Class constants are `VanguardKnight`,
`Ranger`, `Arcanist`, `DruidWarden`, `DemonHunter`, `DeathKnight`,
`Crossbowman`, `Lich`, `Warlock`, and `Berserker`, with IDs 0 through 9.
Draft queries update immediately. Other sampled self data updates next
turn. `worldTick` includes draft ticks for action replay timing.

```basic
if drafting then
  for candidate = 0 to 9
    if heroAvailable(candidate) then
      draftHero(candidate)
      exit for
    end if
  next
else
  attackMove(mapWidth / 2, mapHeight / 2)
end if
```

`lastActionError()` reports `ActionNotDrafting`, `ActionNotDraftTurn`,
`ActionUnknownHero`, or `ActionHeroTaken` for rejected picks. Normal game
commands are rejected with `ActionDrafting` while players are picking.
In player mode, select an available hero in the drafting screen and click
**Lock in hero** when it is your turn. The current picker appears above
the hero grid. Picked heroes turn gray and show their player and team.
The countdown shows the active pick's remaining time. Space pauses or
resumes drafting, including the countdown. Drafting has a separate budget
of up to 100 simulation seconds for ten players. The configured `maxTicks`
and CLI duration flags limit battle time only, starting after the last pick.

## Hero balance, October 7, 2026

Frontlines have the most health. Fighters have the next health tier.
Mages and supports have flatter health growth. Carries start fragile, with
accelerating health and spell damage growth, but retain their late power.
Fighters have stronger early pursuit and short roots to pressure carries.
Frontlines protect allies with short control effects. Mages punish clustered
teams, and protected supports sustain allies during prolonged fights.
These are intended counterplay opportunities, not guaranteed matchup results.
Open Draft still allows any composition and duplicate heroes.

Ranger and Crossbowman both attack from six tiles. Crossbowman's first two
spell ranks deal roughly 10% less damage, while its final ranks stay unchanged.
Supports start with more health but still gain only 20 HP per level.
Druid Warden heals groups; Warlock provides stronger healing to one hero.
A full lane wave has six melee creeps and two casters, with 480 combined HP
and 168 sustained DPS before movement or interruptions. Every creep deals
28 damage per basic attack. Gameplay replay version is 69; this client
supports only that exact version.

## Ability progression

Heroes start at level 1 with one ability point and all four abilities locked.
Each hero level grants another point. Points remain banked until a command
spends them. `levelAbility(slot)` spends one point to unlock rank 1 or upgrade
an already learned ability. Slots 0, 1, 2, and 3 correspond to Q, W, E, and R.

Q, W, and E have four ranks requiring hero levels 1, 3, 5, and 7.
R has three ranks requiring hero levels 6, 12, and 18. Each additional rank
adds 50% of the rank-1 damage, healing, or mana restoration, rounded down.
Carry spell damage instead follows explicit per-rank curves with later growth.
Mana costs, range, charge capacity, and timing stay the same. An upgrade
preserves spent charges and running cooldowns. Pending spells retain the
rank they had when cast. Respawning preserves learned ranks and banked
points and refills only learned abilities.

Hero stats, including basic-attack damage, still grow automatically with
hero level. Ability ranks never increase automatically. The bundled base
and rusher policies explicitly spend points, prioritizing R, W, E, then Q.
Ability use requires explicit `castTarget` or `castPoint` commands, including
slot 0 and ultimates. Items require `useItem` or `useItemAt`. Custom policies
can bank points, choose another upgrade order, and reserve any ability. At
level 20, fully ranking all four abilities leaves five banked points.

Thorn Bloom and Kindred Renewal heal every allied hero and damage enemies
in their area. Either spell can target an ally, an enemy, or the ground.
Warlock's Mending Hex and Dread Pact heal one selected allied hero, including
self, or damage one selected enemy. Dread Pact silences hostile targets only.
All four spells reach six tiles. Healing is capped at maximum HP.

| Support spell | Healing at ranks 1 / 2 / 3 / 4 | Targets |
| --- | --- | --- |
| Druid W: Thorn Bloom | 150 / 225 / 300 / 375 | Allies within a 2-tile circle. |
| Druid E: Kindred Renewal | 160 / 240 / 320 / 400 | Allies within a 2.5-tile circle. |
| Warlock W: Mending Hex | 220 / 330 / 440 / 550 | One selected allied hero. |
| Warlock E: Dread Pact | 240 / 360 / 480 / 600 | One selected allied hero. |

Warlock's ground projectiles remain offensive. Use `castTarget` with an
allied hero's ID to heal. Druid's area casts can heal multiple allies while
hurting nearby enemies. The healing classification does not imply zero damage.
The spell-warning `support` flag is 1 only for effects that deal no damage.
Healing requires an explicit policy command, just like every other ability.

| BASIC function | Meaning |
| --- | --- |
| `levelAbility(slot)` | Unlock or upgrade. Returns 1 on success, 0 on rejection. |
| `abilityPoints()` | Current unspent points. |
| `abilityLevel(slot)` | Current rank, with 0 meaning locked. |
| `abilityMaxLevel(slot)` | Rank limit: 4 for slots 0-2, 3 for slot 3. |
| `abilityRequiredLevel(slot)` | Hero level required for the next rank, or 0 at maximum rank. |
| `canLevelAbility(slot)` | 1 when alive with a point and the required level, otherwise 0. |
| `abilityDamage(slot)` | Damage per target at the learned rank, or 0 when locked. |
| `abilityHeal(slot)` | Healing per target at the learned rank, or 0 when locked. |
| `abilityRestore(slot)` | Mana restoration at the learned rank, or 0 when locked. |
| `abilityManaCost(slot)` | Mana cost of a cast, including while locked. |

These queries update immediately after commands and return 0 for invalid
slots. `abilityCharges(slot)`, `abilityCooldown(slot)`, and
`abilityRecharge(slot)` remain available. Locked abilities have no charges.
Upgrade actions and rejected attempts are recorded for deterministic replays.

```basic
if canLevelAbility(3) then
  levelAbility(3)
elseif canLevelAbility(1) then
  levelAbility(1)
end if
```

Player controls use Shift+Q/W/E/R, Shift-click on an ability, or its gold
"+" button to spend a point. The HUD shows current ranks, locked abilities,
and available points.

## BASIC observations

All bots in one decision phase observe the same starting objects and spell
warnings. Earlier bots' actions do not change later bots' observations in
that phase. New casts appear in the next observation frame. Own inventory,
ability costs, and command results still update immediately when a command
is accepted. Object and spell indices are zero-based and may change next
decision. Keep `objectId(i)` when tracking an object across decisions or
calling `attackTarget`, rather than keeping its list index.

Observations are integers. `worldScale = 60000` is the number of world units per tile, and `tickRate = 24` is the number of simulation ticks per second. `selfX`, `selfY`, `objectX(i)`, `objectY(i)`, `spellX(i)`, and `spellY(i)` use whole global tiles. Facing, speed, range, and velocity retain sub-tile precision in world units. The Y component of these APIs is the second horizontal map axis, not height.

At an exact tile boundary, Red observers select the higher cell and Blue
observers select the lower cell. This makes observed cells rotate exactly
when the arena and teams are swapped. The bundled policies convert global
coordinates into their own team's frame before rounding spatial decisions.

Objects appear in groups: gods, buildings, heroes, then creeps. Within each
group, allies precede enemies, followed by position in the observer's team
frame and stable ID. This order does not depend on simulation storage order.
Spell warnings are ordered by impact tick, hostile before allied casts,
then team-relative position, ability, and stable caster/target identities.

Units plan movement and attacks from the same starting actor state. Movement
is published together, then spell impacts and collected damage resolve before
deaths and rewards. Opponents can kill each other in the same tick. Collision
corrections are also accumulated before moving any participant. Simultaneous
last-hit credit uses the match seed, tick, and team-relative actor geometry
and role, so corresponding fights do not depend on faction-specific IDs.

### Your hero

The existing `selfId`, `selfTeam`, `selfClass`, `selfX`, `selfY`, `selfHp`, `selfMaxHp`, `selfMana`, `selfMaxMana`, `selfGold`, `selfLevel`, `selfLayer`, and `worldTick` remain available. These additional read-only values describe the current hero:

| Value | Meaning |
| --- | --- |
| `selfMoveSpeed` | Unblocked movement speed in world units per tick, including level and equipment bonuses. |
| `selfAttackRange` | Basic-attack range in world units, measured by planar Euclidean distance between centers. Towers and barracks allow at least 105000 units measured from their occupied footprint; gods use 255000 units. |
| `selfAttackDamage` | Current basic-attack damage, including level and equipment bonuses. |
| `selfTarget` | Current ordered or automatically acquired attack target's stable object ID, or zero for none. |
| `selfAttackCooldown` | Ticks until the next basic hit could land if the target stays in range. Includes remaining recovery and the next windup, or the remainder of a current windup. An idle hero reports a full windup. Excludes chasing and is separate from ability cooldowns. Movement can cancel a swing. |
| `selfAttacksLanded` | Lifetime count of successful basic hits, preserved across respawns. Spells do not increment it. |
| `selfPortalCooldown` | Ticks before another Portal Scroll can be used, shared across all inventory stacks and preserved through death. |
| `selfChannelTicks` | Ticks remaining in the current teleport channel, or zero. |
| `selfStunTicks`, `selfRootTicks`, `selfSilenceTicks` | Ticks remaining in these control effects, or zero. |

### Shop, potions, and spawn recovery

Purchases only work inside your own keep, including its spawn room. Elsewhere,
`buyItem(id)` returns 0 with `ActionOutsideKeep`. `canShop()` returns 1 when
purchases are allowed, and `inOwnSpawn()` returns 1 inside your living hero's
own spawn room. These queries read live state.

Inside that spawn room, health and mana each recover at **20% of maximum per
second**, capped at maximum. The larger keep and the enemy spawn give no such
recovery. Normal passive mana regeneration still applies. Damage does not
turn off spawn recovery, and dead heroes cannot recover until they respawn.

| ID | Item | Gold | Effect |
| --- | --- | --- | --- |
| 1 | Health Potion | 30 | 120 health over 10 seconds. |
| 2 | Vitality Elixir | 75 | 90 health immediately. |
| 22 | Mana Potion | 45 | 90 mana over 10 seconds. |
| 3 | Mana Elixir | 90 | 60 mana immediately. |

Each stacks to **8** per slot. `useItem(slot)` spends one dose. Any positive
incoming damage interrupts both active potion regeneration effects; movement
and attacks do not. Health items share a **10-second** cooldown, and mana items
share a separate **10-second** cooldown. Cooldowns begin on use and survive
interruption and death. Full health/mana or a cooldown rejects use without
spending a dose. `itemCooldown(slot)` returns live remaining ticks (24 per
second), or 0 for empty/invalid slots. Inventory icons show stack counts and
remaining cooldowns.

### Portal Scrolls

Buy item **21** for **100 gold**. Scrolls stack to eight per slot. Call
`useItemAt(slot, x, y)` with whole or fractional map coordinates to consume
one scroll and begin a **3-second** channel. The destination is the nearest
visible, walkable point inside a living allied tower's sight radius:
9 tiles for outer towers, 9.5 for inner towers, and 10 for gate/guard towers.
Attack range, vision, and portal landing range share this same radius. Barracks are not
anchors. A distant requested point is clamped into this area; there is no
travel-distance limit. The selected tower must survive until arrival.

The hero cannot move, attack, or cast while channeling, and still takes
damage. Stuns, roots, death, or loss of the anchor interrupt the channel.
The scroll is spent when the channel begins. Completion or interruption
starts a **60-second** cooldown shared by every scroll the hero holds.
Damage alone and silence do not interrupt it. Blazing Blade, Golem Seed,
and Bone Marionette can interrupt the channel on impact.
`useItem(slot)` rejects scrolls because they require a destination.

For human play, click the inventory scroll (or press F/G for the first two
slots), then right-click the map or minimap. Purple circles show tower range, and a
channel bar shows the time remaining. Esc cancels destination selection.

### Visible objects

Loop over indices `0` through `objectCount() - 1`. Object kinds are 1 = god, 2 = hero, 3 = creep, 4 = tower, 5 = barracks, and 6 = neutral mob. Barracks have 950 HP and become exposed after their lane towers fall. Each barracks spawns three melee creeps and one ranged creep per wave, giving six melee creeps and two ranged creeps per lane for each team. Ranged creeps carry a staff and cast magic bolts from up to four tiles away. Destroying a barracks stops its four creeps from spawning. For creeps, `objectClass(i)` is 0 for melee and 1 for ranged. Destroyed buildings leave the object list and release their occupied tiles. New queries respect the same visibility filter:

| Function | Meaning |
| --- | --- |
| `objectLevel(i)` | Hero level. |
| `objectMana(i)` | Hero's current mana. |
| `objectStunTicks(i)`, `objectSilenceTicks(i)`, `objectRootTicks(i)` | Remaining control ticks for a visible unit, or zero. Uses the same frozen, LOS-filtered object list. |
| `objectItemId(i, slot)` | Hero's held item ID, using the same IDs as `itemId` and `buyItem`. Zero means no item. Slots are 0 through 5. |
| `objectItemCount(i, slot)` | Stack count in that hero's inventory slot. |
| `objectFacingX(i)`, `objectFacingY(i)` | Normalized horizontal facing, scaled by `worldScale`. A unit facing positive X reports `(60000, 0)`. |
| `objectTarget(i)` | Current attack target's stable object ID, or zero if absent or not visible to your team. |
| `objectVelX(i)`, `objectVelY(i)` | Actual displacement over the last simulation tick in world units, including collision adjustments. Stationary objects report zero. |

These new object queries return zero for invalid indices or fields that do not apply to that object. Invalid inventory slots also return zero. Hero level, mana, and inventory queries return zero for non-heroes. An object's ID is not a valid substitute for its list index.

Each slain enemy creep provides a shared pool of 15 XP to living heroes within six tiles on the same navigation floor, regardless of starting lane. If the last hitter is among these heroes, they receive 15% of the pool first, then the remaining 85% is split equally among all nearby heroes, including the last hitter. With three heroes, this gives 6.5 XP to the last hitter and 4.25 XP to each teammate. Fractional XP carries forward between kills. If no eligible hero lands the last hit, the full pool is shared equally. A hero last hitter also receives 15 gold; tower and creep last hits grant no gold to heroes.

### Pending spells and warnings

Loop over `0` through `spellCount() - 1`. This list contains unresolved casts from their start through impact, including projectiles and area warnings. Allied casts are observable; enemy casts require their aim position to be visible, matching the viewer's warning visibility. Completed effects are omitted.

| Function | Meaning |
| --- | --- |
| `spellAbility(i)` | Ability enum ID from `content.nim`, beginning at zero. Invalid indices return -1. |
| `spellCasterId(i)` | Caster's stable object ID, or zero if the enemy caster is hidden. |
| `spellX(i)`, `spellY(i)` | Aim/impact position or area center in whole global tiles. This is not the projectile's interpolated flight position. |
| `spellImpactTick(i)` | Absolute simulation tick at impact. Subtract `worldTick` to obtain the remaining ticks. |

Other invalid spell queries return zero. Visibility of an enemy warning does not reveal its hidden caster's identity.

## Death and buyback

The first death takes 9 seconds to respawn, including the 1-second death
animation. Each subsequent death adds 5 seconds, up to a total of 60 seconds.
Death counts belong to each hero and persist after respawning.

`selfDeaths` is the hero's death count. `selfRespawnTicks` is the remaining
respawn delay in ticks, or zero while alive. BASIC decisions continue while
dead so a policy can request buyback.

`buybackPrice()` returns the dead hero's price: 100 gold times their death
count. It returns zero while alive or after the match ends. The price stays
fixed during a death, even as the respawn timer counts down.

`buyback()` returns 1 when accepted and 0 when rejected. It spends the
hero's gold and immediately respawns them with full health, mana, and spell
charges. It preserves inventory, level, XP, and death count. A living hero,
an ended match, or insufficient gold causes rejection without spending gold.
The HUD shows the countdown, buyback price, and any rejection reason while
dead. Buyback attempts are recorded for replay and seeking.

The bundled `players/base.bas` buys back as soon as it can afford the price.
While dead it skips normal commands, resuming them on the decision after buyback.

```basic
if selfRespawnTicks > 0 then
  price = buybackPrice()
  if price > 0 and selfGold >= price then
    accepted = buyback()
  end if
end if
```

## Crowd control

| Hero | Ability | Effect | Rank-one damage |
| --- | --- | --- | --- |
| Vanguard | W: Firebrand Sword | Stun for 0.5 seconds | 37 |
| Vanguard | R: Blazing Blade | Stun for 1 second | 204 |
| Death Knight | W: Afterlight Sickle | Root for 0.5 seconds | 37 |
| Demon Hunter | E: Gale Slash | Root for 0.5 seconds | 60 |
| Berserker | E: Winged Boot | Root for 0.75 seconds | 35 |
| Warlock | E: Dread Pact | Silence for 2 seconds | 84 |
| Druid | R: Golem Seed | Root for 2 seconds | 49 |
| Lich | E: Bone Marionette | Root for 1.5 seconds | 49 |

Control durations stay fixed at every rank. Effects apply on impact to enemy
heroes and creeps; buildings and gods are immune. A stun stops movement,
basic attacks, abilities, and items, and cancels a pending basic swing. Silence prevents
all four abilities but allows movement, basic attacks, and items. Root
prevents movement and teleporting, while allowing in-range attacks,
abilities, and other items. Stun and root interrupt teleport channels.
Already released spells still resolve.

Different effects coexist. Reapplying one keeps the later expiration,
without adding durations. All effects clear on death and respawn. Visible
affected units show icons with countdown rings above their health bars.

## Action feedback

`lastActionError()` returns the reason for your hero's latest submitted
command. A successful action clears it to `NoActionError` (0). A failed
action returns 0 as before, and sets the first failing validation reason.
Read-only queries and automatic basic attacks do not change it.
Unlike the sampled self data, this query updates immediately after commands.

```basic
accepted = castTarget(1, targetId)
if accepted = 0 and lastActionError() = ActionInsufficientMana then
  print "Need more mana"
end if
```

Read-only reason constants are `NoActionError`, `ActionNotAlive`,
`ActionInvalidSlot`, `ActionUnknownItem`, `ActionInsufficientGold`,
`ActionAlreadyEquipped`, `ActionStackFull`, `ActionInventoryFull`,
`ActionEmptySlot`, `ActionNotConsumable`, `ActionFullHealth`, `ActionFullMana`,
`ActionTargetUnavailable`, `ActionOutOfRange`, `ActionNoRoute`,
`ActionInvalidPoint`, `ActionCooldown`, `ActionNoCharges`,
`ActionInsufficientMana`, `ActionSpellLimit`, `ActionChanneling`,
`ActionStunned`, `ActionRooted`, `ActionOutsideKeep`, `ActionAbilityLocked`,
`ActionNoAbilityPoints`, `ActionAbilityMaxLevel`, `ActionHeroLevelRequired`,
`ActionNotDead`, `ActionMatchEnded`, `ActionDrafting`, `ActionNotDrafting`,
`ActionNotDraftTurn`, `ActionUnknownHero`, `ActionHeroTaken`, and `ActionSilenced`
(values 0 through 35).
Unavailable targets share a generic error without exposing hidden state.
This feedback is recorded deterministically through submitted replay actions.

For post-match analysis, the [replay extractor](../../docs/stats.md#gota-replay-events)
resimulates an exact-version replay and exposes typed damage, healing,
death, reward, and rejection events for all players. Its omniscient buffer
is not available to live BASIC policies.

## Terrain and execution

BASIC can inspect the complete static terrain with `terrainKind(x, y)`, `terrainWalkable(x, y)`, `terrainHeight(x, y)`, and `terrainWaterDepth(x, y)`. These use global tile coordinates on `selfLayer`. Each has an explicit `At(x, y, layer)` version, such as `terrainKindAt(x, y, GroundLayer)`. Read-only constants expose `mapWidth`, `mapHeight`, `mapLayers`, the layer names, and terrain kinds. Height and water depth use eighths of a tile; invalid or absent tiles return zero. The Terrain API section of the game documentation lists all constants and edge cases. Static terrain is available through fog, while enemy objects remain visibility-filtered. Walkability also includes team-known building footprints; unseen enemy destruction does not reveal newly open tiles.

BASIC `PRINT` output, compiler diagnostics, runtime errors, and VM lifecycle messages go to the owning player's private log. Each log is limited to 10 MiB. Runtime limit errors disable that VM; other seats continue. Invalid BASIC syntax fails the episode with a player failure diagnostic. Public game logs and action replays contain no BASIC source or private print output.

Battles run up to 28,800 deterministic ticks (20 simulated minutes), plus drafting time, without real-time pacing. Replays run entirely in the browser with playback, seeking, speed, and loop controls. The server exposes `/healthz`; legacy clients are static stubs.

The Competition league schedules 24 games per round with MMR-based matchups,
on a 32-minute interval. Each match uses ten distinct policies when at least
ten are eligible: five different policies on Red and five on Blue, with
one hero per policy. The scheduler uses `team_n`, `team_count: 2`,
`team_layout: "blocks"`, `matchmaking: "elo_softmax"`,
`matchmaking_temperature: 100`, and
`distinct_teammates: true`. Preserve these settings when updating the league.
Separate baseline filler policies complete short rosters and are not ranked
entrants. A policy controlling multiple heroes in a short-roster game receives
their average game score, so extra seats do not multiply it.

Standings use OpenSkill Plackett-Luce, starting with skill (`mu`) 25 and
uncertainty (`sigma`) 25/3. The board shows conservative MMR (`mu - 3 * sigma`),
skill, uncertainty, win rate, and mean game score. Teams are ranked by average
game score. Rating updates use placements rather than the score margin.
Matchmaking uses estimated skill (`mu`), including initial skill for new
entrants, while the leaderboard uses conservative MMR.

The former GotA OpenSkill league is retired. The student league,
NeuralHub at Diablo Valley College, has independent settings and must remain
unchanged when publishing game updates.

## BASIC numbers and coordinates

BASIC uses [Bassy](https://github.com/treeform/bassy) with [Fixxy](https://github.com/treeform/fixxy) Q16.16 decimals enabled. Globals and arrays retain fractional values across decisions. `/` performs decimal division; `\` performs integer division. Decimal operands must fit -32768 through 32767.99998. Integer-only calculations retain the full signed 32-bit range. When converting large world-unit observations, divide them as integers first, for example `(selfAttackRange \ 100) / (worldScale \ 100)` in GotA.

`and`, `or`, `xor`, and `not` are bitwise. Comparisons produce -1 for true and 0 for false; conditions accept any nonzero number. Host flags and action results remain 1 or 0, so use `flag = 0` instead of `not flag` to negate a host flag.

`walkTo(x, y)`, `attackMove(x, y)`, and `castPoint(slot, x, y)` accept fractional tile coordinates. For example, `walkTo(selfX + 0.25, selfY - 0.25)` selects a point a quarter tile from the current tile center. Integers continue to name tile centers. IDs, slots, indices, and terrain queries require exact integers. Passing a fractional value to an integer argument raises a BASIC error instead of truncating it. Accepted fractional destinations are preserved in action replays.

### Neutral camps

The 14 jungle clearings contain seven mirrored pairs. Each half has three
low, two medium, and two high camps. The map preset seed fixes each group's
appearance and size. Members share one chargen appearance, with one larger
leader whenever a group has at least two mobs. All neutrals use melee attacks.

| Tier | Mobs | Normal HP | Damage | XP | Last-hit gold |
| --- | --- | --- | --- | --- | --- |
| Low | 1–2 | 100 | 8 | 20 | 10 |
| Medium | 2–3 | 180 | 14 | 35 | 20 |
| High | 3–5 | 300 | 20 | 50 | 30 |

Leaders are 35% larger, have twice the health, deal 50% more damage, and give
twice the XP and gold. Camps attack when a hero or lane creep from either team
comes within two tiles of a living mob and is in its line of sight. Damaging a
mob also engages the whole group. Idle auto-attacks ignore resting camps,
while explicit attacks, attack-move, and damaging spells can engage them.
Lane creeps can pull camps by approaching, and fight back once the camp engages.
Pulled neutrals can also be attacked by towers.

The group returns home if a member or the aggressor goes beyond 12 tiles from
the camp center, or the aggressor dies or remains out of sight for three seconds.
Returning survivors cannot be damaged or controlled. They heal fully when all
survivors reach home. Dead members stay dead until the whole camp is cleared.
The full group respawns 60 seconds after the final death, waiting longer if any
living hero from either team is within ten tiles, including the boundary.
Creeps and dead heroes do not block respawns. Respawns receive fresh object IDs.

Only the last-hitting unit's team receives neutral XP. Eligible living heroes
within six tiles on the same navigation floor split the pool. An eligible hero
last hitter receives 15% first; the other 85% is shared among all eligible
heroes, including that hero. Otherwise the full pool is shared. Only a hero
last hitter receives gold. XP contributes to Emmett's Glory when the hero's team wins.

Neutral objects use `objectKind(i) = 6`, `objectTeam(i) = 2`, and
`objectClass(i) = 1`, `2`, or `3` for difficulty. Existing health, facing,
velocity, target, control observations and attack/cast commands apply. Units
are LOS-filtered through terrain and brush and grant neither team vision.

| Function | Result |
| --- | --- |
| `campCount()` | Number of public, static camp clearings. |
| `campX(i)`, `campY(i)` | Camp center in the usual team-relative map coordinates. |
| `campTier(i)` | Difficulty 1–3, or 0 for an invalid camp index. |
| `objectCamp(i)` | Zero-based camp index for a visible neutral, otherwise -1. |
| `objectLeader(i)` | 1 for a visible camp leader, otherwise 0. |
| `objectReturning(i)` | 1 for a visible neutral returning home, otherwise 0. |

Static camp queries never expose hidden living counts or respawn timers.
The reference policy farms nearby visible camps between lane fights, beginning
low camps at level 1, medium at level 4, and high at level 7. It starts with at
least 70% health, withdraws below 40%, and prioritizes enemy heroes and lanes.

`players/puller.bas` copies the reference policy and adds camp pulling. When
healthy and a nearby allied wave is available, it walks into neutral aggro,
then leads the camp through the wave without using offensive spells or items.
It resumes normal play when creeps take over, danger appears, or the attempt
times out. Attempts last at most 15 seconds, with 20 seconds between attempts.

To test five base bots against five pullers from the repository root:

```sh
nim r examples/gods_of_the_arena/gota.nim --bot examples/gods_of_the_arena/players/base.bas:5 --bot examples/gods_of_the_arena/players/puller.bas:5
```
