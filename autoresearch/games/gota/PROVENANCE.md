# GotA files: provenance

| File | Source | Identity |
| --- | --- | --- |
| `tools/count_globals.py` token/comment handling | `personal_labs_paintbot_pw/paintbot_pw_lab/tools/strategy_basic.py:91-130` | commit `6b0bffa5085ffc4bf94ecaa8baee463e55599c84`; adapted |
| `tools/count_globals.py` host-data names | `Metta-AI/polyworld/examples/gods_of_the_arena/bots.nim:45-64,195-201` | commit `7365e4e9390e97bbc8b7722b41a2ae88ae008c2b`; historical baseline host |
| `../../tests/fixtures/gota/submitted-v13.bas` | Softmax policy version `40d72eea-ac61-44b0-aaf4-7828fbf30170` | SHA-256 `d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1`; 54,718 bytes |

The submitted policy stays byte-identical. Its adjacent
`submitted-v13.provenance.md` replaces an in-file header under the Phase A parent's
explicit ruling, because a header would change the policy identity.
The remainder of the counter and its small acceptance fixture are new Task 0.1 code.
The counter is a lexical inventory for the historical GotA structured BASIC surface,
not the game compiler or a syntax-validation gate. Its host inputs are historically
pinned; a future host change requires updating the list and checking it against the deployed host.

Run from the repository root:

```sh
python3 autoresearch/games/gota/tools/count_globals.py autoresearch/tests/fixtures/gota/submitted-v13.bas
python3 autoresearch/games/gota/tools/count_globals.py autoresearch/tests/fixtures/gota/submitted-v13.bas --inventory
```

See `../../docs/plans/findings/gota-globals-budget.md` for submitted identity,
source-match limits, the pinned PW runtime inventory, and the packing requirement.

## Task 1.2 toolkit source manifest

| File | Source path | Source commit |
| --- | --- | --- |
| `tools/replay_stats.py` | `gods_of_the_arena_lab/tools/replay_stats.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/compare.py` | `gods_of_the_arena_lab/tools/compare.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/miner_rows.py` | `gods_of_the_arena_lab/tools/miner_rows.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/features.py` | `gods_of_the_arena_lab/tools/features.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/lasthit_eval.py` | `gods_of_the_arena_lab/tools/lasthit_eval.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/gota_episodes.py` | `gods_of_the_arena_lab/tools/gota_episodes.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/expand_replay.nim` | `gods_of_the_arena_lab/tools/expand_replay.nim` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/build_nim.sh` | `gods_of_the_arena_lab/tools/build_expand_replay.sh` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/ab_stats.py` | `.claude/skills/coworld-ab/scripts/ab_stats.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/variance_miner.py` | `.claude/skills/coworld-hypothesis-miner/scripts/variance_miner.py` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `tools/gota_verify.nim` | `players/users/relh/co-gas/polyworld-basic/tools/gota_verify.nim` | `6742feb08f8b119591d09261cadc581065fa698b` |
| `tools/gota_audit.nim` | `players/users/relh/co-gas/polyworld-basic/tools/gota_audit.nim` | `6742feb08f8b119591d09261cadc581065fa698b` |
| `tools/gota_policy_replay.nim` | `players/users/relh/co-gas/polyworld-basic/tools/gota_policy_replay.nim` | `6742feb08f8b119591d09261cadc581065fa698b` |
| `docs/game-guide.md` | `gods_of_the_arena_lab/docs/wiki/game-guide.md` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `docs/mechanics.md` | `gods_of_the_arena_lab/docs/wiki/mechanics.md` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `docs/policy-surface.md` | `gods_of_the_arena_lab/docs/wiki/policy-and-host-surface.md` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |
| `docs/replay-format.md` | `gods_of_the_arena_lab/docs/replay-format.md` | `07dc6f94760fae3cf2de36877e0b6186cfd1dc63` |

## Live replay adaptation and proof

The starting sources above are adapted to **Polyworld 5287c437 replay API**
(full commit `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`), the verified source of
GotA `2026.10.6.1`. The live league resolves to
`cow_162aa34f-f447-4c5d-8db3-480ec83e98f4`.

- Cast kinds are `ActionCastTarget` and `ActionCastPoint`; the ability index is
  `action.slot` (`replays.nim:24-25,119-130`). The expander and audit use that slot.
  Audit snapshots expose live `spellsReady`; the obsolete `manualSpells` field
  is removed. The policy replay reader already used the live slot representation.
- The build script requires the exact recording commit and accepts a tool target.
  It compiles with `-d:replayEvents`, root import paths, and separate target caches.
