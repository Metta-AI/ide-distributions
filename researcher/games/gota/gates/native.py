"""Use the pinned engine compiler and VM counters, not lexical budget estimates."""
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import GateContext
from .limits import LEGACY, POLYWORLD_COMMIT, PRINT, SOFT

# Explicitly selected by gate_probe at the current engine pin.
NATIVE_MODE = {'drafting': True, 'draft_mode': 'OpenDraft', 'replay_mode': False}

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


def validate_measurement(result: dict, *, simulated: bool) -> None:
    """Validate probe evidence before interpreting any counter as policy behavior."""
    if not isinstance(result, dict) or type(result.get('ok')) is not bool:
        raise ValueError('native receipt requires an object and boolean ok')
    outcome = result.get('outcome')
    if outcome not in ('passed', 'failed', 'errored') or result['ok'] != (outcome == 'passed'):
        raise ValueError('native receipt outcome/ok missing or inconsistent')
    if outcome == 'errored':
        error = result.get('error')
        if (not isinstance(error, dict) or
                any(not isinstance(error.get(name), str) or not error[name]
                    for name in ('type', 'message', 'traceback'))):
            raise ValueError('native tool error requires type, message and traceback')
        return
    if 'error' in result:
        error = result['error']
        if (outcome != 'failed' or not isinstance(error, dict) or
                error.get('type') != 'BasicError' or not isinstance(error.get('message'), str) or
                not error['message']):
            raise ValueError('unrecognized native policy rejection receipt')
        return
    if result.get('mode') not in ('legacy', 'structured'):
        raise ValueError('native compile receipt requires the policy mode')
    for name in LEGACY:
        if type(result.get(name)) is not int or result[name] < 0:
            raise ValueError(f'native compile receipt requires nonnegative integer {name}')
    if not simulated:
        if outcome != 'passed':
            raise ValueError('native failed compilation requires explicit BASIC rejection')
        return
    runtime = result.get('runtime')
    if not isinstance(runtime, dict):
        raise TypeError('native simulation receipt requires runtime evidence')
    for name in (*SOFT, *PRINT, 'decisions', 'ticks', 'seed'):
        if type(runtime.get(name)) is not int or runtime[name] < 0:
            raise ValueError(f'native runtime receipt requires nonnegative integer {name}')
    for name in ('seat_decisions', 'scores', 'total_xp'):
        values = runtime.get(name)
        if (not isinstance(values, list) or len(values) != 10 or
                any(type(value) is not int or value < 0 for value in values)):
            raise ValueError(f'native runtime receipt requires ten nonnegative integers: {name}')
    if (sum(runtime['seat_decisions']) != runtime['decisions'] or
            any(score not in (0, 1) for score in runtime['scores']) or
            type(runtime.get('candidate_blue')) is not bool):
        raise ValueError('native runtime receipt has inconsistent seat counts, scores or side')
    errors = runtime.get('errors')
    if not isinstance(errors, list) or any(
            not isinstance(error, dict) or type(error.get('slot')) is not int or
            not 0 <= error['slot'] < 10 or not isinstance(error.get('error'), str) or not error['error']
            for error in errors):
        raise ValueError('native runtime receipt requires explicit VM error records')
    if result['ok'] != (not errors and runtime['decisions'] > 0):
        raise ValueError('native runtime outcome disagrees with measured activity or VM errors')


def measure(artifact: Path, context: GateContext, seed: int | None = None,
            opponent: Path | None = None, blue: bool = False, *, identity: dict | None = None) -> dict:
    batched = identity is not None
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
    identity = validate(binary, 'gate_probe', commit) if identity is None else validate_executable(binary, identity)
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
    validate_measurement(result, simulated=seed is not None)
    after = validate_executable(binary, identity)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest or (opponent and hashlib.sha256(opponent.read_bytes()).hexdigest() != opponent_digest):
        raise ValueError('policy changed during native measurement')
    if identity != after or result.get('engine') != commit or result.get('bassy') != identity['inputs']['dependencies']['bassy']['commit']:
        raise ValueError('native gate identity mismatch')
    if not batched and validate(binary, 'gate_probe', commit) != identity:
        raise ValueError('native inputs changed during measurement')
    result['native_build'] = identity
    result['artifact_sha256'] = digest
    result['polyworld_commit'] = commit
    if seed is not None:
        result['native_mode'] = dict(NATIVE_MODE)
    report.write_text(json.dumps(result, indent=2) + '\n')
    context.measurements[key] = result
    return result


def measure_many(artifact: Path, context: GateContext, matches: list[tuple[int, Path | None, bool]]) -> list[dict]:
    """Bound independent probes; retain input validation at both batch boundaries."""
    from games.gota.tools.native_build import validate

    if context.jobs < 1:
        raise ValueError('native jobs must be positive')
    commit = context.engine_commit or POLYWORLD_COMMIT
    # G1/G3 compilation establishes the binary before concurrent simulation starts.
    if context.native_binary is None:
        measure(artifact, context)
    identity = validate(context.native_binary, 'gate_probe', commit)
    unique = list(dict.fromkeys(matches))
    policies = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in {artifact, *(opponent for _, opponent, _ in matches if opponent)}}
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    quota = Path('/sys/fs/cgroup/cpu.max')
    if quota.is_file():
        allowance, period = quota.read_text().split()
        if allowance != 'max':
            cpus = min(cpus, max(1, int(allowance) // int(period)))
    else:
        for group in ('cpu', 'cpu,cpuacct'):
            quota_v1 = Path('/sys/fs/cgroup') / group / 'cpu.cfs_quota_us'
            if quota_v1.is_file():
                allowance = int(quota_v1.read_text())
                if allowance > 0:
                    period = int(quota_v1.with_name('cpu.cfs_period_us').read_text())
                    cpus = min(cpus, max(1, allowance // period))
    workers = min(context.jobs, cpus, 4)
    def run(match):
        seed, opponent, blue = match
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != expected for path, expected in policies.items()):
            raise ValueError('policy changed during measurement batch')
        return measure(artifact, context, seed, opponent, blue, identity=identity)
    pool = ThreadPoolExecutor(max_workers=workers)
    results = {}
    try:
        for match, result in zip(unique, pool.map(run, unique)):
            results[match] = result
            if result['outcome'] == 'errored' or 'error' in result:
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != expected for path, expected in policies.items()):
            raise ValueError('policy changed during measurement batch')
        if validate(context.native_binary, 'gate_probe', commit) != identity:
            raise ValueError('native inputs changed during measurement batch')
    return [results[match] for match in matches if match in results]


def validate_executable(binary: Path, receipt: dict) -> dict:
    """Check executable bytes and receipt between complete input validation boundaries."""
    from games.gota.tools.native_build import digest, receipt_path
    if (json.loads(receipt_path(binary).read_text()) != receipt or
            digest(binary) != receipt['binary_sha256'] or not os.access(binary, os.X_OK)):
        raise ValueError(f'native executable or receipt changed: {binary}')
    return receipt
