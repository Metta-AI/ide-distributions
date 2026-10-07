"""G5: parse every real policy log; malformed snapshots never disappear silently."""
from pathlib import Path

from ..tools.gota_episodes import _parse_telemetry
from . import GateContext, GateResult
from .g2_episode import inspect_episode


def parse_log(path: Path) -> dict:
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts[:1] != ['LH']:
            continue
        if len(parts) < 12:
            raise ValueError(f'{path}: malformed LH telemetry')
        try:
            [int(value) for value in parts[1:]]
        except ValueError as error:
            raise ValueError(f'{path}: malformed LH telemetry') from error
    failed, telemetry = _parse_telemetry(path)
    if failed or telemetry is None:
        raise ValueError(f'{path}: BASIC failure or missing LH telemetry')
    return vars(telemetry)


def run(artifact: Path, context: GateContext) -> GateResult:
    try:
        if context.episode_dir is None:
            return GateResult(False, {'error': 'G2 episode logs required'})
        inspect_episode(artifact, context.episode_dir)
        seats = [parse_log(context.episode_dir / 'logs' / f'policy_agent_{slot}.log')
                 for slot in range(10)]
        return GateResult(True, {'seats': seats, 'episode_dir': str(context.episode_dir),
                                 'telemetry_kind': 'legacy_LH',
                                 'compiler_trace_coverage': None})
    except (OSError, ValueError) as error:
        return GateResult(False, {'error': str(error)})
