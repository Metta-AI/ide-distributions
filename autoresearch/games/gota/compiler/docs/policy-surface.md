<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/docs/policy-surface.md -->
# Paintbot PW policy surface: what a script can know and do

> **Currency.** Raw BASIC host integration is unchanged at `coworld-v0.3.124` / `7a29ed7a`
> (2026-10-07): diff from the verified0.3.123/28030de6 contains training-map changes only. The engine now imports Bassy, pinned to `77629c038fd2161c61b6a89505f39efa6bb26617`
> in `coworld/dependencies.lock`. `src/polyworld/basic.nim` was deleted; its old line
> references below are historical anchors, not the current runtime. Neural ZIP/oracle
> details remain scoped to 0.3.89 and require separate requalification before use.
> `PW_DOCS_SHA` stays `118e1619` because those wider surfaces are not reverified.

Engine paths are relative to `Metta-AI/paintbot-pw`. Host integration is in
`examples/paintbot/bots.nim` and `observations.nim`; game rules are in
[mechanics.md](mechanics.md). Bassy source is resolved by the pinned dependency tool
under `tmp/coworld/deps/bassy/src/`, including `bassy.nim` and `bassy/numbers.nim`.
Frozen 0.3.89 policies under `reference/` are migration inputs, not current-runtime examples.

### Rules-49 BASIC additions

