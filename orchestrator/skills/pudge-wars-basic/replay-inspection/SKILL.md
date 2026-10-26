---
name: pudge-wars-basic-replay-inspection
description: Use when decoding Pudge Wars Basic replays and logs.
---

# Pudge Wars Basic replay-inspection

This binding implements System Design §5.2 with the existing core procedures.
Doctrine upheld: D-04, D-05, D-07.

Adapted from optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`,
`games/_template/skills/replay-inspection/SKILL.md`, from the GotA `replay-inspection`
binding in this repository, and from the upstream mixin
`games/pudge-wars/skills/replay-inspection/README.md` at
`Metta-AI/polyworld-pudge-wars@cd8111a5`. The core `replay-diagnosis` skill carries the
method: the replay is ground truth, the policy's artifacts are its point of view, and
diagnosis lives in the gap between them.

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
Coworld `pudge-wars-basic` 0.1.8 (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`), source
`Metta-AI/polyworld-pudge-wars` at `cd8111a5`, replay game version 38. Resolve the
current version with `softmax game-version` before new work; a newer source commit
needs a rebuilt headless binary (`tools/native.py build --commit <SHA>`) before replays
recorded on it decode.

## Long commands

Use the core `card` skill's **Long commands** procedure for native builds
(`tools/native.py`), local episodes (`tools/match.py`), `tools/local_check.py`,
`tools/branch.py` and replay inspection of more than a few episodes. A league-length
local 3v3 takes about a second of simulation per episode plus decoding; a first native
build fetches the source and every `nimby.lock` dependency. After exit, read the exit
code, the JSON envelope (`ok`, `error.reason`) and the files the command wrote before
interpreting anything. Exit 1 is a failed check, 2 a rejected input, 3 a tool fault.

## Replays

- A Pudge tape is an **action tape**: setup, every action call (accepted or not) and one
  state hash per tick. It holds no positions or events. Decoding re-simulates it with
  the headless binary built from the **recording** source commit; the decoder checks
  every tick's hash. Format and field reference: `docs/replay-format.md`.
- Hosted replays are gzip (public `replay_url`, or `softmax xp artifacts`); local
  `match.py` tapes are raw `replay.pwr`. `replay_inspect.py` accepts either, gunzips,
  runs `pudge_headless --replay R --duellog DIR` and caches the output next to the replay.
- Version coupling: replay game version 38 at `cd8111a5`. If the episode's game version
  differs from the binary's, build the matching binary first
  (`native.py build --commit <FULL_SHA>` from the manifest's `source_url`). A hash
  mismatch means the wrong binary, never a game fact.
- Viewer for humans: `coworld replay-open <ereq_id>` (the WASM viewer at game version 38).
- Seat mapping: slot i = butcher id i + 1; slots 0–2 West, 3–5 East. League tapes carry
  no seat labels; map slots to policy versions from the episode record.

## Policy artifacts

- Our policy's point of view is its PRINT log and `ANNOTATE` JSONL, both private to our
  seats. Join an annotation to the replay by `time` (= tick) and seat; PRINT lines have no
  tick unless the policy prints `worldTick` (the Base starter prints only mood lines such
  as `pulling you out`, so it has **no decision trace**).
- Local headless runs keep neither (PRINT becomes a speech bubble, `ANNOTATE` returns
  "disabled"); the stdout log only shows `pudge <id> BASIC error: …` and the final
  `scripts: N/M active` line.
- Hosted: `softmax xp artifacts` downloads results and replays only. No installed command
  fetches seat logs or annotations yet, and other players' logs are forbidden (403). When
  intent matters, record "decision trace unavailable" as a coverage gap; do not infer
  intent from the logger's `target` (a nearest-enemy heuristic).
- `player_status.json` (league host) gives `exit_code` 1 / `BASIC VM disabled` per seat;
  a missing results file plus `player_failure.json` means a load failure.

## What to look at

In order of signal for this game:

1. **Our deaths.** For each `kill` event with our seat as `victim`: the preceding
   `hook_landed` on us (who, distance, `flightTicks`), `river_enter` with `by`, `damage`
   causes over the previous ~5 s, our HP and position from the last `state` row, whether
   Blink was available. Classify: caught at range, dumped and drowned, Rot (own or ally),
   melee brawl, headshot, Lightning.
2. **Our hooks.** `hooks[]` for our owner id: result mix (`hit` enemy / `hit` ally /
   `miss` / `denied` / `deny_win` / `cancel`), `hitRateByDist`, and for misses
   `closest.dist` and `targetMoved` (lead error vs target juke).
3. **Hooks aimed at us.** `dodge.dodged / hooksAimedAtMe` and what we did during
   `flightTicks`.
4. **Time off the map and in the river.** Respawn gaps, `timeInRiverSec`, Rot uptime.
5. **Runes and purchases.** `rune_taken`, `buy` rows vs the policy's intended order.
6. **Uncredited deaths and rot suicides** from the results arithmetic.

## Procedure and outputs

Use with core `replay-diagnosis`, before any policy correction (D-05). Retrieve and
decode:

```sh
"$RESEARCH_CLI" softmax xp status <E-id>
"$RESEARCH_CLI" softmax xp artifacts <E-id> --dest .scratch/xp/<E-id>
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/native.py" path
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/replay_inspect.py" <ABS_EPISODE_DIR> --seat <N> --json
"$RESEARCH_PYTHON" "$RESEARCH_HOME/games/pudge-wars-basic/tools/replay_inspect.py" <ABS_EPISODE_DIR> --seat <N> --events kill,damage,hook_landed,river_enter --ticks <A>:<B> --json
```

Verify: replay SHA-256 against the download receipt, terminal status, game version,
seat→policy mapping, "every tick matched", and that `summary.json` per-seat kills,
deaths, hooks thrown and landed equal `results.json`. Any mismatch or decoder failure is
an observation gap; never replace missing counts by zeros.

Inspect the earliest divergence: what our seat could see (fog: enemies on their own bank
are hidden unless within 20 tiles of us or a ward), what it issued (`action` rows), what
the game accepted, and the outcome. Analyst state shows hidden facts; do not blame the
policy for information it could not see. For "would another choice have been better",
run `branch.py <ABS_REPLAY> --slot <N> --ticks <T> --json` (bots continue, so a branch is
a key, not a prediction).

Persist the immutable replay and the claim:

```sh
"$RESEARCH_CLI" records new evidence --meta-file .scratch/evidence.json
"$RESEARCH_CLI" records new claim --meta-file .scratch/claim.json --body-file .scratch/claim.md --slug <slug>
```

The claim names the before metric, the witness tick and seat, the source owner at a
pinned commit (`examples/pudge_wars/sim.nim:a-b@<sha>` or `strategy/policy.bas` lines), a
nearby counterexample and the coverage gaps. A single replay supports a belief, not
competitive strength (D-07). If the cause cannot be shown, request instrumentation
(ANNOTATE in the candidate) through core `replay-diagnosis`; do not edit strategy.
