"""G4: seeds 1..10, side-balanced Glory/win-rate comparison; regressions advisory."""
import hashlib
from pathlib import Path

from . import GateContext, GateResult, gate_run
from .g2_episode import validate_results
from .native import NATIVE_MODE, measure


def metrics(row: dict, slots: range) -> dict:
    validate_results(row)
    ticks = row['ticks']
    scores, xp = row['scores'], row['total_xp']
    return {'glory': sum(max(0, xp[i]) * 1440 // ticks if scores[i] == 1 else 0
                         for i in slots) / 5,
            'win_rate': sum(scores[i] for i in slots) / 5}


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    if set(context.seeds) != set(range(1, 11)) or len(context.seeds) != 10:
        raise ValueError('Screen requires each seed 1..10')
    if context.baseline is None or context.max_ticks < 31200:
        raise ValueError('Baseline and full episode tick budget required')
    identical = (hashlib.sha256(artifact.read_bytes()).digest()
                 == hashlib.sha256(context.baseline.read_bytes()).digest())
    rows = []
    candidate, baseline = [], []
    for seed in context.seeds:
        for blue in (False, True):
            report = measure(artifact, context, seed,
                             None if identical else context.baseline,
                             False if identical else blue)
            if report['outcome'] != 'passed':
                return GateResult(report['outcome'], report)
            runtime = report['runtime']
            candidate_slots = range(5, 10) if blue else range(5)
            baseline_slots = range(5) if blue else range(5, 10)
            a, b = metrics(runtime, candidate_slots), metrics(runtime, baseline_slots)
            candidate.append(a)
            baseline.append(b)
            rows.append({'seed': seed, 'candidate_blue': blue,
                         'candidate': a, 'baseline': b})
    averages = {name: sum(row[name] for row in candidate) / len(candidate)
                for name in ('glory', 'win_rate')}
    reference = {name: sum(row[name] for row in baseline) / len(baseline)
                 for name in averages}
    regression = any(averages[name] < reference[name] for name in averages)
    return GateResult('passed', {'advisory': True, 'regression': regression,
                             'native_mode': dict(NATIVE_MODE),
                             'candidate': averages, 'baseline': reference, 'rows': rows,
                             'mode': 'identical-source mirror' if identical else 'paired teams'})
