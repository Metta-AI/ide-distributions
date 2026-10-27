---
name: pudge-wars-basic-diagnosis
description: Use when explaining a Pudge Wars Basic policy failure.
---

# Pudge Wars Basic diagnosis

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-05, D-07, D-10, D-26.

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/diagnosis/SKILL.md`, from the GotA `diagnosis` binding in this
repository, and from the upstream mixin `games/pudge-wars/skills/diagnosis/README.md` at
`Metta-AI/polyworld-pudge-wars@cd8111a5`. The core `diagnose` skill carries the method:
signals become two to four varied, mechanistic hypotheses, each pinned to a code
location. This skill supplies the failure vocabulary and instruments.

## Worker setup

Follow the core `card` skill first. Run shared commands through the quoted shim.
Resolve tools without changing the worker cwd:

```sh
RESEARCH_HOME="$HERMES_HOME"
RESEARCH_PYTHON="$RESEARCH_HOME/local/venv/bin/python"
mkdir -p .scratch
```

Use absolute paths for episode directories, replays and output files below. Use the
installed profile environment; do not substitute a global CLI or Python. Run every game
tool from `"$RESEARCH_HOME"` with `"$RESEARCH_PYTHON"` as shown. Read
`games/pudge-wars-basic/GAME.md` and its linked `docs/mechanics.md`,
`docs/replay-format.md` and `docs/game-guide.md` under RESEARCH_HOME. This folder
targets Pudge Wars Basic only: league `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0`,
Coworld `pudge-wars-basic`. The knowledge here was written for version 0.1.8
(`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source `Metta-AI/polyworld-pudge-wars` at
`cd8111a5`, replay game version 38. The tools follow the live league by themselves: every
envelope's `engine` block names the version and commit that ran (binaries for a new commit
are built on first use). If `engine.live` is false the league could not be checked and the
run may be stale: say so in the record. If `knowledge.current` is false, treat
game-specific claims here as possibly stale and propose the maintenance card the
`knowledge.message` names (`kb renew ...`).

## Long commands

Use the core `card` skill's **Long commands** procedure for native builds
(`tools/native.py`), local episodes (`tools/match.py`), `tools/local_check.py`,
`tools/branch.py` and replay inspection of more than a few episodes. A league-length
local 3v3 takes about a second of simulation per episode plus decoding; a first native
build fetches the source and every `nimby.lock` dependency. After exit, read the exit
code, the JSON envelope (`ok`, `error.reason`) and the files the command wrote before
interpreting anything. Exit 1 is a failed check, 2 a rejected input, 3 a tool fault.

## Failure vocabulary

Classify a witnessed loss or death against these names (mechanics in `docs/mechanics.md`):

