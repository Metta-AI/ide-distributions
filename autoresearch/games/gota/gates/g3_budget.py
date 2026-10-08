"""G3: hard compile ceilings and soft measured per-decision ceilings."""
from pathlib import Path

from . import GateContext, GateResult, gate_run
from .limits import HARD, LEGACY, PRINT, SOFT
from .native import NATIVE_MODE, measure


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    compiled = measure(artifact, context)
    if compiled.get('ok') is not True:
        return GateResult('failed', compiled)
    limits = HARD if compiled.get("mode") == "structured" else LEGACY
    violations = [name for name, limit in limits.items()
                  if name not in compiled or compiled[name] > limit]
    runtime = []
    if not context.seeds or context.max_ticks < 1:
        raise ValueError('Runtime seeds and positive tick count required')
    for seed in context.seeds:
        measured = measure(artifact, context, seed)
        row = measured.get('runtime', {})
        runtime.append(row)
        if (measured.get('ok') is not True or not row.get('decisions')
                or len(row.get('seat_decisions', [])) != 10
                or any(count <= 0 for count in row['seat_decisions'])):
            violations.append(f'seed {seed}: missing decisions or VM error')
        for name, limit in (SOFT | PRINT).items():
            if name not in row or row[name] > limit:
                violations.append(f'seed {seed}: {name}')
    return GateResult('failed' if violations else 'passed', {'compile': compiled, 'runtime': runtime,
                                      'violations': violations,
                                      'native_mode': dict(NATIVE_MODE),
                                      'sample_ticks': context.max_ticks,
                                      'seeds': list(context.seeds)})
