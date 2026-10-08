"""Freeze committed BASIC inputs and run existing gates without a model compiler."""
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from games.gota.tools.native_build import digest

ENGINE_FIELDS = ('commit', 'coworld_version', 'source_url', 'manifest_sha256')


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def committed_files(candidate, paths):
    """Check exact HEAD bytes; unrelated worker changes are not build inputs."""
    repo = Path(subprocess.check_output(
        ['git', '-C', str(candidate), 'rev-parse', '--show-toplevel'], text=True).strip())
    head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    hashes = {}
    for path in paths:
        path = path.resolve()
        if not path.is_relative_to(candidate.resolve()):
            raise ValueError(f'prebuilt input must be inside candidate: {path}')
        relative = path.relative_to(repo).as_posix()
        blob = subprocess.run(['git', '-C', str(repo), 'show', f'{head}:{relative}'],
                              check=False, capture_output=True)
        if blob.returncode or blob.stdout != path.read_bytes():
            raise ValueError(f'prebuilt input must be committed unchanged: {path}')
        hashes[relative] = digest(path)
    return head, hashes


def validate_source(candidate, artifact, identity):
    sidecar = artifact.with_suffix('.provenance.json')
    head, hashes = committed_files(candidate, [artifact, sidecar])
    provenance = json.loads(sidecar.read_text())
    if provenance.get('schema') != 'gota-prebuilt/1':
        raise ValueError('prebuilt provenance schema must be gota-prebuilt/1')
    if provenance['artifact_sha256'] != digest(artifact):
        raise ValueError('prebuilt artifact hash differs from provenance')
    if provenance['engine'] != {key: identity[key] for key in ENGINE_FIELDS}:
        raise ValueError('prebuilt engine differs from resolved engine; regenerate provenance')
    source = (sidecar.parent / provenance['input']['path']).resolve()
    _, source_hashes = committed_files(candidate, [source])
    hashes.update(source_hashes)
    if digest(source) != provenance['input']['sha256']:
        raise ValueError('prebuilt input hash differs from provenance')
    transform = provenance['transform']
    original = source.read_bytes()
    if transform['name'] == 'identity':
        expected = original
    elif transform['name'] == 'make_v13_live.py':
        from games.gota.tools import make_v13_live
        if transform['sha256'] != digest(Path(make_v13_live.__file__)):
            raise ValueError('prebuilt transform tool hash differs from installed tool')
        expected = make_v13_live.make_live(original.decode()).encode()
    else:
        raise ValueError(f'unsupported prebuilt transform: {transform["name"]}')
    if expected != artifact.read_bytes():
        raise ValueError('prebuilt artifact does not reproduce from declared input and transform')
    return head, hashes, provenance


