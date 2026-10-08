# Shared local GotA gates

Every gate exports `run(artifact: Path, context: GateContext) -> GateResult`.
Import the context and result from `games.gota.gates` with `autoresearch/` on
Python's import path. Both legacy baseline and structured compiler policies are
accepted; the marker selects the native host's corresponding limits.

```python
from pathlib import Path
from games.gota.gates import GateContext
from games.gota.gates import g1_lint

context = GateContext(work_dir=Path('/tmp/gota-check'), baseline=Path('baseline.bas'))
result = g1_lint.run(Path('candidate.bas'), context)
```

From `autoresearch/`, run all five gates with the locked Python environment:

```sh
uv run --locked --offline --python 3.12 python -m games.gota.gates tests/fixtures/gota/v13-live.bas --baseline tests/fixtures/gota/v13-live.bas --manifest /path/to/coworld_manifest.json --work-dir /tmp/gota-gates-new
```

`--gate NAME` selects a gate and can repeat. Each result is printed as JSON;
`WORK_DIR/gates.json` holds the final report. Exit 0 means every selected gate
ran and passed. A G4 behavior regression is advisory and does not change its exit
status; missing or failed measurements do. Use a fresh work directory for G2.
No command uploads or submits a policy. The manifest and container image must
already be available for the intended live release. G2 pins coworld 0.1.57 and
softmax-cli 0.26.38 through locked-project offline `uv run --with` invocations.

## Evidence and limits

- All native measurements retain a verified source/config/dependency/output build
  receipt and validate the engine/Bassy identity reported by the executable. Cache
  acceptance uses that receipt, not file timestamps.
- G1 compiles using the actual GotA host schema and `heroVmLimits` in Polyworld
  `801749e509e888b423ba44ee2188946c22e3ba85`; it is not a lexical syntax estimate.
- G2 executes all ten seats through pinned coworld and reads back staged artifact
  hashes, every completed private log, BASIC errors, ten active scripts, decisions,
  nonzero aggregate actions, and full results. Positive XP is compatibility
  evidence for v13-live, not a general G2 requirement for every possible policy.
- G3 enforces legacy ceilings of 256 globals, 32 arrays, 4096 cells and 65536
  source bytes, or structured ceilings of 512 globals, 128 arrays, 262144 cells
  and 65535 policy bytes (host injection excluded from this byte count), 64 routines
  (including main), and 20000 static instructions. Runtime ceilings are 80000
  instructions, 200000 work units, 1024 PRINT bytes and 128 PRINT events per
  decision. The 80% instruction/work thresholds apply to live 100000/250000
  budgets, not historical 20000/50000 budgets. Missing runtime measurements fail.
- G4 runs native OpenDraft matches for seeds 1 through 10, candidate versus
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

G3 and G4 use `tools/gate_probe.nim` against the pinned engine, OpenDraft mode and
seeded map. Mode metadata records `drafting=true`, `draft_mode=OpenDraft`,
`replay_mode=false`, selected explicitly by the probe.
The positional `false` in the probe controls replay mode, not drafting. Each VM's public instruction/work counters and emitted PRINT callback
are measured during real simulation decisions; compile counts come from Bassy's
public program accessors. A `GateContext` caches measurements in memory by
artifact hash, opponent hash, seed, tick budget and side. Defaults sample all ten
seeds for 31200 ticks each, ending earlier on a game end. A caller may request a
shorter G3 sample, which is explicitly reported as `sample_ticks`; it proves only
those decisions. G4 requires the full tick budget. Native screens use OpenDraft but do not prove an exhaustive resource bound
for every future observation or every manifest setting. G2 supplies separate real manifest execution proof.

Current PRINT accounting follows Bassy `parsePrint` and `chargePrint`, not an
assumed constant number of events per item. `PRINT "X", 12` emits four events and
five bytes; `PRINT "X"; 12;` emits two events and three bytes; empty `PRINT` emits
one newline event and one byte. Tests measure all three through actual engine
callbacks. Failed VMs fail the gate even if they stopped before emitting all text.

Native build requires Nim 2.2.10 and Git. Populate pinned engine and dependency
sources explicitly before building (commands from `autoresearch/`):

```sh
uv run --locked python games/gota/tools/native_build.py prepare 801749e509e888b423ba44ee2188946c22e3ba85
sh games/gota/tools/build_nim.sh 801749e509e888b423ba44ee2188946c22e3ba85 gate_probe
sh games/gota/tools/build_gota_match.sh
```

`prepare` fetches the exact engine commit and every commit in its
`coworld/dependencies.lock`. It refuses existing dirty or wrong-pin sources.
Set `GOTA_ENGINE_SOURCE` to a local Git mirror and `GOTA_DEPENDENCY_ROOT` to a
directory of dependency Git mirrors named by the lockfile for offline setup.
Neither variable changes required pins. Build and receipt validation never fetch.
Builders cache ignored binaries; compiler/runtime errors and missing tools produce
failed gates. Receipts include effective absolute compiler/configuration paths;
after moving an install, rebuild to produce valid receipts at the new location.
No parent repository Git metadata is required, so a `git archive` install works.

The GotA pytest session explicitly prepares current and recorded engine pins and
builds the current probe and match runner. A cold session therefore needs network
access or local mirrors and the same native tools. Replay tests build their
historical helpers offline after setup.
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
See [compiler backend](../compiler/docs/gota-backend.md) for the structured storage contract,
configuration and exact scope of local fixture evidence.
