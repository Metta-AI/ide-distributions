"""G2 episode: the candidate compiles and plays one full league-length episode in every seat.

Runs the league-3v3 variant's own config (seed 2026, 38,400 ticks, first to 30) on the
headless binary with the candidate in all six seats, then re-simulates the recorded replay
and requires every tick's state hash to match. Fails when the policy does not load (compile
or package error, the league's "Policy loading failed"), when any seat's VM is disabled at
runtime (a BasicError such as a budget overrun or a non-integer action argument), or when
the seats made no decisions.

Does not prove: behaviour against other policies (G3/G4), behaviour on other seeds, PRINT
or ANNOTATE output (the headless binary keeps neither), or anything about LLM calls (local
episodes have no LLM endpoint).
"""
from __future__ import annotations

from pathlib import Path

import episodes
from gates_common import GateContext, GateResult, PolicyFailure, gate_run


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    config = context.config
    directory = context.work_dir / 'g2-episode'
    record = episodes.run_episode(context.binary, [artifact] * config['seats'], seed=config['seed'],
                                  max_ticks=config['max_ticks'], kill_goal=config['kill_goal'],
                                  out=directory, variant=context.variant, timeout=context.timeout,
                                  binary_receipt=context.binary_receipt)
    if record['status'] == 'policy_load_failed':
        raise PolicyFailure(f'policy does not load: {next(iter(record["errors"].values()))}')
    failed = [seat for seat in record['seats'] if seat['vm_failed']]
    if failed:
        raise PolicyFailure('BASIC VM disabled at runtime: ' + '; '.join(
            f'seat {seat["slot"]}: {seat["vm_error"]}' for seat in failed))
    if record['decisions'] == 0:
        raise PolicyFailure('the seats made no decisions')
    verify = episodes.resimulate(context.binary, directory / 'replay.pwr', directory / 'replay-check')
    if record['state_hash'] not in verify['stdout']:
        raise ValueError('replay re-simulation ended on a different state hash')
    return GateResult('passed', {'episode_dir': str(directory), 'seed': config['seed'],
                                 'max_ticks': config['max_ticks'], 'end_tick': record['end_tick'],
                                 'scripts_active': record['scripts_active'],
                                 'decisions': record['decisions'], 'state_hash': record['state_hash'],
                                 'replay_verified': True, 'wall_seconds': record['wall_seconds']})
