"""G3 robustness: the candidate's VMs survive league-length episodes against the baseline.

Plays seeds 1..N (default 10) on both sides: candidate in the west seats against the
baseline in the east, and swapped. Fails if any candidate seat's VM is disabled at runtime
(any BasicError, including the 100,000-instruction / 250,000-work-unit per-decision budget)
or if either policy fails to load. The episodes are shared with G4.

Not measured: per-decision instruction and work-unit use. The headless binary does not
report them (only whether a VM failed), so G3 cannot say how close the candidate runs to
the budget; a policy near the limit can still pass here and fail on a rarer state.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import episodes
from gates_common import GateContext, GateResult, PolicyFailure, gate_run


def screen(artifact: Path, context: GateContext) -> list[dict]:
    """Seeds 1..N x sides, candidate vs baseline; cached on the context for G4."""
    if context.screen is not None:
        return context.screen
    if context.baseline is None:
        raise ValueError('G3/G4 need a baseline policy')
    config = context.config
    half = config['seats'] // 2
    jobs = [(seed, side) for seed in range(1, context.seeds + 1) for side in ('west', 'east')]

    def one(job):
        seed, side = job
        seats = ([artifact] * half + [context.baseline] * half if side == 'west'
                 else [context.baseline] * half + [artifact] * half)
        record = episodes.run_episode(
            context.binary, seats, seed=seed, max_ticks=config['max_ticks'], kill_goal=config['kill_goal'],
            out=context.work_dir / 'screen' / f'seed-{seed}-candidate-{side}', variant=context.variant,
            timeout=context.timeout, binary_receipt=context.binary_receipt)
        record['candidate_side'] = side
        record['candidate_slots'] = list(range(half) if side == 'west' else range(half, 2 * half))
        return record

    with ThreadPoolExecutor(max_workers=max(1, context.jobs)) as pool:
        context.screen = list(pool.map(one, jobs))
    return context.screen


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    records = screen(artifact, context)
    problems = []
    for record in records:
        where = f'seed {record["seed"]} candidate {record["candidate_side"]}'
        if record['status'] == 'policy_load_failed':
            problems.append(f'{where}: policy does not load (slots {record["failed_slots"]})')
            continue
        for slot in record['candidate_slots']:
            seat = record['seats'][slot]
            if seat['vm_failed']:
                problems.append(f'{where}: seat {slot} VM disabled: {seat["vm_error"]}')
    baseline_failures = sum(1 for record in records if record['status'] == 'completed'
                            for seat in record['seats']
                            if seat['slot'] not in record['candidate_slots'] and seat['vm_failed'])
    if problems:
        raise PolicyFailure('; '.join(problems[:10]) + (f' (+{len(problems) - 10} more)' if len(problems) > 10 else ''))
    return GateResult('passed', {'episodes': len(records), 'seeds': context.seeds,
                                 'candidate_vm_failures': 0, 'baseline_vm_failures': baseline_failures,
                                 'instruction_work_use': 'not measured (headless binary reports VM failure only)',
                                 'screen_dir': str(context.work_dir / 'screen')})