- The expander reads engine `XpGained` and `GoldGained` events and checks their
  per-tick and final totals against simulation state. Lane last hits and structure
  kills use reward source identities. Nearby/shared creep XP and 200-XP structures
  invalidate the historical `25*LH + 150*kills + 100*buildings` identity.
  Inventory events come from the engine rather than inferred command effects.
  Teleport scrolls are consumed when the channel starts (`sim.nim:4480-4492`).
  Diagnostic action-prefix probes preserve the authoritative event buffer.
- Platform results scores use `scores.nim` Emmett's Glory, while `win` remains 0/1.
  All recorded state hashes and results XP/Glory are checked before statistics
  are accepted. Combat victim ambiguity remains explicit; missing policy identity
  stays unknown. Cast requests include rejected requests; they are distinct from
  successful cast events and damage or healing.
- `raw_spell_counts.nim` is new code based on the live replay API. It decodes the
  tape and counts cast requests without importing the expander or re-simulating.
  Its optional prefix export drops CPU telemetry rather than inventing truncated
  CPU rates. The replay fixture's original state hashes remain unchanged.
- Comparison helper imports are local. SciPy is a locked project dependency.
  Import sorting, executable flags, and narrow lint fixes do not change statistics.
  `features.py` retains its import-only adapter interface: `--help` exits zero but
  emits no CLI help. `lasthit_eval.py` now requires `--manifest` and explicit
  `--reference` unless `--mirror` is used, and invokes pinned coworld 0.1.57.

Verification on 2026-10-07 with coworld 0.1.57, softmax-cli 0.26.38, Nim 2.2.10:

| Recording | Checks | Outcome |
| --- | --- | --- |
| Plan 01 player-only probe | 28,877 hashes; results XP/Glory; reward totals; action expansion | RedTeam; red XP 26,337; blue XP 21,432 |
| Local competition, ten downloaded official starters | 28,909 hashes; results XP/Glory; reward totals | time limit; red XP 13,502; blue XP 12,982 |
| Independent probe spell request count | raw tape vs expanded actions, every hero | equal; hero 100: 98 vs 98 |
| Small fixture | 1,500 original hashes; recorded statistics; raw spell request parity | PASS |

The probe was read from the existing downloaded artifacts; no re-download was
needed. Live identity readback and game download used Games Bond in a temporary
HOME. The original credential bytes were unchanged and the temporary copy deleted.
The ten official starter input bytes were supplied to the local runner unchanged.
The immutable submitted v13 was also attempted and rejected by the live loader;
that separate acceptance blocker is recorded in `gota-toolkit-drift.md`.

## Historical adapter and documentation limits

The copied guide, mechanics, policy surface, and replay format describe their
pinned historical source. They are source references, not current live contracts.
The adaptations above are the live replay contract. The comparison and miner adapters use validated native outcomes rather than
Glory as win indicators, and verified replay enum IDs rather than historical
fixed-seat classes. Without class evidence labels are unknown; class/role grouping
and filters fail with a request for verified replay stats. The LH adapter reports
classes as unknown and retains telemetry-specific accounting assumptions. Do not
infer exact XP accounting from LH snapshots. Use verified replay rows for live
combat and XP analysis.
Nim helper compilation is verified; no new competitive policy claim is made.

## Fixture provenance

`../../tests/fixtures/gota/replay_small.bin` is the first 1,500 ticks of the Plan 01
probe (replay format 6, gameplay version 68), exported through the live encoder.
SHA-256: `728239b80d107a2c2c57ac5fc65be4fabb5763d78abb94d622c842792d5a7a0b`;
194,694 bytes. It is a partial recording, not a completed match.
`replay_small.results.json` records its verified prefix state, and
`replay_small.expected.json` records the Python statistics envelope with only
machine-specific paths removed. These generated fixtures carry provenance here
rather than an in-file header because headers would corrupt binary/JSON formats.

Run from `autoresearch/`:

```sh
uv run --locked --python 3.12 pytest tests/unit/gota
uv run --locked --python 3.12 ruff check games/gota/tools tests/unit/gota/test_tools.py
sh games/gota/tools/build_nim.sh 5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd
uv run --locked --python 3.12 python games/gota/tools/replay_stats.py EPISODE_DIR --ref 5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd --json
sh games/gota/tools/build_nim.sh 5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd raw_spell_counts
games/gota/tools/bin/raw_spell_counts-5287c437 EPISODE_DIR/replay
```

Native build needs Nim, Git, and network access on the first build. Cache and
binaries stay ignored under the tools directory. Reward-only expansion is the
fast default; `--actions` enables exact but slower acceptance probes.

## v13 mechanical live compatibility (Task 1.4)

