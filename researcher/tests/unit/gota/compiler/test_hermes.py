"""Hermes is tested only with a local fake executable, including write guards."""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'games/gota/compiler/tools'))
import strategy_build as sb
from strategy_format import parse_strategy


@pytest.fixture
def generation(tmp_path, monkeypatch):
    source = tmp_path / 'STRATEGY.md'
    fixture = Path(__file__).parent / 'fixtures/strategy_trivial/STRATEGY.md'
    source.write_text(fixture.read_text().replace(',10,11,12,13,14,15', ''))
    stage = tmp_path / 'stages/abcdef12-1'
    (stage / 'units').mkdir(parents=True)
    monkeypatch.setattr(sb, 'REPO', tmp_path)
    monkeypatch.setattr(sb, 'STAGING', stage.parent)
    order = {'build_id': stage.name, 'source_path': source.name, 'source_hashes': {},
             'previous_build': None, 'components': sb.component_map(parse_strategy(source, backend='gota')),
             'units': {'K.position': 'new', 'C.idle': 'new'},
             'compiler': {'agent': 'hermes', 'model': 'test/openweight'}}
    binary = tmp_path / 'bin/hermes'
    binary.parent.mkdir()
    binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, subprocess
if '--version' in sys.argv:
    print('fake Hermes v0.21.5'); sys.exit()
assert os.environ['OPENROUTER_API_KEY'] == 'fake-profile-key'
assert 'GH_TOKEN' not in os.environ
a = sys.argv[1:]
assert a[:4] == ['chat', '--oneshot', '--format', 'stream-json']
assert '--usage-file' not in a
assert a[a.index('-t')+1] == 'file'
assert a[a.index('--max-turns')+1] == '40'
assert a[a.index('--model')+1] == os.environ['OPENROUTER_MODEL'] == 'test/openweight'
p = pathlib.Path(a[a.index('--query-file')+1])
assert 'work_order.json' in p.read_text()
assert 'context/specs/<ID>.md' in p.read_text()
u = pathlib.Path('usage.json')
mode = os.environ.get('FAKE_MODE', '')
work = json.loads(pathlib.Path('work_order.json').read_text())
for key in work['generate']:
    if mode == 'repair_patch':
        if key == 'C.idle':
            path = pathlib.Path('units', key+'.bas')
            if path.exists():
                path.write_text(path.read_text() + ' repaired')
        if key == 'K.config':
            path = pathlib.Path('units', key+'.bas')
            path.write_text(path.read_text().replace('cfgscanradius = 1200', 'cfgscanradius = 1100'))
        continue
    assert pathlib.Path('context/specs', key+'.md').read_text() == work['components'][key]['compiled_text']
    fixture = os.environ.get('FAKE_UNIT_ROOT')
    text = pathlib.Path(fixture, key+'.bas').read_text() if fixture else 'fake '+key
    pathlib.Path('units', key+'.bas').write_text(text)
assert pathlib.Path('context/host_surface.json').is_file()
assert pathlib.Path('context/structures.bas').is_file()
assert pathlib.Path('context/gota-guide.md').is_file()
assert pathlib.Path('context/bassy-language.md').is_file()
pathlib.Path('report_draft.json').write_text('{"guesses": [], "gaps": []}')
print(json.dumps({'type':'system', 'subtype':'init', 'model':os.environ['OPENROUTER_MODEL'], 'session_id':'fake-session'}))
if mode != 'usage_missing':
    print(json.dumps({'type':'result', 'session_id':'fake-session', 'exit_code':0, 'tokens':{'input':123,'output':45,'total':168,'cache_read':0,'cache_write':0}}))
