"""G1: compile with the real live GotA host schema and limits."""
from pathlib import Path

from . import GateContext, GateResult, gate_run
from .native import measure


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    result = measure(artifact, context)
    return GateResult(result['outcome'], result)