| Class | Definition | Witness |
|---|---|---|
| `compile-failure` | a seat fails to load (syntax, > 64 KiB, bad ZIP); the episode has no results | `player_failure.json` `failed_policy_index`; local G1/G2 |
| `vm-disabled` | runtime `BasicError` (budget, division by zero, array bounds, fractional action argument, PRINT overflow, tensor error); the seat freezes for the rest of the game | `BASIC VM disabled` in `player_status.json`; the `BASIC error:` line in `players/<slot>/log.txt` (local, or hosted via `hosted_logs.py`); a butcher with no `action` rows after a tick |
| `budget-overrun` | `vm-disabled` with "instruction limit" / "work limit": > 100,000 instructions or > 250,000 work per tick (each `walkTo/hookTo/blinkTo` costs 400) | the error text; worst-case loops in `policy.bas` |
| `hook-miss-far` | misses concentrated in the 45+ tile band; long flight lets the target move | `hitRateByDist`, `hooks[].closest.dist`, `targetMoved` |
| `hook-miss-lead` | misses with small `closest.dist` but consistent offset: wrong lead (`leadDiv`, velocity sampling) | `hooks[]` aim vs target position at resolve |
| `ally-hook` | our hook latches a teammate outside a deliberate save (heals 120 but wastes the throw and can drag them) | `hooks[]` `hit` with an ally `victim` |
| `clash-loss` | our hooks broken by enemy hooks (`denied`) more than we win (`deny_win`) | `hooks[]` results |
| `caught` | an enemy hook lands on us, we are reeled and killed (often a follow-up or headshot) | `hook_landed` on us → `kill` within ~3 s |
| `dodge-failure` | a hook aimed at us lands although we had time (`flightTicks` ≥ 15) | `dodge.hit / hooksAimedAtMe`, movement in `state` rows |
| `drowning` | dumped or stranded in the river, killed by `drown` damage; Blink unavailable (cooldown, in Rot) or not used | `river_enter` (`by`), `damage` cause `drown`, `timeInRiverSec` |
| `river-time` | long voluntary river time (rune chasing, crossing) exposing us | `river_enter/exit` without `by` |
| `rot-self-damage` | our own Rot hurts us or allies; dying in own Rot gives the enemy a team kill; ally Rot deaths credit nobody | `damage` cause `rot` with `by` = self or ally; Σ deaths > enemy team kills |
| `brawl-loss` | melee or Rot fight entered at an HP disadvantage | `damage` cause `melee`/`rot`, HP at engagement |
| `rune-loss` | enemy takes Big Hook/Lightning/DD runes we contested | `rune_taken` by team, `runeNextKind` timing |
| `economy-stall` | unspent points or gold, missed shop trips, no ward | `buy` rows, `pointsUnspent`, `upgrades` |
| `death-cluster` | several of our deaths within ~10 s: a team-level positioning or rescue failure | `kills[]` timeline |
| `blind` | enemies hidden by fog until their hook lands (no ward, standing deep on our bank) | `state` positions vs fog rule (20 tiles from a teammate) |

## Triage table

Check cheapest first; stop at the first class that explains the witness.

| Order | Check | Instrument | Cost |
|---|---|---|---|
| 1 | Did every seat load and stay enabled? (`compile-failure`, `vm-disabled`, `budget-overrun`) | results / `player_status.json`; local `local_check` G1–G3 | seconds |
| 2 | Is the episode valid (version, seat map, seed, replay hash)? | `replay_inspect` verification | seconds |
| 3 | Did our seat feed? deaths vs teammates and field (8.3 mean) | `evaluate.py` | seconds |
| 4 | How did each death happen? (`caught`, `drowning`, `rot-self-damage`, `brawl-loss`, `death-cluster`) | `replay_inspect --events kill,damage,hook_landed,river_enter` | a minute |
| 5 | Why do our hooks not land? (`hook-miss-far`, `hook-miss-lead`, `ally-hook`, `clash-loss`) | `summary.json` `hooks[]`, `hitRateByDist` | a minute |
| 6 | Were hooks at us dodgeable? (`dodge-failure`, `blind`) | `dodge`, `state` rows, `branch.py` at the launch tick | minutes |
| 7 | Secondary economy (`rune-loss`, `economy-stall`) | `rune_taken`, `buy` | a minute |
| 8 | Was another action better at the witness tick? | `branch.py` options at that tick | minutes |

Seat/side first: confirm the failing seat is ours, then whether it fails on both West and
East (a one-side failure is a coordinate-mirroring bug).

## Instruments

```sh
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/evaluate.py" <ABS_RESULTS_JSON> --seat <N>
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/replay_inspect.py" <ABS_EPISODE_DIR> --seat <N> --events kill,damage,hook_landed,hook_missed,river_enter --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/hosted_logs.py" --xp <xreq_id> --dest <ABS_XP_DIR> --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/branch.py" <ABS_REPLAY> --slot <N> --ticks <T1>,<T2> --horizon 160 --seeds 1 --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/match.py" --seat <ABS_POLICY>:3 --seat base:3 --variant league-3v3 --seeds 1..8 --out <ABS_MATCH_DIR> --jobs 4 --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/compare.py" <ABS_BASE_DIR> <ABS_CAND_DIR> --group seat --metric deaths --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/local_check.py" <ABS_POLICY> --baseline <ABS_BASELINE_POLICY> --work-dir <ABS_CHECK_DIR> --seeds 10 --json
```

