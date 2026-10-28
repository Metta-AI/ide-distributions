"""Native tools against a real local platform with seeded owner evidence.

Set HERMES_PYTHON and SOFTMAX_EVIDENCE_SYSTEM_FIXTURE to run. The private JSON fixture
names server, token, player_id, subject_player_id, policy_version_id, episode_id,
coaching_session_id, analysis_id, policy_sha256 and video_sha256. The local stack
owns its seeded records and objects; no provider, storage or authorization is mocked.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.hermes_python import hermes_python


@pytest.mark.hermes
@pytest.mark.skipif(
    not os.environ.get("SOFTMAX_EVIDENCE_SYSTEM_FIXTURE"), reason="Requires a real local evidence fixture"
)
def test_native_evidence_discovery_and_verified_downloads(tmp_path):
    profile = tmp_path / "orchestrator"
    shutil.copytree(
        Path(__file__).resolve().parents[1], profile,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache"),
    )
    fixture = Path(os.environ["SOFTMAX_EVIDENCE_SYSTEM_FIXTURE"])
    code = r'''
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

fixture = json.loads(Path(sys.argv[1]).read_text())
assert urlparse(fixture['server']).hostname in {'localhost', '127.0.0.1', '::1'}
profile = Path(sys.argv[2])
secret = profile / '.env'
secret.write_text(
    f"SOFTMAX_PLAYER_CREDENTIAL={fixture['token']}\n"
    f"COGAMES_API_URL={fixture['server']}\n"
    f"RESEARCH_PLAYER_ID={fixture['player_id']}\n"
)
secret.chmod(0o600)
from hermes_cli.plugins import get_plugin_manager
from tools.registry import registry
manager = get_plugin_manager()
manager.discover_and_load()
plugin = next(p for p in manager.list_plugins() if p['name'] == 'orchestrator')
assert not plugin['error'], plugin
calls = [
    ('coaching_sessions', {'limit': 1}),
    ('coaching_session', {'coaching_session_id': fixture['coaching_session_id']}),
    ('coaching_analysis', {'coaching_session_id': fixture['coaching_session_id'], 'analysis_id': fixture['analysis_id']}),
    ('coaching_recording', {'coaching_session_id': fixture['coaching_session_id']}),
    ('policy_versions', {'player_id': fixture['subject_player_id']}),
    ('policy_file', {'policy_version_id': fixture['policy_version_id']}),
    ('policy_episodes', {'policy_version_id': fixture['policy_version_id']}),
    ('episode', {'episode_id': fixture['episode_id']}),
    ('episode_replay', {'episode_id': fixture['episode_id']}),
]
receipts = {}
for name, arguments in calls:
    assert registry.get_schema(name)['name'] == name
    output = json.loads(registry.dispatch(name, arguments))
    assert output['ok'], (name, output)
    receipt = output['data']
    content = Path(receipt['path']).read_bytes()
    assert hashlib.sha256(content).hexdigest() == receipt['sha256']
    assert fixture['token'].encode() not in content
    receipts[name] = receipt
assert receipts['coaching_recording']['sha256'] == fixture['video_sha256']
assert receipts['policy_file']['sha256'] == fixture['policy_sha256']
with zipfile.ZipFile(receipts['episode_replay']['path']) as archive:
    assert json.loads(archive.read('results.json')) == {'score': 42}
missing = json.loads(registry.dispatch('coaching_session', {
    'coaching_session_id': 'csn_00000000-0000-4000-8000-000000000000',
}))
assert not missing['ok'] and missing['error']['details']['status'] == 404
print(json.dumps({'passed': list(receipts)}))
'''
    result = subprocess.run(
        [hermes_python(), "-c", code, str(fixture), str(profile)],
        env={**os.environ, "HERMES_HOME": str(profile)},
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(json.loads(result.stdout.splitlines()[-1])["passed"]) == 9
