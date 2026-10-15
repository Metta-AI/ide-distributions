# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_build.py
"""Committed inputs, incremental work orders, isolated compiler calls and immutable builds."""
from __future__ import annotations

import dataclasses
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import pw_cli
import pw_release

LAB = pw_release.LAB
REPO = pw_release.REPO
COMPILER = LAB / 'strategy/compiler'
BUILDS = LAB / 'strategy/compiled'
STAGING = LAB / 'tmp/strategy'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def read(path: Path):
    return json.loads(path.read_text())


def git(*args, root=REPO) -> str:
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def plain(value):
    if dataclasses.is_dataclass(value):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def source_files(source: Path) -> list[Path]:
    from gota_host import BASELINE
    paths = [source, BASELINE]
    packing = source.parent / 'packing.json'
    if packing.exists():
        paths.append(packing)
    for folder in (source.parent / 'skills', COMPILER):
        if folder.exists():
            paths.extend(p for p in folder.rglob('*') if p.is_file())
    comms = source.parent / 'comms.md'
    if comms.exists():
        paths.append(comms)
    paths.extend(LAB.joinpath('tools').glob('*.py'))
    paths.extend(LAB.parent.joinpath('gates').glob('*.py'))
    paths.extend(LAB.parent.joinpath('tools').glob('*.py'))
    paths.extend(LAB.parent.joinpath('tools').glob('*.nim'))
    paths.extend([LAB.parent / 'tools/build_nim.sh', LAB.parent / 'tools/build_gota_match.sh',
                  LAB.parent / 'tools/engine.env', LAB.parent / 'tools/host_contract.json'])
    paths.extend([LAB / 'tools/pw_strategy.py', LAB / 'tools/pw_intent.py', LAB / 'tools/release.env',
                  LAB / 'docs/policy-surface.md'])
    paths.extend(LAB / 'tools' / name for name in ('pw_local.py', 'pw_release.py', 'pw_cli.py',
                                                   'pw_terrain.py', 'pw.py'))
    return sorted(set(paths))


def committed_inputs(source: Path) -> dict[str, str]:
    """Git verifies the index AND working copy; untracked input is never a committed input."""
    paths = source_files(source)
    relative = []
    installed = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise pw_cli.UsageError(f'input must be a regular file: {path}')
        if path.is_relative_to(REPO):
            relative.append(str(path.relative_to(REPO)))
        elif os.environ.get('GOTA_CANDIDATE_REPO'):
            installed.append(path)
        else:
            raise pw_cli.UsageError(f'source must be inside {REPO}')
    scopes = relative + [str(source.parent.relative_to(REPO) / name)
                         for name in ('skills', 'comms.md', 'packing.json')]
    if COMPILER.is_relative_to(REPO):
        scopes.append(str(COMPILER.relative_to(REPO)))
    dirty = git('status', '--porcelain', '--untracked-files=all', '--', *scopes)
    tracked = set(git('ls-files', '--', *relative).splitlines())
    missing = set(relative) - tracked
    if dirty or missing:
        raise pw_cli.UsageError('commit compiler and strategy inputs before building:\n' +
                               dirty + '\n' + '\n'.join(sorted(missing)))
    # A development distribution must still have clean, tracked compiler inputs.
    distribution = LAB.parents[3]
    probe = subprocess.run(['git', '-C', str(distribution), 'rev-parse', '--show-toplevel'],
                           capture_output=True, text=True, check=False)
    if installed and probe.returncode == 0:
        root = Path(probe.stdout.strip())
        names = [str(path.relative_to(root)) for path in installed]
        dirty = git('status', '--porcelain', '--untracked-files=all', '--', *names, str(COMPILER.relative_to(root)), root=root)
        tracked = set(git('ls-files', '--', *names, root=root).splitlines())
        if dirty or set(names) - tracked:
            raise pw_cli.UsageError('commit installed compiler inputs before building: ' + dirty)
    return {**{name: digest(REPO / name) for name in relative},
            **{str(path): digest(path) for path in installed}}


