"""G2: pinned coworld all-ten-seat competition episode and honest VM readback."""
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path

from . import GateContext, GateResult, PolicyFailure, gate_run

PROJECT = Path(__file__).resolve().parents[3]


def validate_results(results: dict) -> None:
    """Require complete finite per-seat results before judging measured behavior."""
    if not isinstance(results, dict) or type(results.get('ticks')) is not int or results['ticks'] <= 0:
        raise ValueError('episode results require positive integer ticks')
    for name in ('scores', 'total_xp'):
        values = results.get(name)
        if (not isinstance(values, list) or len(values) != 10 or
                any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    for value in values)):
            raise ValueError(f'episode results require ten finite nonnegative {name} values')


def inspect_episode(artifact: Path, directory: Path) -> dict:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    for slot in range(10):
        staged = directory / 'players' / str(slot) / 'file'
        if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Artifact identity mismatch at seat {slot}')
        text = (directory / 'logs' / f'policy_agent_{slot}.log').read_text()
        if 'BASIC error:' in text or 'Policy loading failed' in text:
            raise PolicyFailure(f'BASIC failure at seat {slot}')
        if f'Player slot {slot} completed.' not in text:
            raise ValueError(f'Missing seat completion: {slot}')
    game_log = (directory / 'logs/game.stdout.log').read_text()
    scripts = re.search(r'scripts: (\d+)/10 active, (\d+) decisions', game_log)
    actions = re.search(r'replay saved: .*\((\d+) actions\)', game_log)
    if scripts is None or actions is None:
        raise ValueError('Missing runner execution/action measurement markers')
    results = json.loads((directory / 'results.json').read_text())
    validate_results(results)
    if int(scripts[1]) > 10:
        raise ValueError('Runner reports more than ten active seats')
    if int(scripts[1]) != 10 or int(scripts[2]) == 0:
        raise PolicyFailure('Measured inactive seats or zero decisions')
    if int(actions[1]) == 0:
        raise PolicyFailure('Measured zero recorded actions')
    return {'artifact_sha256': digest, 'episode_dir': str(directory),
            'scripts_active': 10, 'decisions': int(scripts[2]), 'actions': int(actions[1]),
            'results': results}


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    if context.manifest is None:
        raise ValueError('Downloaded live manifest required')
    directory = context.work_dir / 'episode'
    if directory.exists():
        raise ValueError('Episode output already exists; use a fresh work directory')
    command = ['uv', 'run', '--project', str(PROJECT), '--locked', '--offline', '--python', '3.12',
               'coworld', 'run-episode', str(context.manifest.resolve()),
               *([str(artifact.resolve())] * 10), '--variant', context.variant,
               '--output-dir', str(directory.resolve())]
    subprocess.run(command, env={**os.environ, 'DOCKER_DEFAULT_PLATFORM': 'linux/amd64'},
                   capture_output=True, text=True, check=True, timeout=context.timeout)
    details = inspect_episode(artifact, directory)
    context.episode_dir = directory
    details['command'] = command
    return GateResult('passed', details)
