"""Run local shared gates: python -m games.gota.gates ARTIFACT --manifest MANIFEST."""
import argparse
import importlib
import json
from pathlib import Path

from . import GateContext, GateResult


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    parser.add_argument('--offline', action='store_true', help='use historical pin explicitly')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--gate', choices=['g1_lint', 'g2_episode', 'g3_budget',
                                          'g4_screen', 'g5_telemetry'], action='append')
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    try:
        from games.gota.tools.engine_identity import provision, resolve
        identity = resolve(args.manifest, offline=args.offline)
        identity, binary = provision(identity)
        manifest = args.work_dir / 'coworld_manifest.json'
        manifest.write_text(json.dumps(identity.pop('manifest'), indent=2) + '\n')
        context = GateContext(args.work_dir, baseline=args.baseline, manifest=manifest,
                              native_binary=binary, engine_commit=identity['commit'])
        (context.work_dir / 'engine_identity.json').write_text(json.dumps(identity, indent=2) + '\n')
        for name in args.gate or ['g1_lint', 'g2_episode', 'g3_budget', 'g4_screen', 'g5_telemetry']:
            try:
                result = importlib.import_module(f'{__package__}.{name}').run(args.artifact, context)
            except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults; never repair them.
                result = GateResult.errored(error)
            results[name] = {'outcome': result.outcome, 'ok': result.ok, 'details': result.details}
            print(json.dumps({'gate': name, **results[name]}), flush=True)
            if result.outcome == 'errored':
                break
    except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults; never repair them.
        result = GateResult.errored(error)
        results['harness'] = {'outcome': result.outcome, 'ok': False, 'details': result.details}
        print(json.dumps({'gate': 'harness', **results['harness']}), flush=True)
    (args.work_dir / 'gates.json').write_text(json.dumps(results, indent=2) + '\n')
    if any(result['outcome'] == 'errored' for result in results.values()):
        return 3
    return 0 if all(result['ok'] for result in results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