`tools/make_v13_live.py` is new local code implementing VERDICT-B2 and B3.
It changes exactly seven executable identifier tokens `is` to the noncolliding
`v13_item_slot` and exactly 64 executable `/` tokens to `\`. Comments, strings,
whitespace, and every other token remain identical. The operator rewrite restores
historical int32 division toward zero, not the live dialect's decimal division.
`tests/unit/gota/test_v13_live.py` proves both exact counts and full token identity.

Immutable original `../../tests/fixtures/gota/submitted-v13.bas`: 54,718 bytes,
SHA-256 `d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1`.
Generated `../../tests/fixtures/gota/v13-live.bas`: 54,795 bytes,
SHA-256 `1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
No header is inserted because token identity is the controlling requirement.
The original remains the identity of the submitted league entry.

The intermediate identifier-only copy was SHA-256
`009f8127286d4115b79ec977c694b36e8a99dd130ca6d6d828ba3c0122cf191e`.
It is no longer the generated fixture. Its actual local episode failed all ten
VMs at runtime; `../../docs/plans/findings/gota-v13-live-runtime.md` preserves that
finding and the approved resolution. The original separately rejects loading
with name `'is'` not a scalar, as recorded in `gota-toolkit-drift.md`.

### Local compatibility and gate execution

Actual coworld 0.1.57 / softmax-cli 0.26.38 local episode, downloaded live GotA
2026.10.6.1 manifest, Polyworld source pin `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`:
all ten final-variant source copies were supplied unchanged. Every private log is
free of BASIC errors; game log reports 10/10 scripts active, 288200 decisions,
861497 recorded actions, and positive XP for every hero. The match timed out at
31200 ticks with all scores zero. This proves execution, not competitive strength.
Evidence is at `/tmp/ide-mvp-task14-v13-integer-episode/`; independent tape counts
at `/tmp/task14-actions-by-hero.json` prove every hero submitted actions.
G2 then executed the same artifact through its public gate API; evidence at
`/tmp/ide-mvp-task14-gates/episode/` passed identity and execution readback.
No credential was needed for these local manifest episodes; no policy or league
was uploaded or changed. A manifest/source pin is recorded, not an independent
container-build provenance proof.

`tools/gate_probe.nim` is new local instrumentation that includes the pinned
engine's host schema, compiler and real simulation decision runner. Compile
counts use Bassy's public accessors; resource samples use every VM's instruction
and work counters, per-seat decisions, and actual PRINT callback events/bytes.
`tools/build_nim.sh` now supports `gate_probe`. `raw_spell_counts.nim` additionally
reports action-tape counts per hero without resimulation. No new compiler,
lexical budget estimator, or synthetic runtime evidence was introduced.
Shared Python gates and their API/CLI are documented in `gates/README.md`.

The plan's historical 20k/50k dynamic ceilings are superseded by VERDICT-B:
soft 80000 instructions and 200000 work units are 80% of live 100000/250000.
Hard storage, source, routine, static-code and PRINT ceilings come from live
`bots.nim:114-138`. PRINT events vary with separators and newline suppression;
actual engine tests observe `PRINT "X", 12`: four events/five bytes,
`PRINT "X"; 12;`: two events/three bytes, empty PRINT: one event/one byte.

Run from `autoresearch/`:

```sh
uv run --locked --python 3.12 python games/gota/tools/make_v13_live.py tests/fixtures/gota/submitted-v13.bas tests/fixtures/gota/v13-live.bas
uv run --locked --python 3.12 pytest tests/unit/gota -q
uv run --locked --python 3.12 ruff check games/gota/gates games/gota/tools tests/unit/gota
uv run --locked --python 3.12 python -m games.gota.gates tests/fixtures/gota/v13-live.bas --baseline tests/fixtures/gota/v13-live.bas --manifest /path/to/coworld_manifest.json --work-dir /tmp/gota-gates-new
```

### Recorded telemetry fixtures

`v13-live-telemetry.log` contains the first five lines from final-variant seat 0's
actual private log. `v13-live-game.log` and `v13-live-results.json` contain the
actual full game summary and result. They carry no credentials or download URLs.
The unit test creates a ten-seat parser envelope using this one recorded log
excerpt, explicit generated completion lines, and matching artifact copies.
That envelope is a test fixture, not independent ten-seat execution evidence.
G5 separately reads all actual G2 logs. It parses historical `LH` snapshots and
reports absent compiler trace coverage explicitly; it does not validate inferred
combat counts or compiler goal telemetry that v13 does not emit.

Recorded fixture SHA-256 values:

- `v13-live-telemetry.log`: `df620bec64a5219964f408b300b65544bd9e2f2286773bf834c2cead0fe47a06` (371 bytes).
- `v13-live-game.log`: `b8bba08428a4e16c4842a80b0fe3c05325a6cf89490fb2ff57b3d2d3a85cf107` (534 bytes).
- `v13-live-results.json`: `27c5159af38f5e8cecbc9a3d2e6762cf22384a5eb62e5718644effd46c3f059c` (175 bytes).

### Final native gate validation and independent review corrections

