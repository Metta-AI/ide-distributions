<!-- Source: Metta-AI/polyworld-pudge-wars @ cd8111a5ee4f2fdc38c027615b33421f6c7d2536; league league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0 readback 2026-10-26 -->

> **Provenance.** Pudge Wars Basic, Coworld `pudge-wars-basic` 0.1.8
> (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source commit `cd8111a5`. League
> `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0` (division `Competition`,
> `div_471aedc1-00f7-41dd-971d-f0beff95a439`), read on 2026-10-26 at about 14:40 PDT with
> `coworld` 0.1.64. Standings and per-policy numbers are a **dated snapshot**: refresh them
> with `softmax leaderboard` and `softmax league-results` before relying on them. Source
> citations are `path:a-b@cd8111a5`.

# Pudge Wars Basic — strategy guide

Read [mechanics.md](mechanics.md) for the rules and [policy-surface.md](policy-surface.md) for
what a BASIC program can see and do. This page is about **how matches are won**, what the
field looks like, what the starters do, and which levers are worth testing.

## How a match is won

Two teams of three butchers stand on opposite river banks. Each seat runs its own BASIC
program every tick. The first team to **30 team kills** wins; otherwise, at 38,400 ticks,
the team with more kills wins and a tie is a draw that scores 0 for everyone
(`examples/pudge_wars/coworld_host.nim:1059-1100@cd8111a5`). Each seat scores 1.0 if its team
won, else 0.0, so the league result is a **team** result.

Measured on 192 league episodes (rounds 2929–2936, 2026-10-26):

- **Every game ended at the kill goal.** No time-outs, no draws. Median length 13,426 ticks
  (≈ 7 minutes); mean loser score 19.5; mean margin 10.5 kills.
- **No side advantage.** West won 97, East 95 (50.5%, SE ±3.6 points). Slot position had no
  meaningful effect on kills.
- **Everyone throws about as often.** About 20 hooks per seat-minute (SD 1.05), roughly two
  thirds of the 65-tick cooldown maximum. Throw volume measures game length, not quality.
- **Hook outcome decides games.** The team with the higher landed/thrown ratio won 90% of
  games, and the team with more landed hooks won 88%. Winners: 52.4% vs losers 45.9%.
  A team needs about 9 landed hooks per kill (hooks deal 200 against 1,200 HP, and some
  landed hooks are ally saves).
- **The leaders win by not dying.** The top four policies average 3.6–4.8 deaths per game
  against a field mean of 8.3, while their kills (8.5–9.2) are near the field mean. Every
  death is a team kill for the enemy and 4 s off the map.
- Deaths exceed kills by 1–2 in about a quarter of games: deaths that credit no team kill
  (most likely ally Rot or uncredited drowning; see mechanics).

Practical reading: a policy gains by **landing a higher share of its hooks, dying less, and
denying enemy hooks**, not by throwing more.

## League format (snapshot 2026-10-26)

- One division, `Competition`; a round every 32 minutes with **24 episodes** run in parallel
  (about 1,080 episodes per day). Scheduler `team_n`, two teams, `blocks` layout (slots 0–2
  West, 3–5 East), `distinct_teammates: true`, matchmaking `elo_softmax` (temperature 100).
- Only champions play. With 7–11 entrants, each entrant plays 13–24 of a round's 24
  episodes; empty seats get filler policies (A-aron's `pudge-basic-*` v1 set).
- **Matchmaking balances teams**: weak policies are more often paired with strong
  teammates, which compresses raw win rates toward 50% and can rank policies in the wrong
  order. Ranking uses OpenSkill (mu 25, sigma 8.33; "MMR" on the board); a new version is
  "settling" while sigma is high (about 5 after 1–2 rounds, about 1.5 when settled).
- `auto_champion: always`: a submission replaces the player's champion immediately.
  Player files are private. A policy is disqualified after 3 consecutive failures (for
  example compile errors that abort episodes).
- `coworld power-analysis` models this division as a free-for-all and its sample sizes do
  not apply. Use the noise numbers in the `pudge-wars-basic-eval-design` skill.

### Standings snapshot (2026-10-26 ≈ 14:40 PDT)

| # | Champion policy | MMR | σ | Deaths/game | Landed/thrown | Notes |
|---|---|---|---|---|---|---|
| 1 | cassowary:v7 | 33.0 | 1.5 | 3.6 | 51.3% | lowest deaths in the field |
| 2 | kookaburra:v7 | 31.1 | 1.5 | 4.1 | 50.9% | same owner as cassowary |
| 3 | myasnik:v28 | 28.2 | 1.5 | 4.8 | 50.6% | 28 versions |
| 4 | buran:v1 → v2 | 25.0 | 4.6 | 4.5 | 46.7% | v2 submitted 14:29, settling |
| 5 | pudge-basic-base:v2 | 23.0 | 1.5 | 6.8 | 50.4% | **the Base starter's source** (A-aron) |
| 6 | stock Base starter (Malcolm Ocean) | 14.5 | 3.2 | 6.5 | 48.9% | unmodified starter, settling |
| 7 | mason-butcher:v20 | 7.1 | 1.6 | 10.4 | 44.3% | "stock Base + brawlMargin 20" |
| 8 | pawchuck-pudge:v1 | 5.5 | 5.0 | 4.8 | 57.0% | 12 games sampled, settling |
| 9 | co-gas-…-richard:v1 | 0.5 | 1.6 | 15.4 | 46.3% | dives and feeds |
| 10 | alex-pudge-alphastar:v1 | −0.6 | 5.4 | 8.4 | 45.7% | settling |
| 11 | co-gas-…-relh:v1 | −1.4 | 1.6 | 14.4 | 47.6% | 0.62 raw win rate, lowest MMR |

A teammate-adjusted logistic fit gives the same order (stock Base ≈ −0.3 logit, the top
three 2–3 logits above it). Top policies' source and type (BASIC, neural, LLM-assisted) are
not visible to us.

**Implication:** the unmodified Base starter sits mid-table, and the gap to the top is mostly
deaths (6.5 vs 3.6–4.8), not accuracy.

## Starter players

All four ship in the package and are byte-identical to repo sources.

### Base (`coworld/players/base.bas`, SHA-256 `f38ba292…`) — our starter

Generated from a five-parameter block plus a fixed controller body; it is a port of the
best earlier Python policy ("v16/E06b") (`coworld/players/base.bas:1-9@cd8111a5`). Measured
upstream: 96/96 local wins against the previous BASIC Base and 48/48 against the stock bots
(local, not hosted). Tunables (`coworld/players/base.bas:2-6@cd8111a5`):

| Tunable | Value | Effect |
|---|---|---|
| `holdBack` | 16 | tiles behind the river lip to stand (`holdX`) |
| `leadDiv` | 30 | hook lead divisor: aim = target + velocity × span ÷ (leadDiv + Speed level); larger = less lead |
| `retreatPct` | 40 | go home to heal below this HP % |
| `recoverUntilPct` | 75 | keep recovering until this HP % |
| `curveEnabled` | 1 | strafe perpendicular to the throw while the hook is out (CurveHook) |

Body constants that matter just as much: threat radius² 485 (~22 tiles) for enemy hooks,
dodge step 10–14 tiles, melee when d² ≤ 30, brawl when `selfHp + 40 ≥ enemy HP`, Rot when an
enemy is within 8 tiles and brawling, rune throws within 36 tiles, shop trip at 12 gold.
See `seed/strategy/STRATEGY.md` for the full structure.

### Aggressive (`58225fd7…`)

"Berserker": pushes into the river, buys Radius > Distance > Speed > Damage, Rots whenever an
unshielded butcher is within 8 tiles above 40% HP, places walls and mines, heavy lead
(`leadNow = 9 + speed`) (`coworld/players/aggressive.bas:1-60@cd8111a5`). Loses to Base.

### Sniper (`550d5d0d…`)

Holds the waterline, buys Distance > Radius > Damage > Speed, throws only at targets at or
below about half HP, never brawls (`coworld/players/sniper.bas:1-61@cd8111a5`). Wastes hook
uptime early against healthy targets.

### Neural (`60533fa6…`)

A hand-weighted 6→6→3 MLP in a ZIP (`examples/pudge_wars/players/neural.bas:1-51@cd8111a5`):
walk home, `safeHookTo` the nearest visible enemy object, or walk to the lip. No lead, no
upgrades, no Blink. It is a packaging template, not a competitor.

## Upstream knowledge pack: leads, not facts

The source repo carries an optimizer-seed mixin at `games/pudge-wars/` (`META.md`,
`closed_levers.md`, `experiments/`, `skills/`). Most of it describes the **Python policy on
the earlier Docker/JSON runtime** (game versions 0.3.x) or BASIC runs at "fair" difficulty
(decisions every 8 ticks with aim noise), which is not league timing. Treat every number as
a lead to re-test here.

| Lead | What upstream reports | Status for us |
|---|---|---|
| Lead divisor 9 → 30 | BASIC, league timing, local self-play, seeds 101401–101424: 3v3 margin +3.27, 95% interval [0.96, 5.73]; duel +0.35 (`games/pudge-wars/experiments/2026-10-13-skill-gradient.md:56-61@cd8111a5`) | already in Base; do not retune on those seeds |
| Nearest-threat coordinate fix | correctness fix confirmed; 3v3 margin −0.50 [−1.69, 0.50] | in Base; no competitive benefit shown |
| Stable fishing lanes + one rune collector | Python era: 19–1 screen; with healing 35–5 (`games/pudge-wars/closed_levers.md:38-38@cd8111a5`) | in Base; component effects not isolated |
| Deliberate short same-bank healing | healing up 77% but 11–9 vs 6–14 only directional (`games/pudge-wars/closed_levers.md:37-37@cd8111a5`) | in Base; benefit unconfirmed |
| E06b bundle (melee-centroid hooks, CurveHook, fountain guard) | 67–29 vs frozen v14, margin +7.1 [4.9, 9.2], local Python (`games/pudge-wars/closed_levers.md:68-80@cd8111a5`) | in Base; parts not isolated |
| Fighter-only scans, headshots aimed at the reeler, more perpendicular Blink | inconsistent selection results, not promoted (`games/pudge-wars/closed_levers.md:32-34@cd8111a5`) | closed unless new evidence; a trajectory-aware threat detector is a different hypothesis |
| Raising the retreat threshold | changed movement, no proven benefit (`games/pudge-wars/closed_levers.md:36-36@cd8111a5`) | open question, test with deaths as primary |
| Early damage ranks, center camping and raids | did not improve native win rate (`games/pudge-wars/closed_levers.md:44-49@cd8111a5`) | lead only (older runtime) |
| Rune control under the formation | rune collection fell 10% despite a win gain (`games/pudge-wars/closed_levers.md:39-39@cd8111a5`) | open |

The mixin's seed lists are **used evidence**: do not use seeds 1001–1012, 2001–2024,
6101–6312, 101401–101502, 917xxx, 918xxx or 1014101–1014602 as fresh holdouts for Base-family
candidates. Its `META.md` field snapshot (2026-09-09) names opponents on the older runtime and
is not the current league.

## Common failure modes

| Failure | What it looks like | Where to look |
|---|---|---|
| Feeding | many deaths, kills near average (the co-gas and mason profile) | `deaths`, `kill` events, death clusters |
| Caught and dragged | death shortly after an enemy `hook_landed` on us, often after a `river_enter` with `by` set | `events.jsonl`, `dodge` in summary |
| Drowning after a dump | `damage` cause `drown`, `timeInRiverSec` high, Blink not ready or blocked by Rot | river events, `blinkReady` logic |
| Rot self/ally damage | `damage` cause `rot` from a teammate or self; deaths with no team kill | `damageTaken.rot`, results kills vs deaths |
| Wasted hooks | low hit rate in the 45+ band, many `miss` with large `closest.dist` | `hitRateByDist`, `hooks[]` |
| Losing clashes | many `denied` results, few `deny_win` | `hooks[]` results |
| Ally hooks | `hit` with an ally `victim` outside a deliberate save | `hooks[]` victim team |
| VM disabled | a seat stands still for the rest of the game; `BASIC VM disabled` / `BASIC error` | `player_status.json`, stdout log |
| Compile failure | no `results.json`, `player_failure.json` instead; counts toward disqualification | local G1/G2 |

## Promising levers

Ranked by how directly they attack what separates the leaders (deaths) and what decides
games (landed share). Each is a hypothesis, not a finding.

1. **Survive longer.** Retreat and recovery thresholds (`retreatPct`, `recoverUntilPct`),
   standing distance (`holdBack`), and leaving the lip after our own throw. Primary metric
   deaths; guardrail kills.
2. **Dodge better.** Base reacts to the nearest enemy hook head within ~22 tiles and steps
   sideways; a trajectory-aware check (is the hook's line actually passing within latch
   distance?) is untested. Measure `dodge.dodged / hooksAimedAtMe`.
3. **Win clashes.** About a quarter of hooks end in a clash. Base counter-throws only at
   enemy hook heads within 14 tiles. Clash timing and Big Hook runes (which win clashes
   outright) are open.
4. **Land a higher share.** Shot selection by distance band (the 45+ band is where most
   throws go and miss), lead modelling with the actual acceleration curve, and aiming
   headshots at enemies already hooked by teammates (instant kills).
5. **Avoid Rot friendly fire.** Base turns Rot on in brawls with allies possibly inside 8
   tiles. Measure Rot damage to self and allies and uncredited deaths.
6. **Vision.** Fog hides enemies standing back on their bank; Base plants the free ward near
   the far lip. Ward placement and timing are untested here.
7. **Economy.** Base buys Distance > Speed > Damage > Radius and never buys walls or mines.
   Alternative orders interact with lead (`leadDiv + speedLevel`) and reach.
8. **Runes.** The schedule is public and deterministic (`runeNextKind`); contesting Big
   Hook and Lightning drops by hook scoop is untested.
