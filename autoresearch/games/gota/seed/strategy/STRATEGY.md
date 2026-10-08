# Strategy: exact v13-live baseline

This source specifies the downloaded v13 artifact mechanically migrated for the
live Bassy host, SHA-256
`1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
The compiler supplies this exact file as `context/base.bas`. Historical module
reconstructions are not the reference. M1 requires identical ordered attempted
commands for five heroes, not merely score or accepted-action agreement.

Ownership follows mutation: configuration outputs are read-only to the farming
capability; every other shared scratch, counter, history array, and planner field
belongs to that capability. This deliberately coarse split retains cross-routine
state. Goals label the selected capability; they do not identify its executed
branch or prove survival, farming, or finishing claims. All components remain
specified until real model generation and the action oracle have passed.

## Goals

### G.survive
- Claim: Avoid preventable deaths while retaining the baseline retreat behavior.
- Priority: 900
- Provenance: Exact downloaded v13-live artifact; descriptive attribution only.
- Status: specified (2026-10-07)
- Evidence: No real M1 generation or equivalence evidence yet.
- Checks:
  - True: The goal predicate holds in the world. Reads: replay
  - Believed: The selected decision carries this goal label. Reads: PWD.r
  - Acted: The farming capability is selected. Reads: PWD.c
  - Acted properly: Its ordered attempted commands match the baseline. Reads: replay
  - Result: The declared goal is achieved. Reads: replay

### G.farm
- Claim: Collect the baseline last-hit income and experience.
- Priority: 700
- Provenance: Exact downloaded v13-live artifact; descriptive attribution only.
- Status: specified (2026-10-07)
- Evidence: No real M1 generation or equivalence evidence yet.
- Checks:
  - True: The goal predicate holds in the world. Reads: replay
  - Believed: The selected decision carries this goal label. Reads: PWD.r
  - Acted: The farming capability is selected. Reads: PWD.c
  - Acted properly: Its ordered attempted commands match the baseline. Reads: replay
  - Result: The declared goal is achieved. Reads: replay

### G.finish
- Claim: Take the baseline hero and structure finishing windows.
- Priority: 600
- Provenance: Exact downloaded v13-live artifact; descriptive attribution only.
- Status: specified (2026-10-07)
- Evidence: No real M1 generation or equivalence evidence yet.
- Checks:
  - True: The goal predicate holds in the world. Reads: replay
  - Believed: The selected decision carries this goal label. Reads: PWD.r
  - Acted: The farming capability is selected. Reads: PWD.c
  - Acted properly: Its ordered attempted commands match the baseline. Reads: replay
  - Result: The declared goal is achieved. Reads: replay

## Knowledge

### K.config
- Summary: Refresh the exact baseline configuration each decision.
- Spec: Implement the assignments in context/base.bas cfgInit, with all outputs below assigned every decision before C.farm runs. Preserve the class-dependent cfgchaseclass branch. Use these exact lower-case output names, which remain independently namespaced in structured mode; do not introduce a second configuration copy. This component issues no host commands and has no persistent initialization. Keep cfgdebug and cfgreportevery even though compiler telemetry replaces LH PRINT. Values are part of the faithful baseline, not new tunables.
- Sources: selfClass and the exact baseline cfgInit assignments.
- Outputs:
  - cfgavoidallytarget -- Exact baseline value: 0.
  - cfgchaseclass -- Exact baseline value: 0 except classes 0,3,4,5,8,9 -> 1.
  - cfgchaseku -- Exact baseline value: 180.
  - cfgdebug -- Exact baseline value: 0.
  - cfgeventstale -- Exact baseline value: 96.
  - cfgfootmanhit -- Exact baseline value: 12.
  - cfgfootmanperiod -- Exact baseline value: 32.
  - cfgherofearrange -- Exact baseline value: 360.
  - cfgherohuntrange -- Exact baseline value: 480.
  - cfgheroperiod -- Exact baseline value: 24.
  - cfgidlehere -- Exact baseline value: 1.
  - cfgksallyku -- Exact baseline value: 480.
  - cfgksholdku -- Exact baseline value: 600.
  - cfgksminhppct -- Exact baseline value: 40.
  - cfgksrecentticks -- Exact baseline value: 48.
  - cfgksteal -- Exact baseline value: 1.
  - cfgmeleestandoff -- Exact baseline value: 120.
  - cfgpoisonmingold -- Exact baseline value: 40.
  - cfgpoisonsecure -- Exact baseline value: 1.
  - cfgpoisonstock -- Exact baseline value: 1.
  - cfgpunish -- Exact baseline value: 1.
  - cfgpunishallyku -- Exact baseline value: 360.
  - cfgpunishchase -- Exact baseline value: 0.
  - cfgpunishchasemaxku -- Exact baseline value: 900.
  - cfgpunishchaseticks -- Exact baseline value: 120.
  - cfgpunishengageku -- Exact baseline value: 720.
  - cfgpunishenterku -- Exact baseline value: 420.
  - cfgpunishexitku -- Exact baseline value: 600.
  - cfgpunishminhppct -- Exact baseline value: 50.
  - cfgpunishstophppct -- Exact baseline value: 35.
  - cfgrangeslack -- Exact baseline value: 6.
  - cfgrecoverpct -- Exact baseline value: 70.
  - cfgreportevery -- Exact baseline value: 240.
  - cfgretreatpct -- Exact baseline value: 35.
  - cfgretreatstep -- Exact baseline value: 600.
  - cfgscanradius -- Exact baseline value: 1200.
  - cfgseenstale -- Exact baseline value: 1.
  - cfgspellsecure -- Exact baseline value: 1.
  - cfgstandoffback -- Exact baseline value: 60.
  - cfgstickbonus -- Exact baseline value: 40.
  - cfgstickslack -- Exact baseline value: 12.
  - cfgtoweravoid -- Exact baseline value: 420.
  - cfgtowerescape -- Exact baseline value: 540.
  - cfgtowerfarmallies -- Exact baseline value: 99.
  - cfgtowerfarmalliesku -- Exact baseline value: 390.
  - cfgtowerperiod -- Exact baseline value: 24.
- Log: cfgchaseclass every 24 ticks
- Checks:
  - True: Configuration matches the exact artifact. Reads: replay
  - Believed: Record the class-dependent chase flag. Reads: `K.config`.cfgchaseclass
- Status: specified (2026-10-07)

## Capabilities

### C.farm
- Summary: Execute the baseline's stateful planning and ordered action attempts.
- Uses: `K.config`
- Spec: Preserve every executable statement of the exact reference except cfgInit (owned by K.config), the LH PRINT block, and top-level END. Place the per-decision main sequence inside c_farm__tick; rename all original mutable scalars, arrays, and helper SUBs to c_farm__ plus their original lower-case names. Read configuration through k_config__ names. Use independent namespaced storage in structured mode. Preserve every array's inclusive DIM bound and every computed index. Keep all original shared scratch values shared, rather than converting them to new SUB locals. Preserve helper parameter scoping and original shared scratch semantics. Define an empty c_farm__start with no action or state reset; no c_farm__init. Always set c_farm__status to 0 at the end. A tick gap can restart the runtime capability but must not reset baseline memory. Never copy the whole policy as top-level code or use an authored-policy fallback.
  The mandatory main sequence is scanObjects, shScanInventory, bookkeeping, pnPlan, svPlan, ksPlan, lhStickId = mnOrderedId, lhPlan, ordered action attempts, shBuy, mnExpectedGold = selfGold - shSpent. Configuration already ran first. Keep mnLaneFront even though its SUB occurs after END in the reference. The main comment claiming shopping before acting is stale: executable order is authoritative.
  scanObjects keeps its single bounded object-list pass, team visibility, ku coordinates, packed hero/tower fields, original object limits, counts, damage/speed helpers, and fmSlot updates. Keep all 32 baseline arrays and ring history bounds, including pvId(63), fmHp/fmSeen/fmEv1/fmEv2(511), and bounded pvN/efN accesses. Keep pvId at its original 64 cells. Runtime storage is separate.
  Bookkeeping initializes only when mnInit is zero, detects worldTick gaps exactly as the reference, clears svLow and mnOrderedId on those gaps, and preserves the ordered gold-delta loops, structure disappearance check, early-hit check, missed/lost counters, and original scratch reuse. Counters remain even though LH printing is removed; do not simplify branches on the assumption that a counter is observational only.
  Preserve pnPlan's tower-punish entry/exit and chase hysteresis, svPlan's retreat/heal hysteresis, ksPlan's hero/structure finishing windows and hold behavior, and lhPlan's predicted incoming damage, ring memory, target scoring, sticky target, secure-spell/poison choices, safe standoff, and chase logic. Port the exact helper calculations and tie-breaking from the reference, including integer division and host comparisons; do not substitute a newer source reconstruction or tune constants.
  First attempt useItem when svHealSlot >= 0. Retreat attempts walkTo and sets acted regardless of its return. Otherwise attempt punish attackTarget, setting acted only on success. Then process the kill-steal branch: when ksSlot >= 0 attempt castTarget, count successful casts, attempt attackTarget for hero IDs, and set ordered/acted regardless of cast success; without a spell set ordered/acted only if attackTarget succeeds. If still unacted, attempt secure cast, poison attack followed by useItem only when that attack succeeds, and then the last-hit attack; preserve every return-value branch and repeated attempted command.
  If still unacted, choose the original standoff or lane front, calculate chase, retain selfTarget checks, idle attackTarget(0), the alternating nudge when already at the walk tile, and the second walkTo on failed navigation when lhState = 1. Clear mnOrderedId exactly where the reference does. Preserve mnLaneFront's lane identity, fighting preference, distance ties, unsafe-front checks, and fallback coordinates.
  Finally preserve shBuy/shTryBuy's exact order, readiness tests, skill-level calls, item costs, inventory flags, poison restock behavior, and shSpent accounting. Preserve all attempted mutating host calls, even when rejected. No gameplay command is emitted by the runtime selector or start routine. Only PRINT differs: the compiler supplies goal-aware PWD/PWB/PWE/PWP telemetry instead of LH. Do not add draft policy, random choices, waits, emergency arbitration, or other gameplay behavior.
- Done when: never
- Checks:
  - True: Planner assumptions match the world. Reads: replay
  - Believed: Use the exact configuration and persistent planner state. Reads: `K.config`.cfgchaseclass
  - Acted: The farming capability is selected. Reads: PWD.r, PWD.c
  - Acted properly: Preserve every attempted action and its order. Reads: replay
  - Result: Match the exact baseline action trajectory. Reads: replay
- Status: specified (2026-10-07)

## Strategy

### ST.roles
- Summary: All ten seats use the same policy.
- Spec: Assign every seat to the common role; original selfId arithmetic retains lane identity.
- Checks:
  - Acted: Assign all ten seats. Reads: PWD.r
- Roles:
  - all = seats 0,1,2,3,4,5,6,7,8,9
- Status: specified (2026-10-07)

### ST.rules
- Summary: Run the stateful baseline on every native decision.
- Spec: Select the single capability without branch arbitration. Goal labels describe intent only.
- Checks:
  - Acted: Select the single rule. Reads: PWD.r, PWD.c
- `R.farm` [100]: ALWAYS DO `C.farm` FOR G.survive, G.farm, G.finish
- Status: specified (2026-10-07)

### ST.commitment
- Summary: Preserve one continuously running capability.
- Spec: No hold, preemption margin, or active interrupt threshold is needed for the single rule.
- Checks:
  - Acted properly: Keep the only rule. Reads: PWD.h
- Params:
  - min_hold = 0 ticks -- No minimum hold.
  - preempt_margin = 0 points -- No preemption margin.
  - interrupt_at = 1001 points -- No rule reaches this threshold.
- Status: specified (2026-10-07)
