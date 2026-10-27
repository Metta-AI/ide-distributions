"""G2 episode: the candidate compiles and plays one full league-length episode in every seat.

Runs the live league-3v3 variant's config (seed, max_ticks, kill goal from the live manifest)
on the league host (pudge_league, the hosted accounting) with the candidate in all six seats,
then re-simulates the recorded replay and requires every tick's state hash to match. Fails
when the policy does not load (player_failure.json: compile or package error), when any
seat's VM is disabled at runtime (player_status exit_code 1, "BASIC VM disabled": a
BasicError such as a budget overrun or a non-integer action argument; the error line comes
from the seat log), or when a seat issued no action. Reports each seat's PRINT log and
annotation counts.

Does not prove: behaviour against other policies (G3/G4), behaviour on other seeds,
per-decision instruction/work headroom (no output reports it), or anything about LLM calls
(local episodes have no LLM endpoint).
"""
from __future__ import annotations

import json
from pathlib import Path

import episodes
from gates_common import GateContext, GateResult, PolicyFailure, gate_run


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    config = context.config
    directory = context.work_dir / 'g2-episode'
    record = context.runner.run([artifact] * config['seats'], seed=config['seed'],
                                max_ticks=config['max_ticks'], kill_goal=config['kill_goal'],
                                out=directory, variant=context.variant, timeout=context.timeout)
    if record['status'] == 'policy_load_failed':
        raise PolicyFailure(f'policy does not load: {next(iter(record["errors"].values()))}')
    failed = [seat for seat in record['seats'] if seat['vm_failed']]
    if failed:
        raise PolicyFailure('BASIC VM disabled at runtime: ' + '; '.join(
            f'seat {seat["slot"]}: {seat["vm_error"]}' for seat in failed))
    actions = seat_actions(directory / 'events.jsonl', config['seats'])
    idle = [slot for slot, count in enumerate(actions) if count == 0]
    if idle:
        raise PolicyFailure(f'seats issued no action: {idle}')
    if not record.get('replay_verified'):
        verify = episodes.resimulate(context.runner.headless, directory / 'replay.pwr', directory / 'replay-check')
        if record['state_hash'] not in verify['stdout']:
            raise ValueError('replay re-simulation ended on a different state hash')
    return GateResult('passed', {'episode_dir': str(directory), 'runner': context.runner.kind,
                                 'seed': config['seed'], 'max_ticks': config['max_ticks'],
                                 'end_tick': record['end_tick'], 'scripts_active': record['scripts_active'],
                                 'seat_actions': actions, 'seat_logs': seat_logs(directory, record),
                                 'state_hash': record['state_hash'], 'replay_verified': True,
                                 'wall_seconds': record['wall_seconds']})


def seat_actions(events: Path, seats: int) -> list[int]:
    """Actions each seat issued (the match log's `action` events; player id = slot + 1)."""
    counts = [0] * seats
    with events.open() as stream:
        for line in stream:
            if '"action"' in line:
                event = json.loads(line)
                if event.get('ev') == 'action' and 1 <= event.get('id', 0) <= seats:
                    counts[event['id'] - 1] += 1
    return counts


def seat_logs(directory: Path, record: dict) -> list[dict] | None:
    """Per seat: PRINT log lines/bytes and ANNOTATE rows (league runner only)."""
    if record.get('runner') != 'league':
        return None
    rows = []
    for seat in record['seats']:
        log, notes = directory / seat['log'], directory / seat['annotations']
        text = log.read_text(errors='replace') if log.is_file() else ''
        rows.append({'slot': seat['slot'], 'log_lines': len(text.splitlines()), 'log_bytes': len(text.encode()),
                     'annotations': len(notes.read_text().splitlines()) if notes.is_file() else 0})
    return rows