Seeds 1 through 10 each ran 31200 native ticks; every seat executed 28820
decisions and no VM failed. Maximum charged instructions 25450, work units
39486, PRINT events 80, PRINT bytes 106. All five gates passed; baseline mirror
screen Glory/win rate both zero on both sides (all timeouts), so this is a
measurement-completion check, not evidence of competitive quality. Final report:
`/tmp/ide-mvp-task14-final-gates/gates.json`.

Native execution uses `drafting=true, draftMode=UniqueDraft, replayMode=false`,
verified against pinned `sim.nim:5849-5856,5954-5955`. The initial fixed-roster
description was incorrect and is corrected here and in the gate README/report.
Actual competition manifest episodes use OpenDraft. Native behavior and its
measured resource samples were not changed during this documentation correction.
Python G3/G4 reports now include the verified native mode metadata.

Review also corrected Glory to floor each winning hero's `XP * 1440 / ticks`
before averaging, matching `scores.nim:7-17`; a fractional regression test first
failed then passed. G4 was recomputed from the unchanged ten measured samples,
without new simulation; zero mirror scores remain zero. The artifact mismatch
test now starts from a valid recorded envelope and changes one staged source,
proving identity rejection rather than missing-file rejection.

The generated BASIC artifact preserves the original's trailing blank line at EOF.
Git's whitespace check flags that inherited blank line when the variant is first
added. Removing it would violate the authorized token/whitespace identity rule;
it is retained deliberately. All other changed files pass the whitespace check.

## Task 1.1 contract evidence

`tests/fixtures/gota/probe_full.results.json` is the complete Plan 01 Task 0.1
probe result, copied byte-for-byte from `/tmp/ide-mvp-task12-probe/results.json`.
Experience request: `xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5`; recorded coworld
version: `2026.10.6.1`; ticks: `28877`; outcome: `RedTeam`.
SHA-256: `0aeacf88f6296960345201eb0895d2e87d5c9a6360864f959719fae578ccf1cc`.
The sidecar `probe_full.provenance.json` records this origin without adding a
header to the result bytes. `replay_small.results.json` remains a derived
1500-tick prefix and is not the full winning result.

`GAME.md`, `tools/evaluate.py`, `tools/deferred.py`, and
`tools/resolve_segment.py` are authored here for Plan 02 Task 1.1, not copied
from a lab. The contract reuses the existing shared gates and replay inspector.
Build and guarded upload remain explicit staged gaps; see GAME.md.

## Final Phase B analysis integration corrections

Replay expansion now uses the pinned engine's `world.outcome()` helper
(`sim.nim:3853-3859`) and checks results.json outcome before results verification.
The authored `tools/replay_outcome.nim` boundary is shared by the expander and a
regression using real engine World state with simultaneous god-death draw flags.
`build_nim.sh` copies that boundary and invalidates cached binaries when it changes.

Episode readers validate completed results through the GAME evaluate adapter.
Wins follow native outcome and seat team, independent of winning Glory magnitude.
Historical comparison/miner `draw_rate`/`draw` aggregate both native `draw` and
`time_limit`; episode records and miner `outcome` preserve the distinction.
Last-hit summaries report draw and timeout separately.

Class ID mapping follows `HeroClass` enum order at Polyworld 5287c437
`content.nim:26-36`. Verified replay classes populate comparison/miner records.
Missing class evidence remains `unknown`; class/role grouping and filters require
verified replay stats. The last-hit runner has no verified class evidence and
prints unknown labels. The recorded prefix's seat 0 is class 6 (Crossbowman),
not a seat-default DeathKnight. Inferred LH accounting limitations remain.
The last-hit command resolves its manifest in the caller's directory before
starting coworld from the distribution's games directory.

No new episodes, policy changes, engine changes, or competitive claims were
needed for these corrections. Focused tests reuse the recorded prefix and full
winning results with synthetic completed metadata envelopes.

Repeated draft classes can put several subject seats in one episode/class group.
Comparison seat rates use mean statistics whenever the actual grouped records
contain multiple seats; the historical class shortcut is removed. Actual one-seat
groups retain rate statistics, and episode-level failure/draw rates remain rates.
The shared statistics engine rejects nonintegral binary success counts; fractions
that happen to sum to integers would otherwise pass that guard and take an
inappropriate Fisher path. This correction changes dispatch, not that engine.

## Compiler vendor (Task 2.1)

All copied compiler code, runtime, inputs, and destination test files are listed individually with pinned source paths and SHA-256 values in [compiler/PROVENANCE.md](compiler/PROVENANCE.md). Source: committed Paintbot `6b0bffa5085ffc4bf94ecaa8baee463e55599c84`. This is a preserved Paintbot baseline, pending GotA adaptation; engine and semantic qualification do not transfer.