- `match.py` reproduces a mechanism locally under league timing (every tick, no aim
  noise) on the native league host and writes `results.json`, `replay.pwr`,
  `summary.json`, `events.jsonl`, `player_status.json` and each seat's
  `players/<slot>/log.txt` and `annotations.jsonl` per episode; seed it away from the
  upstream used-evidence seeds. Local runs explain; they never decide (D-04).
- Hosted decision trace: `hosted_logs.py --xp <xreq_id> --dest <ABS_DIR>` fetches our seats'
  PRINT logs, annotations and artifacts per episode (`<ABS_DIR>/<ereq>/players/<slot>/`).
- `branch.py` scores alternatives from a recorded state with bots continuing; use it to
  ask "was the hook/retreat/rot option better here", not to predict hosted outcomes.
- Instrumentation: add `ANNOTATE(worldTick, "kind", "function", "{...}")` calls to a
  candidate (1,024 work each, 1,000 records cap) to log the branch taken; they appear in
  `players/<slot>/annotations.jsonl` of local runs (default league runner, not
  `--runner headless`) and of hosted episodes fetched with `hosted_logs.py`. No output
  reports per-decision instruction or work use; annotate loop counts to see budget headroom.
- Core: `records find claim --where status=refuted` before proposing (D-10), core
  `diagnose` for the hypothesis method, `pudge-wars-basic-replay-inspection` for decoding.

## Procedure and outputs

If the witnessed failure is a build with `status: errored`, it is a tool fault, not a
strategy problem: follow `pudge-wars-basic-eval-design`'s **Build tool faults** (D-26).
A `status: rejected` build follows its **Rejected build inputs**.

Use after replay inspection, with core `diagnose`. Start from a witness, not a low
aggregate. Check refuted levers first, and the upstream closed levers in
`docs/game-guide.md` (Fighter-only scans, headshots aimed at the reeler and more
perpendicular Blink are closed without new evidence):

```sh
"$RESEARCH_CLI" records find claim --where status=refuted
"$RESEARCH_CLI" records show <C-id>
```

Follow observation → memory → chosen action → accepted action → outcome to the earliest
owner, pinned to `strategy/policy.bas` lines or `examples/pudge_wars/*.nim` at the
recording commit. Write two to four independent explanations using the core hypothesis
schema (`statement`, `prediction`, `lever`, `topics`, `source`, `state: proposed`). For
example, high deaths may come from standing inside enemy hook range with no vision
(`blind`), from a dodge that steps along the hook's line (`dodge-failure`), or from
staying in brawls under Rot (`rot-self-damage`); each needs a different witness and
falsification. Preserve a nearby counterexample and name the distinguishing condition.

```sh
"$RESEARCH_CLI" records new hypothesis --meta-file .scratch/hypothesis.json --body-file .scratch/hypothesis.md --slug <slug>
```

Output: evidence refs, before metric, mechanism, pinned source location, predicted metric
and a bounded next step. If coverage is missing (for example the policy wrote no
annotations, or a seat log was not fetched), record the gap and request instrumentation; do not claim an unobserved behaviour failed (D-07).

## Rejected build inputs

A build with `status: rejected` and `error.reason: invalid_input` is a correctable input refusal, not a tool fault. Read `error.details.path` and `error.details.action`, correct and commit truthful inputs within the card scope, then retry with a new output directory. Preserve the rejected report; do not overwrite it, invent provenance, or bypass a check. If correction needs unavailable facts or authority, request help and block. This explicit result takes precedence over generic CLI-failure handling.
