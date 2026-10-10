"""G2: one all-ten-seat competition episode and honest VM readback.

The default runner is the pinned native match binary (`tools/gota_local.py`), which
needs no Docker. `GOTA_G2_RUNNER=docker` selects pinned `coworld run-episode` instead.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from . import GateContext, GateResult, PolicyFailure, gate_run

PROJECT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import gota_local  # the shared tools import their siblings by bare name

RUNNERS = ('native', 'docker')
TEAM_OUTCOMES = {'RedTeam': 0, 'BlueTeam': 1}


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


def runner() -> str:
    value = os.environ.get('GOTA_G2_RUNNER') or 'native'
    if value not in RUNNERS:
        raise ValueError(f'GOTA_G2_RUNNER must be one of {", ".join(RUNNERS)}, not {value!r}')
    return value


def is_native(directory: Path) -> bool:
    """Native episodes carry gota_local's job.json; coworld episodes never do."""
    return (directory / 'job.json').is_file()


def seat_log(directory: Path, slot: int) -> Path:
    """One seat's private policy log (PRINT output and BASIC errors) for either runner."""
    if is_native(directory):
        return directory / f'seat-{slot}.log'
    return directory / 'logs' / f'policy_agent_{slot}.log'


def inspect_native_episode(artifact: Path, directory: Path) -> dict:
    """Read back a gota_local match: seat identity, VM failures, activity and results."""
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    meta = json.loads((directory / 'meta.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    if not meta.get('engine') or result.get('engine') != meta['engine']:
        raise ValueError('Native episode binary engine differs from the requested engine')
    seats, heroes = meta.get('seats'), result.get('heroes')
    if (not isinstance(seats, list) or not isinstance(heroes, list) or
            len(seats) != 10 or len(heroes) != 10):
        raise ValueError('Native episode requires ten seats and ten heroes')
    for slot, (seat, hero) in enumerate(zip(seats, heroes)):
        if seat.get('sha256') != digest:
            raise ValueError(f'Artifact identity mismatch at seat {slot}')
        if hero.get('slot') != slot or hero.get('team') not in (0, 1):
            raise ValueError(f'Native episode hero record malformed at seat {slot}')
        text = seat_log(directory, slot).read_text()
        if hero.get('vm_failed') or 'BASIC error:' in text:
            raise PolicyFailure(f'BASIC failure at seat {slot}: {hero.get("vm_error")}')
    # Every seat must have loaded the same bytes inside the engine.
    sources = {hero.get('policy_source_sha1') for hero in heroes}
    if len(sources) != 1 or not all(isinstance(source, str) and source for source in sources):
        raise ValueError('Native seats did not all load one policy source')
    decisions = [hero.get('decisions') for hero in heroes]
    actions = result.get('actions')
    if any(type(count) is not int or count < 0 for count in [*decisions, actions]):
        raise ValueError('Missing native decision/action measurements')
    outcome = result.get('outcome')
    if outcome not in (*TEAM_OUTCOMES, 'draw', 'time_limit'):
        raise ValueError(f'Unknown native episode outcome {outcome!r}')
    results = {'ticks': result.get('ticks'), 'outcome': outcome,
               'seed': result.get('config', {}).get('seed'),
               'scores': [int(TEAM_OUTCOMES.get(outcome) == hero['team']) for hero in heroes],
               'total_xp': [hero.get('xp') for hero in heroes]}
    validate_results(results)
    if any(count == 0 for count in decisions):
        raise PolicyFailure('Measured inactive seats or zero decisions')
    if actions == 0:
        raise PolicyFailure('Measured zero recorded actions')
    return {'artifact_sha256': digest, 'episode_dir': str(directory), 'engine': meta['engine'],
            'scripts_active': 10, 'decisions': sum(decisions), 'seat_decisions': decisions,
            'actions': actions, 'results': results}


def inspect_episode(artifact: Path, directory: Path) -> dict:
    if is_native(directory):
        return inspect_native_episode(artifact, directory)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    for slot in range(10):
        staged = directory / 'players' / str(slot) / 'file'
        if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Artifact identity mismatch at seat {slot}')
        text = seat_log(directory, slot).read_text()
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


def docker_episode(artifact: Path, context: GateContext, directory: Path) -> dict:
    command = ['uv', 'run', '--project', str(PROJECT), '--locked', '--offline', '--python', '3.12',
               'coworld', 'run-episode', str(context.manifest.resolve()),
               *([str(artifact.resolve())] * 10), '--variant', context.variant,
               '--output-dir', str(directory.resolve())]
    subprocess.run(command, env={**os.environ, 'DOCKER_DEFAULT_PLATFORM': 'linux/amd64'},
                   capture_output=True, text=True, check=True, timeout=context.timeout)
    details = inspect_episode(artifact, directory)
    details['command'] = command
    return details


def native_episode(artifact: Path, context: GateContext, directory: Path) -> dict:
    """Run the manifest variant's seed, ticks and draft on the engine's native match binary.

    The engine is the context's resolved commit, or the engine.env pin when unset. The
    binary carries that engine's competition game_config template; a manifest setting it
    cannot reproduce is a tool error, never a silently different episode. That check reads
    the binary's own output, so it runs after the match. The match runs with
    `COGAME_LLM=off` (no LLM requests from policies) and is killed after `context.timeout`.
    """
    commit = context.engine_commit or gota_local.gota_cli.engine()['POLYWORLD_COMMIT']
    manifest = json.loads(context.manifest.read_text())
    [config] = [variant['game_config'] for variant in manifest['variants']
                if variant['id'] == context.variant]
    native_build = gota_local.binary_identity(commit)
    result = gota_local.match([artifact] * 10, seed=config['seed'], ticks=config['max_ticks'],
                              draft=config['draft_mode'], out=directory, replay=True,
                              commit=commit, timeout=context.timeout,
                              env={**os.environ, 'COGAME_LLM': 'off'},
                              executable=gota_local.binary_path(commit))
    if result['config'] != config:
        raise ValueError(f'Native match game_config differs from manifest variant {context.variant}')
    details = inspect_native_episode(artifact, directory)
    details['native_build'] = native_build
    return details


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    if context.manifest is None:
        raise ValueError('Downloaded live manifest required')
    directory = context.work_dir / 'episode'
    if directory.exists():
        raise ValueError('Episode output already exists; use a fresh work directory')
    selected = runner()
    episode = native_episode if selected == 'native' else docker_episode
    details = episode(artifact, context, directory)
    context.episode_dir = directory
    details['runner'] = selected
    return GateResult('passed', details)
