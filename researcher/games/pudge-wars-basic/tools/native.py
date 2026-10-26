#!/usr/bin/env python3
"""Fetch, build and locate the Pudge Wars Basic headless binary (pudge_headless).

The binary is the game's own simulator, `examples/pudge_wars/pw.nim` compiled with
`-d:release -d:headless`, at the commit the league runs (tools/engine.env). It runs local
episodes (`--bot`), re-simulates replays (`--replay --duellog`) and scores counterfactual
branches (`--branch`). It reproduces the league host's per-tick state hashes exactly.

  prepare  fetch the game source at the exact commit and every nimby.lock dependency at its
           locked commit into tools/.cache/ (network: github.com over HTTPS, via git)
  build    compile tools/bin/<commit8>/pudge_headless offline and write a receipt next to it
           (sources, dependency pins, toolchain, options, binary sha256); prepares first if needed
  path     print the binary path, building it if it is missing or its receipt is stale

The build links tools/curl_stub.c instead of loading libcurl at startup (see that file), so
the binary needs only libc and libm: it runs on hosts without libcurl or libssl.
The game repo (Metta-AI/polyworld-pudge-wars) and the nimby.lock dependencies are public on
GitHub, so prepare needs no credentials. PUDGE_ENGINE_SOURCE (a clone or git bundle holding
the pinned commit) and PUDGE_DEPENDENCY_ROOT (a directory holding one git repo per nimby.lock
name) are optional mirrors for offline hosts.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
from cli_contract import Errored, Rejected

TOOLS = Path(__file__).resolve().parent
CACHE = TOOLS / '.cache'
BIN = TOOLS / 'bin'
BINARY_NAME = 'pudge_headless'
RECEIPT_SCHEMA = 'pudge-native-build/1'
ENTRY = 'examples/pudge_wars/pw.nim'
# Logical compile options; absolute --path/--import/--nimcache/-o values are added at build time.
OPTIONS = ['-d:release', '-d:headless', '--hints:off', '--warnings:off', '--noNimblePath',
           '--dynlibOverride:libcurl', '--import:tools/curl_stub.nim']
LOCAL_FILES = ('curl_stub.c', 'curl_stub.nim', 'native.py', 'engine.env')
GIT_ENV = {'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def execute(command, *, cwd=None, timeout=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=cwd, env={**os.environ, **GIT_ENV}, capture_output=True,
                              text=True, check=True, timeout=timeout)
    except FileNotFoundError as error:
        raise Errored(f'{command[0]} is not installed: {error}', reason='environment_missing') from None
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or '').strip()[-2000:]
        raise Errored(f'{" ".join(map(str, command[:4]))} failed (exit {error.returncode}): {detail}',
                      reason='command_failed') from None


def git(directory: Path, *args) -> str:
    return execute(['git', '-C', str(directory), *args]).stdout.strip()


def default_commit() -> str:
    return cli_contract.engine()['POLYWORLD_PUDGE_COMMIT']


def check_commit(commit: str) -> str:
    if not re.fullmatch('[0-9a-f]{40}', commit or ''):
        raise Rejected(f'commit must be a full 40-character lowercase sha, not {commit!r}')
    return commit


def source_dir(commit: str) -> Path:
    return CACHE / f'source-{commit[:12]}'


def dependency_dir(name: str, commit: str) -> Path:
    return CACHE / 'deps' / f'{name}-{commit[:12]}'


def binary_path(commit: str) -> Path:
    return BIN / commit[:8] / BINARY_NAME


def receipt_path(binary: Path) -> Path:
    return binary.with_name(binary.name + '.receipt.json')


def verify_checkout(directory: Path, commit: str) -> None:
    """A cached checkout must sit at the pin with no local changes; never repair it."""
    head = git(directory, 'rev-parse', 'HEAD')
    if head != commit:
        raise Errored(f'{directory} is at {head}, not the pin {commit}; move it aside and prepare again',
                      reason='source_mismatch')
    if git(directory, 'status', '--porcelain', '--untracked-files=no'):
        raise Errored(f'{directory} has local changes; move it aside and prepare again',
                      reason='source_modified')


def populate(directory: Path, source: str, commit: str) -> None:
    """Fetch exactly one commit into a new directory (atomic rename), or verify an existing one."""
    if directory.exists():
        verify_checkout(directory, commit)
        return
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = directory.with_name(f'{directory.name}.prepare-{os.getpid()}')
    try:
        execute(['git', 'init', '-q', str(staging)])
        execute(['git', '-C', str(staging), 'fetch', '-q', '--depth=1', source, commit], timeout=600)
        execute(['git', '-C', str(staging), 'checkout', '-q', '--detach', commit])
        verify_checkout(staging, commit)
        staging.rename(directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def lock_entries(source: Path) -> list[dict]:
    """nimby.lock: one `name version url sha` line per dependency."""
    entries = []
    for number, line in enumerate((source / 'nimby.lock').read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4 or not re.fullmatch('[0-9a-f]{40}', parts[3]):
            raise Errored(f'nimby.lock:{number} is not "name version url sha": {line!r}',
                          reason='bad_lockfile')
        name, version, url, commit = parts
        entries.append({'name': name, 'version': version, 'url': url, 'commit': commit})
    return entries


def prepare(commit: str) -> dict:
    """Fetch the pinned source and dependencies; return what was prepared."""
    check_commit(commit)
    started = time.monotonic()
    repo = os.environ.get('PUDGE_ENGINE_SOURCE') or cli_contract.engine()['POLYWORLD_PUDGE_REPO']
    source = source_dir(commit)
    try:
        populate(source, repo, commit)
    except Errored as error:
        raise Errored(f'{error}. Check network access to {repo}, or set PUDGE_ENGINE_SOURCE to a '
                      'clone or git bundle that holds the pinned commit', reason=error.reason) from None
    entries = lock_entries(source)
    mirror = os.environ.get('PUDGE_DEPENDENCY_ROOT')

    def fetch(entry):
        origin = str(Path(mirror) / entry['name']) if mirror else entry['url']
        populate(dependency_dir(entry['name'], entry['commit']), origin, entry['commit'])

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, entries))
    pinned_bassy = cli_contract.engine().get('BASSY_COMMIT')
    bassy = next((entry['commit'] for entry in entries if entry['name'] == 'bassy'), None)
    if commit == default_commit() and pinned_bassy and bassy != pinned_bassy:
        raise Errored(f'nimby.lock pins bassy {bassy}, engine.env says {pinned_bassy}',
                      reason='pin_mismatch')
    return {'commit': commit, 'source': source, 'dependencies': len(entries),
            'seconds': round(time.monotonic() - started, 2)}


def dependency_paths(entries: list[dict]) -> list[str]:
    """Nim search paths: a package's src/ when it has one, else its root (libcurl)."""
    paths = []
    for entry in entries:
        root = dependency_dir(entry['name'], entry['commit'])
        paths.append(str(root / 'src') if (root / 'src').is_dir() else str(root))
    return paths