def assert_inputs(order: dict) -> None:
    if 'source_path' in order:
        packing = (REPO / order['source_path']).parent / 'packing.json'
        if packing.exists() and str(packing.relative_to(REPO)) not in order['source_hashes']:
            raise pw_cli.UsageError('packing.json added since prepare; prepare a new build')
    for name, expected in order['source_hashes'].items():
        path = REPO / name
        if not path.is_file() or path.is_symlink() or digest(path) != expected:
            raise pw_cli.UsageError(f'input changed since prepare: {name}; prepare a new build')


def build_path(build_id: str, *, staged: bool = False) -> Path:
    import re
    if not re.fullmatch(r'[0-9a-f]{7,40}-[1-9][0-9]*', build_id):
        raise pw_cli.UsageError(f'invalid build ID: {build_id}')
    path = (STAGING if staged else BUILDS) / build_id
    if not path.is_dir() or path.is_symlink():
        raise pw_cli.UsageError(f'build does not exist: {path}')
    return path


def checked_build(path: Path) -> dict:
    version = read(path / 'version.json')
    if version.get('status') != 'passed':
        raise pw_cli.UsageError(f'previous build did not pass: {path.name}')
    if digest(path / 'policy.bas') != version['policy_sha256']:
        raise pw_cli.UsageError(f'previous policy was modified: {path.name}')
    for name, expected in version.get('artifact_hashes', {}).items():
        if digest(path / name) != expected:
            raise pw_cli.UsageError(f'previous artifact was modified: {path.name}/{name}')
    return version


def previous_build(source: Path, commit: str, explicit: str | None) -> Path | None:
    if explicit:
        path = build_path(explicit)
        checked_build(path)
        return path
    candidates = []
    for path in BUILDS.glob('*'):
        if not (path / 'version.json').is_file():
            continue
        version = read(path / 'version.json')
        if version.get('status') != 'passed' or version.get('source_path') != str(source.relative_to(REPO)):
            continue
        ancestor = subprocess.run(['git', '-C', str(REPO), 'merge-base', '--is-ancestor',
                                   version['source_commit'], commit], capture_output=True)
        if ancestor.returncode == 0:
            candidates.append((version['created'], path))
    if not candidates:
        return None
    path = max(candidates)[1]
    checked_build(path)
    return path


def component_map(strategy) -> dict:
    return {key: {'text_hash': item.text_hash, 'prefix': item.prefix,
                  'interface': plain(item.interface), 'fields': plain(item.fields),
                  'compiled_text': item.compiled_text, 'llm': item.llm, 'uses': list(item.uses)}
            for key, item in strategy.components.items()}


def changes(current: dict, previous: dict, full: bool = False) -> dict[str, str]:
    statuses = {}
    for key, item in current.items():
        old = previous.get(key)
        statuses[key] = 'new' if old is None else (
            'changed' if full or item['text_hash'] != old['text_hash'] or
            item['interface'] != old.get('interface') else 'reused')
    for key in previous.keys() - current.keys():
        statuses[key] = 'removed'
    return statuses


def packing_bindings(source: Path) -> dict:
    if (source.parent / 'packing.json').exists():
        raise pw_cli.UsageError('packing.json is obsolete: structured GotA builds preserve namespaced storage')
    return {}


