#!/usr/bin/env python3
"""Build a committed semantic candidate or gate an exact prebuilt BASIC artifact."""
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
DEFAULT_AGENT_TIMEOUT_S = 1200
DEFAULT_AGENT_BUDGET_S = 1800


def validate_manifest(path, *, offline=False):
    from games.gota.tools.engine_identity import resolve
    return resolve(path, offline=offline)


def agent_limit(value, flag, environment, default):
    """Resolve a positive limit; an empty host setting leaves the default in place."""
    setting = flag if value is not None else environment
    if value is None:
        value = os.environ.get(environment) or default
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise TypeError
        seconds = int(value)
        if seconds < 1:
            raise ValueError
        return seconds
    except (TypeError, ValueError):
        raise ValueError(f'{setting} must be a positive integer number of seconds') from None


def build(candidate, out, manifest=None, *, timeout=None, budget=None, offline=False, prebuilt=None):
    """Output is new and immutable; failure retains evidence without an upload envelope."""
    if prebuilt is None:
        timeout = agent_limit(timeout, '--agent-timeout', 'GOTA_AGENT_TIMEOUT_S',
                              DEFAULT_AGENT_TIMEOUT_S)
        budget = agent_limit(budget, '--agent-budget', 'GOTA_AGENT_BUDGET_S',
                             DEFAULT_AGENT_BUDGET_S)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(TOOLS))
    identity = validate_manifest(manifest, offline=offline)
    if out.exists():
        raise ValueError(f'output already exists (immutable): {out}')
    if prebuilt is not None:
        from games.gota.tools import prebuilt as existing
        return existing.build(candidate, out, prebuilt, identity)
    source = candidate / 'STRATEGY.md'
    repo = subprocess.check_output(['git', '-C', str(candidate), 'rev-parse', '--show-toplevel'], text=True).strip()
    os.environ['GOTA_CANDIDATE_REPO'] = repo
    import pw_cli
    python = os.environ.get('GOTA_HERMES_PYTHON')
    if not python:
        raise pw_cli.EnvironmentMissing('Hermes Python is not configured', 'set GOTA_HERMES_PYTHON to the installed Hermes Python')

    from games.gota.tools.engine_identity import provision
    identity, _ = provision(identity)
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='.gota-build-', dir=out.parent))
    os.environ['GOTA_ANALYSIS_DIR'] = str(scratch / 'analysis')
    import pw_strategy
    import strategy_build as compiler

    resolved_manifest = identity.pop('manifest')
    manifest = scratch / 'manifest.json'
    manifest.write_text(json.dumps(resolved_manifest, indent=2) + '\n')
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
    if not ready.stdout.strip():
        raise pw_cli.EnvironmentMissing('Hermes security readiness returned empty output', 'verify GOTA_HERMES_PYTHON and GOTA_TIRITH_BIN')
    scanner = Path(ready.stdout.strip().splitlines()[-1])
    if scanner.resolve() != (home / 'bin/tirith').resolve() or not os.access(scanner, os.X_OK):
        raise ValueError('Hermes security preflight did not return an installed tirith executable')
    os.environ['GOTA_COMPILER_HERMES_HOME'] = str(home)
    compiler.BUILDS = scratch / 'compiled'
    compiler.STAGING = scratch / 'staging'
    args = pw_strategy.parser().parse_args(['compile', '--source', str(source), '--agent', 'hermes',
                                           '--manifest', str(manifest), '--agent-timeout', str(timeout),
                                           '--agent-budget', str(budget)])
    report = pw_cli.Report('gota_build', json_mode=True)
    with contextlib.redirect_stdout(sys.stderr):
        result = pw_strategy.run(args, report, engine_identity=identity)
    target = Path(result['build'])
    analysis = scratch / 'analysis'
    if analysis.exists():
        analysis.rename(target / 'analysis')
    version = compiler.read(target / 'version.json')
    version['engine'] = identity
    version['compiler']['security_scanner_sha256'] = hashlib.sha256(scanner.read_bytes()).hexdigest()
    version['artifact'] = 'policy.bas'
    version['strategy_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    installed = {name: value for name, value in version['source_hashes'].items() if Path(name).is_absolute()}
    version['compiler']['installed_source_hashes'] = installed
    version['driver'] = {'candidate_commit': version['source_commit'],
                         'installed_source_sha256': hashlib.sha256(json.dumps(installed, sort_keys=True).encode()).hexdigest(),
                         'installed_commit': None if installed else version['source_commit']}
    compiler.dump(target / 'version.json', version)
    shutil.copyfile(manifest, target / 'coworld_manifest.json')
    target.rename(out)
    shutil.rmtree(scratch)
    return {'ok': version['status'] == 'passed', 'build_id': version['build_id'],
            'status': version['status'], 'error': result.get('error'), 'build': str(out),
            'gates': version['gates'], 'artifact': str(out / 'policy.bas')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--prebuilt', type=Path, help='committed BASIC artifact with adjacent .provenance.json; no model compiler')
    parser.add_argument('--manifest', type=Path, default=None)
    parser.add_argument('--agent-timeout', metavar='SECONDS',
                        help=f'per-generation compiler timeout; overrides GOTA_AGENT_TIMEOUT_S (default: {DEFAULT_AGENT_TIMEOUT_S}s)')
    parser.add_argument('--agent-budget', metavar='SECONDS',
                        help=f'cumulative generation budget; overrides GOTA_AGENT_BUDGET_S (default: {DEFAULT_AGENT_BUDGET_S}s)')
    parser.add_argument('--offline', action='store_true', help='explicit historical pinned engine; no live lookup')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        manifest = args.manifest or (os.environ.get('GOTA_MANIFEST') if args.offline else None)
        options = {'offline': args.offline, 'timeout': args.agent_timeout, 'budget': args.agent_budget}
        if args.prebuilt is not None:
            options['prebuilt'] = args.prebuilt.resolve()
        result = build(args.candidate.resolve(), args.out.resolve(), Path(manifest).resolve() if manifest else None, **options)
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and isinstance(error.code, int):
            raise
        from games.gota.gates import exception_summary
        result = {'ok': False, 'status': 'errored', 'build': str(args.out.resolve()),
                  'error': exception_summary(error), 'gates': {}}
        # Never overwrite an existing immutable build, even if the caller reused --out.
        if not args.out.exists():
            args.out.mkdir(parents=True)
            for name in ('report.json', 'version.json'):
                (args.out / name).write_text(json.dumps(result, indent=2) + '\n')
            (args.out / 'report.md').write_text(
                '# Build error\n\nStatus: **errored**.\n\n```json\n' +
                json.dumps(result['error'], indent=2) + '\n```\n')
        print(json.dumps(result))
        if isinstance(error, (ValueError, KeyError, RuntimeError)):
            return 2
        return getattr(error, 'exit_code', 3)
    print(json.dumps(result))
    return 0 if result['ok'] else 3 if result.get('status') == 'errored' else 1


if __name__ == '__main__':
    raise SystemExit(main())
