"""G1: compile with the real live GotA host schema and limits."""
import subprocess
from pathlib import Path

from . import GateContext, GateResult
from .native import measure


def run(artifact: Path, context: GateContext) -> GateResult:
    try:
        result = measure(artifact, context)
        return GateResult(result.get('ok') is True, result)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return GateResult(False, {'error': str(error)})