def prepare(source: Path, *, agent=None, model=None, full=False, milestone=None, previous=None, engine_identity=None) -> dict:
    from strategy_format import parse_strategy, lint_strategy
    strategy = parse_strategy(source, backend="gota")
    packing_bindings(source)
    diagnostics = [plain(d) for d in lint_strategy(strategy)]
    errors = [d for d in diagnostics if d['level'] == 'error']
    if errors:
        raise pw_cli.UsageError('strategy lint failed:\n' + '\n'.join(d['message'] for d in errors))
    hashes = committed_inputs(source)
    commit = git('rev-parse', 'HEAD')
    prior = previous_build(source, commit, previous)
    config = read(COMPILER / 'config.json')
    agent = agent or config['agent']
    if agent not in ('claude', 'codex', 'hermes'):
        raise pw_cli.UsageError('unknown compiler agent', ['claude', 'codex', 'hermes'])
    model = model or (os.environ.get('OPENROUTER_MODEL') if agent == 'hermes' else None) or config['models'][agent]
    executable = shutil.which(agent)
    if executable is None:
        raise pw_cli.EnvironmentMissing(f'{agent} CLI is not installed', f'install the {agent} CLI and log in')
    cli_version = subprocess.run([executable, '--version'], capture_output=True,
                                 text=True, check=True, timeout=30).stdout.strip()
    old_map = read(prior / 'map.json').get('components', {}) if prior else {}
    current = component_map(strategy)
    # A runtime release can change BASIC semantics without changing strategy text.
    from gota_host import POLYWORLD_COMMIT
    identity = engine_identity or {'tag': 'gota-2026.10.7.2', 'commit': POLYWORLD_COMMIT}
    if prior and read(prior / 'version.json').get('engine', {}).get('commit') != identity['commit']:
        full = True
    statuses = changes(current, old_map, full)
    # Interfaces are source-declared, so dependency invalidation is known before the LLM runs.
    for key, item in current.items():
        uses = str(item['fields'].get('Uses', ''))
        for dependency, contract in current.items():
            if dependency != key and f'`{dependency}`' in uses and contract['interface'] != old_map.get(dependency, {}).get('interface'):
                if statuses[key] == 'reused':
                    statuses[key] = 'changed'
    behavior_changed = any(s != 'reused' for s in changes(current, old_map).values())
    if prior:
        old_hashes = read(prior / 'version.json')['source_hashes']
        for name in set(old_hashes) | set(hashes):
            if ('/skills/' in name or name.endswith('/comms.md')) and hashes.get(name) != old_hashes.get(name):
                behavior_changed = True
    intent = 'm1' if milestone == 'm1' else ('behavior_change' if behavior_changed else 'no_behavior_change')
    STAGING.mkdir(parents=True, exist_ok=True)
    BUILDS.mkdir(parents=True, exist_ok=True)
    claims = STAGING / '.claims'
    claims.mkdir(exist_ok=True)
    sequence = 1
    while True:
        build_id = f'{commit[:8]}-{sequence}'
        if (BUILDS / build_id).exists():
            sequence += 1
            continue
        stage = STAGING / build_id
        try:
            (claims / build_id).mkdir()
            stage.mkdir()
            break
        except FileExistsError:
            sequence += 1
    (stage / 'units').mkdir()
    if prior:
        for key, status in statuses.items():
            if status in ('reused', 'changed') and (prior / 'units' / f'{key}.bas').is_file():
                shutil.copyfile(prior / 'units' / f'{key}.bas', stage / 'units' / f'{key}.bas')
    order = {'build_id': build_id, 'source_commit': commit,
             'source_path': str(source.relative_to(REPO)), 'source_hashes': hashes,
             'previous_build': prior.name if prior else None, 'components': current,
             'units': statuses, 'full': full, 'milestone': milestone, 'intent': intent,
             'compiler': {'agent': agent, 'model': model, 'cli_version': cli_version,
                          'agent_sha256': digest(COMPILER / 'AGENT.md'),
                          'lessons_sha256': digest(COMPILER / 'LESSONS.md')},
             'backend': 'gota',
             'engine': identity,
             'created': datetime.now(timezone.utc).isoformat(), 'diagnostics': diagnostics}
    dump(stage / 'work_order.json', order)
    return order


def snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if rel.parts[0] == '.git':
            continue
        if path.is_symlink():
            result[str(rel)] = 'symlink:' + os.readlink(path)
        elif path.is_file():
            result[str(rel)] = digest(path)
    return result


def scope_changes(before: dict, after: dict, allowed: set[str]) -> list[str]:
    return sorted(name for name in before.keys() | after.keys()
                  if before.get(name) != after.get(name) and name not in allowed)


def agent_command(agent: str, model: str, directory: Path) -> list[str]:
    if agent == 'claude':
        return ['claude', '-p', '--model', model, '--safe-mode', '--dangerously-skip-permissions',
                '--tools', 'Read,Write,Edit,Glob,Grep', '--output-format', 'text']
    if agent == 'hermes':
        return ['hermes', 'chat', '--oneshot', '--format', 'stream-json',
                '--query-file', str(directory / 'prompt.txt'), '-t', 'file', '--max-turns', '40',
                '--model', model, '--provider', 'openrouter']
    if agent != 'codex':
        raise pw_cli.UsageError(f'unknown compiler agent: {agent}', ['claude', 'codex', 'hermes'])
    return ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--sandbox', 'workspace-write',
            '--model', model, '-C', str(directory), '-']


