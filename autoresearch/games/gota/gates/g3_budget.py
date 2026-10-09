"""G3: hard compile ceilings and soft measured per-decision ceilings."""
from pathlib import Path

from . import GateContext, GateResult, gate_run
from .limits import HARD, LEGACY, PRINT, SOFT
from .native import NATIVE_MODE, measure


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    compiled = measure(artifact, context)
    if compiled['outcome'] != 'passed':
        return GateResult(compiled['outcome'], compiled)
    limits = HARD if compiled.get("mode") == "structured" else LEGACY
    violations = [name for name, limit in limits.items()
                  if compiled[name] > limit]
    runtime = []
    if not context.seeds or context.max_ticks < 1:
        raise ValueError('Runtime seeds and positive tick count required')
    for seed in context.seeds:
        measured = measure(artifact, context, seed)
        if measured['outcome'] == 'errored' or 'error' in measured:
            return GateResult(measured['outcome'], measured)
        row = measured['runtime']
        runtime.append(row)
        if (measured['outcome'] == 'failed' or not row['decisions']
                or any(count <= 0 for count in row['seat_decisions'])):
            violations.append(f'seed {seed}: zero decisions or VM error')
        for name, limit in (SOFT | PRINT).items():
            if row[name] > limit:
                violations.append(f'seed {seed}: {name}')
    return GateResult('failed' if violations else 'passed', {'compile': compiled, 'runtime': runtime,
                                      'violations': violations,
                                      'native_mode': dict(NATIVE_MODE),
                                      'sample_ticks': context.max_ticks,
                                      'seeds': list(context.seeds)})
