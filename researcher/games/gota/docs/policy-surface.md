<!-- Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/docs/wiki/policy-and-host-surface.md -->

> Historical source reference. This file describes the pinned September source.
> See [toolkit provenance](../PROVENANCE.md) for verified live replay adaptations.

# Gods of the Arena — policy and host surface

> **Currency.** Verified at polyworld `7365e4e9` (coworld 2026.9.16.3, 2026-09-16).
> Re-verify when the league's manifest points at a new polyworld commit: diff
> `examples/gods_of_the_arena/bots.nim`, `observations.nim`, and `src/polyworld/basic.nim`.

## The language

Polyworld implements a small, custom BASIC compiler and register-machine interpreter
in Nim. It resembles a structured QBasic subset; it is not full QBasic, FreeBASIC,
Visual Basic, Python, or Nim. The uploaded artifact is plain BASIC source in one file.

| Surface | Supported behavior |
| --- | --- |
| Values | Signed 32-bit integers; variables initially zero; case-insensitive names |
| Arithmetic | `+ - * / MOD`; integer division; wrapping integer overflow |
| Conditions | `= <> < <= > >=`, logical `AND OR XOR NOT`; false is zero |
| Control flow | Structured `IF … THEN … ELSE … END IF`, `WHILE … WEND` |
| Storage | Global scalar variables and fixed one-dimensional `DIM` arrays |
| Procedures | `SUB … END SUB`, parameters, calls, `RETURN`, `EXIT SUB`; no user functions returning expression values |
| Termination | End of source, `END`, or `STOP` ends this decision |
| Logging | `PRINT` with literal text and integer expressions; apostrophe/`REM` comments |

Array bounds are inclusive: `DIM seen(9)` allocates ten integers, indexed 0–9.
Arrays are declared at top level with literal bounds. Parameters are local to calls;
ordinary scratch variables are globals, including those assigned inside subroutines.
Do not assume dynamic arrays, structures, imports, FOR/NEXT, GOTO, floating point,
or general string variables exist. The generic VM has optional string-host support,
but GotA does not register those string functions.

Boolean operators evaluate both operands. Use nested IF blocks when a second
expression is only safe conditionally. Division by zero and invalid array access
raise VM errors. Integer scaling is possible, but intermediate overflow wraps.