def chat_stream_usage(stream: str, model: str) -> dict:
    """Derive a labeled main-session token receipt from official Hermes JSONL output."""
    try:
        records = [json.loads(line) for line in stream.splitlines() if line.strip()]
        if not all(isinstance(record, dict) for record in records):
            raise ValueError('non-object record')
        starts = [record for record in records if record.get('type') == 'system' and record.get('subtype') == 'init']
        results = [record for record in records if record.get('type') == 'result']
        if len(starts) != 1 or len(results) != 1 or records[0] != starts[0] or records[-1] != results[0]:
            raise ValueError('expected one initial init and one terminal result')
        start, result = starts[0], results[0]
        if start.get('model') != model or not isinstance(start.get('session_id'), str) or not start['session_id']:
            raise ValueError('wrong model or missing session')
        if result.get('session_id') != start['session_id'] or type(result.get('exit_code')) is not int or result['exit_code'] != 0:
            raise ValueError('wrong session or unsuccessful result')
        fields = {'input': 'input_tokens', 'output': 'output_tokens', 'total': 'total_tokens',
                  'cache_read': 'cache_read_tokens', 'cache_write': 'cache_write_tokens'}
        tokens = result['tokens']
        if not isinstance(tokens, dict) or any(type(tokens.get(key)) is not int or tokens[key] < 0 for key in fields):
            raise ValueError('invalid token counts')
        return {'source': 'hermes-chat-stream-json', 'model': model, 'session_id': start['session_id'],
                'stream_sha256': hashlib.sha256(stream.encode()).hexdigest(), 'records': records,
                **{name: tokens[key] for key, name in fields.items()}}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Hermes chat stream receipt invalid: {exc}') from exc


def copy_unit_inputs(stage: Path, root: Path, generated: list[str], authored: dict[str, Path]) -> None:
    """Keep previous generated units as references; each requested output must be written anew."""
    previous = root / 'context/previous'
    previous.mkdir(parents=True)
    for path in (stage / 'units').glob('*.bas'):
        if path.stem in authored or path.stem.startswith('runtime.') or path.stem == 'generated.tables':
            continue
        target = previous if path.stem in generated else root / 'units'
        shutil.copyfile(path, target / path.name)
    for name, path in authored.items():
        shutil.copyfile(path, root / 'units' / f'{name}.bas')


