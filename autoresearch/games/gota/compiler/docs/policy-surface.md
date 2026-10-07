<!-- Destination adaptation, Task 2.3. Original Paintbot source hash remains in ../PROVENANCE.md. -->
# GotA compiler policy surface

Verified from cached Polyworld `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`,
`examples/gods_of_the_arena/{bots,sim,content,events,terrains}.nim`, and Bassy
`a02e6dd5889310b13d21725c891bde8d01539f08`, `src/bassy.nim`.
This is the unstructured legacy host used by v13-live. Do not add the
`@gota-structures` marker: that opts into a different host and storage contract.
The separate `games/gota/docs/policy-surface.md` is a historical September reference.

## Execution and storage

Ten independent hero VMs execute BASIC each decision, including while dead.
Platform seats 0–4 are Red and 5–9 Blue. Host hero IDs are 100–109; strategy role
seats remain 0–9 and the generator adds 100 when comparing selfId. Global/array
state persists. Array indices are zero-based with inclusive DIM bounds. Only SUB
parameters are local. Runtime errors disable the VM. A policy file contains no
outer infinite loop: completion returns control to the engine each decision.

Bassy supports integers and Q16.16 decimals. This compiler preserves the integer
strategy contract: use `\` for integer division; `/` produces decimals. Comparisons
return -1 or 0; logical operators are bitwise and evaluate both operands. Units
use the existing DIM/SUB/IF/WHILE subset. One DIM or SUB declaration per line is
required by packing, even though the engine permits additional syntax.

| Budget | Live hard maximum | Compiler gate |
| --- | ---: | ---: |
| Globals | 256 | 256 |
| Arrays | 32 | 32 |
| Array cells | 4096 | 4096 |
| Source bytes | 65536 | 65536 |
| Routines, including main | 64 | 64 |
| Static instructions | 20000 | 20000 |
| Instructions per decision | 100000 | 80000 |
| Work units per decision | 250000 | 200000 |
| PRINT bytes per decision | 1024 | 1024 measured |
| PRINT events per decision | 128 | 128 measured |

The generated telemetry retains the conservative 512-byte/64-event allocation;
that is compiler headroom, not a claim that the host PRINT maximum is smaller.
Generated PRINT uses semicolons: one event per item plus its final newline.
Comma separators, trailing semicolons and empty PRINT have different costs: `PRINT "X",12`
uses four events/five bytes; `PRINT "X";12;` two events/three bytes; empty PRINT one
of each. Shared native tests measure these actual callbacks. G3 measures whole-VM
PRINT, including every source line. Component units never PRINT directly.

## Read-only host data

The following case-insensitive names exactly match HeroDataNames, map/unit/layer
constants, and ActionError/HeroClass/TerrainKind values registered by initHeroHost.
This list is tested against the pinned cached source and `gota_host.HOST_DATA`.

```text
actionabilitylocked actionabilitymaxlevel actionalreadyequipped actionchanneling actioncooldown
actiondrafting actionemptyslot actionfullhealth actionfullmana actionherolevelrequired
actionherotaken actioninsufficientgold actioninsufficientmana actioninvalidpoint actioninvalidslot
actioninventoryfull actionmatchended actionnoabilitypoints actionnocharges actionnoroute
actionnotalive actionnotconsumable actionnotdead actionnotdrafting actionnotdraftturn
actionoutofrange actionoutsidekeep actionrooted actionsilenced actionspelllimit
actionstackfull actionstunned actiontargetunavailable actionunknownhero actionunknownitem
arcanist berserker bluefortlayer crossbowman deathknight
demonhunter drafting draftmode draftturnid druidwarden
groundlayer lich mapheight maplayers mapwidth
noactionerror ranger redfortlayer selfattackcooldown selfattackdamage
selfattackrange selfattackslanded selfchannelticks selfclass selfdeaths
selfgold selfhp selfid selflayer selflevel
selfmana selfmaxhp selfmaxmana selfmovespeed selfportalcooldown
selfrespawnticks selfrootticks selfsilenceticks selfstunticks selftarget
selfteam selfx selfy terraingrass terrainmarsh
terrainnone terrainroad terrainrock terraintrees terrainwall
terrainwater tickrate vanguardknight warlock waterlayer
worldscale worldtick
```

`selfX/selfY` and action positions use team-relative tiles, not Paintbot centimeters.
`worldScale` converts sub-tile world fields; `tickRate` supplies the tick frequency.
Query object-list indices with objectCount/objectId etc.; actions take stable IDs.
Pending spell lists and enemy observations retain the engine's visibility filters.
Draft status, silence/root/stun and cooldown observations are live inputs.

## Grammar keywords

These are Bassy isReserved grammar keywords (built-in function names are separately
reserved by the engine). Prefix component identifiers; never reuse host names.

```text
and as call case dim do else elseif end eqv
exit false for gosub goto if imp is let loop
mod next not on or print rem return select step
stop sub then to true type until wend while xor
```

## Host calls and generation authority

Use the exact v13-live reference supplied as `context/base.bas` for calls needed by
the baseline decomposition. It is the compatibility-adjusted submitted policy,
SHA-256 `1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
Do not copy it into the artifact or use it as an assembly fallback. Every executable
unit comes from declared strategy components and passes the unit namespace/ABI checks.

The canonical signature registrations are `bots.nim:initHeroHost` at the pin above.
Common calls include objectCount(), objectId(i), objectKind(i), objectX(i),
objectY(i), objectHp(i), walkTo(x,y), attackMove(x,y), attackTarget(id),
castTarget(slot,id), castPoint(slot,x,y), abilityLevel(slot), canLevelAbility(slot),
levelAbility(slot), buyback(), lastActionError(), and terrainWalkableAt(x,y,layer).
Unknown names or arities fail actual G1 compilation. COM components and Paintbot
heart/shout/grenade interfaces are not part of this compiler. Avoid inferring
signatures from the historical policy surface.

See [backend and packing](gota-backend.md) for the build, gate and immutable input contracts.