Sources: [compiler/runtime](https://github.com/Metta-AI/polyworld/blob/7365e4e9/src/polyworld/basic.nim),
[language examples/tests](https://github.com/Metta-AI/polyworld/blob/7365e4e9/tests/test_basic.nim).

## How a policy executes

There are ten independently controlled heroes. Platform seats 0–4 are Red; 5–9 are
Blue. One supplied file controls one hero. Reusing a file across seats creates
separate VMs, with no shared script memory.

The source is compiled at initialization. Each living hero's decision restarts from
the top every simulation tick (24 ticks per simulated second). Globals and arrays
persist; instruction/work/logging budgets reset. There is no need to write an endless
outer game loop. Dead heroes skip decisions, and a runtime-failed VM stays disabled.
This is deterministic simulation time, not a promise of 24 wall-clock calls/second.

Sources: [host lifecycle](https://github.com/Metta-AI/polyworld/blob/7365e4e9/examples/gods_of_the_arena/bots.nim),
[simulation](https://github.com/Metta-AI/polyworld/blob/7365e4e9/examples/gods_of_the_arena/sim.nim).

## Observation and action surface

Read-only self data includes identity, team/class, tile position/layer, HP/mana,
gold, level, world tick, movement speed, basic-attack range/damage/cooldown, the
current attack target, and lifetime basic hits landed. Object queries include
allied objects and visible enemy objects' IDs, kinds (1 fort, 2 hero, 3 footman,
4 tower, 5 barracks), teams/classes, tile positions, HP and alive/attackable
status, plus hero level, mana and inventory, unit facing, current target, and
last-tick velocity. A pending-spell list exposes every unresolved allied cast and
every enemy cast whose aim tile your team can see: ability, caster (0 when the enemy
caster is hidden), impact tile, and impact tick. Object-list and spell-list indexes
are temporary; use object IDs for actions. Enemy objects remain visibility-filtered.
Static terrain can be queried through fog. Read `mapWidth`, `mapHeight`, and layers
for the active map dimensions; `worldScale` (60000 world units per tile) and
`tickRate` (24) convert the sub-tile fields.

| Calls | Use |
| --- | --- |
| `walkTo(x,y)` | Request movement; clears the attack target |
| `attackTarget(id)` | Select an enemy; engine approaches and repeats basic attacks |
| `buyItem(id)`, `useItem(slot)` | Manage six inventory slots and purchases |
| `castTarget(slot,id)`, `castPoint(slot,x,y)` | Explicitly cast abilities, indexed 0–3 |
| `abilityCharges(slot)`, `abilityCooldown(slot)`, `abilityRecharge(slot)` | Inspect current ability availability |
| `spellCount()`, `spellAbility(i)`, `spellCasterId(i)`, `spellX(i)`, `spellY(i)`, `spellImpactTick(i)` | Read pending casts and area warnings your team can see |
| `terrainKind`, `terrainWalkable`, `terrainHeight`, `terrainWaterDepth` | Read static terrain on selfLayer; each also has an explicit-layer `At` form |

Action calls report accepted/rejected. Acceptance alone does not prove an eventual
hit, arrival, or objective effect. Bot combat also has automatic ability behavior;
explicit casting exists alongside it. The starter makes no explicit spell calls.

There is no registered file, network, external LLM, or inter-hero chat API. `PRINT`
is private diagnostic output. Development tools outside the game may generate or
analyze BASIC, but the submitted policy still executes within this host.

The object list and the self fields are both sampled at the start of the decision
and stay fixed through it: after buying or casting, `selfGold` and `selfMana` still
contain that decision's original values and the object list is not refreshed.
Inventory, ability, and spell queries read the current world, so a successful cast
shows up in `spellCount()` during the same decision.

`walkTo` clears combat targeting before pathfinding; a return of zero can therefore
still have that side effect. `attackTarget` acceptance checks enemy identity and
life; visibility and structure exposure are enforced later in combat. Return 1
is not proof that the target is currently hittable; `selfTarget` on a later tick
shows whether the engine still holds the order. Stay within observed targets
and intended game rules.

`terrainWalkable` also reflects building footprints: a tile under a tower or
barracks reads 0 until your team has seen that building destroyed. Unseen enemy
destruction does not reveal newly open tiles.

Recorded action entries capture requests before acceptance; they are not counts of
successful actions. Accepted commands increment the command metric; actual effect
still requires inspecting impact. See [mechanics](https://softmax.com/gods-of-the-arena/wiki/mechanics).

Source: [registered host API](https://github.com/Metta-AI/polyworld/blob/7365e4e9/examples/gods_of_the_arena/bots.nim).

## Enforced limits

These are GotA's overrides, not the much larger generic VM defaults.

| Limit | Value |
| --- | --- |
| Source | 64 KiB |
| Compiled code | 20,000 VM instructions |
| Executed instructions | 20,000 per hero decision |
| Work budget | 50,000 units per hero decision |
| Logical VM memory | 2 MiB |
| Globals | 256 |
| Registered host data / functions | 64 / 64 maximum |
| Arrays | 32, with 4,096 total integer elements |
| Routines | 64 including the top-level routine |
| Parameters | 16 per routine |
| Registers | 256 |
| Syntax nesting / call depth | 32 / 16 |
| PRINT | 1,024 bytes and 128 events per decision |
| Private player log | 10 MiB per episode |

Work units charge expensive operations separately: `walkTo` costs 800,
`castTarget`/`castPoint` 80, attack/buy/use 20, terrain queries 32, the extended
object queries (`objectLevel` through `objectVelY`) and every spell query 16,
the basic object-field and inventory/ability queries 4, and `objectCount` 2.
Ordinary bytecode execution also consumes budget. These are deterministic
operation limits, not a wall-clock inference allowance.

Compilation failure fails the episode with a player diagnostic. A runtime BASIC
error (including exhaustion) disables that hero VM; other seats continue. A script
that compiles can still exhaust its runtime budget, including during a busy late-game decision.

Sources: [exact limits](https://github.com/Metta-AI/polyworld/blob/7365e4e9/examples/gods_of_the_arena/bots.nim),
[private logging and compilation failure](https://github.com/Metta-AI/polyworld/blob/7365e4e9/src/polyworld/coworld.nim).

## Complete host names and units

Self data: `selfId selfTeam selfClass selfX selfY selfHp selfMaxHp selfMana
selfMaxMana selfGold selfLevel worldTick selfLayer selfMoveSpeed selfAttackRange
selfAttackDamage selfTarget selfAttackCooldown selfAttacksLanded`.
Map data: `mapWidth mapHeight mapLayers`.
Unit constants: `worldScale` (60000 world units per tile), `tickRate` (24 ticks per second).
Layer constants: `GroundLayer RedFortLayer BlueFortLayer WaterLayer`.
Terrain constants: `TerrainNone TerrainGrass TerrainRoad TerrainRock TerrainTrees
TerrainMarsh TerrainWall TerrainWater` (enum values 0–7).

| Queries | Arguments | Work |
| --- | --- | --- |
| `objectCount()` | None | 2 |
| `objectId`, `objectKind`, `objectTeam`, `objectClass`, `objectX`, `objectY`, `objectHp`, `objectAlive` | Object-list index | 4 |
| `objectLevel`, `objectMana`, `objectFacingX`, `objectFacingY`, `objectTarget`, `objectVelX`, `objectVelY` | Object-list index | 16 |
| `objectItemId`, `objectItemCount` | Object-list index, inventory slot 0–5 | 16 |
| `spellCount()` | None | 16 |
| `spellAbility`, `spellCasterId`, `spellX`, `spellY`, `spellImpactTick` | Spell-list index | 16 |
| `itemId`, `itemCount` | Inventory slot 0–5 | 4 |
| `abilityCharges`, `abilityCooldown`, `abilityRecharge` | Ability slot 0–3 | 4 |
| `terrainKind`, `terrainWalkable`, `terrainHeight`, `terrainWaterDepth` | x,y; current hero layer | 32 |
| `terrainKindAt`, `terrainWalkableAt`, `terrainHeightAt`, `terrainWaterDepthAt` | x,y,layer | 32 |

Positions (`selfX/Y`, `objectX/Y`, `spellX/Y`) are whole tiles; `selfMoveSpeed`,
`selfAttackRange`, facing and velocity are world units (divide by `worldScale`).
The "Y" of every query is the second horizontal axis, not height.

There is no registered `objectLayer`, maximum-object-HP query, direct attack-move,
sell-item or manual-spell-mode function, even where internal engine helpers exist.
Read host registration before assuming an engine function is callable from BASIC.
Terrain queries return zero for invalid/missing tiles; zero is also a valid result
for some fields. The extended object queries return zero for an invalid index or a
field that does not apply (level, mana, and inventory are zero for non-heroes);
`spellAbility` returns -1 for an invalid index, the other spell queries zero.
Ability cooldown/recharge values are ticks (24 per simulated second).

## Starter

The official [base.bas](https://github.com/Metta-AI/polyworld/blob/7365e4e9/coworld/gota/players/base.bas) selects the nearest visible living enemy,
issues an attack, uses and buys supplies/equipment, and walks to (64,64) if no enemy
was selected. It does not explicitly cast spells and uses none of the extended
observations. Its globals persist, but targeting
is recalculated every decision. The literal (64,64) is the starter destination,
not the center of the current default 116×116 map or a suggested policy objective.

See [mechanics](https://softmax.com/gods-of-the-arena/wiki/mechanics) for game rules and [game guide](https://softmax.com/gods-of-the-arena/wiki/game-guide) for kits/items.

---
Maintained by Codex, an automated agent working for James Boggs.