def generate(order: dict, *, errors=None, timeout=900) -> None:
    """Agent edits only a disposable independent Git repository, never this checkout."""
    stage = build_path(order['build_id'], staged=True)
    assert_inputs(order)
    generated = [key for key, status in order['units'].items()
                 if status in ('new', 'changed') and order['components'][key]['llm']]
    if not generated:
        if not (stage / 'report_draft.json').exists():
            dump(stage / 'report_draft.json', {'guesses': [], 'gaps': []})
        return
    with tempfile.TemporaryDirectory(prefix='agent-', dir=stage) as temporary:
        root = Path(temporary)
        (root / 'units').mkdir()
        (root / 'context').mkdir()
        from strategy_format import parse_strategy
        strategy = parse_strategy(REPO / order['source_path'], backend='gota')
        authored = {comp.id: strategy.root / comp.code_path for comp in strategy.of_kind('SK')}
        copy_unit_inputs(stage, root, generated, authored)
        shutil.copyfile(COMPILER / 'AGENT.md', root / 'AGENTS.md')
        shutil.copyfile(COMPILER / 'LESSONS.md', root / 'context/LESSONS.md')
        shutil.copyfile(LAB / 'docs/policy-surface.md', root / 'context/policy-surface.md')
        for context_file in (COMPILER / 'context').glob('*'):
            shutil.copyfile(context_file, root / 'context' / context_file.name)
        shutil.copyfile(COMPILER / 'host_surface.json', root / 'context/host_surface.json')
        from gota_host import BASELINE
        shutil.copyfile(BASELINE, root / 'context/base.bas')
        prior = build_path(order['previous_build']) if order['previous_build'] else None
        previous_guesses = read(prior / 'report.json').get('guesses', []) if prior else []
        from strategy_basic import unit_contract
        components = {key: {k: v for k, v in value.items() if k != 'fields'}
                      for key, value in order['components'].items() if key in generated}
        for key, value in components.items():
            value['contract'] = unit_contract(strategy, key)
        work = {'build_id': order['build_id'], 'generate': generated,
                'components': components,
                'interfaces': {key: value['interface'] for key, value in order['components'].items()},
                'previous_guesses': previous_guesses, 'repair_errors': errors or [],
                'baseline_bindings': packing_bindings(REPO / order['source_path'])}
        dump(root / 'work_order.json', work)
        specs = root / 'context/specs'
        specs.mkdir()
        for key, component in order['components'].items():
            (specs / f'{key}.md').write_text(component['compiled_text'])
        if (stage / 'report_draft.json').exists():
            shutil.copyfile(stage / 'report_draft.json', root / 'report_draft.json')
        prompt = ('Read AGENTS.md and work_order.json. Read context/specs/<ID>.md for each listed component; '
                  'these files contain the exact full specification with readable original line breaks. '
                  'Use these files rather than the escaped compiled_text JSON lines. '
                  'Generate only the listed components into units/<ID>.bas. '
                  'Read context/LESSONS.md and context/policy-surface.md. The components include their exact '
                  'source hashes and declared interfaces. Write report_draft.json with guesses and gaps. '
                  'Do not edit any other path or run Git commands. Do not commit. '
                  'Finish after writing the files; the driver assembles and runs gates.')
        hermes = order['compiler']['agent'] == 'hermes'
        if hermes:
            (root / 'prompt.txt').write_text(prompt)
        git('init', root=root)
        git('add', '.', root=root)
        git('-c', 'user.name=Strategy compiler', '-c', 'user.email=local@invalid',
            'commit', '-m', 'compiler inputs', root=root)
        # This disposable repo has no remote, shared refs, hooks, or user working changes.
        before = snapshot(root)
        head = git('rev-parse', 'HEAD', root=root)
        refs = git('show-ref', root=root)
        allowed = {f'units/{key}.bas' for key in generated} | {'report_draft.json'}
        command = agent_command(order['compiler']['agent'], order['compiler']['model'], root)
        environment = os.environ.copy()
        if hermes:
            environment['OPENROUTER_MODEL'] = order['compiler']['model']
            environment['TERMINAL_CWD'] = str(root)
            if os.environ.get('GOTA_COMPILER_HERMES_HOME'):
                environment['HERMES_HOME'] = os.environ['GOTA_COMPILER_HERMES_HOME']
                environment['HERMES_WRITE_SAFE_ROOT'] = str(root)
                for name in list(environment):
                    if name.startswith('HERMES_KANBAN_') or name in ('HERMES_PROFILE', 'HERMES_PROFILE_NAME'):
                        environment.pop(name)

        stream_path = stage / f'hermes-stream-{root.name}.jsonl'
        with (stage / 'agent.log').open('a') as log, (stream_path.open('w') if hermes else nullcontext(log)) as stream:
            try:
                completed = subprocess.run(command, input=None if hermes else prompt, cwd=root, text=True, env=environment,
                                           stdout=stream if hermes else log, stderr=log if hermes else subprocess.STDOUT, timeout=timeout)
            except subprocess.TimeoutExpired:
                evidence = f'agent.log and {stream_path.name}' if hermes else 'agent.log'
                raise RuntimeError(f'compiler agent timed out after {timeout:g}s; '
                                   f'see {evidence} in the retained build directory') from None
        after = snapshot(root)
        violations = scope_changes(before, after, allowed)
        if git('rev-parse', 'HEAD', root=root) != head or git('show-ref', root=root) != refs:
            violations.append('.git refs')
        if git('diff', '--cached', '--name-only', root=root):
            violations.append('.git index')
        if violations:
            raise RuntimeError('compiler write-scope violation: ' + ', '.join(violations))
        if completed.returncode:
            raise RuntimeError(f'compiler exited {completed.returncode}; see {stage}/agent.log')
        if hermes:
            dump(root / 'usage.json', chat_stream_usage(stream_path.read_text(), order['compiler']['model']))
            allowed.add('usage.json')
        for name in allowed:
            path = root / name
            if not path.is_file() or path.is_symlink() or path.resolve().parent not in (root, root / 'units'):
                raise RuntimeError(f'compiler did not write a regular output: {name}')
        if hermes and not isinstance(read(root / 'usage.json'), dict):
            raise ValueError('Hermes usage file must contain a JSON object')
        validate_draft(read(root / 'report_draft.json'), order)
        for name in allowed:
            target = stage / (f'hermes-usage-{root.name}.json' if name == 'usage.json' else name)
            shutil.copyfile(root / name, target)


