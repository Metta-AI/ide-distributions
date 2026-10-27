"""G4 screen (advisory): candidate vs baseline over the G3 episodes (seeds x both sides).

Per episode, the candidate's seats and the baseline's seats are each averaged; the screen
reports the means over episodes of: win (1/0, 0.5 draw), team_kill_margin, kills, deaths,
enemy_hook_accuracy (hooks that latched an enemy per hook thrown, from events.jsonl; see
episodes.enemy_hook_hits) and the host's raw landed_ratio (hooksLanded/hooksThrown, which
also counts ally and allied-mine latches). `regression` is true when the
candidate's win mean is below 0.5 (it loses the paired screen) or its kill margin is
negative. Advisory: it never blocks a build; a tool fault still errors.

Not proof of league strength: one baseline, local seeds, bots on both sides.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import episodes
import evaluate
from gates_common import GateContext, GateResult, gate_run
from gates_g3_robustness import screen

FIELDS = ('win', 'team_kill_margin', 'kills', 'deaths', 'enemy_hook_accuracy', 'landed_ratio')


def side_metrics(directory: Path, slots: list[int]) -> dict:
    results = json.loads((directory / 'results.json').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    players = {player['seat']: player for player in summary['players']}
    hits = episodes.enemy_hook_hits(directory / 'events.jsonl', {player['id']: player['team'] for player in players.values()})
    rows = []
    for slot in slots:
        base = evaluate.metrics(results, slot)
        player = players[slot]
        thrown = player['hooksThrown']
        rows.append({**{key: base[key] for key in ('win', 'team_kill_margin', 'kills', 'deaths')},
                     'enemy_hook_accuracy': hits[player['id']] / thrown if thrown else 0.0,
                     'landed_ratio': player['hooksLanded'] / thrown if thrown else 0.0})
    return {key: statistics.fmean(row[key] for row in rows) for key in FIELDS}


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    records = screen(artifact, context)
    seats = context.config['seats']
    rows = []
    for record in records:
        if record['status'] != 'completed':
            raise ValueError(f'screen episode seed {record["seed"]} did not complete; see G3')
        directory = context.work_dir / 'screen' / f'seed-{record["seed"]}-candidate-{record["candidate_side"]}'
        others = [slot for slot in range(seats) if slot not in record['candidate_slots']]
        rows.append({'seed': record['seed'], 'candidate_side': record['candidate_side'],
                     'candidate': side_metrics(directory, record['candidate_slots']),
                     'baseline': side_metrics(directory, others)})
    candidate = {key: statistics.fmean(row['candidate'][key] for row in rows) for key in FIELDS}
    baseline = {key: statistics.fmean(row['baseline'][key] for row in rows) for key in FIELDS}
    identical = context.baseline is not None and context.baseline.read_bytes() == artifact.read_bytes()
    return GateResult('passed', {'advisory': True, 'episodes': len(rows),
                                 'regression': candidate['win'] < 0.5 or candidate['team_kill_margin'] < 0,
                                 'mode': 'identical-source mirror' if identical else 'paired sides',
                                 'candidate': candidate, 'baseline': baseline, 'rows': rows})