def toolchain() -> dict:
    nim = shutil.which('nim')
    if nim is None:
        raise Errored('nim is not on PATH; install Nim 2.2.10', reason='environment_missing')
    version = execute([nim, '--version']).stdout.splitlines()[0]
    wanted = cli_contract.engine().get('NIM_VERSION', '2.2.10')
    if not version.startswith(f'Nim Compiler Version {wanted} '):
        raise Errored(f'expected Nim {wanted}, found: {version}', reason='environment_missing')
    compiler = 'clang' if sys.platform == 'darwin' else 'gcc'
    found = shutil.which(compiler)
    if found is None:
        raise Errored(f'{compiler} is not on PATH', reason='environment_missing')
    cc_version = execute([found, '--version']).stdout.splitlines()[0]
    return {'nim': version, 'nim_sha256': digest(Path(nim).resolve()), 'cc': cc_version,
            'platform': f'{sys.platform}-{platform.machine()}'}


def build_inputs(commit: str) -> dict:
    """Everything a build depends on; a receipt is valid only while these are unchanged."""
    source = source_dir(commit)
    verify_checkout(source, commit)
    entries = lock_entries(source)
    for entry in entries:
        verify_checkout(dependency_dir(entry['name'], entry['commit']), entry['commit'])
    return {'schema': RECEIPT_SCHEMA, 'target': BINARY_NAME, 'entry': ENTRY,
            'engine': {'commit': commit, 'tree': git(source, 'rev-parse', 'HEAD^{tree}')},
            'dependencies': {entry['name']: {key: entry[key] for key in ('version', 'url', 'commit')}
                             for entry in entries},
            'local_files': {name: digest(TOOLS / name) for name in LOCAL_FILES},
            'toolchain': toolchain(), 'options': OPTIONS}