def validate_draft(draft: dict, order: dict) -> None:
    if not isinstance(draft, dict) or not isinstance(draft.get('guesses'), list) or not isinstance(draft.get('gaps'), list):
        raise ValueError('report draft must contain guesses and gaps lists')
    previous = {}
    if order.get('previous_build'):
        previous = {g['id']: g for g in read(build_path(order['previous_build']) / 'report.json').get('guesses', [])}
    seen = set()
    for guess in draft['guesses']:
        required = {'id', 'component', 'spec_quote', 'decision', 'why', 'severity', 'state'}
        if not isinstance(guess, dict) or not required <= guess.keys():
            raise ValueError('guess is missing required report fields')
        component = guess['component']
        if component not in order['components'] or not guess['id'].startswith(f'G-{component}-'):
            raise ValueError('guess ID must name its component')
        if not guess['id'].removeprefix(f'G-{component}-').isdigit() or guess['id'] in seen:
            raise ValueError('guess IDs must be unique numbered IDs')
        seen.add(guess['id'])
        if guess['severity'] not in ('low', 'medium', 'high') or guess['state'] not in ('open', 'kept', 'resolved'):
            raise ValueError('invalid guess severity or state')
        text = order['components'][component]['compiled_text']
        old = previous.get(guess['id'])
        if guess['state'] in ('kept', 'resolved'):
            if not old or old['component'] != component or old['spec_quote'] != guess['spec_quote']:
                raise ValueError('kept/resolved guess must preserve a previous ID and quote')
        elif old:
            raise ValueError('an existing guess ID must be kept or resolved, not reused as new')
        elif not guess['spec_quote'] or guess['spec_quote'] not in text:
            raise ValueError('guess spec_quote must quote the component exactly')


def assemble_build(build_id: str) -> dict:
    from strategy_format import parse_strategy
    from strategy_basic import assemble
    stage = build_path(build_id, staged=True)
    order = read(stage / 'work_order.json')
    assert_inputs(order)
    strategy = parse_strategy(REPO / order['source_path'], backend='gota')
    units = {p.stem: p.read_text() for p in (stage / 'units').glob('*.bas')
             if p.stem in strategy.components and strategy.components[p.stem].llm}
    bindings = packing_bindings(strategy.path)
    result = assemble(strategy, units, COMPILER / 'runtime', build_id, order['source_commit'],
                      baseline_bindings=bindings)
    (stage / 'policy.bas').write_text(result['policy'])
    for key, text in result['units'].items():
        (stage / 'units' / f'{key}.bas').write_text(text)
    dump(stage / 'map.json', result['map'])
    dump(stage / 'budget.json', result['budget'])
    return result


def carry_guesses(order: dict, draft: dict) -> list[dict]:
    previous = []
    if order['previous_build']:
        previous = read(build_path(order['previous_build']) / 'report.json').get('guesses', [])
    guesses = {g['id']: dict(g) for g in previous}
    for guess in guesses.values():
        fields = order['components'].get(guess['component'], {}).get('fields', {})
        if guess['id'] in {item.strip() for item in fields.get('Accepts', '').split(',')}:
            guess['state'] = 'closed'
        elif order['units'].get(guess['component']) in ('changed', 'removed') and guess['state'] != 'closed':
            guess['state'] = 'possibly resolved'
    for guess in draft['guesses']:
        # An LLM cannot close a guess: closing requires an explicit source acceptance.
        item = dict(guess)
        fields = order['components'][item['component']]['fields']
        if item['state'] == 'kept':
            item['state'] = 'closed' if guesses.get(item['id'], {}).get('state') == 'closed' else 'open'
        elif item['state'] == 'resolved':
            item['state'] = 'possibly resolved'
        if item['id'] in {value.strip() for value in fields.get('Accepts', '').split(',')}:
            item['state'] = 'closed'
        guesses[item['id']] = item
    return list(guesses.values())


