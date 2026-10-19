"""Batch probes keep deterministic evidence and reject changed executable/input bytes."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from games.gota.gates import GateContext, native
from games.gota.tools import native_build


@pytest.fixture
def probe(tmp_path, monkeypatch):
    binary = tmp_path / 'probe'
    binary.write_text(f'''#!{sys.executable}
import json, pathlib, sys, time
seed = int(sys.argv[4])
root = pathlib.Path(__file__).parent
with (root/'calls').open('a') as stream:
    stream.write(str(seed)+'\\n')
time.sleep(0.05)
result = {{'ok':True, 'outcome':'passed', 'mode':'legacy', 'engine':'a'*40, 'bassy':'b'*40,
           'globals':0,'arrays':0,'array_cells':0,'source_bytes':4,'routines':0,'static_instructions':1,
           'runtime':{{'seed':seed,'ticks':31200,'decisions':10,'seat_decisions':[1]*10,
                      'scores':[0]*10,'total_xp':[seed]*10,'candidate_blue':sys.argv[7]=='blue',
                      'errors':[],'instructions':1,'work':1,'print_bytes':0,'print_events':0}}}}
pathlib.Path(sys.argv[3]).write_text(json.dumps(result))
''')
    binary.chmod(0o755)
    source = tmp_path / 'source'
    source.write_text('original')
    def inputs(*a, **kw):
        return {'dependencies': {'bassy': {'commit': 'b'*40}}, 'source': source.read_text()}
    monkeypatch.setattr(native_build, 'inputs', inputs)
    native_build.write_receipt(binary, inputs())
    artifact = tmp_path / 'policy.bas'
    artifact.write_text('x=1\n')
    context = GateContext(tmp_path / 'work', native_binary=binary, engine_commit='a'*40, jobs=2)
    return artifact, context, binary, source


def test_batch_preserves_seed_side_order_and_deduplicates(probe):
    artifact, context, binary, _ = probe
    matches = [(2, None, True), (1, None, False), (2, None, True)]
    rows = native.measure_many(artifact, context, matches)
    assert [(r['runtime']['seed'], r['runtime']['candidate_blue']) for r in rows] == [(2, True), (1, False), (2, True)]
    assert sorted((binary.parent / 'calls').read_text().splitlines()) == ['1', '2']
    context.jobs = 1
    assert native.measure_many(artifact, context, matches) == rows
    assert len((binary.parent / 'calls').read_text().splitlines()) == 2


@pytest.mark.parametrize('mutation', ['binary', 'receipt', 'source', 'policy'])
def test_mutation_during_batch_invalidates_evidence(probe, mutation):
    artifact, context, binary, source = probe
    script = binary.read_text()
    destination = {'binary': binary, 'receipt': native_build.receipt_path(binary), 'source': source, 'policy': artifact}[mutation]
    script += f'pathlib.Path({str(destination)!r}).write_text("modified")\n'
    binary.write_text(script)
    native_build.write_receipt(binary, native_build.inputs())
    with pytest.raises((ValueError, json.JSONDecodeError), match='changed|stale|Expecting'):
        native.measure_many(artifact, context, [(1, None, False)])


@pytest.mark.parametrize('changed_path', ['artifact', 'opponent'])
def test_change_after_last_probe_invalidates_batch(probe, monkeypatch, changed_path):
    artifact, context, _, _ = probe
    opponent = artifact.with_name('opponent.bas')
    opponent.write_bytes(artifact.read_bytes())
    measure = native.measure
    def change_after_return(*args, **kwargs):
        result = measure(*args, **kwargs)
        (artifact if changed_path == 'artifact' else opponent).write_text('x=2\n')
        return result
    monkeypatch.setattr(native, 'measure', change_after_return)
    with pytest.raises(ValueError, match='policy changed during measurement batch'):
        native.measure_many(artifact, context, [(1, opponent, False)])


def test_probe_fault_cancels_queued_matches(probe):
    artifact, context, binary, _ = probe
    binary.write_text(binary.read_text().replace('time.sleep(0.05)', 'if seed == 1: sys.exit(7)\ntime.sleep(0.2)'))
    native_build.write_receipt(binary, native_build.inputs())
    with pytest.raises(subprocess.CalledProcessError):
        native.measure_many(artifact, context, [(seed, None, False) for seed in range(1, 21)])
    assert len((binary.parent / 'calls').read_text().splitlines()) < 20