def build(candidate, out, artifact, identity):
    from games.gota.gates import GateContext, GateResult, g1_lint, g2_episode, g3_budget, g4_screen
    from games.gota.tools.engine_identity import provision
    from games.gota.tools.native_build import TOOLS, validate

    artifact = artifact.resolve()
    head, hashes, provenance = validate_source(candidate, artifact, identity)
    identity, binary = provision(identity)
    native = {'gate_probe': validate(binary, 'gate_probe', identity['commit']),
              'gota_match': validate(TOOLS / 'bin' / f'gota_match-{identity["commit"][:8]}',
                                     'gota_match', identity['commit'])}
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.gota-build-', dir=out.parent))
    frozen = scratch / 'policy.bas'
    frozen.write_bytes(artifact.read_bytes())
    dump(scratch / 'prebuilt.provenance.json', provenance)
    manifest = scratch / 'coworld_manifest.json'
    dump(manifest, identity.pop('manifest'))
    context = GateContext(scratch / 'verify', baseline=frozen, manifest=manifest,
                          native_binary=binary, engine_commit=identity['commit'])
    context.work_dir.mkdir()
    dump(scratch / 'native_provenance.json', native)
    gates = {}
    error = None
    for name, module in zip(('G1', 'G2', 'G3', 'G4'), (g1_lint, g2_episode, g3_budget, g4_screen)):
        try:
            result = module.run(frozen, context)
            for measurement in context.measurements.values():
                if measurement.get('native_build') != native['gate_probe']:
                    raise ValueError('gate used a different native build from verification provenance')
            if digest(frozen) != provenance['artifact_sha256']:
                raise ValueError('frozen prebuilt artifact changed during gates')
        except Exception as fault:  # noqa: BLE001 - retain arbitrary tool faults and stop.
            result = GateResult.errored(fault)
        gates[name] = {'outcome': result.outcome, 'passed': result.ok,
                       'summary': result.details, 'advisory': name == 'G4'}
        if result.outcome == 'errored':
            error = result.details['error']
            break
        if not result.ok and name in ('G1', 'G2'):
            break
    gates['G5'] = {'applicable': False, 'passed': False, 'advisory': True,
                   'summary': 'not applicable: prebuilt'}
    status = 'errored' if error else ('passed' if all(
        gates.get(name, {}).get('passed') is True for name in ('G1', 'G2', 'G3')) else 'failed')
    warnings = [{'gate': name, 'summary': receipt['summary']} for name, receipt in gates.items()
                if receipt.get('advisory') and receipt.get('outcome') == 'failed']
    # Recheck the committed closure after gates; never publish a stale source receipt.
    try:
        current_head, current_hashes = committed_files(candidate, [artifact, artifact.with_suffix('.provenance.json'),
                                                                  artifact.parent / provenance['input']['path']])
        if (current_head, current_hashes) != (head, hashes):
            raise ValueError('prebuilt committed inputs changed during gates')
        if (validate(binary, 'gate_probe', identity['commit']) != native['gate_probe'] or
                validate(TOOLS / 'bin' / f'gota_match-{identity["commit"][:8]}',
                         'gota_match', identity['commit']) != native['gota_match']):
            raise ValueError('native build changed during prebuilt gates')
    except Exception as fault:  # noqa: BLE001 - retain final provenance faults with gate evidence.
        result = GateResult.errored(fault)
        gates['verification'] = {'outcome': result.outcome, 'passed': False, 'summary': result.details}
        status, error = 'errored', result.details['error']
    driver = {str(path): digest(path) for path in (Path(__file__), Path(__file__).with_name('build.py'))}
    version = {'build_id': out.name, 'backend': 'gota', 'status': status, 'gates': gates,
               'artifact': 'policy.bas', 'policy_sha256': digest(frozen),
               'source_commit': head, 'source_path': next(iter(hashes)), 'source_hashes': hashes,
               'strategy_sha256': provenance['artifact_sha256'], 'prebuilt': provenance,
               'created': datetime.now(UTC).isoformat(), 'engine': identity, 'native_builds': native,
               'compiler': {'mode': 'prebuilt', 'prebuilt_provenance': provenance, 'installed_source_hashes': driver},
               'driver': {'candidate_commit': head, 'installed_commit': None,
                          'installed_source_sha256': hashlib.sha256(json.dumps(driver, sort_keys=True).encode()).hexdigest()}}
    report = {'build_id': out.name, 'status': status, 'gates': gates,
              'error': error, 'warnings': warnings}
    dump(scratch / 'gates.json', gates)
    dump(scratch / 'report.json', report)
    lines = [f'# Build {out.name}', '', f'Status: **{status}**. Mode: prebuilt.', '',
             '| Gate | Outcome | Evidence |', '| --- | --- | --- |']
    lines.extend(f'| {name} | {receipt.get("outcome", "not executed")} | {receipt["summary"]} |'
                 for name, receipt in gates.items())
    if warnings:
        lines.extend(['', '## Warnings', '', *(f'- {row["gate"]}: {row["summary"]}' for row in warnings)])
    if error:
        lines.extend(['', '## Tool error', '', '```json', json.dumps(error, indent=2), '```'])
    (scratch / 'report.md').write_text('\n'.join(lines) + '\n')
    dump(scratch / 'version.json', version)
    scratch.rename(out)
    return {'ok': status == 'passed', 'build_id': out.name, 'status': status,
            'error': error, 'build': str(out), 'gates': gates, 'artifact': str(out / 'policy.bas')}
