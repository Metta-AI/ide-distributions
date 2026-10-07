# Shared local GotA gates

Every gate exports `run(artifact: Path, context: GateContext) -> GateResult`.
Import the context and result from `games.gota.gates` with `autoresearch/` on
Python's import path. The BASIC artifact must use the legacy host surface.
The future compiler build path must produce that same artifact surface.

```python
from pathlib import Path
from games.gota.gates import GateContext
from games.gota.gates import g1_lint

context = GateContext(work_dir=Path('/tmp/gota-check'), baseline=Path('baseline.bas'))
result = g1_lint.run(Path('candidate.bas'), context)
```

From `autoresearch/`, run all five gates with the locked Python environment:

```sh
uv run --locked --python 3.12 python -m games.gota.gates tests/fixtures/gota/v13-live.bas --baseline tests/fixtures/gota/v13-live.bas --manifest /path/to/coworld_manifest.json --work-dir /tmp/gota-gates-new
```

`--gate NAME` selects a gate and can repeat. Each result is printed as JSON;
`WORK_DIR/gates.json` holds the final report. Exit 0 means every selected gate
ran and passed. A G4 behavior regression is advisory and does not change its exit
status; missing or failed measurements do. Use a fresh work directory for G2.
No command uploads or submits a policy. The manifest and container image must
already be available for the intended live release. G2 pins coworld 0.1.57 and
softmax-cli 0.26.38 through locked-project `uv run --with` invocations.

## Evidence and limits

- G1 compiles using the actual GotA host schema and `heroVmLimits` in Polyworld
  `5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`; it is not a lexical syntax estimate.
- G2 executes all ten seats through pinned coworld and reads back staged artifact
  hashes, every completed private log, BASIC errors, ten active scripts, decisions,
  nonzero aggregate actions, and full results. Positive XP is compatibility
  evidence for v13-live, not a general G2 requirement for every possible policy.
- G3 enforces 256 globals, 32 arrays, 4096 cells, 65536 source bytes, 64 routines
  (including main), and 20000 static instructions. Runtime ceilings are 80000
  instructions, 200000 work units, 1024 PRINT bytes and 128 PRINT events per
  decision. The 80% instruction/work thresholds apply to live 100000/250000
  budgets, not historical 20000/50000 budgets. Missing runtime measurements fail.
- G4 runs native UniqueDraft matches for seeds 1 through 10, candidate versus
  baseline on each side. It computes winning lifetime XP per simulated minute, floored for each hero before
  averaging (Glory) and win rate for the five candidate/baseline heroes. Regressions are
  advisory. Identical artifact hashes reuse one mirror match per seed and read
  both teams for both side assignments. These are local screens; they do not
  establish league performance or full historical behavioral equivalence.
- G5 parses the legacy `LH` snapshots from all ten G2 logs through the existing
  toolkit parser, rejecting every malformed snapshot before parsing the final
  one. It reports `compiler_trace_coverage: null`: submitted v13 has no compiler
  goal trace. Passing LH parsing does not establish compiler tracing coverage,
  exact kill attribution, or truth of the policy's inferred combat counters.

G3 and G4 use `tools/gate_probe.nim` against the pinned engine, UniqueDraft mode and
seeded map. Mode metadata records `drafting=true`, `draft_mode=UniqueDraft`,
`replay_mode=false`, matching `newGame` defaults at pinned `sim.nim:5849-5856`.
The positional `false` in the probe controls replay mode, not drafting. Each VM's public instruction/work counters and emitted PRINT callback
are measured during real simulation decisions; compile counts come from Bassy's
public program accessors. A `GateContext` caches measurements in memory by
artifact hash, opponent hash, seed, tick budget and side. Defaults sample all ten
seeds for 31200 ticks each, ending earlier on a game end. A caller may request a
shorter G3 sample, which is explicitly reported as `sample_ticks`; it proves only
those decisions. G4 requires the full tick budget. Native screens differ from the
competition manifest's OpenDraft mode and do not prove an exhaustive resource bound
for every future observation. G2 supplies separate real manifest execution proof.

Current PRINT accounting follows Bassy `parsePrint` and `chargePrint`, not an
assumed constant number of events per item. `PRINT "X", 12` emits four events and
five bytes; `PRINT "X"; 12;` emits two events and three bytes; empty `PRINT` emits
one newline event and one byte. Tests measure all three through actual engine
callbacks. Failed VMs fail the gate even if they stopped before emitting all text.

Native build requires Nim 2.x (verified with 2.2.10), Git, and network on first
use. `tools/build_nim.sh` synchronizes the engine's pinned dependencies and caches
ignored binaries; compiler/runtime errors and missing tools produce failed gates.
Unit tests use short real native simulations and recorded telemetry fixtures.
The fixture envelope assembled by a test is a parser test, not a new episode.
Full manifest and ten-seed execution evidence is recorded separately in provenance.

## Compiler adapter

The GotA compiler driver calls these same modules in G1–G5 order through
`compiler/tools/gota_gates.py`. It selects the verified cached native probe
explicitly and never builds/fetches implicitly. Its GateContext may carry a
`compiler_map`: shared G5 then parses compiler telemetry through the existing
strict reader on all ten G2 logs, requiring decisions and rejecting malformed
or backwards-tick lines and BASIC failures. Legacy LH parsing is unchanged.
Compiler parsing still reports `compiler_trace_coverage: null`; full trace
coverage, goal truth and semantic equivalence are separate validations.
See [compiler backend](../compiler/docs/gota-backend.md) for the packing contract,
configuration and exact scope of local fixture evidence.
