"""Run local shared gates: python -m games.gota.gates ARTIFACT --manifest MANIFEST."""
import argparse
import importlib
import json
from pathlib import Path

from . import GateContext


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--gate', choices=['g1_lint', 'g2_episode', 'g3_budget',
                                          'g4_screen', 'g5_telemetry'], action='append')
    args = parser.parse_args()
    context = GateContext(args.work_dir, baseline=args.baseline, manifest=args.manifest)
    results = {}
    for name in args.gate or ['g1_lint', 'g2_episode', 'g3_budget', 'g4_screen', 'g5_telemetry']:
        result = importlib.import_module(f'{__package__}.{name}').run(args.artifact, context)
        results[name] = {'ok': result.ok, 'details': result.details}
        print(json.dumps({'gate': name, **results[name]}), flush=True)
    context.work_dir.mkdir(parents=True, exist_ok=True)
    (context.work_dir / 'gates.json').write_text(json.dumps(results, indent=2) + '\n')
    return 0 if all(result['ok'] for result in results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
