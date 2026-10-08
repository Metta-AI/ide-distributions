<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/tests/fixtures/strategy_trivial/STRATEGY.md -->
# Strategy: compiler fixture

This file is source. Generated BASIC is never edited by hand.
This fixture checks the compiler. It is not a competitive policy.

## Knowledge
### K.position
- Summary: Read our horizontal position.
- Spec: Set x to selfX on every tick.
- Outputs:
  - x -- Our horizontal position in centimeters.
- Log: x every 24 ticks
- Checks:
  - Believed: Log our position. Reads: `K.position`.x
- Status: specified (2026-09-30)

## Capabilities
### C.idle
- Summary: Wait without issuing commands.
- Spec: Read x from `K.position` into our private observed_x global. Set status to zero. Issue no host commands.
- Uses: `K.position`
- Done when: never
- Checks:
  - Acted: Select this capability. Reads: PWD.r, PWD.c
  - Acted properly: Start this capability. Reads: PWE.e
  - Result: Issue no movement commands. Reads: replay
- Status: specified (2026-09-30)

## Strategy
### ST.roles
- Summary: Every seat has the same role.
- Spec: Use one role for all seats.
- Roles:
  - all = seats 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
- Checks:
  - Acted: Every seat selects the idle rule. Reads: PWD.r
- Status: specified (2026-09-30)

### ST.rules
- Summary: Always wait.
- Spec: Select the idle capability.
- Checks:
  - Acted: Select the only rule. Reads: PWD.r, PWP.n
- Status: specified (2026-09-30)
- `R.idle` [100]: ALWAYS DO `C.idle`

### ST.commitment
- Summary: Select the best rule each tick.
- Spec: Apply no minimum hold or priority margin. Disable the emergency interrupt threshold.
- Params:
  - min_hold = 0 ticks -- This fixture has no hold.
  - preempt_margin = 0 points -- This fixture has no margin.
  - interrupt_at = 1001 points -- No rule reaches this threshold.
- Checks:
  - Acted properly: No alternative rule is held back. Reads: PWD.h
- Status: specified (2026-09-30)
