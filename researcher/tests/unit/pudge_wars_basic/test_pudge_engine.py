"""Live-engine tracking (mocked API, never the network), knowledge freshness, and hosted_logs.py
against a fake shared CLI."""
import json
import os
import subprocess
import sys
import textwrap
import urllib.error

import pytest
from support.pudge_wars_basic import FIXTURES, ROOT, TOOLS, fixture

LIVE = json.loads((FIXTURES / 'live_engine.json').read_text())
NEW_COMMIT = 'd4b3cca11444d49ff896f8b806c0dcd296c01c84'


@pytest.fixture
def isolated(tools, tmp_path, monkeypatch):
    """engine.py with its caches in tmp_path and no override record."""
    monkeypatch.delenv('PUDGE_LIVE_ENGINE', raising=False)
    monkeypatch.setattr(tools.engine, 'LIVE_CACHE', tmp_path / 'live-engine.json')
    monkeypatch.setattr(tools.engine, 'COWORLD_CACHE', tmp_path / 'coworlds')
    return tools.engine


def api(version='0.1.9', commit=NEW_COMMIT, coworld_id='cow_11111111-2222-3333-4444-555555555555'):
    manifest = json.loads(json.dumps(LIVE['manifest']))
    manifest['game']['runnable']['source_url'] = f'https://github.com/Metta-AI/polyworld-pudge-wars/tree/{commit}'
    calls = []

    def fetch(url, timeout=15):
        calls.append(url)
        if '/leagues/' in url:
            return {'id': LIVE['league_id'], 'game': {'canonical_coworld_id': coworld_id}}
        assert url.endswith(f'/coworlds/{coworld_id}')
        return {'id': coworld_id, 'version': version, 'manifest': manifest, 'manifest_hash': 'sha256:abc'}

    return fetch, calls


def offline_fetch(url, timeout=15):
    raise urllib.error.URLError('no network in tests')


def test_live_lookup_follows_league_to_coworld_and_caches(isolated):
    fetch, calls = api()
    record = isolated.resolve_live(fetch=fetch, clock=lambda: 1000.0)
    assert (record['live'], record['source'], record['version'], record['commit']) == (True, 'api', '0.1.9', NEW_COMMIT)
    assert record['repo'] == 'https://github.com/Metta-AI/polyworld-pudge-wars.git'
    assert len(calls) == 2
    again = isolated.resolve_live(fetch=fetch, clock=lambda: 1000.0 + 3599)
    assert again['source'] == 'cache' and len(calls) == 2
    expired = isolated.resolve_live(fetch=fetch, clock=lambda: 1000.0 + 3601)
    assert expired['source'] == 'api' and len(calls) == 3  # league re-read; the Coworld record is immutable


def test_unreachable_api_uses_stale_cache_then_fallback_with_warnings(isolated):
    fetch, _ = api()
    isolated.resolve_live(fetch=fetch, clock=lambda: 0.0)
    stale = isolated.resolve_live(fetch=offline_fetch, clock=lambda: 10_000.0)
    assert (stale['live'], stale['stale_risk'], stale['source']) == (False, True, 'stale-cache')
    assert 'newer version' in stale['warning'] and stale['commit'] == NEW_COMMIT
    isolated.LIVE_CACHE.unlink()
    fallback = isolated.resolve_live(fetch=offline_fetch)
    assert (fallback['live'], fallback['source']) == (False, 'fallback')
    assert fallback['commit'] == LIVE['commit'] and 'tools/engine.env' in fallback['warning']


def test_offline_mode_never_fetches(isolated):
    def explode(url, timeout=15):
        raise AssertionError('offline mode fetched')
    record = isolated.resolve_live(offline=True, fetch=explode)
    assert record['source'] == 'fallback' and record['live'] is False


def test_knowledge_and_starter_freshness(tools):
    record = {**LIVE, 'commit': NEW_COMMIT, 'version': '0.1.9'}
    knowledge = tools.engine.knowledge(record)
    assert knowledge['current'] is False
    assert (f'kb renew --game-repo <clone> --old {LIVE["commit"]} --new {NEW_COMMIT} --version 0.1.9'
            in knowledge['message'])
    assert tools.engine.knowledge(LIVE)['current'] is True
    changed = tools.engine.starters({**LIVE, 'players': {**LIVE['players'], 'base': '0' * 64}})
    assert changed['current'] is False and changed['changed'][0]['id'] == 'base'
    assert tools.engine.starters(LIVE) == {'current': True, 'changed': []}


