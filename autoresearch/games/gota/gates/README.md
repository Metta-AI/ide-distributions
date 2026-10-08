# Shared local GotA gates

Every gate exports `run(artifact: Path, context: GateContext) -> GateResult`.
`GateResult.outcome` is `passed`, `failed`, or `errored`; its computed `ok` is true
only for `passed`. A deliberate policy rejection (compile diagnostics, resource
violations, VM failures or malformed policy telemetry) is `failed`. Unexpected
exceptions, missing tools/files, subprocess failures and broken harness invariants
are `errored`, retaining exception type, message and a traceback summary under
`details.error`. Raw exceptions from a gate or its import are caught at the
runner boundary too. An errored result immediately stops later gates.
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
uv run --locked --python 3.12 python -m games.gota.gates tests/fixtures/gota/v13-live.bas --baseline tests/fixtures/gota/v13-live.bas --work-dir /tmp/gota-gates-new
```

`--gate NAME` selects a gate and can repeat. Each result is printed as JSON;
`WORK_DIR/gates.json` holds the final report, including an errored harness if
startup fails. Exit 0 means every selected gate ran and passed; exit 1 means a
policy failed verification; exit 3 means a tool or harness errored. Gates omitted
after an error were not run. A G4 behavior regression is advisory and does not change its exit
status; missing or failed measurements do. Use a fresh work directory for G2.
No command uploads or submits a policy. The manifest and container image must
already be available for the intended live release. G2 uses the project lockfile for coworld and softmax-cli through offline
`uv run`; it does not override the locked versions. `tools/lasthit_eval.py` uses
the same `uv run --project PROJECT --locked --offline --python 3.12` invocation,
with the project directory resolved from the script rather than the caller's
working directory. Cache the locked dependencies before either command.

## Evidence and limits

- All native measurements retain a verified source/config/dependency/output build
  receipt and validate the engine/Bassy identity reported by the executable. Cache
  acceptance uses that receipt, not file timestamps.
- G1 compiles using the actual GotA host schema and limits at the resolved full
  engine pin. The native receipt binds engine/Bassy source and executable bytes.
  The probe reports a known Bassy `BasicError` during compilation as `failed`.
  Unexpected native exceptions report `errored` with their native type, message
  and stack trace; release builds enable stack traces for the probe. Simulation
  VM error flags remain explicit policy failures. The Python reader validates
  outcome consistency, compile counters and complete runtime counters/results
  before interpreting measurements as policy evidence.
- G2 executes all ten seats through pinned coworld and reads back staged artifact
  hashes, every completed private log, BASIC errors, ten active scripts, decisions,
  nonzero aggregate actions, and full results. Missing runner/completion markers,
  truncated results and non-finite measurements are tool errors. Complete
  measurements showing inactive seats, zero decisions or zero actions are policy
  failures. Positive XP is compatibility
  evidence for v13-live, not a general G2 requirement for every possible policy.
- G3 enforces legacy ceilings of 256 globals, 32 arrays, 4096 cells and 65536
  source bytes, or structured ceilings of 512 globals, 128 arrays, 262144 cells
  and 65535 policy bytes (host injection excluded from this byte count), 64 routines
  (including main), and 20000 static instructions. Runtime ceilings are 80000
  instructions, 200000 work units, 1024 PRINT bytes and 128 PRINT events per
  decision. The 80% instruction/work thresholds apply to live 100000/250000
  budgets, not historical 20000/50000 budgets. Missing or malformed runtime
  measurements error and stop verification; measured ceiling violations fail.
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
Builders cache ignored binaries. Measured policy compile/runtime failures produce
failed gates; missing tools, build/receipt failures and unexpected exceptions
produce errored gates. Receipts include effective absolute compiler/configuration paths;
after moving an install, rebuild to produce valid receipts at the new location.
No parent repository Git metadata is required, so a `git archive` install works.

GotA tests explicitly request `prepare_native_sources` when they inspect pinned
engine sources or build replay helpers, and `prepare_native_tools` when they
execute the current probe or match runner. These session fixtures prepare current
and recorded engine pins; the latter also builds the current helpers with Nim
2.2.10. A cold native test run needs network access or local mirrors. Replay tests
build historical helpers offline after source preparation. Pure Python tests,
including seed, evaluate-binding and mocked compiler tests, need neither Nim nor
source preparation. The real-agent process guard remains autouse for every GotA
test. For example, from `autoresearch/`, this needs only the locked Python environment:

```sh
uv run --locked --offline pytest tests/unit/gota/test_seed.py tests/unit/gota/test_evaluate_binding.py tests/unit/gota/test_native_build.py -q
```

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

## Engine resolution

The command resolves live league version and canonical Coworld runnable source
through the shared CLI/transport, verifies their versions agree, prepares exact
source/dependency pins, and checks the supported host contract before running
gates. Configure the same profile/player environment as the build binding in
[GAME.md](../GAME.md). It writes `engine_identity.json` and
`coworld_manifest.json` in the work directory. Optional `--manifest PATH` must
match that fetched manifest; explicit `--offline --manifest PATH` selects the
historical pin without a network lookup. An online failure never falls back.

Programmatic GateContext callers may set `engine_commit` explicitly. Its omitted
value retains the historical test/audit pin; that default is not live resolution.
Historical equivalence and goal audits retain their immutable M1 engine semantics.
