"""Goal attribution requires a checked build and bound native evidence."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from games.gota.gates.limits import POLYWORLD_COMMIT
from games.gota.tools.equivalence import sha256
from games.gota.tools.goal_audit import verify_native_identity


@pytest.fixture
def evidence(tmp_path):
    policy = tmp_path / 'policy.bas'
    policy.write_text('END\n')
    result = {'engine': POLYWORLD_COMMIT, 'bassy': 'a02e6dd5889310b13d21725c891bde8d01539f08',
              'heroes': [{'slot': seat, 'policy_source_sha1': hashlib.sha1(policy.read_bytes()).hexdigest()}
                         for seat in range(10)]}
    (tmp_path / 'result.json').write_text(json.dumps(result))
    job = {'seed': 1, 'ticks': 28800, 'draft': 'open', 'oracle': True}
    (tmp_path / 'job.json').write_text(json.dumps(job))
    for name in ['trace.jsonl'] + [f'seat-{seat}.log' for seat in range(10)]:
        (tmp_path / name).write_text('')
    receipt = {'engine': POLYWORLD_COMMIT, 'bassy': result['bassy'], 'binary_sha256': 'a' * 64,
               'binary_fingerprint': 'b' * 64, 'seed': 1, 'max_battle_ticks': 28800,
               'candidate_seats': list(range(5)), 'seat_policy_sha256': [sha256(policy)] * 10,
               'seat_policy_sha1': [hashlib.sha1(policy.read_bytes()).hexdigest()] * 10,
               'artifacts': {p.name: sha256(p) for p in tmp_path.iterdir() if p.name != 'policy.bas'}}
    expected_native = {'binary_sha256': 'a' * 64, 'fingerprint': 'b' * 64}
    receipt['native_build'] = expected_native
    (tmp_path / 'identity.json').write_text(json.dumps(receipt))
    for seat in range(10):
        (tmp_path / f'policy-{seat}.bas').write_bytes(policy.read_bytes())
    version = {'engine': {'commit': POLYWORLD_COMMIT}, 'artifact_hashes': {'policy.bas': sha256(policy)},
               'native_builds': {'gota_match': expected_native}}
    return tmp_path, version, receipt


def test_native_identity_accepts_bound_policy(evidence):
    directory, version, _ = evidence
    assert verify_native_identity(directory, version)['candidate_seats'] == list(range(5))


@pytest.mark.parametrize('mismatch', ['build', 'policy', 'engine', 'seat', 'log', 'fingerprint', 'binary', 'valid_fingerprint', 'native_receipt', 'missing_expected'])
def test_native_identity_rejects_mismatch(evidence, mismatch):
    directory, version, receipt = evidence
    if mismatch == 'native_receipt': receipt['native_build'] = {'binary_sha256': '3' * 64, 'fingerprint': '4' * 64}
    if mismatch == 'missing_expected': version.pop('native_builds')
    if mismatch == 'build': version['artifact_hashes']['policy.bas'] = '0' * 64
    if mismatch == 'policy': receipt['seat_policy_sha1'][0] = '0' * 40
    if mismatch == 'engine': version['engine']['commit'] = '0' * 40
    if mismatch == 'seat': receipt['candidate_seats'] = [5, 6, 7, 8, 9]
    if mismatch == 'log': (directory / 'seat-0.log').write_text('changed')
    if mismatch == 'binary': receipt['binary_sha256'] = '3' * 64
    if mismatch == 'valid_fingerprint': receipt['binary_fingerprint'] = '4' * 64
    if mismatch == 'fingerprint': receipt['binary_fingerprint'] = ''
    (directory / 'identity.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        verify_native_identity(directory, version)