def test_stale_engine_warns_in_every_envelope(tmp_path):
    record = {**LIVE, 'commit': NEW_COMMIT, 'version': '0.1.9', 'live': False,
              'warning': 'the live engine lookup failed (test)'}
    path = tmp_path / 'engine.json'
    path.write_text(json.dumps(record))
    completed = subprocess.run([sys.executable, str(TOOLS / 'evaluate.py'), '--results',
                                str(fixture('hosted_west_win.results.json')), '--json'],
                               capture_output=True, text=True, check=False, cwd=ROOT,
                               env={**os.environ, 'PUDGE_LIVE_ENGINE': str(path)})
    envelope = json.loads(completed.stdout)
    assert envelope['ok'] is True
    assert envelope['engine']['live'] is False and envelope['engine']['stale_risk'] is True
    assert envelope['knowledge']['current'] is False
    assert any('lookup failed' in warning for warning in envelope['warnings'])
    assert any('kb renew' in warning for warning in envelope['warnings'])
    assert 'warning:' in completed.stderr


def test_episode_metadata_picks_the_replay_engine(tools, tmp_path):
    (tmp_path / 'episode.json').write_text(json.dumps({'engine_commit': NEW_COMMIT}))
    assert tools.engine.episode_engine(tmp_path) == (NEW_COMMIT, 'episode.json engine_commit')
    (tmp_path / 'episode.json').write_text(json.dumps({'coworld_id': LIVE['coworld_id']}))
    commit, how = tools.engine.episode_engine(tmp_path)
    assert commit == LIVE['commit'] and LIVE['coworld_id'] in how
    (tmp_path / 'episode.json').unlink()
    assert tools.engine.replay_commit(tmp_path / 'replay')[1].startswith('live engine')


def test_source_url_must_pin_a_commit(tools):
    manifest = json.loads(json.dumps(LIVE['manifest']))
    manifest['game']['runnable']['source_url'] = 'https://github.com/Metta-AI/polyworld-pudge-wars'
    with pytest.raises(ValueError, match='does not pin'):
        tools.engine.record_from_coworld({'manifest': manifest})


FAKE_CLI = textwrap.dedent('''\
    #!{python}
    import json, os, pathlib, sys
    log = pathlib.Path(os.environ["FAKE_LOG"])
    with log.open("a") as stream:
        stream.write(json.dumps(sys.argv[1:]) + "\\n")
    assert sys.argv[1:4] == ["softmax", "exec", "--"] and sys.argv[4] == "coworld", sys.argv
    args = sys.argv[5:]
    results = pathlib.Path(os.environ["FAKE_RESULTS"]).read_text()
    pv = "a4bb470c-be97-4f7c-acfa-6dfe79b1c3ee"
    def value(flag):
        return pathlib.Path(args[args.index(flag) + 1])
    if args[0] == "episodes":
        print(json.dumps({{"id": args[1], "coworld_id": "{coworld}", "coworld_version": "0.1.8",
                          "replay_url": None,
                          "participants": [{{"position": slot, "policy_version_id": pv if slot == 1 else f"other-{{slot}}"}}
                                           for slot in range(6)]}}))
    elif args[0] == "episode-logs":
        directory = value("-d"); directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{{args[1]}}-policy_agent_1.log").write_text("Player slot 1 started.\\ntick 1\\n")
        (directory / f"{{args[1]}}-policy_agent_1.zip").write_bytes(b"PK\\x05\\x06" + bytes(18))
    elif args[0] == "episode-results":
        value("-o").write_text(results)
    elif args[0] == "episode-annotations":
        if args[2] != pv:
            sys.exit(1)
        value("-o").write_text(json.dumps({{"schema_version": 1, "time": 1, "kind": "k", "function": "f", "args": {{}}}}) + "\\n")
    elif args[0] == "xp-request":
        directory = value("-d"); directory.mkdir(parents=True, exist_ok=True)
        receipts = []
        for ereq in ("ereq_00000000-0000-0000-0000-000000000001", "ereq_00000000-0000-0000-0000-000000000002"):
            for name, data in (("policy_agent_1.log", "tick 1\\n"), ("results.json", results), ("replay", "R")):
                folder = directory / (ereq[-1] + name.replace(".", "_")); folder.mkdir()
                (folder / name).write_text(data)
                receipts.append({{"kind": "artifact", "state": "completed", "episode_request_id": ereq,
                                 "artifact_id": ereq + name, "filename": name,
                                 "path": str((folder / name).relative_to(directory))}})
        (directory / "manifest.jsonl").write_text("".join(json.dumps(r) + "\\n" for r in receipts))
    else:
        sys.exit(f"unexpected {{args}}")
''')


