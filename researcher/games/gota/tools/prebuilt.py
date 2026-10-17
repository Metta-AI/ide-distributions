"""Freeze committed BASIC inputs and run existing gates without a model compiler."""
import hashlib
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from games.gota.tools.build_input import InputRejected
from games.gota.tools.native_build import digest, fingerprint

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
            raise InputRejected(f'prebuilt input must be inside candidate: {path}', path=path,
                                action='Place the input inside the candidate directory and commit it.')
        relative = path.relative_to(repo).as_posix()
        if not path.is_file():
            raise InputRejected(f'prebuilt input is missing: {path}', path=path,
                                action='Restore or create this exact required input from known source and commit it. '
                                       'For policy.bas the sidecar is policy.provenance.json.')
        tracked = subprocess.check_output(
            ['git', '-C', str(repo), 'ls-tree', '-z', head, '--', relative])
        if not tracked:
            raise InputRejected(f'prebuilt input must be committed unchanged: {path}', path=path,
                                action='Commit the candidate input before building.')
        blob = subprocess.check_output(['git', '-C', str(repo), 'show', f'{head}:{relative}'])
        if blob != path.read_bytes():
            raise InputRejected(f'prebuilt input must be committed unchanged: {path}', path=path,
                                action='Review and commit the intended input bytes before building.')
        hashes[relative] = digest(path)
    return head, hashes


def validate_source(candidate, artifact, identity):
    sidecar = artifact.with_suffix('.provenance.json')
    correction = ('Correct the declared provenance to match the actual committed input and resolved '
                  'engine; then commit it.')
    head, hashes = committed_files(candidate, [artifact, sidecar])
    provenance = json.loads(sidecar.read_text())
    if provenance.get('schema') != 'gota-prebuilt/1':
        raise InputRejected('prebuilt provenance schema must be gota-prebuilt/1', path=sidecar,
                            action=correction)
    if provenance['artifact_sha256'] != digest(artifact):
        raise InputRejected('prebuilt artifact hash differs from provenance', path=sidecar,
                            action=correction)
    if provenance['engine'] != {key: identity[key] for key in ENGINE_FIELDS}:
        raise InputRejected('prebuilt engine differs from resolved engine; regenerate provenance', path=sidecar,
                            action=correction)
    source = (sidecar.parent / provenance['input']['path']).resolve()
    _, source_hashes = committed_files(candidate, [source])
    hashes.update(source_hashes)
    if digest(source) != provenance['input']['sha256']:
        raise InputRejected('prebuilt input hash differs from provenance', path=sidecar,
                            action=correction)
    transform = provenance['transform']
    original = source.read_bytes()
    if transform['name'] == 'identity':
        expected = original
    elif transform['name'] == 'make_v13_live.py':
        from games.gota.tools import make_v13_live
        if transform['sha256'] != digest(Path(make_v13_live.__file__)):
            raise InputRejected('prebuilt transform tool hash differs from installed tool', path=sidecar,
                            action=correction)
        expected = make_v13_live.make_live(original.decode()).encode()
    else:
        raise InputRejected(f'unsupported prebuilt transform: {transform["name"]}', path=sidecar,
                            action=correction)
    if expected != artifact.read_bytes():
        raise InputRejected('prebuilt artifact does not reproduce from declared input and transform', path=sidecar,
                            action=correction)
    return head, hashes, provenance


def build(candidate, out, artifact, identity, *, started=None):
    from games.gota.gates import (
        GateContext,
        GateResult,
        g1_lint,
        g2_episode,
        g3_budget,
        g4_screen,
        retain_episode_locators,
    )
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
        gate_started = time.monotonic()
        try:
            if fingerprint(json.loads(manifest.read_text())) != identity['manifest_sha256']:
                raise ValueError('manifest changed after engine resolution')
            result = module.run(frozen, context)
            for measurement in context.measurements.values():
                if measurement.get('native_build') != native['gate_probe']:
                    raise ValueError('gate used a different native build from verification provenance')
            if digest(frozen) != provenance['artifact_sha256']:
                raise ValueError('frozen prebuilt artifact changed during gates')
            if fingerprint(json.loads(manifest.read_text())) != identity['manifest_sha256']:
                raise ValueError('manifest changed after engine resolution')
        except Exception as fault:  # noqa: BLE001 - retain arbitrary tool faults and stop.
            result = GateResult.errored(fault)
        gates[name] = {'outcome': result.outcome, 'passed': result.ok,
                       'summary': result.details, 'advisory': name == 'G4',
                       'seconds': round(time.monotonic() - gate_started, 3)}
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
    retain_episode_locators(gates, scratch)
    version = {'build_id': out.name, 'backend': 'gota', 'status': status, 'gates': gates,
               'artifact': 'policy.bas', 'policy_sha256': digest(frozen),
               'source_commit': head, 'source_path': next(iter(hashes)), 'source_hashes': hashes,
               'strategy_sha256': provenance['artifact_sha256'], 'prebuilt': provenance,
               'created': datetime.now(UTC).isoformat(), 'started': started,
               'finished': datetime.now(UTC).isoformat(timespec='seconds'), 'engine': identity, 'native_builds': native,
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