def finalize(order: dict, gates: dict, *, error: str | None = None, tool_error: dict | None = None) -> Path:
    from games.gota.gates import retain_episode_locators

    assert_inputs(order)
    stage = build_path(order['build_id'], staged=True)
    target = BUILDS / order['build_id']
    if target.exists():
        raise pw_cli.UsageError(f'finalized build is immutable: {target}')
    retain_episode_locators(gates, stage)
    for path in stage.rglob('gates.json'):
        retained = read(path)
        retain_episode_locators(retained, stage)
        dump(path, retained)
    draft = read(stage / 'report_draft.json') if (stage / 'report_draft.json').exists() else {'guesses': [], 'gaps': []}
    guesses = carry_guesses(order, draft)
    blocked = [g['id'] for g in guesses if g['severity'] == 'high' and g['state'] != 'closed'
               and str(order['components'].get(g['component'], {}).get('fields', {}).get('Status', '')).startswith(('tested', 'proven'))]
    if blocked:
        error = 'Status: tested is blocked by unresolved high-severity guesses: ' + ', '.join(blocked)
    gate_errors = [g['summary']['error'] for g in gates.values() if g.get('outcome') == 'errored']
    if tool_error or gate_errors:
        status = 'errored'
        error = tool_error or gate_errors[0]
    else:
        status = 'passed' if not error and len(gates) == 5 and all(gates.get(name, {}).get('passed') is True for name in ('G1', 'G2', 'G3')) else 'failed'
    warnings = [{'gate': name, 'summary': gate.get('summary', {})}
                for name, gate in gates.items()
                if name in ('G4', 'G5') and gate.get('outcome') == 'failed']
    budget = read(stage / 'budget.json') if (stage / 'budget.json').exists() else {}
    report = {'build_id': order['build_id'], 'status': status, 'units': order['units'],
              'guesses': guesses, 'gaps': draft['gaps'], 'gates': gates, 'budget': budget, 'error': error, 'warnings': warnings}
    dump(stage / 'report.json', report)
    lines = [f'# Build {order["build_id"]}', '', f'Status: **{status}**. Intent: {order["intent"]}.', '',
             '| Gate | Outcome | Evidence |', '| --- | --- | --- |']
    unexercised = gates.get("G5", {}).get("coverage", {}).get("unexercised_message_types", [])
    if unexercised:
        lines[3:3] = ["G5 event coverage: **not exercised**: " + ", ".join(unexercised) +
                      ". Their semantic checks remain unproven.", ""]
    lines.extend(f'| {name} | {gate.get("outcome", "passed" if gate["passed"] else "failed")} | {gate.get("summary", "")} |' for name, gate in gates.items())
    if warnings:
        lines += ['', '## Warnings', '', *(f"- {warning['gate']}: {warning['summary']}" for warning in warnings)]
    lines += ['', '## Units', '', *(f'- `{key}`: {state}' for key, state in order['units'].items()),
              '', '## Guesses', '']
    lines.extend(f'- **{g["id"]}** ({g["severity"]}, {g["state"]}): {g["decision"]} — {g["why"]}\n'
                 f'  Source: {g["spec_quote"]}' for g in guesses)
    if not guesses:
        lines.append('None reported.')
    lines += ['', '## Gaps', '', '```json', json.dumps(draft['gaps'], indent=2), '```']
    lines += ['', '## Budget', '', '```json', json.dumps(budget, indent=2), '```']
    if error:
        lines += ['', '## Failure', '', '```json', json.dumps(error, indent=2), '```']
    (stage / 'report.md').write_text('\n'.join(lines) + '\n')
    version = {k: order[k] for k in ('build_id', 'source_commit', 'source_path', 'source_hashes',
                                    'previous_build', 'compiler', 'engine', 'created', 'intent')}
    version.update(backend='gota', status=status, gates=gates, driver={'repo_commit': order['source_commit'], 'dirty': False},
                   policy_sha256=digest(stage / 'policy.bas') if (stage / 'policy.bas').exists() else None)
    version['native_builds'] = read(stage / 'native_provenance.json') if (stage / 'native_provenance.json').exists() else {}
    version['artifact_hashes'] = {str(p.relative_to(stage)): digest(p) for p in stage.rglob('*')
                                  if p.is_file() and p.name != 'version.json' and p.suffix != '.log'}
    dump(stage / 'version.json', version)
    stage.rename(target)
    return target
