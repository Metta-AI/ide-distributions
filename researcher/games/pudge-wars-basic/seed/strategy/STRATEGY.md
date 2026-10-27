# Strategy: Base starter (pudge-basic-base v2)

The champion policy is `strategy/policy.bas`, a byte-identical copy of the Pudge Wars Basic
package's `base` player file (SHA-256
`f38ba2929c91f985d73afa5a54ec3e5da582e5e71122069cbc2847b19fc48511`, 748 lines; source
`coworld/players/base.bas` at `Metta-AI/polyworld-pudge-wars@cd8111a5`). There is no
compiler: `policy.bas` is the policy, and this file describes it so a researcher can choose
one component to change. Line numbers below are `policy.bas` lines.

Upstream generated the file from five semantic parameters plus a fixed controller body (a
port of their retained Python policy "v16/E06b"). Upstream measured it locally at league
timing: 96/96 wins against the previous BASIC Base and 48/48 against the stock bots. Its
hosted strength was unmeasured at release. In the 2026-10-26 league snapshot the
unmodified starter sat mid-table (MMR 14.5, still settling) and A-aron's identical
`pudge-basic-base:v2` at 23.0; the leaders die about 2–3 times less per game.

A candidate copies this file and `policy.bas` to `strategy/candidates/<E-id>/`, changes one
component in `policy.bas`, and states here what changed and which metric it should move.

## Parameters

Tunable constants at the top of the file (lines 2–6). Upstream's validated ranges are in
parentheses.

| Name | Value | Meaning |
|---|---|---|
| `holdBack` | 16 (4–20) | standing line: `holdX = riverWest − holdBack` (East: `riverEast + holdBack`) |
| `leadDiv` | 30 (20–40) | hook lead: aim = target + velocity × span ÷ (`leadDiv` + Speed level); larger means less lead |
| `retreatPct` | 40 | start recovering below 40% HP |
| `recoverUntilPct` | 75 | keep recovering until 75% HP |
| `curveEnabled` | 1 | strafe perpendicular to the throw while the hook is out |

Constants in the body that are equally tunable (editing them leaves upstream's generator,
which does not matter here): threat radius² 485 (~22 tiles, line 269), counter-throw at
enemy hook heads within 14 tiles (line 488), close-target throw at d² ≤ 400 (line 476),
melee at d² ≤ 30 (line 586), brawl rule `selfHp + 40 ≥ target HP` or target ≤ 120 HP (line
303), Rot when an enemy is within 8 tiles while brawling and HP > 96 (line 306), rune throw
window ±36 tiles (line 558), heal pair distance 4–24 tiles (line 524), shop trip at 12 gold
(line 619), dodge step 10 tiles (line 600) and dodge Blink 14 tiles (lines 405–408), rune
collector window `runeSoon ≤ 256` (line 627), lane spread 32 + 64·rank ÷ (n − 1) (line 645).

## Knowledge

What the policy remembers (globals persist across ticks; reset on respawn, lines 154–161):

- **Stable roster** (lines 169–222): up to 10 butchers keyed by `objectId` in arrays
  `ids, px, py, hp, peak, team, alive, layer, shield, caught, rot`. Fogged butchers keep
  their last values.
- **Velocity estimate** (lines 203–215): position resampled every ≥ 8 ticks; `vx, vy` in
  tiles per 8 ticks; reset after 96 ticks unseen.
- **Peak HP** as a lower bound on each ally's max HP (no host data gives it).
- **Last seen enemy** (`lastSeen, lastX, lastY, lastVx, lastVy`, lines 259–265) for blind
  pokes.
- **Hook roster** per tick (lines 272–292): which butchers own a hook (`air`), who is being
  carried (`carried`), whether our own hook is out or reeling an ally.
- Upgrade-derived reach and clearance (lines 337–345): `reach = 60 + 4·Distance`, ×1.5 with
  Big Hook.

## Situations

Per-tick classifications (lines 224–309):

- `target`: nearest visible, unshielded enemy; `catchTarget`: nearest enemy already hooked;
  `swimmer`: nearest enemy in the river.
- `threat`: nearest flying enemy hook head within ~22 tiles, with a dodge side away from it.
- `recovering`: HP below `retreatPct` until `recoverUntilPct`.
- `brawl`: we can afford melee; `wantRot` / `fleeRot`: our Rot on, or flee another's Rot.
- `free` (line 362): on our bank, not hooked, not recovering, no threat, no foreign Rot.

## Skills

Helper subs (lines 31–151): `root` (integer square root), `disk` (point-to-segment
radius test), `throwAt` (clamp, cap at reach, reject throws through the fountain, reject
lanes that would hit an unshielded teammate or their half-step prediction, then `hookTo`
or `rescueAlly`), `moveTo` (clamped `walkTo`), `curveEnd` (perpendicular ray clipped to our
bank).

## Capabilities

In program order; the first accepted hook each tick wins (`thrown`).

1. **Economy** (lines 311–336): spend points Distance > Speed > Damage > Radius; in the
   shop take the free ward, a flask below 50% HP, Longflight Brand then River Treads to rank
   5, then Big Hook if no buff. Never walls or mines.
2. **Rescue** (lines 363–387): if an ally `allyNeedsRescue`, throw `rescueAlly` and hold
   still while the ally is reeled.
3. **Blink** (lines 392–444): from the river to our lip; a mid-pinch crossing home when
   stranded on the enemy bank; a 14-tile dodge away from a threat.
4. **Hooks** (lines 446–585), in priority: wounded ally–enemy melee pair midpoint; close
   target (d ≤ 20); midpoint of a caught enemy and its reeler; counter-throw at an incoming
   hook head; swimmer; deliberate ally heal (one safe, deterministic pair); led shot at the
   target within reach; rune within 36 tiles; blind poke at the last-seen position or the
   far lip.
5. **Melee** (line 586): `attackTarget` when within d² ≤ 30 and brawling.
6. **Movement** (lines 588–683), in priority: after Blink nothing; flee Rot; dodge step;
   leave the river; chase a caught enemy up to our line; go home when recovering or losing
   a close fight; answer a team ping; shop trip; otherwise hold `holdX` on a lane (rank
   spread, one rune collector, a small juke).
7. **Ward** (lines 684–696): plant near the far lip when carrying one.
8. **CurveHook** (lines 698–748): after a throw, walk perpendicular to it until the hook
   resolves.

## Strategy

A ranged "fishing" formation: three butchers hold separate lanes 16 tiles behind their
lip, throw every time the hook is ready (led shots, blind pokes when nothing is visible),
save and heal teammates with ally hooks, retreat to the pads below 40% HP, and Blink out of
danger. It avoids crossing the river except by Blink at the pinch.

What a change can target: survival (retreat thresholds, standing line, dodge rule, Rot
use), hook selection and aim (priorities, lead, distance cut-offs, counter-throws for
clashes), team play (rescue, heal pairs, lanes, rune collector), economy order, vision
(wards). See `docs/game-guide.md`, "Promising levers", and check refuted claims first.

## Open questions

- Which component produces Base's 6.5–6.8 deaths per game in the league (versus 3.6–4.8 for
  the leaders): standing inside enemy reach, dodge failures, brawls, or drowning?
- Does Base's Rot rule (line 306) kill teammates? Ally Rot deaths credit no team kill.
- Base prints only mood lines; it has no decision trace. A candidate that needs intent
  evidence should add `ANNOTATE` calls (league host only).
