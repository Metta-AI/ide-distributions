#!/usr/bin/env python3
"""Compile a committed candidate using the installed qualified GotA compiler."""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'games/gota/compiler/tools'


def validate_manifest(path):
    from games.gota.gates.limits import POLYWORLD_COMMIT
    data = json.loads(path.read_text())
    url = data['game']['runnable']['source_url']
    if f'/tree/{POLYWORLD_COMMIT}/' not in url:
        raise ValueError(f'manifest source_url must pin Polyworld {POLYWORLD_COMMIT}')


def build(candidate, out, manifest, *, timeout=900):
    """Output is new and immutable; failure retains evidence without an upload envelope."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TOOLS))
    validate_manifest(manifest)
    source = candidate / 'STRATEGY.md'
    repo = subprocess.check_output(['git', '-C', str(candidate), 'rev-parse', '--show-toplevel'], text=True).strip()
    if out.exists():
        raise ValueError(f'output already exists (immutable): {out}')
    os.environ['GOTA_CANDIDATE_REPO'] = repo
    import pw_cli
    python = os.environ.get('GOTA_HERMES_PYTHON')
    if not python:
        raise pw_cli.EnvironmentMissing('Hermes Python is not configured', 'set GOTA_HERMES_PYTHON to the installed Hermes Python')
    import pw_strategy
    import strategy_build as compiler
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.gota-build-', dir=out.parent))
    home = scratch / 'hermes-home'
    (home / 'bin').mkdir(parents=True)
    scanner_source = os.environ.get('GOTA_TIRITH_BIN')
    if not scanner_source:
        raise pw_cli.EnvironmentMissing('tirith is not provisioned', 'set GOTA_TIRITH_BIN to the officially installed verified executable')
    shutil.copy2(scanner_source, home / 'bin/tirith')
    environment = os.environ.copy()
    environment['HERMES_HOME'] = str(home)
    for name in ('HERMES_PROFILE', 'HERMES_PROFILE_NAME', 'TIRITH_BIN', 'TIRITH_ENABLED', 'TIRITH_FAIL_OPEN'):
        environment.pop(name, None)
    environment['TIRITH_BIN'] = str(home / 'bin/tirith')
    ready = subprocess.run([python, '-c',
        ('from tools.tirith_security import ensure_installed; p = ensure_installed(); '
        'assert p, "tirith installation failed"; print(p)')],
        env=environment, capture_output=True, text=True, check=True, timeout=120)
    scanner = Path(ready.stdout.strip().splitlines()[-1])
    if scanner.resolve() != (home / 'bin/tirith').resolve() or not os.access(scanner, os.X_OK):
        raise ValueError('Hermes security preflight did not return an installed tirith executable')
    os.environ['GOTA_COMPILER_HERMES_HOME'] = str(home)
    compiler.BUILDS = scratch / 'compiled'
    compiler.STAGING = scratch / 'staging'
    args = pw_strategy.parser().parse_args(['compile', '--source', str(source), '--agent', 'hermes',
                                           '--manifest', str(manifest), '--agent-timeout', str(timeout)])
    report = pw_cli.Report('gota_build', json_mode=True)
    with contextlib.redirect_stdout(sys.stderr):
        result = pw_strategy.run(args, report)
    target = Path(result['build'])
    version = compiler.read(target / 'version.json')
    version['compiler']['security_scanner_sha256'] = hashlib.sha256(scanner.read_bytes()).hexdigest()
    version['artifact'] = 'policy.bas'
    version['strategy_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    installed = {name: value for name, value in version['source_hashes'].items() if Path(name).is_absolute()}
    version['compiler']['installed_source_hashes'] = installed
    version['driver'] = {'candidate_commit': version['source_commit'],
                         'installed_source_sha256': hashlib.sha256(json.dumps(installed, sort_keys=True).encode()).hexdigest(),
                         'installed_commit': None if installed else version['source_commit']}
    compiler.dump(target / 'version.json', version)
    target.rename(out)
    shutil.rmtree(scratch)
    return {'ok': version['status'] == 'passed', 'build_id': version['build_id'],
            'gates': version['gates'], 'artifact': str(out / 'policy.bas')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--manifest', type=Path, default=os.environ.get('GOTA_MANIFEST'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        if args.manifest is None:
            raise ValueError('supply --manifest PATH or GOTA_MANIFEST (downloaded Coworld manifest)')
        result = build(args.candidate.resolve(), args.out.resolve(), Path(args.manifest).resolve())
    except SystemExit as error:
        if isinstance(error.code, int):
            raise
        print(json.dumps({'ok': False, 'error': str(error)}))
        return getattr(error, 'exit_code', 2)
    except (OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 3
    except (ValueError, KeyError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 2
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
