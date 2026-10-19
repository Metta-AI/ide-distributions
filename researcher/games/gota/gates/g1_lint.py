"""G1: compile with the real live GotA host schema and limits."""
from pathlib import Path

from . import GateContext, GateResult, gate_run
from .limits import HARD, LEGACY
from .native import measure


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    result = measure(artifact, context)
    if result['outcome'] == 'passed':
        limits = HARD if result['mode'] == 'structured' else LEGACY
        violations = [name for name, limit in limits.items() if result[name] > limit]
        if violations:
            return GateResult('failed', {'compile': result, 'violations': violations})
    return GateResult(result['outcome'], result)