def read_receipt(binary: Path) -> dict | None:
    try:
        return json.loads(receipt_path(binary).read_text())
    except (OSError, ValueError):
        return None


def current_receipt(binary: Path, inputs: dict) -> dict | None:
    """The receipt if it matches these inputs and the binary bytes; otherwise None."""
    receipt = read_receipt(binary)
    if (receipt is None or receipt.get('inputs') != inputs or not binary.is_file()
            or not os.access(binary, os.X_OK) or receipt.get('binary_sha256') != digest(binary)):
        return None
    return receipt


def build(commit: str, *, force: bool = False) -> dict:
    """Compile (or reuse) the binary for this commit; return its receipt."""
    check_commit(commit)
    if not source_dir(commit).is_dir():
        prepare(commit)
    inputs = build_inputs(commit)
    binary = binary_path(commit)
    if not force:
        receipt = current_receipt(binary, inputs)
        if receipt is not None:
            return {**receipt, 'binary': str(binary), 'reused': True}
    source = source_dir(commit)
    binary.parent.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_name(f'{binary.name}.tmp-{os.getpid()}')
    command = ['nim', 'c', *[option for option in OPTIONS if not option.startswith('--import:')],
               f'--import:{TOOLS / "curl_stub.nim"}',
               *[f'--path:{path}' for path in dependency_paths(lock_entries(source))],
               f'--nimcache:{CACHE / f"nimcache-{commit[:8]}"}', f'-o:{temporary}', ENTRY]
    started = time.monotonic()
    try:
        execute(command, cwd=source, timeout=1800)
        if build_inputs(commit) != inputs:
            raise Errored('build inputs changed during compilation; build again', reason='inputs_changed')
        receipt = {'schema': RECEIPT_SCHEMA, 'inputs': inputs, 'binary_sha256': digest(temporary),
                   'built_at': datetime.now(UTC).isoformat(timespec='seconds'),
                   'build_seconds': round(time.monotonic() - started, 2)}
        temporary.replace(binary)
        receipt_path(binary).write_text(json.dumps(receipt, indent=2) + '\n')
    finally:
        temporary.unlink(missing_ok=True)
    return {**receipt, 'binary': str(binary), 'reused': False}


def ensure(commit: str | None = None) -> tuple[Path, dict]:
    """(binary, receipt) for the commit, preparing and building as needed."""
    receipt = build(commit or default_commit())
    return Path(receipt['binary']), receipt


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='native.py',
        examples=['python3 games/pudge-wars-basic/tools/native.py prepare --json',
                  'python3 games/pudge-wars-basic/tools/native.py build --json',
                  'python3 games/pudge-wars-basic/tools/native.py path'])
    parser.add_argument('command', choices=['prepare', 'build', 'path'])
    parser.add_argument('--commit', help='engine commit (default: POLYWORLD_PUDGE_COMMIT in tools/engine.env)')
    parser.add_argument('--force', action='store_true', help='build: recompile even if the receipt is current')

    def body(args):
        commit = check_commit(args.commit or default_commit())
        if args.command == 'prepare':
            return prepare(commit)
        receipt = build(commit, force=args.force)
        if args.command == 'path':
            return {'binary': receipt['binary'], 'binary_sha256': receipt['binary_sha256'],
                    'commit': commit, 'reused': receipt['reused']}
        return receipt

    if argv is None:
        argv = sys.argv[1:]
    if 'path' in argv and '--json' not in argv and not {'-h', '--help'} & set(argv):
        # Plain `path` prints only the binary path, for shell use.
        try:
            args = parser.parse_args(argv)
            print(ensure(check_commit(args.commit or default_commit()))[0])
            return 0
        except cli_contract.ToolError as error:
            print(f'native.py: {error.reason}: {error}', file=sys.stderr)
            return error.exit_code
    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