At `244dc62b`, `bots.nim:69-99` registers these names (all cost 4 work units):
`selfDestruct()`, `gunRange()`, `hasSniper()`, `mistingTicks()`, `radarTicks()`,
`radarBoost()`, `playerMisting(id)` and `playerRadar(id)`. They are reserved host names.
`gunRange` returns the current seat's actual range; player item readers are fog-gated.
Mister/radar timers report remaining ticks. `radarBoost` indicates doubled outgoing damage.
Self-destruct is a command for this tick only. See [mechanics](mechanics.md#5-combat).

Rules 49 give a cog one life: after death its BASIC program never runs again, so it cannot
print a later death event. The audit closes activations using the replay death instead.
The unchanged baseline compiles without host-name collisions but retains old strategic
assumptions; qualification measures compiler fidelity, not competitive suitability.

## 1. Upload formats

The pod entrypoint is `python /runtime/host.py` (`coworld/paintbot/Dockerfile`). It stages each
seat's file before the engine starts (`host.py:99-124`):

| Format | Detected by | Limits | Staging |
| --- | --- | --- | --- |
| Raw BASIC | anything not a ZIP | UTF-8, at most 128 KiB, not WASM (`host.py:43-53`) | written as the seat's source |
| Neural BASIC ZIP (0.3.89 reference; reverify before use) | starts with `PK\x03\x04` | exactly `manifest.json` (8 KiB), `policy.bas` (128 KiB), `model.bin` (16 MiB); no encryption (`neural_package.py:11-13`, `693-712`) | manifest schema `paintbot-neural-basic/1` or `/2`, SHA-256 of both payloads must match, contract hashes known and paired (a retired contract is refused by name, §5.10), decoder options and user inputs validated, a PWNET002 model's structure and operation budget checked for the match's seat count, and a teams.view.1 actor's input count checked (`neural_package.py:713-793`; the host passes the seat count, `host.py:105`). Writes `policy.bas` plus `.model.bin` / `.neural.json` sidecars (`neural_package.py:796-800`) |

WASM is no longer accepted (`host.py:45-46`). There is no other format. Any staging failure,
including a rejected neural package, puts an idle stub in that seat, but the platform then records
the episode as failed (§4). The host refuses a roster
of fewer than 2 or more than 256 seats (`host.py:89-90`, since 0.3.76); the `paintbot-pw` manifest's config
schema still requires exactly 16 `tokens`, so every paintbot-pw match has 16 seats.

## 2. The BASIC dialect (Bassy at 0.3.123)

The upstream migration guide is `docs/bassy-porting.md`; implementation is the pinned
Bassy dependency plus `bots.nim` / `observations.nim`. These are breaking changes for
policies generated for the former Polyworld BASIC runtime:

| Surface | Current behavior |
| --- | --- |
| Division | `/` performs fixed-point division (`3 / 2` is 1.5); `\` is integer division (`3 \ 2` is 1). Preserve truncation order when porting integer algorithms. |
| Booleans | Comparisons and `TRUE` yield -1; `FALSE` yields 0. `AND`, `OR`, `XOR`, `NOT` are bitwise. Normalize truth when source requires 0/1 or combines flags with comparisons. |
| Host arguments | Numeric coordinates and indices must be exact integers. Fractional intermediate values cannot be passed as integer host arguments. |
| Observation records | Host prepends `me` and `agents(Seats - 1)` declarations. `me` has self fields including `tick` and `seats`; agents expose visible/x/y/hp/team/carrying. Do not redeclare these records. Legacy scalar host data and functions remain registered. |
| Record refresh | Referenced self fields refresh each living decision. Agent columns load lazily through SeatView and charge four work units per seat per loaded column. They retain visibility/disguise constraints. |
| Record memory | Authored typed record arrays persist between decisions; bounds are inclusive. Writing observation records affects only local policy memory, never world state. |
| Execution | Bassy binds observations then calls `compileNative()`; decisions invalidate observation arrays, refresh scalars, reset the string pool and execute. |
| Budgets | `bots.nim:37-50`: 128 KiB source, 50,000 instructions, 125,000 work units, 2 MiB memory, 512 globals, 4,096 array elements, call depth 16, 1,024 print bytes and 128 print events. Compiler G3 intentionally uses lower thresholds. |

The strategy compiler preserves its int32/truncating/0-or-1 source contract using Bassy
integer division and explicit flags. Pre-Bassy units require a full rebuild; they cannot be
reused based on unchanged source text alone.
Do not infer compatibility from the unchanged rules number (49) or a successful tool build.

## 3. Per-tick execution

- Every tick, before the world steps, each **living, non-disabled** seat runs its script once from
  the first line (`bots.nim:244-279`). Dead cogs do not run (`bots.nim:249`), so a script sees
  nothing while waiting to respawn.
- `restart()` clears registers and budgets but **keeps globals and arrays** (`basic.nim:2267-2279`).
  Persistent memory = globals and arrays. The idiom is `IF started = 0 THEN started = 1 ... END IF`
  for one-time setup (`base.bas:97-114`).
- The string pool is reset every decision; handles do not survive to the next tick
  (`bots.nim:255`, `basic.nim:2790-2798`).
- All seats decide against the same frozen world, then the world steps once with all commands
  (`game.nim:583-602`; every perception builtin reads that world through the seat's `SeatView`,
  `seat_view.nim:73-94`). Host reads during a decision never see another seat's action.
- `END`, `STOP` or falling off the end finishes the decision.

### Commands: what persists and what does not

Commands are cleared every tick (`bots.nim:272`), but the engine keeps some state on the cog
(`mechanics.nim:617-621`):

| Call | Persists? |
| --- | --- |
| `walkTo(x, y)` | **Yes**: sets the cog's goal, which it keeps walking to until a new `walkTo` (or a respawn resets goal to the spawn point, `sim.nim:843`). |
| `lookAt(x, y)` / `shootAt(x, y)` | The aim point persists; it is also the centre of the vision cone. A `walkTo` without any aim call this tick turns the aim to the walk goal (`mechanics.nim:619-620`). |
| `shootAt` (the trigger) | No; must be called on the tick you want to fire. |
| `chargeGrenade(1)` | No; charge is kept, but **not calling it (or calling it with 0) releases the grenade** that tick if charge > 0. |
| `sneak(1)` | No; call every tick you want to sneak. |

The aim sentinel is the point (0, 0): `lookAt(0, 0)` does nothing (`mechanics.nim:619`, **inferred**
edge case).

### Budgets per decision (`bots.nim:34-47`)

| Limit | Value |
| --- | --- |
| Source | 128 KiB |
| VM instructions | **50,000** |
| Work units | **125,000** |
| Memory | 2 MiB |
| Array elements (total) | 4,096 |
| Globals | 512 |
| Call depth | 16 |
| Print | 1,024 bytes, 128 events |
| String pool | 1,024 handles, 64 KiB, 1,024 bytes per string (`bots.nim:174-177`, `basic.nim:2745-2749`) |

Work units: most VM ops cost 1-9 (`basic.nim:1776-1809`); each host call costs the value in its
registration (listed in section 5). The budget is checked at the start of each basic block, so a
block that would overrun fails before it runs (`basic.nim:2447-2458`). `PW_BASIC_PEAKS=1` prints
per-seat peaks in a local run (`game.nim:615-620`). Measured at `d0728ab1` over 11 local
seeds: `base.bas` peaks at 9,116 instructions (18% of 50,000) and 15,538 work units (12% of
125,000); `jev.bas` without an oracle at 13,909 / 21,140. Host costs are unchanged at 0.3.89 and a
`jev.bas` spot check (seed 3) gives identical peaks under 0.3.80 and 0.3.89. The guide's "5,670 /
8,722" (guide line 736) is out of date ([field analysis §8](reports/2026-09-29-league-field-analysis.md)).

Since 0.3.75 the instruction and work budgets scale with the seat count **above 16 seats only**
(`50,000 × seats / 16`, `bots.nim:43-45`), for crowd matches such as Heartland Big. At 16 seats
(every paintbot-pw match) they are exactly the numbers above.

## 4. Failure modes

| Failure | When | Consequence | Evidence |
| --- | --- | --- | --- |
| Host rejects the file (WASM, > 128 KiB, not UTF-8, a neural ZIP that fails its package checks, including a retired contract or decoder option, hash mismatch, failed download) | staging | The host writes a player-failure record naming the slot and stages an idle stub (`idle = 1`) in its place, so the engine starts (`host.py:96-124`, `31-40`). **The platform then records the episode as failed**: live on 2026-09-30 (0.3.89, round 2510), three episodes with `aaron-paintbot-pw:v57` ended `status: failed`, `error_type: player_error`, `error: "Policy initialization failed: ValueError"`, `failed_policy_index` = that policy's seat, with **no scores and no replay**. Treat a staging failure like a compile error: the episode is lost and attributed to the policy. (Which check that upload failed is not visible; the message carries only the exception type.) | `host.py:96-124`, `31-40`; live round episodes |
| **BASIC compile error** (syntax, unknown name, a host-function name such as `rnd` used as a variable, array or `SUB` name, calling an FFA-only function in the teams game, a limit exceeded at compile) | engine start | The engine writes a player-failure record naming the slot and **stops without writing results**: the whole episode fails for all 16 seats. | `bots.nim:194-195`, `coworld.nim:216-234` |
| Neural model rejected at load (dimensions, contract mismatch or wrong pairing, a retired contract, over the operation budget, teams.view.1 in FFA-kin or in a match without exactly 16 seats, ffa.view.1 in the teams game) | engine start | Seat is marked failed and **never runs**, not even its BASIC. Its cog stands at spawn all match. | `bots.nim:178-191`, `197`, `neural_host.nim:371-386`, `417-465` |
| **Runtime error**: instruction or work budget exceeded, divide by zero, array index out of range, bad string handle, string pool full, print limit, call depth, `rnd(n)` with n < 1, a neural host call used wrongly | any tick | Seat is **disabled for the rest of the episode**. The error goes to the seat's log ("BASIC error: ...") and its status becomes "BASIC VM disabled". | `bots.nim:263-266`, `249`, `149`, `coworld.nim:182-185`, `198-210` |

What a disabled cog does afterwards (**inferred** from `mechanics.nim:616-623`): it issues no new
commands, but the engine keeps walking it toward its last `walkTo` goal and keeps its last aim. It
never shoots, charges or captures by intent. After its next death it respawns and stands still.
It still counts toward team lives, so it can be farmed for kills.

Practical rules: bound every `WHILE` loop by a constant or by 16 / `heartCount()`; guard every
division; keep `PRINT` to a few lines per tick; never read a string handle from a previous tick;
cap `strNew` and `heardText` use (each distinct `strNew` literal is interned once per tick, but
`heardText`, `strCat` and friends each allocate).

## 5. Host API

All values are int32. Coordinates are world units (1 unit = 1 cm); **"Y" is the second
horizontal axis** (the engine's `z`). Unless noted, a function costs 4 work units. Source: the
registrations and costs in `bots.nim:48-157`, whose perception builtins each call one
`seat_view.nim` proc of the same name (the answers and fog rules live there, `seat_view.nim:98-467`),
plus `oracle.nim:247-340`, `basic.nim:3003-3093`, `neural_host.nim:744-870`.

### 5.1 Read-only data (set before each decision, `seat_view.nim:46-48`, `278-283`; `bots.nim:253-257`)

| Name | Meaning |
| --- | --- |
| `selfId` | seat 0-15 (0 .. seats−1 in a match of another size) |
| `selfTeam` | `selfId mod 2` (FFA-kin: the seat) |
| `selfX`, `selfY`, `selfHp` | position; HP 0-10 in rules 49 (teams before rules 49: 0-3) |
| `armorHp` | armor 0-3 |
| `livesLeft` | lives including the current one (1 at start in rules 49; previously 4) |
| `hasGrenade`, `hasSpray`, `grenadeCharge` | 0/1, 0/1, 0-24 |
| `trenchId` | trench index you stand in, -1 outside |
| `worldTick` | current tick |
| `homeX`, `homeY` | your team's home point (base heart area); FFA-kin: your spawn anchor |
| `heartX`, `heartY`, `ownHeartX`, `ownHeartY`, `ownHeartStolen`, `carrying` | **legacy capture-the-flag fields.** Under rules 47 nothing sets `carrying` (only rules < 13 code does, `mechanics.nim:822`), so these read the enemy home, your home, 0 and 0 (`seat_view.nim:249-262`). Use `control*` instead. |

### 5.2 Players (fog-gated)

Vision is the per-cog cone of mechanics.md section 6. Queries take an **observed identity**: a
disguised enemy answers to the seat it impersonates; of two visible bodies with one identity the
nearer is reported (`seat_view.nim:105-116`).

| Function | Returns | Hidden / invalid |
| --- | --- | --- |
| `visible(slot)` | 1 if seen (self is always 1) | 0 |
| `playerX(slot)`, `playerY(slot)` | position | -1 |
| `playerHp(slot)` | base HP | **0** |
| `playerCarrying(slot)` | legacy, always 0 | 0 |
| `playerTeam(slot)` | observed team (FFA-kin: seat) | -1 |
| `nearAgents(radius)` (16 WU) | count of seen agents within radius (clamped to 20,000), nearest first, max 64 | |
| `nearAgentId/X/Y/Hp/Team(k)` | entry k of the last `nearAgents` list | -1 (Hp 0) past the end |
| `hasUniform()` | 1 if **you** are disguised | |

`nearAgents` is cheaper than looping `visible(i)` over 16 seats in large games; `players/nearby.bas`
in the repo is `base.bas` rewritten that way (guide line 129).

### 5.3 Objectives (public, no fog)

| Function | Returns |
| --- | --- |
| `heartCount()` | 10 on Heartwick and the ten shipped-size generated maps; **100** on `big-twin-mesas` and **126** on `big-deep-forest` since 0.3.66 (verified with a local run; the map files are unchanged through `118e1619`) |
| `controlX(i)`, `controlY(i)` | heart position |
| `controlOwner(i)` | -1 neutral, 0 Red, 1 Blue (FFA-kin: seat) |
| `controlCaptureTeam(i)` | team (FFA-kin: seat) currently capturing, -1 idle |
| `controlCaptureTicks(i)` | 0-71 |
| `controlContested(i)` | 0/1 |
| `controlPoints(i)` | 1 (big hearts ended at rules 27) |
| `glory(team)` | current glory of team 0 or 1; -1 otherwise |
| `teamLives(team)`, `teamCogsOut(team)` | lives left (summed over the team's cogs) / cogs out of the match (dead, no lives left) for team 0 or 1; -1 for any other team and in FFA-kin (`seat_view.nim:353-354`, `359-360`). New in 0.3.72 (`teamLives`) and 0.3.75 / rules 47 (`teamCogsOut`); neither is version-gated, but only rules 47+ pays the cogs award |
| `awardBehind()`, `awardBehindSeconds()`, `awardBehindCogs()`, `awardBehindCogsSeconds()` | the match's configured behind-in-lives and behind-in-cogs glory awards and periods (league: 5 / 5 / 10 / 5); -1 in FFA-kin (`seat_view.nim:355-356`, `361-362`); see [mechanics.md §1.2](mechanics.md) |

Invalid indices return -1 (`seat_view.nim:375-392`).

### 5.4 Pickups and glory hearts (fog-gated)

| Function | Returns |
| --- | --- |
| `pickupCount()` | number of pickup stations (public) |
| `pickupVisible(id)` | 1 if ready and in view |
| `pickupX/Y(id)`, `pickupKind(id)` | position; 0 grenade, 1 spray, 2 medkit, 3 armor, 4 uniform. -1 if not ready or not in view |
| `gloryHeartCount()` | glory hearts on the field (public) |
| `gloryHeartX/Y(id)`, `gloryHeartTicksLeft(id)` | -1 if not in view |

A pickup that is respawning is indistinguishable from one out of view.

### 5.5 Map geometry (public)

| Function | Cost | Returns |
| --- | --- | --- |
| `mapMinX()`, `mapMinY()`, `mapMaxX()`, `mapMaxY()` | 4 | bounds |
| `terrainHeight(x, y)` | 4 | ground height (cm) |
| `waterAt(x, y)` | 8 | 1 if lake water |
| `trenchAt(x, y)` | 8 | trench index or -1 |
| `trenchCount()`, `trenchX/Y(i)` (centre), `trenchW/H(i)` | 4 | trench geometry |

### 5.6 Actions and `rnd`

| Function | Effect |
| --- | --- |
| `walkTo(x, y)` | set goal; the engine pathfinds (goal clamped 100 inside bounds) |
| `lookAt(x, y)` | set aim / facing (clamped to bounds) |
| `shootAt(x, y)` | set aim and pull the trigger: gun windup, or a spray burst if you hold a can |
| `chargeGrenade(held)` | nonzero = charge this tick; stop to throw along the aim |
| `sneak(on)` | half speed, no footstep sounds, this tick only |

`rnd(n)` (4 work units, since 0.3.89) returns 0 .. n−1 from the seat's own SplitMix64 stream,
seeded from the match seed and the slot on the seat's first decision (`bots.nim:9-13`, `29-33`,
`146-151`, `250-252`). It never touches the world's random stream or another seat's, so it does
not change the world hash; a tape replays the recorded commands, not the draws. `n` below 1 is a
runtime error that disables the seat (checked locally: `rnd(0)` → "seat disabled: rnd needs
n >= 1"). The stream is a deterministic function of (match seed, slot): the same seat in a
same-seed match draws the same numbers. Before 0.3.89 a script had to roll its own generator (as
`base.bas` does for its footwork legs), and `rnd` was an ordinary name; it is now taken (§2).

### 5.7 Speech and sound

| Function | Cost | Returns |
| --- | --- | --- |
| `shout(handle)` | 68 | 1 if queued; max 4 per tick (then 0); text truncated to 256 bytes |
| `heardCount()` | 4 | messages heard this tick |
| `heardSlot(i)` | 4 | speaker's **observed** seat (a disguised enemy shows its disguise) |
| `heardX(i)`, `heardY(i)` | 4 | speaker's **exact position** |
| `heardText(i)` | 4 | new string handle (allocates in the pool) |
| `soundCount()` | 4 | cues from the last 24 ticks |
| `soundKind(i)` | 4 | 0 footsteps, 1 gunfire, 2 explosion, 3 spray |
| `soundDirection(i)` | 4 | 0 E, 1 SE, 2 S, 3 SW, 4 W, 5 NW, 6 N, 7 NE |
| `soundDistance(i)` | 4 | 0 <= 6 m, 1 <= 18 m, 2 farther |
| `soundAge(i)` | 4 | ticks |

Shouts are delivered on the **next** tick to every other living cog within 12.8 m, **both teams**,
regardless of vision (`bots.nim:56-58`, `seat_view.nim:324-333`). A shout therefore reveals your exact
position to any enemy in earshot. `PRINT` is private.

String toolkit (`basic.nim:3003-3093`, unchanged since the string pool was added), cost in work
units: `strNew("lit")` 68 (returns the interned literal), `strLen` 2, `strByte(h, i)` 3, `strAsc`
3, `strChr` 6, `strFromInt` 8, `strVal`, `strCat`, `strCatInt`, `strMid`, `strEq`, `strCmp`,
`strWord`, `strWordCount`, `strUpper`, `strLower`, `strTrim` 68 each, `strFind` 1,028. The 68 is
`linearCost = 4 + maxStringLength div 16` and the 1,028 `searchCost = 4 + maxStringLength`, both
from the 1,024-byte string limit (`basic.nim:3009-3010`, `2748`); the cost is flat, whatever the
string's actual length. Handle 0 is the empty string. A literal shout (`strNew` + `shout`) costs
136 work units, so four shouts a tick are 544 of the 125,000 budget; parsing heard text with
`strWord`/`strEq` costs 68 per call, and a `strFind` scan is the one string call worth budgeting.

### 5.8 FFA-kin only (Heartland)

Heartland is its own coworld (`heartland`, tags `heartland-v*`) since 2026-09-28; the
`paintbot-pw` manifest no longer ships the `heartland` variants, though its config schema still
accepts `"mode": "ffa_kin"`. It runs the same engine, so this section stays as a reference.

Registered only when `mode` is `ffa_kin` (`bots.nim:119-133`). In the teams game these names do
not exist; a teams script may use them as variables, and **calling one there is a compile error,
which fails the whole episode** (section 4).

`gameMode()` (1), `seatCount()` (new: 16, or 50 in Heartland Big), `kin(slot)` (round(100 r):
100, 50, 25, 0; -1 invalid), `gene(slot, i)`, `seatScore(slot)` (raw score in tenths),
`seatAlive(slot)`, `heartOwner(i)`, `territoryBoost()`, `greatHeartCount()`, `greatHeartX/Y(i)`,
`greatHeartPresent(i)`, `greatHeartProgress(i)` (0-119), `greatHeartDormant(i)`. Before rules 48
all were public with no line of sight. **Rules 48 (FFA-kin fog of war):** `kin`, `gene`,
`seatScore` and `seatAlive` read -1 for any seat other than yourself that you cannot see this
tick (`seat_view.nim:394-417`, `sim.nim:240-245`); `seatCount`, hearts and great hearts stay public.

### 5.9 Advisor oracle (0.3.89 reference) (`oracle.nim`, `runtime/oracle.py`)

A seat can have the host ask an LLM on its behalf. Asking never blocks; answers arrive on a later
tick.

| Function | Cost | Semantics |
| --- | --- | --- |
| `oracleAvailable()` | 4 | 1 if the host has an oracle |
| `oracleReady()` | 4 | 0 = an ask would be accepted; >0 = ticks to wait; -1 = no oracle or a request in flight |
| `oracleState(key, int)`, `oracleStateText(key, text)` | 8 / 16 | add a `state` field (max 256; keys 1-64 bytes; `a.b[0].c` paths build nested JSON) |
| `oracleNote(text)` | 16 | append to `state.notes` (max 16) |
| `oracleQuestion(key, kind, instructions)` | 16 | kind 0 yes/no, 1 score, 2 choice (max 64 questions) |
| `oracleCriterion(key, label, text)`, `oracleCriterionField(key, label, field, text)` | 16 | criteria (max 16 per question) |
| `oracleAsk()` | 68 | returns request id >= 1, or 0 if refused (no oracle, one in flight, fewer than 24 ticks since the last ask, no question, body over 32 KiB). Always clears the draft. |
| `oraclePoll(id)` | 4 | 0 pending, -1 failed or unknown, else number of answers |
| `oracleAnswer(id, key)` | 8 | yes/no: P(true) x 1000; score x 1000; choice: criterion index; -1 missing |
| `oracleConfidence(id, key)`, `oracleProbability(id, key, label)` | 8 | x 1000, -1 missing |

- The draft is cleared at the start of every tick (`oracle.nim:100-103`): build and ask in one
  decision. Store the request id in a global.
- One request in flight per seat; minimum 24 ticks between asks (`oracle.nim:18`, `297-298`);
  the last 4 answers are kept (`oracle.nim:17`, `133`).
- Hosted: the host posts to the platform LLM sidecar (`/v1/systemone`, model
  `typesafe/jev-1.13` per guide lines 759-770), with a 2 s deadline plus grace
  (`oracle.py:52`, `105`). Cost counts against the seat's per-episode LLM spend limit; a seat with
  no budget gets no answers. Whether the `paintbot-pw` leagues grant any spend is an **open
  question**.
- Every ask and answer is journaled to the asking seat's private log (`bots.nim:160-163`,
  `oracle.nim:303-314`).

### 5.10 Neural BASIC (0.3.89 reference) (ZIP uploads)

A secondary lane for this lab (we upload plain BASIC), documented so it can be used or read
correctly. Upstream references: `neural_basic.md` (package, selection options, BASIC I/O),
`neural_actor.md` (actor formats, contract columns) and `docs/neural/seat-view.md`.

**Since 0.3.89 a neural seat sees and acts exactly like a plain BASIC seat.** Its observation is
built only from its `SeatView`, the values its BASIC builtins read on the same tick, and the
network's output reaches the game only through `policy.bas`: the neural builtins return numbers
(observation values, logits, head choices) and the script calls `walkTo`, `lookAt`, `shootAt`,
`chargeGrenade`, `sneak` and `shout` itself (`neural_host.nim:1-4`, `744-747`). The minimal loop:

```basic
paintbot_observe(neuralObservation())
run_neural_net(neuralModel(), neuralObservation(), neuralLogits(), neuralState())
neuralSample()
' then turn neuralChoice(0) .. neuralChoice(4) into walkTo / lookAt / shootAt / ...
```

`examples/paintbot/players/neural_decode.bas` is the reference reading of the teams heads (the
training library decodes with the same file) and `players/neural_policy.bas` is a complete
policy: the three lines above followed by that decoder.

**Per-tick calls** (`neural_host.nim:744-870`).

- `paintbot_observe` costs 512 work units, `run_neural_net` 16, `neuralSample` 48, `neuralRow` 8,
  every other neural builtin 4. Observe, infer and sample each at most once per tick, in that
  order; misuse (a repeat, a wrong handle, inference without a fresh observation, reading a
  choice before `neuralSample`, an out-of-range index, a head with every choice masked,
  non-finite logits) is a runtime error that disables the seat (`neural_host.nim:570-572`,
  `753-777`, `824-835`).
- None of them acts. `paintbot_act`, `neuralDecode`, `neuralIssue`, the `cmdWalk` … `cmdDirect`
  readers, `cmdSet`, `neuralGoalX/Z` and `neuralAimX/Z` were removed in 0.3.89: a script that
  calls them no longer compiles.
- Recurrent state resets at match start, on death and on respawn; user inputs persist across
  deaths and restart from `init` each match (`neural_host.nim:512-526`).
- Native inference has its own budget: 4,000,000 counted operations per seat per tick at 16
  seats, scaled by seats/16 above 16 (`neural_host.nim:10-15`), checked once at load against the
  model's published operation count (`neural_host.nim:409-415`); over budget, the seat is refused
  at load and its log gets a `neural: peak_ops=...` line. Every neural seat logs that line at
  match end (`neural_basic.md` "Budget and telemetry").

**Contracts** (`neural_contract.nim:11-34`; staging `neural_package.py:21-25`, `731-740`). The
actor's embedded hashes select the encoder and must equal the manifest's:

| Observation contract | Id | Floats | Game |
| --- | --- | --- | --- |
| teams.view.1 | `paintbot-pw.teams.view.1` | 512 = self 25, 10 hearts × 10, 16 identities × 10, 32 pickups × 5, 8 sounds × 5, 9 probes × 3 (`neural_contract.nim:38-53`, columns in `encodeTeamsView`, `196`) | teams game, exactly 16 seats |
| teams.view.1u*K* | `paintbot-pw.teams.view.1u<K>` | 512 + K; K user inputs, 1-128, set from BASIC with `neuralInput` (`neural_package.py:67-72`) | as teams.view.1 |
| ffa.view.1 | `paintbot-pw.ffa.view.1` | per match (`ffaViewLayout`) | FFA-kin at any seat count |

| Action contract | Id | Heads |
| --- | --- | --- |
| teams.view.1 | `paintbot-pw.teams.view.1.action.51-25-2-2-2` | `[51, 25, 2, 2, 2]`: movement (0 stay, 1-10 hearts, 11-42 visible pickups, 43-50 compass steps), aim (0 keep, 1-16 identities, 17-24 compass), fire, grenade, sneak, as `neural_decode.bas` reads them |
| teams.view.1 aim-offset | `paintbot-pw.teams.view.1.action.51-25-2-2-2-23-23` | the same plus two 23-bin heads (`neuralChoice(5)`, `neuralChoice(6)`); the reference decode adds `((ix − 11) × 28, (iz − 11) × 28)`, mirrored for team 1, to an identity aim point. Nothing native computes a lead (`neural_contract.nim:19-29`) |
| ffa.view.1 pointer | `paintbot-pw.ffa.view.1.action.pointer` | sized by the match; the objective and aim heads point at the ffa.view.1 rows of the same tick |

teams.view.1 (and u*K*) pairs with either teams action contract, ffa.view.1 with its pointer
contract (`neural_host.nim:381-386`). teams.view.1 is refused in FFA-kin and in any match that
does not have exactly 16 seats; ffa.view.1 is refused in the teams game (`neural_host.nim:371-379`).
Every paintbot-pw match has 16 seats, so for this league the choice is teams.view.1 or its
u*K* form with one of the two teams action contracts.

**Retired in 0.3.89 and refused by name** at staging and at load: observation contracts v1, v2,
v3, v2u*K*, v3u*K*, ffa.v1 and ffa.v2; action contracts v1, v2 and ffa.v2 pointer
(`neural_package.py:40-52`, `728-730`). Their observations carried state no BASIC builtin reads
(gun cooldown and windup, spray cooldown, shield, respawn, the current aim, heart meters, the end
tick, blocked/traversable probes) and their actions were decoded natively (identity lead, planned
own step) (`neural_basic.md` lines 38-45). A bundle built on them must be retrained; uploaded as
is, it fails staging (§4).

**Actor formats** (`neural_actor.md`). `PWNET001`: one fixed MinGRU, hidden 64/128/256.
`PWNET002`: a layer stack from a fixed menu (1 DENSE, 2 RMSNORM, 3 MINGRU, 4 RESIDUAL,
5 ENTITY_ATTN, 6 CONCAT_INPUT, 7 TOKEN_MLP, 8 TOKEN_MIX, 9 POINTER, 10 SEGMENT_NEAR,
11 ATTN_POOL, 12 PAD, 13 COND_HEAD, 14 TOKEN_PAIR; TOKEN_MLP/TOKEN_MIX may carry a LayerNorm),
at most 64 layers, widths up to 4,096, 4,194,304 parameters, 4,096 state floats
(`neural_package.py:218-220`, layer checks `430-680`). COND_HEAD is a learned conditional head:
it may name only heads 0-4 and cannot be combined with `decoder.joint_sampling`
(`neural_package.py:781-784`). A structural **layout word** (high 16 bits `0xFFFE`) is resolved
against the match's ffa.view.1 layout at load and is refused under any other contract
(`neural_package.py:100-106`, `211-217`).

**Manifest and selection options** (schema `paintbot-neural-basic/2`). Default selection is
headwise argmax. A schema-2 manifest may add `decoder` options, validated identically at staging
and load (`neural_package.py:742-764`; `neural_host.nim:306-361`), all of which only reshape the
selection from the network's own distribution: `sampling` (categorical, temperature 0.01-10,
chosen heads, 5 and 6 only under aim-offset; a seat-owned stream seeded from the match seed and
slot, so the world hash is untouched), `forbid_objectives` (movement choices never selected) and
`joint_sampling`; plus `user_inputs` `{"count": K, "init": [...]}` for teams.view.1u*K* (values
±1,000,000, read by the net as value/1000 one tick later). Under ffa.view.1 pointer only
`sampling` is accepted. The retired native rules `fire_hold_teammates`, `strafe_legs`, `aim_snap`,
`steady_shot`, `aim_retarget`, `shot_gate`, `spray_aim` and `spray_gate` are refused by name
(`neural_package.py:57-58`, `749-751`): write them in `policy.bas`.

**Head-level BASIC API** (`neural_host.nim:744-870`; `neural_basic.md` lines 85-113):
`neuralInput(i, v)`; `neuralObs(i)` (observation × 1000); `neuralLogit(i)` (logit × 1000, after
`run_neural_net`); `neuralMask(h, bits)`, `neuralMaskFrom(h, first, bits)`,
`neuralTemperature(h, milli)` before selection; `neuralSample()`; `neuralChoice(h)`;
`neuralSetChoice(h, i)`; `neuralLayout(i)` (observation layout words 0-15, head sizes 16-22) and
`neuralRow(section, k)` (ffa.view.1 only: which seat or heart row k shows this tick).

**These names exist in every seat.** The neural builtins are registered for plain BASIC seats too
(`bots.nim:53`), so, like every host function, none of them can be used as a variable, array or
`SUB` name (§2).

## 6. Starter policies (`../reference/`)

The copies in `../reference/` are the files the league runs: `base.bas`
(`sha256:3679f5bb...`) and `jev.bas` (`sha256:fec43ac7...`) from `coworld/paintbot/players/`.
Both files are byte-identical from `d0728ab1` (0.3.79) through `118e1619` (tag
`coworld-v0.3.89`; re-hashed against the tag 2026-09-30), so every line number below holds at
both. Their hashes equal the `player[].file` entries of the live coworld record saved as
`../reference/manifest-0.3.80.json` (read 2026-09-29). Against the
**0.3.65** files (`sha256:587cbe41...`/`c36db417...`, `../reference/manifest-0.3.65.json`)
they differ only in array sizes and loop caps raised from 16/32 to 64 (`avoidUntil`,
`pickupMemory*`, the heart and pickup loops) so they do not overrun on the big maps' 100+
hearts, on the same lines.

`../reference/heartland/` holds `ffa.bas` and `ffa_blind.bas` from `coworld/heartland/players/`
(unchanged from `d0728ab1` through `118e1619`; 932/936 lines, rules-48 fog: remembered kin,
256-seat arrays). They target the separate Heartland coworld and **do not compile in the teams
game** (`pw_local.py compile` at coworld-v0.3.79: `compile_failed` on every seat).

### `base.bas` (manifest `baseline`, 677 lines)

Each cog, every tick:

1. **Scan** all 16 seats: pick the best visible enemy within 52.5 m (nearest, wounded favoured),
   count foes within 26 m and friends within 12 m (`base.bas:142-180`).
2. **Squads**: seats `(selfId / 2) mod 8` split into two squads of four. Each squad picks the
   cheapest heart not owned by us, measured from a reference point above or below home (mirrored
   for Blue), neutral hearts preferred, and hearts it failed to reach in 3 s avoided for 15 s. Two
   members stand in the ring; two cover from outside and step in if nobody is capturing
   (`base.bas:204-298`). No communication: the choice is a pure function of public ownership.
3. **Supplies**: remembers seen pickups for 10 s; detours for a grenade if unarmed, a medkit if
   hurt, armor if at full HP, when no enemy is close (`base.bas:300-344`). It never takes spray or
   uniforms.
4. **Refuse bad fights**: more foes than friends nearby, head for the heart far from them and near
   us (`base.bas:346-370`).
5. **Look**: with no target, sweep in a 4-way cycle, face the latest speaker, or the most recent
   gunfire/explosion sector (`base.bas:372-434`). Routine shouts every 15 s, staggered by seat
   (`base.bas:436-446`).
6. **Footwork**: in contact, random 3-9 tick legs across the line of fire; shots are ordered only at
   the start of a leg long enough to cover the windup (`base.bas:448-493`).
7. **Dry route**: if the straight line to a target over 8 m away crosses water, detour through
   the best of six side points (`base.bas:494-564`).
8. **Aim**: lead the target by 6 ticks of its last motion, subtract its own drift, hold fire if a
   visible teammate is within 95 units of the line; tracks its own cooldown estimate
   (`gunWait`) because BASIC cannot read it (`base.bas:566-622`).
9. **Grenade**: charge to match distance (4-12.5 m) unless a visible teammate is within 4.5 m of
   the target; it does not check its own distance to the blast (`base.bas:634-665`).
10. **Sneak** near the objective when something is heard but nothing seen (`base.bas:667-674`).

It ignores glory entirely: it takes supplies (which resets the quiet-supplies clock), never
seeks glory hearts, and never reads `teamLives`/`teamCogsOut`. Its header comment claims a 20,000-instruction budget (stale; the budget is
50,000).

### `jev.bas` (manifest `basic-jev`, 2,644 lines)

`base.bas` with an advisor layer spliced in by `coworld/paintbot/tools/make_jev_baseline.py`
(`jev.bas:1-8`; the same 64-cap edit as `base.bas`). One cog per squad asks the oracle to choose the squad objective from three
code-ranked capture candidates (or keep current), relays the pick by shout (`"Alpha, push
Forge."`), and squadmates adopt it silently. It carries dozens of switches in its init block
(`jev.bas:570-713`): `useObjective`, `useRetreat`, `useDial`, `useNouls`, `useRelay`,
`useLeader`, `useExamples` and the logging switch `useTrace` (one seat per team logs hearts held
and score once a second, `jev.bas:705-707`) are on and the rest off. With no
oracle every ask is refused and it plays exactly like `base.bas` (guide lines 849-853, not
re-verified by running). Because its
callouts are public within 12.8 m, enemies in earshot can read the squad plan.

### `heartland/ffa.bas` (Heartland baseline, 932 lines) and `heartland/ffa_blind.bas`

FFA-kin only; it calls `kin`, `seatAlive` and other FFA functions, so **uploading it to a teams
league fails the episode at compile**. It never shoots cogs with `kin >= 50` (nor cousins),
prefers strangers seen hurting relatives, splits hearts among siblings by rank so no two stand on
one heart, guards held hearts, joins great hearts when others gather, and late in the match waits
at a great heart or steals a stranger's heart (`ffa.bas:1-22`). Since rules 48 it remembers each
cog's `kin` once seen and treats a never-seen cog as a stranger (guide line 496). Movement, aim, dodge and dry-route
code are `base.bas`'s. `ffa_blind.bas` is a generated control that replaces every `kin(x)` with
"100 for me, 0 for everyone else" (`diff ffa.bas ffa_blind.bas`).
