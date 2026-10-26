#!/usr/bin/env python3
"""Local gates for a Pudge Wars Basic policy (the local_check binding).

  G1 static      upload rules: UTF-8 BASIC <= 64 KiB; ZIP <= 16 MiB, <= 256 entries, canonical
                 names, exactly one .bas, tensors.json shape (gates_g1_static.py)
  G2 episode     compiles and plays one league-length league-3v3 episode (seed 2026) in all
                 six seats: no VM disabled, decisions made, replay re-verifies (gates_g2_episode.py)
  G3 robustness  seeds 1..N on both sides against --baseline: no candidate VM disabled
                 (gates_g3_robustness.py; per-decision instruction/work use is not measured)
  G4 screen      advisory, only with --full or --gate G4: candidate vs baseline over the
                 G3 episodes: win, kill margin, kills, deaths, enemy hook accuracy
                 (gates_g4_screen.py)

Default: G1-G3. --full: G1-G4. --gate G (repeatable) runs ONLY the named gates.

ARTIFACT is a .bas file, a ZIP package, or a package directory (packed deterministically
into WORK_DIR/package.zip first). --baseline is a policy path or a starter name (base,
aggressive, sniper, neural). G2-G4 build the headless binary on first use (native.py).
Gates run in order and stop at the first errored gate; G1 or G2 failing skips the rest.
Writes WORK_DIR/gates.json (per gate) and WORK_DIR/evaluation.json (status, artifact hash,
complete). Status: passed when every non-advisory gate run passed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
import episodes
import gates_common
import gates_g1_static
import gates_g2_episode
import gates_g3_robustness
import gates_g4_screen
import native
from cli_contract import Rejected
from gates_common import GATE_NAMES, GateContext, GateResult

MODULES = {'G1': gates_g1_static, 'G2': gates_g2_episode, 'G3': gates_g3_robustness, 'G4': gates_g4_screen}


def run_gates(artifact: Path, context: GateContext, names: list[str]) -> tuple[dict, str]:
    """Run the named gates in order; return ({gate: receipt}, status)."""
    gates: dict[str, dict] = {}
    for name in GATE_NAMES:
        if name not in names:
            continue
        if name != 'G1' and context.binary is None:
            try:
                context.binary, context.binary_receipt = native.ensure()
            except Exception as error:  # noqa: BLE001 - a missing toolchain is a recorded fault
                result = GateResult('errored', {'error': gates_common.exception_summary(error)})
                gates[name] = {'outcome': 'errored', 'passed': False, 'advisory': name in gates_common.ADVISORY,
                               'seconds': 0.0, 'summary': result.details}
                break
        started = time.monotonic()
        result = MODULES[name].run(artifact, context)
        gates[name] = {'outcome': result.outcome, 'passed': result.ok,
                       'advisory': name in gates_common.ADVISORY,
                       'seconds': round(time.monotonic() - started, 3), 'summary': result.details}
        print(json.dumps({'gate': name, 'outcome': result.outcome}), file=sys.stderr, flush=True)
        if result.outcome == 'errored' or (name in ('G1', 'G2') and not result.ok):
            break
    if any(receipt['outcome'] == 'errored' for receipt in gates.values()):
        status = 'errored'
    elif all(gates.get(name, {}).get('passed') for name in names if name not in gates_common.ADVISORY):
        status = 'passed'
    else:
        status = 'failed'
    return gates, status


def selected_gates(values: list[str] | None, full: bool) -> list[str]:
    if values and full:
        raise Rejected('give --gate or --full, not both')
    names = values or (list(GATE_NAMES) if full else ['G1', 'G2', 'G3'])
    unknown = [name for name in names if name not in GATE_NAMES]
    if unknown:
        raise Rejected(f'unknown gate {unknown}; valid: {", ".join(GATE_NAMES)}')
    return [name for name in GATE_NAMES if name in names]


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='local_check.py',
        examples=['python3 games/pudge-wars-basic/tools/local_check.py cand.bas --baseline base --work-dir .scratch/check --json',
                  'python3 games/pudge-wars-basic/tools/local_check.py cand.bas --baseline base --work-dir W --gate G1 --gate G2'])
    parser.add_argument('artifact', type=Path, help='.bas file, ZIP package, or package directory')
    parser.add_argument('--baseline', required=True, help='baseline policy path or starter name (for G3/G4)')
    parser.add_argument('--work-dir', type=Path, required=True, help='new or empty directory for evidence')
    parser.add_argument('--gate', action='append',
                        help='run only this gate (G1..G4); repeat for several (default: G1-G3)')
    parser.add_argument('--full', action='store_true', help='run G1-G4 (adds the advisory G4 screen)')
    parser.add_argument('--seeds', type=int, default=10, help='G3/G4 seeds 1..N, each played from both sides (default 10)')
    parser.add_argument('--jobs', type=int, default=2, help='episodes run in parallel (default 2)')
    parser.add_argument('--binary', type=Path, help='use this pudge_headless instead of native.py path')

    def body(args):
        names = selected_gates(args.gate, args.full)
        if args.seeds < 1 or args.jobs < 1:
            raise Rejected('--seeds and --jobs must be positive')
        if args.work_dir.exists() and any(args.work_dir.iterdir()):
            raise Rejected(f'--work-dir must be new or empty: {args.work_dir}')
        if not args.artifact.exists():
            raise Rejected(f'artifact does not exist: {args.artifact}')
        work_dir = args.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        artifact = gates_common.artifact_file(args.artifact.resolve(), work_dir)
        artifact_sha256 = gates_common.sha256(artifact)
        context = GateContext(work_dir, baseline=episodes.resolve_policy(args.baseline),
                              seeds=args.seeds, jobs=args.jobs)
        if args.binary is not None:
            if not args.binary.is_file():
                raise Rejected(f'--binary does not exist: {args.binary}')
            context.binary = args.binary.resolve()
            context.binary_receipt = native.read_receipt(context.binary)
        gates, status = run_gates(artifact, context, names)
        if gates_common.sha256(artifact) != artifact_sha256:
            raise cli_contract.Errored('artifact changed during the gates')
        evaluation = {'validation_scope': 'full' if set(names) == set(GATE_NAMES) else 'local_check', 'status': status, 'artifact': str(artifact),
                      'artifact_sha256': artifact_sha256, 'baseline': str(context.baseline),
                      'baseline_sha256': gates_common.sha256(context.baseline),
                      'complete': set(gates) == set(names), 'gates_requested': names,
                      'binary': str(context.binary) if context.binary else None,
                      'engine_commit': ((context.binary_receipt or {}).get('inputs') or {}).get('engine', {}).get('commit'),
                      'seeds': args.seeds, 'gates': gates}
        episodes.write_json(work_dir / 'gates.json', gates)
        episodes.write_json(work_dir / 'evaluation.json', evaluation)
        if status == 'errored':
            raise cli_contract.Errored('a gate errored (tool or environment fault); see gates.json',
                                       reason='gate_errored', data=evaluation)
        if status != 'passed':
            raise cli_contract.Failed('a gate failed; see gates.json', reason='gates_failed', data=evaluation)
        return evaluation

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
