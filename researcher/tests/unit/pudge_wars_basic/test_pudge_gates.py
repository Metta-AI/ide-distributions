"""G1 static rules (raw BASIC and ZIP packages), gate selection, and the deterministic packer.
Pure Python; the native gates are in test_pudge_native.py."""
import io
import json
import zipfile

import pytest
from support.pudge_wars_basic import GAME


def g1(tools, tmp_path, path):
    return tools.gates_g1_static.run(path, tools.gates_common.GateContext(tmp_path / 'work'))


def make_zip(path, entries):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def tensors(*entries):
    return json.dumps({'tensors': list(entries)}).encode()


def test_starters_pass(tools, tmp_path):
    for name in ('base.bas', 'aggressive.bas', 'sniper.bas', 'neural.zip'):
        result = g1(tools, tmp_path, GAME / 'baselines' / name)
        assert result.outcome == 'passed', (name, result.details)
    assert g1(tools, tmp_path, GAME / 'baselines/neural.zip').details['tensors'] == 3


@pytest.mark.parametrize('data,message', [
    (b'', 'empty'), (b'x' * (64 * 1024 + 1), 'source limit'), (b'\xff\xfe bad', 'UTF-8'),
    (b"t = tensorLoad(\"w\")\n", 'ZIP package')])
def test_raw_basic_rules(tools, tmp_path, data, message):
    path = tmp_path / 'policy.bas'
    path.write_bytes(data)
    result = g1(tools, tmp_path, path)
    assert result.outcome == 'failed' and message in result.details['error']


def test_commented_tensor_load_is_not_a_call(tools, tmp_path):
    path = tmp_path / 'policy.bas'
    path.write_text("' tensorLoad(\"w\") lives in the ZIP version\nREM tensorLoad(\"x\")\nwalkTo(selfX, selfY)\n")
    assert g1(tools, tmp_path, path).outcome == 'passed'


WEIGHTS = b'\0' * 16
OK_TENSOR = {'name': 'w', 'dtype': 'float32', 'shape': [2, 2], 'resource': 'weights.bin', 'offset': 0}


@pytest.mark.parametrize('entries,message', [
    ({'a.bas': b'x = 1\n', 'b.BAS': b'y = 1\n'}, 'exactly one .bas'),
    ({'weights.bin': WEIGHTS}, 'exactly one .bas'),
    ({'p.bas': b'x = 1\n', '../escape.bin': b'1'}, 'canonical'),
    ({'p.bas': b'x = 1\n', 'a//b.bin': b'1'}, 'canonical'),
    ({'p.bas': b'x = 1\n', 'c:\\w.bin': b'1'}, 'canonical'),
    ({'p.bas': b'x = tensorLoad("w")\n', 'weights.bin': WEIGHTS}, 'no tensors.json'),
    ({'p.bas': b'x = 1\n', 'tensors.json': b'{', 'weights.bin': WEIGHTS}, 'not JSON'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors(), 'weights.bin': WEIGHTS}, '1..256'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors(OK_TENSOR, OK_TENSOR), 'weights.bin': WEIGHTS}, 'unique'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors({**OK_TENSOR, 'dtype': 'float64'}), 'weights.bin': WEIGHTS}, 'dtype'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors({**OK_TENSOR, 'shape': [3, 3]}), 'weights.bin': WEIGHTS}, 'outside'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors({**OK_TENSOR, 'resource': 'missing.bin'})}, 'not in the package'),
    ({'p.bas': b'x = 1\n', 'tensors.json': tensors({**OK_TENSOR, 'shape': [1] * 9}), 'weights.bin': WEIGHTS}, 'dimensions'),
    ({'p.bas': b'x' * (64 * 1024 + 1)}, 'source limit')])
def test_zip_rules(tools, tmp_path, entries, message):
    result = g1(tools, tmp_path, make_zip(tmp_path / 'policy.zip', entries))
    assert result.outcome == 'failed', result.details
    assert message in result.details['error']


def test_zip_entry_and_size_limits(tools, tmp_path):
    many = {'p.bas': b'x = 1\n', **{f'r/{i}.bin': b'1' for i in range(256)}}
    assert '256' in g1(tools, tmp_path, make_zip(tmp_path / 'many.zip', many)).details['error']
    big = tmp_path / 'big.zip'
    with zipfile.ZipFile(big, 'w', compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('p.bas', b'x = 1\n')
        archive.writestr('blob.bin', io.BytesIO(bytes(16 * 1024 * 1024)).getvalue())
    assert 'limit' in g1(tools, tmp_path, big).details['error']


def test_valid_package_passes(tools, tmp_path):
    entries = {'p.bas': b'x = tensorLoad("w")\n', 'tensors.json': tensors(OK_TENSOR), 'weights.bin': WEIGHTS}
    result = g1(tools, tmp_path, make_zip(tmp_path / 'policy.zip', entries))
    assert result.outcome == 'passed', result.details


def test_package_directory_packs_deterministically(tools, tmp_path):
    package = tmp_path / 'package'
    (package / 'data').mkdir(parents=True)
    (package / 'policy.bas').write_text('x = tensorLoad("w")\n')
    (package / 'tensors.json').write_bytes(tensors({**OK_TENSOR, 'resource': 'data/weights.bin'}))
    (package / 'data/weights.bin').write_bytes(WEIGHTS)
    first = tools.gates_common.artifact_file(package, tmp_path / 'a')
    second = tools.gates_common.artifact_file(package, tmp_path / 'b')
    assert first.read_bytes() == second.read_bytes()
    assert zipfile.ZipFile(first).namelist() == ['data/weights.bin', 'policy.bas', 'tensors.json']
    assert g1(tools, tmp_path, first).outcome == 'passed'


def test_gate_selection(tools):
    select = tools.local_check.selected_gates
    assert select(None, False) == ['G1', 'G2', 'G3']
    assert select(None, True) == ['G1', 'G2', 'G3', 'G4']
    assert select(['G4'], False) == ['G4']
    assert select(['G2', 'G1'], False) == ['G1', 'G2']
    with pytest.raises(tools.cli_contract.Rejected):
        select(['G1'], True)
    with pytest.raises(tools.cli_contract.Rejected):
        select(['G5'], False)


def test_unexpected_gate_exception_is_errored(tools, tmp_path):
    @tools.gates_common.gate_run
    def broken(artifact, context):
        raise RuntimeError('tool bug')

    result = broken(tmp_path, None)
    assert result.outcome == 'errored' and result.details['error']['type'] == 'RuntimeError'
