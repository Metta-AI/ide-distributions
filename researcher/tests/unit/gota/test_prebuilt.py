"""Prebuilt freezes committed bytes, bypasses models, and reuses the real gates."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ide_research_core.softmax_tool.upload import _read_build

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from games.gota.tools import build, engine_identity, make_v13_live
from games.gota.tools.build_input import InputRejected
from games.gota.tools.native_build import fingerprint


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit(candidate):
    for args in (['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit', '-m', 'input']):
        subprocess.run(['git', '-C', str(candidate), *args], check=True, capture_output=True)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    directory = tmp_path / 'candidate'
    directory.mkdir()
    subprocess.run(['git', 'init', str(directory)], check=True, capture_output=True)
    artifact = directory / 'baseline.bas'
    artifact.write_bytes((ROOT / 'tests/fixtures/gota/v13-live.bas').read_bytes())
    identity = {'commit': 'a' * 40, 'coworld_version': 'test', 'source_url': 'test',
                'manifest_sha256': fingerprint({'test': True}), 'manifest': {'test': True}}
    provenance = {'schema': 'gota-prebuilt/1', 'artifact_sha256': sha(artifact),
                  'input': {'path': 'baseline.bas', 'sha256': sha(artifact)},
                  'transform': {'name': 'identity'}, 'engine': {k: v for k, v in identity.items() if k != 'manifest'}}
    artifact.with_suffix('.provenance.json').write_text(json.dumps(provenance))
    commit(directory)
    monkeypatch.setattr(build, 'validate_manifest', lambda *a, **kw: dict(identity))
    monkeypatch.setattr(engine_identity, 'provision', lambda value: (value, Path('/verified/probe'), {target: {'fingerprint': target} for target in ('gate_probe', 'gota_match')}))
    from games.gota.tools import native_build
    monkeypatch.setattr(native_build, 'validate', lambda binary, target, engine: {'fingerprint': target})
    for name in ('pw_strategy', 'pw_cli', 'strategy_build'):
        monkeypatch.setitem(sys.modules, name, None)  # Even importing a model/compiler is forbidden.
    monkeypatch.delenv('GOTA_HERMES_PYTHON', raising=False)
    monkeypatch.delenv('GOTA_TIRITH_BIN', raising=False)
    return directory, artifact


def fake_gates(monkeypatch, *, fault=None, blocking=False):
    from games.gota.gates import GateResult, g1_lint, g2_episode, g3_budget, g4_screen, g5_telemetry
    calls = []
    def gate(name):
        def run(artifact, context):
            calls.append(name)
            assert artifact.parent.name.startswith('.gota-build-')
            if name == fault:
                raise OSError('broken gate fixture')
            return GateResult('failed' if blocking and name == 'G1' else 'passed', {'measured': True})
        return run
    for name, module in zip(('G1', 'G2', 'G3', 'G4'), (g1_lint, g2_episode, g3_budget, g4_screen)):
        monkeypatch.setattr(module, 'run', gate(name))
    monkeypatch.setattr(g5_telemetry, 'run', lambda *a: pytest.fail('prebuilt has no compiler telemetry'))
    return calls


def test_prebuilt_exact_bytes_shared_reader_and_no_compiler(candidate, tmp_path, monkeypatch):
    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    out = tmp_path / 'build'
    result = build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['ok'] and calls == ['G1', 'G2', 'G3', 'G4']
    version, frozen = _read_build(out)
    assert frozen.read_bytes() == artifact.read_bytes()
    assert version['policy_sha256'] == sha(artifact)
    assert version['prebuilt'] == json.loads(artifact.with_suffix('.provenance.json').read_text())
    assert version['source_commit'] == subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()
    assert version['gates']['G5'] == {'applicable': False, 'passed': False, 'advisory': True,
                                     'summary': 'not applicable: prebuilt'}
    assert version['native_builds']['gate_probe']['fingerprint'] == 'gate_probe'
    assert all(version['gates'][name]['seconds'] >= 0 for name in ('G1', 'G2', 'G3', 'G4'))
    assert version['started'] <= version['finished']
    with pytest.raises(ValueError, match='immutable'):
        build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)


@pytest.mark.parametrize('boundary', ['before', 'G1', 'G2', 'G3', 'G4'])
def test_prebuilt_manifest_fault_stops_gates_and_retains_error(candidate, tmp_path, monkeypatch, boundary):
    from games.gota.gates import g1_lint, g2_episode, g3_budget, g4_screen
    from games.gota.tools import prebuilt
    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    if boundary == 'before':
        original_dump = prebuilt.dump
        def wrong_manifest(path, value):
            original_dump(path, {'wrong': True} if path.name == 'coworld_manifest.json' else value)
        monkeypatch.setattr(prebuilt, 'dump', wrong_manifest)
    else:
        module = dict(zip(('G1', 'G2', 'G3', 'G4'), (g1_lint, g2_episode, g3_budget, g4_screen)))[boundary]
        original_run = module.run
        def mutate(policy, context):
            result = original_run(policy, context)
            context.manifest.write_text(json.dumps({'wrong': True}))
            return result
        monkeypatch.setattr(module, 'run', mutate)
    out = tmp_path / 'failed-manifest'
    result = build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['status'] == 'errored'
    assert calls == ([] if boundary == 'before' else ['G1', 'G2', 'G3', 'G4'][:int(boundary[1])])
    assert result['error']['type'] == 'ValueError'
    assert 'manifest changed after engine resolution' in result['error']['message']
    for receipt in ('report.json', 'version.json'):
        assert json.loads((out / receipt).read_text())['status'] == 'errored'
    assert json.loads((out / 'report.json').read_text())['error'] == result['error']
    assert artifact.read_bytes() == (out / 'policy.bas').read_bytes()
    with pytest.raises(Exception, match='gates did not pass'):
        _read_build(out)


def test_prebuilt_episode_locator_survives_output_move(candidate, tmp_path, monkeypatch):
    from games.gota.gates import GateResult, g2_episode
    directory, artifact = candidate
    fake_gates(monkeypatch)
    original_command = []
    def episode(policy, context):
        evidence = context.work_dir / 'episode'
        evidence.mkdir()
        (evidence / 'results.json').write_text('{}')
        original_command.extend(['coworld', '--output-dir', str(evidence)])
        return GateResult('passed', {'episode_dir': str(evidence), 'command': list(original_command)})
    monkeypatch.setattr(g2_episode, 'run', episode)
    out = tmp_path / 'unrelated-output/build'
    result = build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    surfaces = [result, json.loads((out / 'version.json').read_text()),
                json.loads((out / 'report.json').read_text()), {'gates': json.loads((out / 'gates.json').read_text())}]
    for surface in surfaces:
        summary = surface['gates']['G2']['summary']
        assert (out / summary['episode_dir'] / 'results.json').is_file()
        assert summary['command'] == original_command
    assert not Path(original_command[-1]).exists()


@pytest.mark.parametrize('mutation,message', [('dirty', 'committed'), ('dirty_sidecar', 'committed'), ('untracked', 'committed'), ('provenance_hash', 'hash'),
                                               ('input_hash', 'hash'), ('engine', 'engine'),
                                               ('escape', 'candidate'), ('tool', 'transform')])
def test_prebuilt_rejects_bad_input_before_any_gate(candidate, tmp_path, monkeypatch, mutation, message):
    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    sidecar = artifact.with_suffix('.provenance.json')
    value = json.loads(sidecar.read_text())
    if mutation == 'dirty':
        artifact.write_text('changed')
    elif mutation == 'dirty_sidecar':
        sidecar.write_text('{}')
    elif mutation == 'untracked':
        subprocess.run(['git', '-C', str(directory), 'rm', '--cached', str(artifact)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(directory), '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                        'commit', '-m', 'remove artifact from HEAD'], check=True, capture_output=True)
    else:
        if mutation == 'provenance_hash':
            value['artifact_sha256'] = '0' * 64
        elif mutation == 'input_hash':
            value['input']['sha256'] = '0' * 64
        elif mutation == 'engine':
            value['engine']['commit'] = 'b' * 40
        elif mutation == 'escape':
            value['input']['path'] = '../outside.bas'
        elif mutation == 'tool':
            value['transform'] = {'name': 'unknown'}
        sidecar.write_text(json.dumps(value))
        commit(directory)
    with pytest.raises(InputRejected, match=message):
        build.build(directory, tmp_path / 'bad', offline=True, prebuilt=artifact, full_evaluation=True)
    assert calls == []


@pytest.mark.parametrize('fault,blocking,status', [('G4', False, 'errored'), (None, True, 'failed')])
def test_prebuilt_preserves_failure_and_reader_refuses(candidate, tmp_path, monkeypatch, fault, blocking, status):
    directory, artifact = candidate
    calls = fake_gates(monkeypatch, fault=fault, blocking=blocking)
    out = tmp_path / 'failed'
    result = build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['status'] == status and not result['ok']
    assert (out / 'report.json').is_file() and (out / 'report.md').is_file()
    with pytest.raises(Exception, match='gates did not pass'):
        _read_build(out)
    assert calls == (['G1', 'G2', 'G3', 'G4'] if fault else ['G1'])
    if fault:
        assert result['error']['type'] == 'OSError'
        assert 'broken gate fixture' in result['error']['traceback']


def test_generator_records_reproducible_committed_input_and_engine(tmp_path, monkeypatch):
    directory = tmp_path / 'candidate'
    directory.mkdir()
    subprocess.run(['git', 'init', str(directory)], check=True, capture_output=True)
    source = directory / 'submitted-v13.bas'
    source.write_bytes((ROOT / 'tests/fixtures/gota/submitted-v13.bas').read_bytes())
    commit(directory)
    identity = {'commit': 'a' * 40, 'manifest_sha256': 'm', 'coworld_version': 'test',
                'source_url': 'test', 'manifest': {'test': True}}
    monkeypatch.setattr(engine_identity, 'resolve', lambda *a, **kw: identity)
    output = directory / 'v13-live.bas'
    monkeypatch.setattr(sys, 'argv', ['make_v13_live', str(source), str(output), '--offline', '--manifest', str(tmp_path / 'manifest')])
    make_v13_live.main()
    provenance = json.loads(output.with_suffix('.provenance.json').read_text())
    assert output.read_bytes() == (ROOT / 'tests/fixtures/gota/v13-live.bas').read_bytes()
    assert provenance['input']['sha256'] == sha(source)
    assert provenance['artifact_sha256'] == sha(output)
    assert provenance['transform']['sha256'] == sha(Path(make_v13_live.__file__))
    assert provenance['engine'] == {k: v for k, v in identity.items() if k != 'manifest'}


def test_real_prebuilt_bootstrap_champion_with_recorded_episode_and_mock_screen(tmp_path, monkeypatch, prepare_native_tools):
    """Real native prebuilt -> researcher uploads and integrates -> orchestrator submits ->
    the first champion is published."""
    import shutil
    from dataclasses import replace

    from support.research_board import PLAYER, ResearchBoard
    from unit.softmax.test_softmax_cli import base_env, softmax

    from games.gota.gates import g2_episode, g3_budget, g4_screen
    from games.gota.gates.limits import POLYWORLD_COMMIT
    from games.gota.tools import native_build
    from ide_research_core import workspace
    from ide_research_core.champion_submission import CHAMPION_PATH, in_flight, latest_champion
    from ide_research_core.io import gitops
    from ide_research_core.softmax_tool.upload import policy_name

    board = ResearchBoard(tmp_path, seed_champion=False)
    (board.canonical / CHAMPION_PATH).write_text((ROOT / 'games/gota/seed/strategy/STRATEGY.md').read_text())
    with (board.canonical / '.gitignore').open('a') as ignore:
        ignore.write('.scratch/\n')
    gitops.commit_all(board.canonical, 'seed shipped GotA strategy')
    gitops.git(board.canonical, 'push', '-q', 'origin', 'main')
    worker = board.admit(mode='maintenance')
    worker.start()
    directory = worker.worktree / 'strategy/candidates/bootstrap'
    directory.mkdir(parents=True)
    source = directory / 'submitted-v13.bas'
    source.write_bytes((ROOT / 'games/gota/baselines/submitted-v13.bas').read_bytes())
    commit(directory)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'game': {'runnable': {'source_url':
        f'https://github.com/Metta-AI/polyworld/tree/{POLYWORLD_COMMIT}/examples/gods_of_the_arena'}}}))
    artifact = directory / 'v13-live.bas'
    monkeypatch.setattr(sys, 'argv', ['make_v13_live', str(source), str(artifact), '--offline', '--manifest', str(manifest)])
    make_v13_live.main()
    commit(directory)
    input_commit = gitops.head(worker.worktree)
    real_run = subprocess.run
    def recorded_process(argv, **kwargs):
        if argv[:4] == [sys.executable, '-m', 'ide_research_core', 'champion']:
            return real_run(argv, **kwargs)  # the orchestrator's submit, below
        if argv[:5] == [sys.executable, '-m', 'ide_research_core', 'softmax', 'upload-build']:
            assert kwargs['env']['RESEARCH_SOFTMAX'] == 'fake'
            assert kwargs['env']['RESEARCH_GAME'] == 'gota'
            assert argv[5:] == ['--name', 'opening-rush', '--build-dir', str(out)]
            return real_run(argv, **kwargs)
        if argv[0] != 'uv':
            assert Path(argv[0]).name in ('git', 'nim', f'gota_match-{POLYWORLD_COMMIT[:8]}', f'gate_probe-{POLYWORLD_COMMIT[:8]}'), argv
            return real_run(argv, **kwargs)
        assert 'coworld' in argv and 'run-episode' in argv
        episode = Path(argv[argv.index('--output-dir') + 1])
        fixture = ROOT / 'tests/fixtures/gota'
        (episode / 'logs').mkdir(parents=True)
        for seat in range(10):
            player = episode / 'players' / str(seat)
            player.mkdir(parents=True)
            shutil.copyfile(artifact, player / 'file')
            (episode / 'logs' / f'policy_agent_{seat}.log').write_text(
                (fixture / 'v13-live-telemetry.log').read_text() + f'Player slot {seat} completed.\n')
        shutil.copyfile(fixture / 'v13-live-game.log', episode / 'logs/game.stdout.log')
        shutil.copyfile(fixture / 'v13-live-results.json', episode / 'results.json')
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setenv('GOTA_G2_RUNNER', 'docker')
    monkeypatch.setattr(subprocess, 'run', recorded_process)
    real_budget = g3_budget.run
    monkeypatch.setattr(g3_budget, 'run', lambda policy, context: real_budget(
        policy, replace(context, seeds=(1,), max_ticks=3000)))
    screen_calls = []
    def measured_screen(policy, context, seed, baseline, blue):
        screen_calls.append((seed, blue))
        return {'outcome': 'passed', 'runtime': {'ticks': 31200, 'scores': [1] * 5 + [0] * 5,
                                                'total_xp': [10] * 10}}
    monkeypatch.setattr(g4_screen, 'measure_many', lambda artifact, context, matches: [measured_screen(artifact, context, *match) for match in matches])
    # Even Python model imports are forbidden on this path.
    for name in ('pw_strategy', 'pw_cli', 'strategy_build'):
        monkeypatch.setitem(sys.modules, name, None)
    out = worker.worktree / '.scratch/build/champion'
    result = build.build(directory, out, manifest, offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['ok'], result
    version, frozen = _read_build(out)
    assert frozen.read_bytes() == artifact.read_bytes()
    assert version['gates']['G1']['summary']['native_build']['fingerprint'] == version['native_builds']['gate_probe']['fingerprint']
    assert version['gates']['G3']['summary']['runtime'][0]['decisions'] > 0
    assert version['gates']['G2']['summary']['scripts_active'] == 10
    assert len(screen_calls) == 20
    assert version['engine']['native_fingerprints']['gate_probe'] == version['native_builds']['gate_probe']['fingerprint']
    assert g2_episode.inspect_episode(frozen, out / 'verify/episode')['scripts_active'] == 10
    assert native_build.validate(native_build.TOOLS / f'bin/gate_probe-{POLYWORLD_COMMIT[:8]}',
                                 'gate_probe', POLYWORLD_COMMIT) == version['native_builds']['gate_probe']

    # A14 has no experiment, frozen design, evaluation outcome or decision. The researcher's
    # bootstrap card uploads the starter build and integrates the version record.
    env = base_env(board, worker, RESEARCH_GAME='gota')
    assert worker.store.find('experiment') == []
    code, response = softmax(env, 'upload-build', '--name', 'opening-rush', '--build-dir', str(out))
    assert code == 0, response
    data = response['data']
    assert data['version_id'].startswith('V-')
    assert data['platform_policy_version_id'].startswith('pv_')
    assert data['version_id'] != data['platform_policy_version_id']
    meta = worker.store.load(data['version_id'])[0]
    assert meta['experiment'] is None and meta['player_id'] == PLAYER
    assert meta['policy_name'] == policy_name('opening-rush', PLAYER)
    assert meta['artifact_sha256'] == meta['strategy_sha256'] == sha(artifact)
    assert meta['source_commit'] == input_commit
    assert meta['build']['source_path'] == str(artifact.relative_to(worker.worktree))
    assert meta['build']['source_hashes'] == version['source_hashes']
    assert meta['build']['source_hashes'][str(source.relative_to(worker.worktree))] == sha(source)
    assert meta['build']['compiler']['mode'] == 'prebuilt'
    assert meta['build']['compiler']['prebuilt_provenance'] == version['prebuilt']
    assert meta['build']['engine'] == version['engine']
    assert meta['gates'] == version['gates']
    [uploaded] = worker.store.find('version_event', version=data['version_id'], kind='uploaded')
    assert worker.store.load(uploaded)[0]['data']['policy_version_id'] == data['platform_policy_version_id']
    worker.write_journal()
    workspace.integrate(board.canonical, branch=workspace.stage(worker.worktree)["branch"])

    # The orchestrator submits it as the first champion, then reports until it is published.
    league = 'league_3c60897b-25cf-4b37-9d1a-8554c1198f28'
    champion_env = base_env(board, RESEARCH_GAME='gota')

    def champion(*argv):
        run = subprocess.run([sys.executable, '-m', 'ide_research_core', 'champion', *argv, '--league', league],
                             capture_output=True, text=True, env=champion_env, check=False)
        return run.returncode, json.loads(run.stdout)

    code, response = champion('bootstrap', '--version', data['version_id'])
    assert code == 0, response
    current = in_flight(board.canonical)
    assert current.kind == 'bootstrap' and current.experiment is None
    assert current.stage == 'submitted' and current.version_id == data['version_id']
    [submission] = board.sm.submissions(PLAYER)
    assert submission['league_id'] == league
    assert submission['policy_version_id'] == data['platform_policy_version_id']
    [submitted] = board.store.find('version_event', version=data['version_id'], kind='submitted')
    assert board.store.load(submitted)[0]['data']['champion_submission_id'] == current.id
    fake_state = json.loads((board.canonical / '.supervisor/runtime/fake_softmax.json').read_text())
    assert list(fake_state['uploads']) == [data['platform_policy_version_id']]
    assert fake_state['xp'] == {} and board.store.find('experiment') == []

    code, refused = champion('bootstrap', '--version', data['version_id'])
    assert code == 1 and refused['error']['reason'] == 'submission_in_flight'
    code, status = champion('status')
    assert code == 0 and status['data']['conflict'] is None, status
    assert latest_champion(board.canonical).platform_policy_version_id == data['platform_policy_version_id']
    assert board.sm.leaderboard_champion(PLAYER, league) == data['platform_policy_version_id']
    assert (board.canonical / CHAMPION_PATH).read_bytes() == (ROOT / 'games/gota/seed/strategy/STRATEGY.md').read_bytes()
    assert board.store.load(data['version_id'])[0] == meta
    assert len(board.sm.submissions(PLAYER)) == 1
    assert not gitops.git(board.canonical, 'ls-files', '.scratch')
    code, refused = champion('bootstrap', '--version', data['version_id'])
    assert code == 1 and refused['error']['reason'] == 'champion_exists'


def test_shared_upload_rejects_tampered_prebuilt_hash(candidate, tmp_path, monkeypatch):
    from ide_research_core.softmax_tool import upload
    directory, artifact = candidate
    fake_gates(monkeypatch)
    out = tmp_path / 'build'
    build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    (out / 'policy.bas').write_text('tampered frozen bytes')
    monkeypatch.setattr(upload, 'worker_store', lambda ctx: object())
    # Refusal happens before any record or platform call, with no fake identity success.
    with pytest.raises(Exception, match='artifact sha256'):
        upload.upload(SimpleNamespace(hermes_home=tmp_path), None, out, 'E-test', 'ply_fake', 'opening-rush')


def test_prebuilt_retains_post_gate_source_fault(candidate, tmp_path, monkeypatch):
    from games.gota.gates import GateResult, g4_screen
    directory, artifact = candidate
    fake_gates(monkeypatch)
    def mutate(policy, context):
        artifact.write_text('dirty during gate')
        return GateResult('passed', {'measured': True})
    monkeypatch.setattr(g4_screen, 'run', mutate)
    out = tmp_path / 'build'
    result = build.build(directory, out, offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['status'] == 'errored'
    assert result['gates']['verification']['summary']['error']['type'] == 'InputRejected'
    assert (out / 'report.json').is_file()
    with pytest.raises(Exception, match='gates did not pass'):
        _read_build(out)


def test_runtime_baseline_is_exact_distributed_fixture():
    original = ROOT / 'tests/fixtures/gota/submitted-v13.bas'
    runtime = ROOT / 'games/gota/baselines/submitted-v13.bas'
    assert runtime.read_bytes() == original.read_bytes()
    assert sha(runtime) == 'd0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1'


def test_shared_upload_preserves_prebuilt_provenance_in_existing_experiment(candidate, tmp_path, monkeypatch):
    """Use the existing record/transport fixture; no real platform mutation occurs."""
    monkeypatch.syspath_prepend(str(ROOT / 'tests/unit'))
    from unit.softmax.test_upload import Workspace

    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    shared = tmp_path / 'shared'
    shared.mkdir()
    workspace = Workspace(shared)
    build.build(directory, workspace.build, offline=True, prebuilt=artifact, full_evaluation=True)
    from ide_research_core.softmax_tool.upload import checked_build
    receipt, frozen, artifact_hash = checked_build(workspace.build)
    assert receipt['status'] == 'passed'
    assert set(receipt['gates']) == {'G1', 'G2', 'G3', 'G4', 'G5'}
    assert all(receipt['gates'][name]['passed'] for name in ('G1', 'G2', 'G3', 'G4'))
    assert receipt['gates']['G5']['applicable'] is False
    assert frozen.read_bytes() == artifact.read_bytes() and artifact_hash == sha(artifact)
    version_id = workspace.run()
    assert calls == ['G1', 'G2', 'G3', 'G4']  # Build checks once; upload reuses their receipts.
    meta = workspace.store.load(version_id)[0]
    assert meta['artifact_sha256'] == sha(artifact)
    provenance = json.loads(artifact.with_suffix('.provenance.json').read_text())
    assert meta['build']['compiler']['prebuilt_provenance'] == provenance
    assert meta['gates']['G5']['passed'] is False
    assert workspace.meta()['candidate_binding']['version_id'] == version_id
    assert len(workspace.uploads()) == 1


def test_generator_from_installed_distribution_with_separate_committed_worker(tmp_path):
    """No test-fixture paths or writable distribution are needed at runtime."""
    import shutil
    installed = tmp_path / 'distribution'
    shutil.copytree(ROOT / 'games/gota', installed / 'games/gota',
                    ignore=shutil.ignore_patterns('compiled', 'tmp', 'bin', '.cache', '__pycache__'))
    directory = tmp_path / 'worker/candidate'
    directory.mkdir(parents=True)
    subprocess.run(['git', 'init', str(directory)], check=True, capture_output=True)
    original = directory / 'submitted-v13.bas'
    shutil.copyfile(installed / 'games/gota/baselines/submitted-v13.bas', original)
    commit(directory)
    from games.gota.gates.limits import POLYWORLD_COMMIT
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'game': {'runnable': {'source_url':
        f'https://github.com/Metta-AI/polyworld/tree/{POLYWORLD_COMMIT}/examples/gods_of_the_arena'}}}))
    output = directory / 'v13-live.bas'
    result = subprocess.run([sys.executable, str(installed / 'games/gota/tools/make_v13_live.py'),
                             str(original), str(output), '--offline', '--manifest', str(manifest)],
                            cwd=directory, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['artifact_sha256'] == '1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c'
    assert not (installed / 'tests').exists()
    sidecar = output.with_suffix('.provenance.json')
    before = output.read_bytes(), sidecar.read_bytes()
    repeated = subprocess.run([sys.executable, str(installed / 'games/gota/tools/make_v13_live.py'),
                               str(original), str(output), '--offline', '--manifest', str(manifest)],
                              cwd=directory, check=False, capture_output=True, text=True)
    assert repeated.returncode != 0 and 'must not exist' in repeated.stderr
    assert before == (output.read_bytes(), sidecar.read_bytes())


def test_prebuilt_rejects_wrong_mechanical_tool_hash(candidate, tmp_path, monkeypatch):
    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    original = directory / 'submitted-v13.bas'
    original.write_bytes((ROOT / 'tests/fixtures/gota/submitted-v13.bas').read_bytes())
    sidecar = artifact.with_suffix('.provenance.json')
    provenance = json.loads(sidecar.read_text())
    provenance['input'] = {'path': original.name, 'sha256': sha(original)}
    provenance['transform'] = {'name': 'make_v13_live.py', 'sha256': '0' * 64}
    sidecar.write_text(json.dumps(provenance))
    commit(directory)
    with pytest.raises(ValueError, match='transform tool hash'):
        build.build(directory, tmp_path / 'bad', offline=True, prebuilt=artifact, full_evaluation=True)
    assert not calls


def test_cli_prebuilt_input_rejection_retains_nonuploadable_report(candidate, tmp_path, monkeypatch, capsys):
    directory, artifact = candidate
    calls = fake_gates(monkeypatch)
    artifact.write_text('dirty input')
    out = tmp_path / 'errored'
    monkeypatch.setattr(sys, 'argv', ['build', str(directory), '--out', str(out),
                                    '--prebuilt', str(artifact), '--offline', '--json'])
    assert build.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'rejected' and not result['ok']
    assert result['error']['reason'] == 'invalid_input'
    assert not calls and not (out / 'policy.bas').exists()
    assert (out / 'report.json').is_file() and (out / 'report.md').is_file()
    with pytest.raises(Exception, match='names no artifact'):
        _read_build(out)


def test_starter_policy_contract_matches_the_upload_only_bootstrap():
    game = (ROOT / 'games/gota/GAME.md').read_text()
    assert '\n## Starter policy\n' in game
    section = game.split('\n## Starter policy\n', 1)[1].split('\n## ', 1)[0]
    commands = ('make_v13_live.py', 'tools/build.py', 'upload_build')
    positions = [section.index(command) for command in commands]
    assert positions == sorted(positions)
    assert '-m games.gota.gates' not in section
    assert 'Local validation' in section
    assert 'strategy/candidates/bootstrap' in section
    assert '.scratch/build/champion' in section
    assert 'git -C "$CARD_WORKTREE"' in section
    assert '"$RESEARCH_PYTHON"' in section and 'cd "$RESEARCH_HOME"' in section
    assert 'build-evidence/<build-id>' in section
    assert 'unevaluated' in section and 'bootstrap_champion' in section
    assert '/workspace/versions/' not in section
    skill = (ROOT / 'skills/gota/eval-design/SKILL.md').read_text()
    assert 'coordinator uploads' in skill
    assert 'strategy/candidates/bootstrap' in skill and '.scratch/build/champion' in skill
    assert 'stage' in skill and 'coordinator uploads and submits' in skill
    for path in (ROOT / 'skills/gota').glob('*/SKILL.md'):
        assert 'upload-baseline' not in path.read_text()
        assert 'handoff is pending' not in path.read_text()
    assert 'handoff is pending' not in game


def test_completed_build_validation_contract_separates_evaluation():
    game = (ROOT / 'games/gota/GAME.md').read_text()
    contract = game.split('## Local validation\n', 1)[1].split('\n## ', 1)[0]
    for requirement in ('status: passed', 'validation_scope: build', 'not_run',
                        '--full-evaluation', 'G1–G3', 'G4/G5', 'evaluation.json'):
        assert requirement in contract
    for name in ('build-upload', 'candidate'):
        skill = (ROOT / f'skills/autoresearch/{name}/SKILL.md').read_text()
        assert 'not_run' in skill
        assert 'local_check' in skill



def test_wrong_sidecar_name_is_rejected_then_corrected(candidate, tmp_path, monkeypatch, capsys):
    directory, artifact = candidate
    sidecar = artifact.with_suffix('.provenance.json')
    mistaken = artifact.with_name(artifact.name + '.provenance.json')
    sidecar.rename(mistaken)
    commit(directory)
    calls = fake_gates(monkeypatch)
    original_provision = engine_identity.provision
    monkeypatch.setattr(engine_identity, 'provision', lambda *_: pytest.fail('rejected input reached native setup'))
    rejected = tmp_path / 'rejected'
    argv = ['build', str(directory), '--prebuilt', str(artifact), '--offline', '--json', '--out']
    monkeypatch.setattr(sys, 'argv', [*argv, str(rejected)])
    assert build.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'rejected'
    assert result['error']['reason'] == 'invalid_input'
    assert result['error']['details']['path'] == str(sidecar)
    assert 'policy.provenance.json' in result['error']['details']['action']
    assert 'traceback' not in result['error']
    assert not calls and not (rejected / 'policy.bas').exists()
    with pytest.raises(Exception, match='names no artifact'):
        _read_build(rejected)
    report = (rejected / 'report.json').read_bytes()
    mistaken.rename(sidecar)
    commit(directory)
    monkeypatch.setattr(engine_identity, 'provision', original_provision)
    monkeypatch.setattr(sys, 'argv', [*argv, str(tmp_path / 'corrected')])
    assert build.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'passed'
    assert calls == ['G1']
    from ide_research_core.softmax_tool.upload import checked_build
    version, frozen, checked_hash = checked_build(tmp_path / 'corrected')
    assert checked_hash == sha(artifact) == sha(frozen)
    assert version['validation_scope'] == 'build'
    assert version['evaluation'] == {'status': 'not_run'}
    assert (rejected / 'report.json').read_bytes() == report


@pytest.mark.parametrize('fault', [ValueError('broken native receipt'), KeyError('broken invariant'), RuntimeError('engine fault')])
def test_native_fault_is_not_an_input_rejection(candidate, tmp_path, monkeypatch, capsys, fault):
    directory, artifact = candidate
    def fail(_):
        raise fault
    monkeypatch.setattr(engine_identity, 'provision', fail)
    calls = fake_gates(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['build', str(directory), '--prebuilt', str(artifact),
                                    '--out', str(tmp_path / 'errored'), '--offline', '--json'])
    assert build.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'errored' and 'traceback' in result['error']
    assert not calls


def test_post_gate_input_rejection_remains_integrity_fault(candidate, tmp_path, monkeypatch):
    directory, artifact = candidate
    from games.gota.gates import GateResult, g1_lint
    fake_gates(monkeypatch)
    def remove_input(*_):
        artifact.with_suffix('.provenance.json').unlink()
        return GateResult('passed', {})
    monkeypatch.setattr(g1_lint, 'run', remove_input)
    result = build.build(directory, tmp_path / 'changed', offline=True, prebuilt=artifact, full_evaluation=True)
    assert result['status'] == 'errored'
    assert result['gates']['verification']['outcome'] == 'errored'
    with pytest.raises(Exception, match='gates did not pass'):
        _read_build(tmp_path / 'changed')


def test_git_storage_failure_is_not_uncommitted_input(candidate, tmp_path, monkeypatch, capsys):
    directory, artifact = candidate
    original = subprocess.check_output
    def broken_tree(argv, **kwargs):
        if 'ls-tree' in argv:
            raise subprocess.CalledProcessError(128, argv, stderr=b'object database unavailable')
        return original(argv, **kwargs)
    monkeypatch.setattr(subprocess, 'check_output', broken_tree)
    calls = fake_gates(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['build', str(directory), '--prebuilt', str(artifact),
                                    '--out', str(tmp_path / 'errored'), '--offline', '--json'])
    assert build.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'errored'
    assert result['error']['type'] == 'CalledProcessError'
    assert not calls