if mode == 'prohibited': pathlib.Path('context/base.bas').write_text('changed')
if mode == 'prompt': p.write_text('changed')
if mode == 'extra': pathlib.Path('unexpected.txt').write_text('changed')
if mode == 'probe': pathlib.Path('probe_cap.txt').write_text('changed')
if mode == 'spec': pathlib.Path('context/specs/C.idle.md').write_text('changed')
if mode == 'index':
    pathlib.Path('units/C.idle.bas').write_text('staged mutation')
    subprocess.run(['git','add','units'],check=True)
if mode == 'refs': subprocess.run(['git','branch','bad'],check=True)
if mode in ('symlink','missing'):
    pathlib.Path('units/C.idle.bas').unlink()
    if mode == 'symlink': pathlib.Path('units/C.idle.bas').symlink_to('../context/base.bas')
if mode == 'usage_symlink':
    u.symlink_to('context/base.bas')
if mode == 'nonzero': sys.exit(7)
''')
    binary.chmod(0o755)
    # Expose only the fake backend and the tools needed for the scratch Git repo.
    # No real agent executable can be resolved, even before backend support exists.
    (binary.parent / 'python3').symlink_to(sys.executable)
    (binary.parent / 'git').symlink_to(shutil.which('git'))
    monkeypatch.setenv('PATH', str(binary.parent))
    monkeypatch.setenv('GOTA_HERMES_PYTHON', sys.executable)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile'))
    runtime = tmp_path / 'fake-runtime'
    package = runtime / 'tools/environments'
    package.mkdir(parents=True)
    (runtime / 'tools/__init__.py').touch()
    (package / '__init__.py').touch()
    (package / 'local.py').write_text(
        'def served_profile_child_env(*, target_home, inherit_credentials):\n'
        '    assert target_home and inherit_credentials\n'
        '    return {"OPENROUTER_API_KEY": "fake-profile-key", "GH_TOKEN": "not-for-compiler"}\n'
    )
    monkeypatch.setenv('PYTHONPATH', str(runtime))
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('GH_TOKEN', raising=False)
    run = sb.subprocess.run
    def local_only(command, **kwargs):
        assert command[0] in ('git', sys.executable, str(binary)), f'forbidden test subprocess: {command[0]}'
        return run(command, **kwargs)
    monkeypatch.setattr(sb.subprocess, 'run', local_only)
    return order, stage


def test_fake_hermes_outputs_and_usage(generation):
    order, stage = generation
    sb.generate(order)
    assert (stage / 'units/C.idle.bas').read_text() == 'fake C.idle'
    assert json.loads(next(stage.glob('hermes-usage-*.json')).read_text())['input_tokens'] == 123


@pytest.mark.parametrize('timeout,budget', [(1234, 1800), (1800, 1800)])
@pytest.mark.parametrize('failure_round', [1, 2])
def test_generation_timeout_retains_errored_build_without_repairs(
        generation, monkeypatch, timeout, budget, failure_round):
    import subprocess
    from types import SimpleNamespace

    import gota_gates
    import pw_cli
    import pw_strategy

    order, stage = generation
    order.update(source_commit='abcdef12', engine={}, created='now', intent='behavior_change')
    monkeypatch.setattr(sb, 'BUILDS', stage.parent.parent / 'builds')
    sb.BUILDS.mkdir()
    monkeypatch.setattr(sb, 'prepare', lambda *a, **kw: order)
    assemblies, gate_runs = [], []
    def assemble(*a):
        assemblies.append(True)
        (stage / 'policy.bas').write_text('prior completed policy')
    def verify(*a, **kw):
        gate_runs.append(True)
        return {'G1': {'outcome': 'failed', 'passed': False, 'summary': {}}}
    monkeypatch.setattr(sb, 'assemble_build', assemble)
    monkeypatch.setattr(gota_gates, 'verify', verify)
    run = sb.subprocess.run
    generations, now = [], [0.0]
    monkeypatch.setattr(pw_strategy.time, 'monotonic', lambda: now[0])
    def expire(command, **kwargs):
        if command[0] == sys.executable:
            generations.append(kwargs['timeout'])
            if len(generations) < failure_round:
                completed = run(command, **kwargs)
                now[0] += 200
                return completed
            now[0] += kwargs['timeout']
            kwargs['stdout'].write('{"type":"system","subtype":"init"}\n')
            kwargs['stderr'].write('fake timeout diagnostic\n')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return run(command, **kwargs)
    monkeypatch.setattr(sb.subprocess, 'run', expire)
    args = SimpleNamespace(action='compile', full_evaluation=True, source=sb.REPO / order['source_path'],
                           agent_timeout=timeout, agent_budget=budget, agent='hermes', model=None, full=False,
                           milestone=None, previous=None, manifest=stage / 'manifest.json')
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    target = Path(result['build'])
    effective = min(timeout, budget - 200) if failure_round == 2 else timeout
    assert generations == ([timeout] if failure_round == 1 else [timeout, effective])
    assert result['status'] == 'errored'
    assert result['error']['type'] == 'RuntimeError'
    streams = list(target.glob('hermes-stream-*.jsonl'))
    stream = next(path for path in streams if json.loads(path.read_text().splitlines()[0]).get('model') is None)
    assert result['error']['message'] == (
        f'compiler agent timed out after {effective:g}s; '
        f'see agent.log and {stream.name} in the retained build directory')
    assert assemblies == gate_runs == [True] * (failure_round - 1)
    if failure_round == 1:
        assert result['gates'] == {}
    else:
        assert (target / 'policy.bas').read_text() == 'prior completed policy'
    assert sb.read(target / 'version.json')['status'] == 'errored'
    assert f'compiler agent timed out after {effective:g}s' in (target / 'report.md').read_text()
    assert (target / 'agent.log').read_text() == 'fake timeout diagnostic\n'
    assert len(streams) == failure_round
    assert len(list(target.glob('hermes-usage-*.json'))) == failure_round - 1


@pytest.mark.parametrize('repair', [False, True])
@pytest.mark.parametrize('mode,match', [('prohibited','write-scope'), ('prompt','write-scope'),
    ('extra','write-scope'), ('probe','write-scope'), ('spec','write-scope'), ('index','write-scope'), ('refs','write-scope'),
    ('symlink','regular output'), ('missing','regular output'),
    ('usage_symlink','write-scope'), ('usage_missing','Hermes chat stream'), ('nonzero','exited 7')])
def test_fake_hermes_rejects_bad_outputs(generation, monkeypatch, mode, match, repair):
    if repair:
        sb.generate(generation[0])
    monkeypatch.setenv('FAKE_MODE', mode)
    error_type = ValueError if mode == 'usage_missing' else RuntimeError
    with pytest.raises(error_type, match=match):
        sb.generate(generation[0], errors=[{'message': 'repair'}] if repair else [])


def test_unknown_agent_fails_closed(tmp_path):
    import pw_cli
    with pytest.raises(pw_cli.UsageError, match='unknown compiler agent'):
        sb.agent_command('unknown-backend', 'unused', tmp_path)


def test_fake_full_generation_assembles_specified_v13(generation, monkeypatch):
    import strategy_basic
    order, stage = generation
    root = Path(__file__).resolve().parents[4]
    source = sb.REPO / order['source_path']
    source.write_text((root / 'games/gota/seed/strategy/STRATEGY.md').read_text())
    strategy = parse_strategy(source, backend='gota')
    order['components'] = sb.component_map(strategy)
    order['units'] = {'K.config': 'new', 'C.farm': 'new'}
    monkeypatch.setenv('FAKE_UNIT_ROOT', str(root / 'tests/fixtures/gota/v13_units'))
    sb.generate(order)
    units = {path.stem: path.read_text() for path in (stage / 'units').glob('*.bas')}
    result = strategy_basic.assemble(strategy, units, sb.COMPILER / 'runtime', stage.name, 'a' * 40)
    assert result['budget']['globals'] == 384
    assert result['budget']['arrays'] == 102
    assert result['budget']['array_cells'] == 157993


@pytest.mark.parametrize('default_agent', [False, True])
def test_fake_prepare_to_verified_goal_audit(generation, monkeypatch, capsys, default_agent):
    """Exercise real source/map validation; only native evidence and gate results are fixtures."""
    import hashlib

    import pw_cli
    import strategy_audit

    from games.gota.tools import goal_audit
    from games.gota.tools.equivalence import BASSY_COMMIT, POLYWORLD_COMMIT

    _, _ = generation
    root = Path(__file__).resolve().parents[4]
    temporary = sb.REPO
    seed = root / 'games/gota/seed/strategy/STRATEGY.md'
    # Copy the actual complete input closure into a new local Git repository.
    for original in sb.source_files(seed):
        destination = temporary / original.relative_to(root.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    lab = temporary / 'researcher/games/gota/compiler'
    source = lab.parent / 'seed/strategy/STRATEGY.md'
    monkeypatch.setattr(sb, 'LAB', lab)
    monkeypatch.setattr(sb, 'COMPILER', lab / 'strategy/compiler')
    monkeypatch.setattr(sb, 'STAGING', lab / 'strategy/.staging')
    monkeypatch.setattr(sb, 'BUILDS', lab / 'strategy/compiled')
    import gota_host
    monkeypatch.setattr(gota_host, 'BASELINE', temporary / 'researcher/tests/fixtures/gota/v13-live.bas')
    original_git = sb.git
    monkeypatch.setattr(sb, 'git', lambda *args, **kwargs: original_git(*args, root=temporary))
    sb.git('init')
    sb.git('add', 'researcher')
    sb.git('-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit', '-m', 'fixture inputs')
    monkeypatch.setenv('FAKE_UNIT_ROOT', str(root / 'tests/fixtures/gota/v13_units'))
    if default_agent:
        monkeypatch.setenv('OPENROUTER_MODEL', 'test/openweight')
        order = sb.prepare(source, full=True, milestone='m1')
    else:
        order = sb.prepare(source, agent='hermes', model='test/openweight', full=True, milestone='m1')
    assert order['compiler']['agent'] == 'hermes'
    assert order['compiler']['model'] == 'test/openweight'
    assert order['compiler']['cli_version'] == 'fake Hermes v0.21.5'
    sb.generate(order)
    sb.assemble_build(order['build_id'])
    native_fixture = {'gota_match': {'binary_sha256': 'a' * 64, 'fingerprint': 'b' * 64}}
    sb.dump(sb.build_path(order['build_id'], staged=True) / 'native_provenance.json', native_fixture)
    build = sb.finalize(order, {f'G{i}': {'passed': True, 'summary': 'test fixture only'} for i in range(1, 6)}, full_evaluation=True)
    _, version, mapping, _ = strategy_audit.load_build(order['build_id'])
    assert version['artifact_hashes']['native_provenance.json'] == sb.digest(build / 'native_provenance.json')
    assert version['native_builds'] == native_fixture
    logs = temporary / 'native-fixture'
    logs.mkdir()
    policy = (build / 'policy.bas').read_bytes()
    source_sha1 = hashlib.sha1(policy).hexdigest()
    for seat in range(10):
        (logs / f'policy-{seat}.bas').write_bytes(policy)
        (logs / f'seat-{seat}.log').write_text(
            f'PWD v=3 g=G.survive,G.farm,G.finish t={seat + 1} r=1 c=1 i=0,0,0 h=0 p=0 f=0\n'
            'PWD v=3 g=G.survive,G.farm,G.finish t=11 r=1 c=1 i=0,0,0 h=0 p=0 f=0\n')
    sb.dump(logs / 'job.json', {'seed': 1, 'ticks': 1, 'draft': 'open', 'oracle': True})
    sb.dump(logs / 'result.json', {'engine': POLYWORLD_COMMIT, 'bassy': BASSY_COMMIT,
        'config': {'seed': 1, 'max_ticks': 1, 'draft_mode': 'open'}, 'ticks': 11, 'draft_ticks': 10, 'battle_ticks': 1, 'outcome': 'time_limit',
        'heroes': [{'slot': seat, 'policy_source_sha1': source_sha1, 'vm_failed': False,
                    'decisions': 2} for seat in range(10)]})
    rows = [{'type': 'tick', 'tick': seat + 1, 'phase': 'draft', 'decision_order': [100 + seat], 'actions': []} for seat in range(10)]
    rows.append({'type': 'tick', 'tick': 11, 'phase': 'battle', 'decision_order': list(range(100, 110)), 'actions': []})
    (logs / 'trace.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    artifacts = ['job.json', 'result.json', 'trace.jsonl'] + [f'seat-{seat}.log' for seat in range(10)]
    sb.dump(logs / 'identity.json', {'engine': POLYWORLD_COMMIT, 'bassy': BASSY_COMMIT,
        'binary_sha256': 'a' * 64, 'binary_fingerprint': 'b' * 64,
        'native_build': native_fixture['gota_match'], 'seed': 1,
        'max_battle_ticks': 1, 'candidate_seats': list(range(5)),
        'seat_policy_sha256': [version['policy_sha256']] * 10, 'seat_policy_sha1': [source_sha1] * 10,
        'artifacts': {name: sb.digest(logs / name) for name in artifacts}})
    output = temporary / 'audit.json'
    argv = ['--build', order['build_id'], '--logs', str(logs), '--out', str(output)]
    assert goal_audit.main(argv) == 0
    report = sb.read(output)
    assert all(seat['coverage_status'] == 'complete' for seat in report['seats'])
    assert all(goal['claim_status'] == 'unmeasurable' for goal in report['goals'].values())
    # Updating a mutable map and its hash cannot override committed source truth.
    mapping['goals']['G.survive']['claim'] = 'invented claim'
    sb.dump(build / 'map.json', mapping)
    version['artifact_hashes']['map.json'] = sb.digest(build / 'map.json')
    sb.dump(build / 'version.json', version)
    with pytest.raises(pw_cli.UsageError, match='committed source: goals'):
        goal_audit.main(argv)


def test_chat_stream_usage_is_explicit_and_cumulative():
    events = [
        {'type': 'system', 'subtype': 'init', 'model': 'test/model', 'session_id': 'session-1'},
        {'type': 'text', 'text': 'done'},
        {'type': 'result', 'session_id': 'session-1', 'exit_code': 0,
         'tokens': {'input': 123, 'output': 45, 'total': 188, 'cache_read': 20, 'cache_write': 0}},
    ]
    stream = ''.join(json.dumps(event) + '\n' for event in events)
    receipt = sb.chat_stream_usage(stream, 'test/model')
    assert receipt['source'] == 'hermes-chat-stream-json'
    assert receipt['input_tokens'] == 123 and receipt['cache_read_tokens'] == 20
    assert receipt['records'] == events
    assert len(receipt['stream_sha256']) == 64
    assert 'estimated_cost_usd' not in receipt and 'api_calls' not in receipt


@pytest.mark.parametrize('mode', ['missing', 'malformed', 'model', 'session', 'exit', 'tokens', 'duplicate'])
def test_chat_stream_usage_rejects_invalid_receipts(mode):
    events = [
        {'type': 'system', 'subtype': 'init', 'model': 'test/model', 'session_id': 'session-1'},
        {'type': 'result', 'session_id': 'session-1', 'exit_code': 0,
         'tokens': {'input': 123, 'output': 45, 'total': 168, 'cache_read': 0, 'cache_write': 0}},
    ]
    if mode == 'missing': events.pop()
    if mode == 'model': events[0]['model'] = 'wrong/model'
    if mode == 'session': events[1]['session_id'] = 'wrong-session'
    if mode == 'exit': events[1]['exit_code'] = 1
    if mode == 'tokens': events[1]['tokens']['input'] = -1
    if mode == 'duplicate': events.append(events[-1])
    stream = 'not-json\n' if mode == 'malformed' else ''.join(json.dumps(event) + '\n' for event in events)
    with pytest.raises(ValueError, match='Hermes chat stream'):
        sb.chat_stream_usage(stream, 'test/model')


def test_compiler_child_isolates_outer_profile(generation, monkeypatch):
    order, stage = generation
    home = stage / 'isolated-home'
    monkeypatch.setenv('GOTA_COMPILER_HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', '/outer/profile')
    monkeypatch.setenv('HERMES_PROFILE', 'autoresearch')
    monkeypatch.setenv('HERMES_KANBAN_TASK', 'outer-task')
    original = sb.subprocess.run
    def isolated(command, **kwargs):
        if command[0] == sys.executable:
            env = kwargs['env']
            assert env['HERMES_HOME'] == str(home)
            assert env['HERMES_WRITE_SAFE_ROOT'] == str(kwargs['cwd'])
            assert 'HERMES_PROFILE' not in env
            assert 'HERMES_KANBAN_TASK' not in env
        return original(command, **kwargs)
    monkeypatch.setattr(sb.subprocess, 'run', isolated)
    sb.generate(order)


@pytest.mark.parametrize('repair,reject_repair,full_evaluation', [(False, False, False), (False, False, True), (True, False, True), (True, True, True)])
def test_real_binding_seed_from_archive_and_separate_worker(generation, monkeypatch, tmp_path, repair, reject_repair, full_evaluation):
    """Real source closure/prepare/generate/assemble/finalize; external gates and model fake only."""
    import importlib.util

    import gota_gates
    import gota_host

    from ide_research_core.softmax_tool.upload import _read_build

    _, _ = generation
    distribution = Path(__file__).resolve().parents[4]
    seed = distribution / 'games/gota/seed/strategy/STRATEGY.md'
    archive = tmp_path / 'archive/autoresearch'
    for original in sb.source_files(seed):
        destination = archive / original.relative_to(distribution)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    worker = tmp_path / 'worker'
    candidate = worker / 'strategy/candidates/E'
    candidate.mkdir(parents=True)
    source = candidate / 'STRATEGY.md'
    shutil.copyfile(seed, source)
    original_git = sb.git
    for args in (('init',), ('add', '.'), ('-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit', '-m', 'seed')):
        original_git(*args, root=worker)
    monkeypatch.setattr(sb, 'REPO', worker)
    monkeypatch.setattr(sb, 'git', lambda *args, root=worker: original_git(*args, root=root))
    lab = archive / 'games/gota/compiler'
    monkeypatch.setattr(sb, 'LAB', lab)
    monkeypatch.setattr(sb, 'COMPILER', lab / 'strategy/compiler')
    monkeypatch.setattr(gota_host, 'BASELINE', archive / 'tests/fixtures/gota/v13-live.bas')
    monkeypatch.setenv('FAKE_UNIT_ROOT', str(distribution / 'tests/fixtures/gota/v13_units'))
    monkeypatch.setenv('OPENROUTER_MODEL', 'test/openweight')
    commands, verified_policies, verified_farm_units = [], [], []
    def verify_fake(build_id, **kwargs):
        stage = sb.build_path(build_id, staged=True)
        episode = stage / 'verify/attempt-fake/episode'
        episode.mkdir(parents=True, exist_ok=True)
        verified_policies.append((stage / 'policy.bas').read_bytes())
        verified_farm_units.append((stage / 'units/C.farm.bas').read_bytes())
        (episode / 'results.json').write_text('{}')
        commands[:] = ['coworld', '--output-dir', str(episode)]
        gates = {f'G{i}': {'outcome': 'passed', 'passed': True, 'summary': {}} for i in (range(1, 6) if full_evaluation else (1,))}
        for name in (('G2', 'G5') if full_evaluation else ()):
            gates[name]['summary'] = {'episode_dir': str(episode), 'command': list(commands)}
        if repair and (len(verified_policies) == 1 or reject_repair):
            gates['G1'] = {'outcome': 'failed', 'passed': False, 'summary': {'diagnostic': 'fixture repair'}}
            monkeypatch.setenv('FAKE_MODE', 'repair_patch')
        sb.dump(stage / 'gates.json', gates)
        sb.dump(episode.parent / 'gates.json', gates)
        return gates
    monkeypatch.setattr(gota_gates, 'verify', verify_fake)
    # Establish the canonical module for restoration after this isolated archive test.
    importlib.import_module("pw_strategy")
    monkeypatch.delitem(sys.modules, 'pw_strategy')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location('archive_build_binding', distribution / 'games/gota/tools/build.py')
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    monkeypatch.setattr(binding, 'ROOT', archive)
    monkeypatch.setattr(binding, 'TOOLS', lab / 'tools')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'game': {'runnable': {'source_url':
        f'https://github.com/Metta-AI/polyworld/tree/{gota_host.POLYWORLD_COMMIT}/examples/gods_of_the_arena'}}}))
    out = tmp_path / 'separate-build-output'
    from games.gota.tools import engine_identity
    monkeypatch.setattr(engine_identity, 'provision', lambda identity: (identity, None, {}))
    result = binding.build(candidate, out, manifest, offline=True, full_evaluation=full_evaluation)
    if reject_repair:
        assert not result['ok'] and result['status'] == 'failed'
        assert len(verified_policies) == 3
        with pytest.raises(Exception, match='gates did not pass'):
            _read_build(out)
        return
    assert result['ok']
    assert len(verified_policies) == (2 if repair else 1)
    if repair:
        assert verified_policies[0] != verified_policies[1]
        assert (out / 'units/C.farm.bas').read_bytes() == verified_farm_units[0] == verified_farm_units[1]
    version, artifact = _read_build(out)
    assert version['source_commit'] == original_git('rev-parse', 'HEAD', root=worker)
    assert version['source_path'] == 'strategy/candidates/E/STRATEGY.md'
    assert version['strategy_sha256'] == sb.digest(source)
    assert version['policy_sha256'] == sb.digest(artifact)
    assert version['driver']['installed_commit'] is None
    assert str(lab / 'tools/strategy_build.py') in version['compiler']['installed_source_hashes']
    assert not (archive.parent / '.git').exists()
    surfaces = [result, version, sb.read(out / 'report.json'),
                {'gates': sb.read(out / 'gates.json')},
                {'gates': sb.read(out / 'verify/attempt-fake/gates.json')}]
    assert version['validation_scope'] == ('full' if full_evaluation else 'build')
    assert version['evaluation']['status'] == ('passed' if full_evaluation else 'not_run')
    for surface in surfaces:
        for name in (('G2', 'G5') if full_evaluation else ()):
            summary = surface['gates'][name]['summary']
            assert (out / summary['episode_dir'] / 'results.json').is_file()
            assert summary['command'] == commands
    assert not Path(commands[-1]).exists()
    sb.checked_build(out)  # All retained receipt bytes match artifact_hashes after both moves.


def test_repair_patches_current_round_and_preserves_other_generated_units(generation, monkeypatch):
    order, stage = generation
    sb.generate(order)
    original = (stage / 'units/K.position.bas').read_bytes()
    monkeypatch.setenv('FAKE_MODE', 'repair_patch')
    sb.generate(order, errors=[{'message': 'repair C.idle'}])
    assert (stage / 'units/C.idle.bas').read_text() == 'fake C.idle repaired'
    assert (stage / 'units/K.position.bas').read_bytes() == original
    assert len(list(stage.glob('hermes-usage-*.json'))) == 2