@pytest.fixture
def fake_cli(tmp_path):
    cli = tmp_path / 'research'
    cli.write_text(FAKE_CLI.format(python=sys.executable, coworld=LIVE['coworld_id']))
    cli.chmod(0o755)
    env = {**os.environ, 'RESEARCH_CLI': str(cli), 'FAKE_LOG': str(tmp_path / 'calls.jsonl'),
           'FAKE_RESULTS': str(fixture('hosted_west_win.results.json'))}
    return env, tmp_path / 'calls.jsonl'


def hosted_logs(env, *args):
    return subprocess.run([sys.executable, str(TOOLS / 'hosted_logs.py'), *map(str, args), '--json'],
                          capture_output=True, text=True, check=False, cwd=ROOT, env=env)


def test_hosted_logs_episode_layout(fake_cli, tmp_path):
    env, calls = fake_cli
    ereq = 'ereq_1f9ac57f-150f-4585-8dac-245f0f0856c8'
    completed = hosted_logs(env, '--episode', ereq, '--dest', tmp_path / 'out')
    assert completed.returncode == 0, completed.stderr
    [episode] = json.loads(completed.stdout)['data']['episodes']
    assert episode['our_slots'] == [1] and episode['annotations'] == {'1': 'downloaded'}
    folder = tmp_path / 'out' / ereq
    assert (folder / 'players/1/log.txt').read_text().startswith('Player slot 1 started.')
    assert (folder / 'players/1/annotations.jsonl').is_file() and (folder / 'players/1/artifact.zip').is_file()
    assert json.loads((folder / 'episode.json').read_text())['coworld_id'] == LIVE['coworld_id']
    assert (folder / 'results.json').read_text() == fixture('hosted_west_win.results.json').read_text()
    commands = [json.loads(line)[4:] for line in calls.read_text().splitlines()]
    assert ['episode-logs', ereq, '--mine', '-d', str(folder / '.downloads')] in commands
    words = {word for command in commands for word in command}
    assert not words & {'login', 'logout', 'player', 'token', '--server'}


def test_hosted_logs_xp_layout(fake_cli, tmp_path):
    env, _ = fake_cli
    completed = hosted_logs(env, '--xp', 'xreq_00000000-0000-0000-0000-00000000000a', '--dest', tmp_path / 'xp')
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)['data']
    assert data['with_logs'] == 2
    for episode in data['episodes']:
        folder = tmp_path / 'xp' / episode['episode']
        assert (folder / 'players/1/log.txt').read_text() == 'tick 1\n'
        assert (folder / 'results.json').is_file() and (folder / 'replay').is_file()


def test_hosted_logs_needs_research_cli_and_valid_ids(tmp_path, monkeypatch):
    env = {key: value for key, value in os.environ.items() if key != 'RESEARCH_CLI'}
    missing = hosted_logs(env, '--episode', 'ereq_1f9ac57f-150f-4585-8dac-245f0f0856c8', '--dest', tmp_path)
    assert missing.returncode == 2 and 'RESEARCH_CLI' in missing.stdout
    bad = hosted_logs({**env, 'RESEARCH_CLI': '/bin/false'}, '--xp', 'nope', '--dest', tmp_path)
    assert bad.returncode == 2 and 'xreq_' in bad.stdout
