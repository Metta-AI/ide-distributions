"""Use the pinned engine compiler and VM counters, not lexical budget estimates."""
import hashlib
import json
import subprocess
from pathlib import Path

from . import GateContext
from .limits import POLYWORLD_COMMIT

# Explicitly selected by gate_probe at the current engine pin.
NATIVE_MODE = {'drafting': True, 'draft_mode': 'OpenDraft', 'replay_mode': False}

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def measure(artifact: Path, context: GateContext, seed: int | None = None,
            opponent: Path | None = None, blue: bool = False) -> dict:
    commit = context.engine_commit or POLYWORLD_COMMIT
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    opponent_digest = hashlib.sha256(opponent.read_bytes()).hexdigest() if opponent else None
    key = (digest, seed, context.max_ticks, opponent_digest, blue)
    context.work_dir.mkdir(parents=True, exist_ok=True)
    binary = context.native_binary
    if binary is None:
        build = subprocess.run(['sh', str(TOOLS / 'build_nim.sh'), commit,
                                'gate_probe'], capture_output=True, text=True,
                               check=True, timeout=context.timeout)
        binary = Path(build.stdout.strip().splitlines()[-1])
        context.native_binary = binary
    from games.gota.tools.native_build import validate
    identity = validate(binary, 'gate_probe', commit)
    if key in context.measurements:
        if context.measurements[key]['native_build'] != identity:
            raise ValueError('native identity changed during verification')
        return context.measurements[key]
    report = context.work_dir / (hashlib.sha256(repr(key).encode()).hexdigest() + '.json')
    command = [str(binary), 'compile' if seed is None else 'simulate',
               str(artifact.resolve()), str(report.resolve())]
    if seed is not None:
        command += [str(seed), str(context.max_ticks), str((opponent or artifact).resolve()),
                    'blue' if blue else 'red']
    subprocess.run(command, capture_output=True, text=True, check=True, timeout=context.timeout)
    result = json.loads(report.read_text())
    after = validate(binary, 'gate_probe', commit)
    if identity != after or result.get('engine') != commit or result.get('bassy') != identity['inputs']['dependencies']['bassy']['commit']:
        raise ValueError('native gate identity mismatch')
    result['native_build'] = identity
    result['artifact_sha256'] = digest
    result['polyworld_commit'] = commit
    if seed is not None:
        result['native_mode'] = dict(NATIVE_MODE)
    report.write_text(json.dumps(result, indent=2) + '\n')
    context.measurements[key] = result
    return result
