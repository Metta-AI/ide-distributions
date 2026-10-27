<!-- Source: Metta-AI/polyworld-pudge-wars @ cd8111a5ee4f2fdc38c027615b33421f6c7d2536; Bassy treeform/bassy @ a02e6dd5889310b13d21725c891bde8d01539f08 -->

> **Provenance.** Pudge Wars Basic, Coworld `pudge-wars-basic` 0.1.8
> (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source commit `cd8111a5`; the BASIC VM is
> `treeform/bassy` pinned by `nimby.lock:2-2@cd8111a5` at `a02e6dd5`. Written 2026-10-26 (PDT).
> Game citations are `path:a-b@cd8111a5`; Bassy citations are `bassy:path:a-b@a02e6dd5`.
> Re-verify `examples/pudge_wars/bots.nim` when the manifest's source commit changes.

# Pudge Wars Basic — policy surface

A policy is one Bassy BASIC program (UTF-8, ≤ 64 KiB) or a ZIP package with exactly one
`.bas` plus tensor weights. There is no compiler from `STRATEGY.md`: a candidate is the
hand-written `policy.bas` itself (GAME.md `compiler: basic`).

## Execution model

- One VM per seat. The whole program runs **once per tick**, from line 1, for each living
  butcher (`examples/pudge_wars/bots.nim:716-833@cd8111a5`, `examples/pudge_wars/bots.nim:839-854@cd8111a5`).
- `restart` keeps globals, arrays, records and tensor handles between ticks
  (`bassy:src/bassy.nim:3730-3760@a02e6dd5`). Initialise once behind a flag
  (`if initialized = 0 then … initialized = 1`).
- Dead butchers do not run, so state you keep across death must be reset by detecting respawn
  (the Base starter checks `selfShield` rising, `coworld/players/base.bas:154-161@cd8111a5`).
- Actions apply to the world immediately and return 1 (accepted) or 0 (rejected). A
  script may issue several per tick; the last movement order wins (`walkTo`, `attackTarget`
  and a hook cast each overwrite movement, `examples/pudge_wars/sim.nim:2014-2106@cd8111a5`).

## Language (Bassy)

- QBasic-like, case-insensitive. Scalars are implicitly declared and start at 0 / "", so
  **a misspelled variable silently reads 0** (`bassy:docs/language.md:7-13@a02e6dd5`).
- int32 and Q16.16 fixed point; no floats. `/` is fixed division, `\` integer division;
  division by zero raises an error (`bassy:docs/language.md:15-47@a02e6dd5`). Mixing an int
  outside ±32767 with a fixed value raises an error.
- Comparisons return −1/0; `and/or/not` are **bitwise**, and both sides of `and`/`or` are
  evaluated, including action calls (`bassy:docs/language.md:115-164@a02e6dd5`). Pudge host
  flags are 1/0, so write `flag = 0`, not `not flag`.
- `if/elseif/else`, `select case`, `for/while/do`, `goto/gosub`, `sub` with by-value
  parameters. **No local variables and no value-returning functions**: return results in
  globals and give each sub's scratch names a unique prefix (Base does,
  `coworld/players/base.bas:31-65@cd8111a5`).
- 1-D `dim` arrays (literal bound, top level), `type` records. No `^`, no `ABS`/`SQR`/`RND`,
  no `VAL`: hand-roll abs and integer square root (`bassy:README.md:54-64@a02e6dd5`).
  No RNG: derive variety from `worldTick` and `selfId`.

## Host data (read-only, refreshed each tick)

Names `examples/pudge_wars/bots.nim:57-90@cd8111a5`, values `examples/pudge_wars/bots.nim:748-819@cd8111a5`.

| Name | Meaning |
|---|---|
| `selfId`, `selfTeam` | butcher id (slot + 1); 0 West, 1 East |
| `selfX`, `selfY` | own tile, 0..127 |
| `selfHp`, `selfMaxHp` | HP; 1,200 + 35 per kill |
| `selfKills`, `selfPoints`, `selfGold` | kills, unspent upgrade points, gold |
| `selfInShop` | 1 on own shop pad |
| `selfLayer` | 0 West bank, 1 East bank, 2 river |
| `selfHooked`, `selfShield`, `selfRot` | hooked flag, spawn-shield ticks, Rot on |
| `selfBuff` | 0 none, else rune kind + 1 (1 Haste, 2 DD, 3 Regen, 4 Big Hook, 5 Lightning) |
| `hookReady` | alive, off cooldown, not winding up, no own hook out |
| `blinkReady` | off cooldown, not winding up, not hooked, not in any Rot |
| `worldTick` | current tick |
| `riverWest`, `riverEast` | 52, 76 |
| `homeX`, `homeY` | own spawn pad (seeded) |
| `allyPingX/Y/Age/By`, `teamPingResponder` | newest team ping; the one bot chosen to answer |
| `runeSoon`, `runeNextKind/X/Y` | ticks to the next rune and where/what it is |

Not exposed: exact cooldown ticks, teammates' max HP and cooldowns, enemy velocity,
item prices, walkability.

## Query functions

(`examples/pudge_wars/bots.nim:153-244@cd8111a5`, registered `examples/pudge_wars/bots.nim:564-586@cd8111a5`)

- `objectCount()`; `objectId/Kind/Team/X/Y/Hp/Alive/Layer/Hooked/Shielded/Rot(i)`. Kinds 1
  butcher, 2 wall, 3 mine, 4 ward. Fog-clipped: own team always, enemies only when visible,
  enemy mines and wards never. **Indices are not stable across ticks; key state by
  `objectId`.**
- `hookCount()`; `hookTeam/X/Y/Flying/Victim/Owner(h)` for visible hook heads.
- `runeCount()`; `runeX/Y/Kind(r)`.
- `itemRank(k)`: 0 Treads, 1 Brand, 2 Palisade, 3 Hooktrap, 4 Ward.
  `upgradeLevel(k)`: 0 Damage, 1 Distance, 2 Speed, 3 Radius.
- `allyNeedsRescue(id)`: teammate hooked by an enemy or drowning below 60% HP.

## Actions

All take integer tiles or ids; **a fractional argument is a runtime error that disables the
VM** (`bassy:docs/language.md:90-93@a02e6dd5`).

| Call | Effect |
|---|---|
| `walkTo(x, y)` | move target, snapped to own layer within 24 tiles |
| `hookTo(x, y)` | Meat Hook at the tile centre; 9-tick windup (caster coasts) |
| `safeHookTo(x, y)` | `hookTo` only if ready and the lane is clear of teammates |
| `rescueAlly(id)` | hook a teammate who needs rescue, if in range and unclaimed |
| `blinkTo(x, y)` | up to 20 tiles along the ray |
| `attackTarget(id)` | melee-chase an enemy; 0 clears |
| `halt()`, `ping(x, y)`, `rotToggle()` | stop; team ping; flip Rot |
| `buyUpgrade(k)` | spend a point (anywhere) |
| `buyPower(k)` | shop item, on own pad only |
| `placeWall/placeMine/placeWard(x, y)` | props (see mechanics) |

Each action is recorded to the replay before it is applied, accepted or not
(`examples/pudge_wars/bots.nim:245-259@cd8111a5`).

## Limits (`pudgeVmLimits`, `examples/pudge_wars/bots.nim:101-123@cd8111a5`)

| Limit | Value |
|---|---|
| Source | 64 KiB |
| Compiled bytecode | 20,000 instructions |
| Per decision (tick) | **100,000 instructions, 250,000 work units** |
| Memory | 2 MiB VM, 32 MiB native (tensors, package) |
| Globals / arrays / elements | 256 / 32 / 4,096 |
| Routines / parameters / call depth | 64 / 16 / 16 |
| PRINT | 1,024 bytes and 128 events per decision |

Work costs: queries 4, counts 2, `walkTo/hookTo/safeHookTo/rescueAlly/blinkTo` **400 each**,
other actions 20 (`examples/pudge_wars/bots.nim:564-603@cd8111a5`); `ANNOTATE` 1,024
(`src/polyworld/annotations.nim:129-129@cd8111a5`).

## Failure behaviour

| Situation | Result |
|---|---|
| Upload > 16 MiB, bad ZIP, not exactly one `.bas`, source > 64 KiB, syntax error | seat rejected at load; the host writes `player_failure.json` with `failed_policy_index` and **the episode produces no results** (`examples/pudge_wars/fileplayers.nim:144-180@cd8111a5`) |
| Any runtime `BasicError` (budget, division by zero, array bounds, fractional action argument, PRINT overflow, tensor or LLM error) | `vm.failed`: **that seat never runs again this episode**; its butcher keeps its last orders; `player_status.json` shows `exit_code 1, "BASIC VM disabled"` (`examples/pudge_wars/bots.nim:827-833@cd8111a5`, `examples/pudge_wars/fileplayers.nim:130-142@cd8111a5`) |
| Illegal game action (cooldown, off-pad purchase, off-map) | the call returns 0; no error |

The league disqualifies a policy after 3 consecutive failures (division settings,
2026-10-26). Pre-validate with `local_check` before any upload.

## Neural ZIP packages

ZIP detected by content; ≤ 16 MiB, ≤ 256 entries, exactly one `.bas` (≤ 64 KiB), canonical
relative paths (`src/polyworld/policies.nim:5-63@cd8111a5`). `tensors.json` at the root is
needed only if the program calls `tensorLoad`, and is validated **at the first
`tensorLoad`**, so a bad package disables the VM at runtime rather than failing upload
(`src/polyworld/tensors.nim:256-290@cd8111a5`). Operators: dense, CSR, element-wise,
relu/sigmoid/tanh, argmax, gather/scatter (`src/polyworld/tensors.nim:927-964@cd8111a5`);
no convolution or training. `tensorDense` weights are `[outputs, inputs]`.

## Other host functions

- **PRINT** goes only to the seat's private log in the league (10 MiB cap, no tick prefix:
  print `worldTick` yourself) (`examples/pudge_wars/fileplayers.nim:51-57@cd8111a5`).
- **`ANNOTATE(tick, kind$, function$, args$)`** writes a private per-seat JSONL record
  (1,000 records / 2 MiB) and returns 0 when accepted (`src/polyworld/annotations.nim:113-130@cd8111a5`).
  Strings have no backslash escapes; build JSON with `CHR$(34)`.
- **LLM / oracle** functions exist (`src/polyworld/llms.nim:459-517@cd8111a5`); with no
  endpoint `llmAvailable` returns 0. An LLM error disables the VM.

## Starter policies

The package ships four player files, byte-identical to repo sources (SHA-256 in the
manifest): `base` (`f38ba292…`, the strong default and our starter), `aggressive`
(`58225fd7…`), `sniper` (`550d5d0d…`) and `neural` (`60533fa6…`, an illustrative 6→6→3 MLP).
See [game-guide.md](game-guide.md#starter-players) and `seed/strategy/STRATEGY.md`.

## Stale upstream docs about the surface

- `examples/pudge_wars/CLAUDE.md:50-51@cd8111a5` and `duel_loop.md:197-201`: "20k
  instructions per decision", "integer math only", "no `elseif`" — all wrong at this commit.
- `coworld/GAME_HOSTED_MIGRATION.md:53-54@cd8111a5`: "base.bas documents the whole API" — the
  generated Base has no API comments; read `bots.nim`.
- Bassy's README lists `TYPE` records as unsupported (`bassy:README.md:64-64@a02e6dd5`); its
  language reference and tests say they work. Test before relying on records.
- The softmax.com wiki page says 20,000 instructions and 50,000 work units and omits ZIP
  packages; the 0.1.8 manifest README and the code say 100,000 / 250,000.
