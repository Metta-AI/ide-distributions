"""Offline native build receipts shared by gate and match executables.

A receipt binds verified Git pins, actual source/config/dependency contents,
compiler/options and the executable bytes. Nothing is fetched or installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
TARGETS = {'gota_match', 'gate_probe', 'expand_replay', 'raw_spell_counts',
           'gota_verify', 'gota_audit', 'gota_policy_replay'}
SOURCE_SUFFIXES = {'.nim', '.nims', '.bas', '.cfg', '.h', '.c', '.cpp'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def execute(command, *, cwd=None, env=None):
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=True)


def git(directory: Path, *args) -> str:
    return execute(['git', '-C', str(directory), *args],
                   env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0'}).stdout.strip()


def source_cache(commit: str, tools: Path) -> Path:
    primary = tools / '.cache/polyworld'
    if primary.is_dir() and git(primary, 'rev-parse', 'HEAD') == commit:
        return primary
    return tools / f'.cache/polyworld-{commit[:8]}'


def repository(directory: Path, commit: str) -> dict:
    if git(directory, 'rev-parse', 'HEAD') != commit:
        raise ValueError(f'native source pin mismatch: {directory}')
    git(directory, 'diff', '--exit-code')
    git(directory, 'diff', '--cached', '--exit-code')
    names = git(directory, 'ls-files', '-z').split('\0')
    files = {name: digest(directory / name) for name in names if name}
    # Include untracked source files as well: they can affect import resolution.
    # Ignore Git metadata, generated caches and dependency copies (checked separately).
    for root, dirs, entries in os.walk(directory):
        dirs[:] = [name for name in dirs if name not in {'.git', 'tmp', 'nimcache', '.cache', 'bin'}]
        for name in entries:
            path = Path(root) / name
            relative = str(path.relative_to(directory))
            # Builder-owned copies are represented by canonical local helper inputs.
            if path.name.startswith('lab_') or path.name == 'replay_outcome.nim':
                continue
            if path.suffix in SOURCE_SUFFIXES and relative not in files:
                files[relative] = digest(path)
    return {'commit': commit, 'tree': git(directory, 'rev-parse', 'HEAD^{tree}'),
            'files_sha256': fingerprint(files), 'file_count': len(files)}


def build_environment(clone: Path) -> dict:
    return {**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'POLYWORLD_DEPS': str(clone / 'tmp/coworld/deps')}


def toolchain(clone: Path, source: Path) -> dict:
    nim = shutil.which('nim')
    if nim is None:
        raise ValueError('Nim 2.2.10 is required')
    env = build_environment(clone)
    version = execute([nim, '--version'], env=env).stdout.splitlines()[0]
    if not version.startswith('Nim Compiler Version 2.2.10 '):
        raise ValueError(f'expected Nim 2.2.10, got {version}')
    # Nim itself identifies the real configuration chain, including user/install
    # and directory configs. Do not guess paths or disable configuration loading.
    dump = execute([nim, 'dump', '--hints:on', str(source)], cwd=clone, env=env)
    configs = re.findall(r"used config file '([^']+)'", dump.stderr)
    if str(clone / 'config.nims') not in configs:
        raise ValueError('native toolchain did not load Polyworld config.nims')
    return {'nim': str(Path(nim).resolve()), 'nim_sha256': digest(Path(nim)), 'version': version,
            'configs': {name: digest(Path(name)) for name in configs},
            'environment': {name: os.environ.get(name) for name in
                            ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS', 'NIMFLAGS', 'NIMBLE_DIR')}}


def inputs(target: str, commit: str, *, tools=TOOLS) -> dict:
    if target not in TARGETS or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('unsupported native target or incomplete engine pin')
    clone = source_cache(commit, tools)
    engine = repository(clone, commit)
    dependencies = {}
    for line in (clone / 'coworld/dependencies.lock').read_text().splitlines():
        if line.strip():
            name, _, _, pin = line.split()
            dependencies[name] = repository(clone / 'tmp/coworld/deps' / name, pin)
    builder = 'build_gota_match.sh' if target == 'gota_match' else 'build_nim.sh'
    names = [target + '.nim', builder, 'native_build.py', 'engine.env']
    if target != 'gota_match':
        names.append('replay_outcome.nim')
    source = clone / f'examples/gods_of_the_arena/tools/lab_{target}.nim'
    if digest(source) != digest(tools / f'{target}.nim'):
        raise ValueError('native copied source differs from canonical helper')
    if target != 'gota_match' and digest(source.parent / 'replay_outcome.nim') != digest(tools / 'replay_outcome.nim'):
        raise ValueError('native copied replay helper differs from canonical helper')
    return {'schema': 'gota-native-inputs/1', 'target': target, 'engine': engine,
            'dependencies': dependencies,
            'local_files': {name: digest(tools / name) for name in names},
            'toolchain': toolchain(clone, source),
            'options': ['--hints:off', '--path:.', '-d:release', '-d:replayEvents',
                        f'-d:PolyworldCommit={commit}',
                        '-d:BassyCommit=' + dependencies.get('bassy', {}).get('commit', '')]}


def receipt_path(binary: Path) -> Path:
    return binary.with_name(binary.name + '.receipt.json')


def write_receipt(binary: Path, snapshot: dict) -> dict:
    receipt = {'schema': 'gota-native-build/1', 'inputs': snapshot,
               'fingerprint': fingerprint(snapshot), 'binary_sha256': digest(binary)}
    receipt_path(binary).write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def validate(binary: Path, target: str, commit: str, *, tools=TOOLS) -> dict:
    receipt = json.loads(receipt_path(binary).read_text())
    current = inputs(target, commit, tools=tools)
    if (receipt.get('schema') != 'gota-native-build/1' or receipt.get('inputs') != current
            or receipt.get('fingerprint') != fingerprint(current)
            or receipt.get('binary_sha256') != digest(binary) or not os.access(binary, os.X_OK)):
        raise ValueError(f'stale or modified native build: {binary}; rebuild offline')
    return receipt


def finish_build(binary: Path, before: dict, target: str, commit: str, *, tools=TOOLS) -> dict:
    after = inputs(target, commit, tools=tools)
    if after != before:
        raise ValueError('native inputs changed during compilation; rebuild offline')
    return write_receipt(binary, after)


def build(target: str, commit: str, *, tools=TOOLS) -> Path:
    clone = source_cache(commit, tools)
    output = tools / f'bin/{target}-{commit[:8]}'
    try:
        validate(output, target, commit, tools=tools)
        return output
    except (OSError, ValueError):
        pass  # Recompile only; missing/dirty/wrong-pin inputs still fail below.
    source = clone / f'examples/gods_of_the_arena/tools/lab_{target}.nim'
    shutil.copyfile(tools / f'{target}.nim', source)
    if target != 'gota_match':
        shutil.copyfile(tools / 'replay_outcome.nim', source.parent / 'replay_outcome.nim')
    before = inputs(target, commit, tools=tools)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f'.tmp.{os.getpid()}')
    receipt_path(output).unlink(missing_ok=True)
    try:
        command = ['nim', 'c', *before['options'],
                   f'--nimcache:{tools}/.cache/nimcache-{target}-{commit[:8]}',
                   f'-o:{temporary}', str(source)]
        result = execute(command, cwd=clone, env=build_environment(clone))
        if result.stderr:
            print(result.stderr, file=sys.stderr, end='')
        finish_build(temporary, before, target, commit, tools=tools)
        temporary.replace(output)
        receipt_path(temporary).replace(receipt_path(output))
    finally:
        temporary.unlink(missing_ok=True)
        receipt_path(temporary).unlink(missing_ok=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=sorted(TARGETS))
    parser.add_argument('commit')
    args = parser.parse_args()
    print(build(args.target, args.commit))


if __name__ == '__main__':
    main()
