# Pudge Wars Basic files: provenance

Prepared 2026-10-26 (PDT). This folder targets **Pudge Wars Basic** only: the game-hosted
BASIC league `league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0`, Coworld `pudge-wars-basic`
version 0.1.8 (`cow_c23845f0-444f-4d2f-864f-5764c58eda4a`).

## Sources

| Source | Identity | Used for |
|---|---|---|
| Game source `Metta-AI/polyworld-pudge-wars` | commit `cd8111a5ee4f2fdc38c027615b33421f6c7d2536` (the manifest's `game.runnable.source_url`) | every `path:a-b@cd8111a5` citation in `docs/`, the seed claims and lessons |
| BASIC VM `treeform/bassy` | commit `a02e6dd5889310b13d21725c891bde8d01539f08` (`nimby.lock:2-2@cd8111a5`) | `bassy:path:a-b@a02e6dd5` citations in `docs/policy-surface.md` |
| Coworld package `cow_c23845f0-444f-4d2f-864f-5764c58eda4a` | `coworld_manifest.json`, `player-files/<sha256>`, downloaded with `coworld` 0.1.64 | starter bytes, variants, results schema |
| Upstream knowledge pack (optimizer-seed mixin) | `games/pudge-wars/` in the game source at `cd8111a5` (`MIXIN.md`, `META.md`, `closed_levers.md`, `experiments/`, `skills/{diagnosis,eval-design,replay-inspection}/README.md`) | leads in `docs/game-guide.md`, the skills' upstream notes, the seed lessons' scars |
| League readback | `coworld` 0.1.64 and `softmax_cli` 0.26.40, read-only, 2026-10-26 ≈ 14:40 PDT: division results, rounds 2929–2936 (192 episodes, their `results.json`), one hosted replay `ereq_1f9ac57f-…` | the standings and noise numbers in `docs/game-guide.md` and the skills; `seed/evidence/snapshots/` |
| Local runs | native `pwleague` and headless builds of `cd8111a5` on macOS (Nim 2.2.10), duel and 3v3 samples, a compile-failure sample, an ANNOTATE probe | `docs/replay-format.md` field descriptions |
| GotA game folder in this repository | `researcher/games/gota/` and `researcher/skills/gota/` on branch `pudge-wars-game` | structure of the skills, seed and seed test |
| Binding skill templates | `researcher/games/_template/skills/*/SKILL.md` (optimizer-seed `cfa0cb335809e67490921d07b89be6d2afcd5833`) | the headings every skill fills |

## Vendored byte-for-byte

| File | Source | SHA-256 |
|---|---|---|
| `seed/strategy/policy.bas` | package `player-files/f38ba292…` = `coworld/players/base.bas@cd8111a5` (manifest player `base`) | `f38ba2929c91f985d73afa5a54ec3e5da582e5e71122069cbc2847b19fc48511` |
| `seed/evidence/snapshots/league-standings-2026-10-26.json` | `coworld results div_471aedc1-00f7-41dd-971d-f0beff95a439 --json` | `6a1f95381228bf7ae2d3b167ed1ef28fbd013e4ad57c1cad4aa511171abf07ef` |
| `seed/evidence/snapshots/league-episodes-2026-10-26.txt` | analysis of the 192 sampled league `results.json` files (rounds 2929–2936) | `fe6735b30f1b6d5729a9b9f779e2327d82d0ba2d7d484cf21c99d13ba7ea8244` |
| `seed/evidence/source-citations.json` excerpts | exact line ranges of the game source at `cd8111a5`, each with excerpt and file SHA-256 | per entry |

The baselines in `baselines/` and their sidecars are documented by the tools that ship them
(`baselines/*.provenance.md`).

## Adapted, not copied

- `docs/*.md`: written for this folder from the source at `cd8111a5`; every rule is cited.
  They correct stale upstream prose (`examples/pudge_wars/duel_loop.md`,
  `examples/pudge_wars/CLAUDE.md`, the softmax.com wiki, comments in `content.nim` and
  `sim.nim`, `readme.md` and `coworld/deployment.json`); the corrections are listed in
  `docs/mechanics.md` and `docs/policy-surface.md`.
- `skills/*/SKILL.md`: GotA's skill shape (worker setup, long commands, procedure with exact
  commands) plus every template heading, filled with Pudge mechanics, the upstream mixin's
  failure classes and evaluation advice, and the 2026-10-26 league noise numbers.
- `seed/strategy/STRATEGY.md`: a description of the Base starter written from its source.
- `seed/kb/`, `seed/lessons/`: new records. Claims are verified only from source citations;
  league facts are beliefs backed by the dated snapshots. Lessons are tentative transfers from
  the upstream mixin and cite its exact lines.
- `researcher/tests/unit/pudge_wars_basic/test_seed.py`: modelled on GotA's seed test.

## Not vendored

The mixin's instruments (`games/pudge-wars/instruments/*`) target the earlier Python and
Docker player runtime and its campaign tooling; they were read, not copied. The mixin's
`META.md` field snapshot (2026-09-09) concerns that earlier runtime and is cited only as
history.

## Known upstream errors found while preparing this folder

- `hooks_landed` does **not** double-count headshots: the headshot branch increments it once
  and returns before the general latch increment (`examples/pudge_wars/sim.nim:4429-4460@cd8111a5`).
- The duel logger's `accuracy` is the same `hooksLanded / hooksThrown` ratio as the results
  file, not enemy accuracy (`examples/pudge_wars/duellog.nim:858-866@cd8111a5`).
