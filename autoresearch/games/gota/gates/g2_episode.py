"""G2: pinned coworld all-ten-seat competition episode and honest VM readback."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from . import GateContext, GateResult

PROJECT = Path(__file__).resolve().parents[3]


def inspect_episode(artifact: Path, directory: Path) -> dict:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    for slot in range(10):
        staged = directory / 'players' / str(slot) / 'file'
        if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Artifact identity mismatch at seat {slot}')
        text = (directory / 'logs' / f'policy_agent_{slot}.log').read_text()
        if 'BASIC error:' in text or 'Policy loading failed' in text:
            raise ValueError(f'BASIC failure at seat {slot}')
        if f'Player slot {slot} completed.' not in text:
            raise ValueError(f'Missing seat completion: {slot}')
    game_log = (directory / 'logs/game.stdout.log').read_text()
    scripts = re.search(r'scripts: (\d+)/10 active, (\d+) decisions', game_log)
    actions = re.search(r'replay saved: .*\((\d+) actions\)', game_log)
    if scripts is None or int(scripts[1]) != 10 or int(scripts[2]) == 0:
        raise ValueError('Missing active ten-seat VM execution')
    if actions is None or int(actions[1]) == 0:
        raise ValueError('Missing nonzero recorded actions')
    results = json.loads((directory / 'results.json').read_text())
    if (len(results.get('scores', [])) != 10 or len(results.get('total_xp', [])) != 10
            or results.get('ticks', 0) <= 0):
        raise ValueError('Incomplete ten-seat result')
    return {'artifact_sha256': digest, 'episode_dir': str(directory),
            'scripts_active': 10, 'decisions': int(scripts[2]), 'actions': int(actions[1]),
            'results': results}


def run(artifact: Path, context: GateContext) -> GateResult:
    try:
        if context.manifest is None:
            return GateResult(False, {'error': 'Downloaded live manifest required'})
        directory = context.work_dir / 'episode'
        if directory.exists():
            raise ValueError('Episode output already exists; use a fresh work directory')
        command = ['uv', 'run', '--project', str(PROJECT), '--locked', '--python', '3.12',
                   '--with', 'coworld==0.1.57', '--with', 'softmax-cli==0.26.38',
                   'coworld', 'run-episode', str(context.manifest.resolve()),
                   *([str(artifact.resolve())] * 10), '--variant', context.variant,
                   '--output-dir', str(directory.resolve())]
        subprocess.run(command, env={**os.environ, 'DOCKER_DEFAULT_PLATFORM': 'linux/amd64'},
                       capture_output=True, text=True, check=True, timeout=context.timeout)
        details = inspect_episode(artifact, directory)
        context.episode_dir = directory
        details['command'] = command
        return GateResult(True, details)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return GateResult(False, {'error': str(error)})
