"""Resolve one live GotA identity; offline operation is always explicit."""
import json
import os
import re
import subprocess
from pathlib import Path

from games.gota.tools.native_build import fingerprint


def from_manifest(manifest, version, *, mode):
    try:
        url = manifest['game']['runnable']['source_url']
    except (TypeError, KeyError) as error:
        raise ValueError('manifest must contain game.runnable.source_url') from error
    if not isinstance(url, str):
        raise ValueError('manifest source_url must be a string')  # noqa: TRY004 - malformed JSON is a value error
    match = re.fullmatch(r'https://github.com/Metta-AI/polyworld/tree/([0-9a-f]{40})/examples/gods_of_the_arena/?', url)
    if not match:
        raise ValueError('manifest source_url must pin a complete Polyworld GotA commit')
    if not isinstance(version, str) or not version.strip():
        raise ValueError('Coworld version is missing')
    return {'schema': 'gota-engine/1', 'mode': mode, 'commit': match[1],
            'tag': 'gota-' + version, 'coworld_version': version,
            'source_url': url, 'manifest_sha256': fingerprint(manifest)}


def resolve(path: Path | None = None, *, offline=False):
    manifest = json.loads(path.read_text()) if path else None
    if offline:
        if manifest is None:
            raise ValueError('--offline requires --manifest PATH or GOTA_MANIFEST')
        from games.gota.tools.gota_cli import engine
        pin = engine()
        identity = from_manifest(manifest, pin['COWORLD_VERSION'], mode='offline')
        if identity['commit'] != pin['POLYWORLD_COMMIT']:
            raise ValueError('offline manifest differs from historical engine pin')
        identity['manifest'] = manifest
        return identity
    cli = os.environ.get('AUTORESEARCH_CLI')
    if not cli:
        raise OSError('set AUTORESEARCH_CLI to resolve the live GotA engine; use --offline only for historical builds')
    result = subprocess.run([cli, 'softmax', 'game-version'], capture_output=True,
                            text=True, check=True, timeout=60)
    envelope = json.loads(result.stdout)
    if not isinstance(envelope, dict) or envelope.get('ok') is not True or not isinstance(envelope.get('data'), dict):
        raise ValueError('shared game-version failed or returned an invalid envelope')
    live = envelope['data']
    from autoresearch.softmax_tool.platform_cli import PlatformCli
    transport = PlatformCli(os.environ)
    league = transport.run_json('coworld', ['leagues', live['league_id']])
    game = league['game']
    coworld_id = game.get('canonical_coworld_id') or game.get('coworld_id')
    if not coworld_id:
        raise ValueError('live league has no canonical Coworld')
    row = transport.run_json('coworld', ['show', coworld_id])
    if row['version'] != live['game_version']:
        raise ValueError('Coworld changed during engine resolution; retry with current manifest')
    identity = from_manifest(row['manifest'], live['game_version'], mode='live')
    if manifest is not None and identity['manifest_sha256'] != fingerprint(manifest):
        raise ValueError('downloaded manifest differs from live Coworld; download the current manifest')
    identity['coworld_id'] = coworld_id
    identity['manifest'] = row['manifest']
    return identity


HOST_CONTRACT = json.loads(Path(__file__).with_name('host_contract.json').read_text())


def check_surface(surface, identity):
    if surface['engine'] != {'polyworld_commit': identity['commit'],
                             'bassy_commit': identity['bassy_commit']}:
        raise ValueError('native host surface engine identity mismatch')
    contract = {key: surface[key] for key in ('host_data', 'host_functions', 'records', 'limits')}
    contract['structured_storage'] = surface['baseline']['structured_storage']
    if fingerprint(contract) != HOST_CONTRACT['surface_sha256']:
        raise ValueError('unsupported live GotA host contract: review scalar/record/storage/limit changes before compiling')
    return fingerprint(contract)


def provision(identity):
    """Prepare exact pins, validate receipts and reject changed compiler contracts."""
    from games.gota.tools.native_build import build, prepare, validate
    prepare(identity['commit'])
    receipts = {}
    binaries = {}
    for target in ('gate_probe', 'gota_match'):
        binary = build(target, identity['commit'])
        receipts[target] = validate(binary, target, identity['commit'])
        binaries[target] = binary
    identity = dict(identity)
    identity['bassy_commit'] = receipts['gate_probe']['inputs']['dependencies']['bassy']['commit']
    identity['native_fingerprints'] = {target: receipt['fingerprint'] for target, receipt in receipts.items()}
    result = subprocess.run([str(binaries['gota_match']), 'host'], capture_output=True,
                            text=True, check=True, timeout=60)
    identity['host_contract_sha256'] = check_surface(json.loads(result.stdout), identity)
    if validate(binaries['gota_match'], 'gota_match', identity['commit']) != receipts['gota_match']:
        raise ValueError('native host identity changed during contract check')
    return identity, binaries['gate_probe']
