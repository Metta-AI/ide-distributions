"""Build adapter for the shared G1–G5 gates; no duplicate gate implementation."""

import time
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp

from gota_host import BASELINE, cached_probe

from games.gota.gates import (
    GateContext,
    GateResult,
    g1_lint,
    g2_episode,
    g3_budget,
    g4_screen,
    g5_telemetry,
)


def verify(build_id: str, *, context: GateContext | None = None, manifest: Path | None = None, full_evaluation: bool = True) -> dict:
    import strategy_build as builds

    try:
        return _verify(build_id, context=context, manifest=manifest, full_evaluation=full_evaluation)
    except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults; never repair them.
        result = GateResult.errored(error)
        gates = {'verification': {'outcome': result.outcome, 'passed': False,
                                  'summary': result.details}}
        builds.dump(builds.build_path(build_id, staged=True) / 'gates.json', gates)
        return gates


def _verify(build_id: str, *, context: GateContext | None = None, manifest: Path | None = None, full_evaluation: bool = True) -> dict:
    import strategy_build as builds
    from strategy_format import lint_strategy, parse_strategy

    stage = builds.build_path(build_id, staged=True)
    order = builds.read(stage / "work_order.json")
    builds.assert_inputs(order)
    commit = order['engine']['commit']
    strategy = parse_strategy(builds.REPO / order["source_path"], backend="gota")
    errors = [d.to_dict() for d in lint_strategy(strategy) if d.level == "error"]
    policy = stage / "policy.bas"
    if not policy.is_file():
        raise ValueError("assemble the staged build before verify")
    if context is None:
        config = builds.read(builds.COMPILER / "config.json").get("gates", {})
        manifest = manifest or config.get("manifest")
        context = GateContext(
            stage / "verify",
            baseline=BASELINE,
            engine_commit=commit,
            native_binary=cached_probe(commit),
            manifest=Path(manifest) if manifest else None,
        )
    context.work_dir.mkdir(parents=True, exist_ok=True)
    # Each repair round owns its evidence, including when callers reuse a context.
    context = replace(
        context,
        work_dir=Path(mkdtemp(prefix="attempt-", dir=context.work_dir)),
        native_binary=context.native_binary or cached_probe(commit),
        engine_commit=commit,
        episode_dir=None,
        measurements={},
        compiler_map=builds.read(stage / "map.json"),
    )
    from games.gota.tools.native_build import TOOLS, validate
    native_builds = {
        'gate_probe': validate(context.native_binary, 'gate_probe', commit),
        'gota_match': validate(TOOLS / 'bin' / f'gota_match-{commit[:8]}',
                               'gota_match', commit),
    }
    from games.gota.tools.native_build import fingerprint
    engine = order['engine']
    if engine.get('manifest_sha256'):
        if context.manifest is None or fingerprint(builds.read(context.manifest)) != engine['manifest_sha256']:
            raise ValueError('manifest changed after engine resolution')
        expected = engine['native_fingerprints']
        if any(receipt['fingerprint'] != expected[target] for target, receipt in native_builds.items()):
            raise ValueError('native build changed after engine resolution')
    builds.dump(stage / 'native_provenance.json', native_builds)
    gates = {}
    selected = list(zip(("G1", "G2", "G3", "G4", "G5"),
                        (g1_lint, g2_episode, g3_budget, g4_screen, g5_telemetry)))
    for name, module in selected if full_evaluation else selected[:1]:
        gate_started = time.monotonic()
        try:
            result = module.run(policy, context)
            for measurement in context.measurements.values():
                if measurement.get('native_build') != native_builds['gate_probe']:
                    raise ValueError('gate used a different native build from verification provenance')
        except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults; never repair them.
            result = GateResult.errored(error)
        seconds = round(time.monotonic() - gate_started, 3)
        gates[name] = {"outcome": result.outcome, "passed": result.ok, "summary": result.details,
                       "advisory": name in ("G4", "G5"), "seconds": seconds}
        if result.outcome == 'errored':
            break
        if name == "G1" and errors:
            gates[name] = {"outcome": "failed", "passed": False,
                           "summary": {"lint": errors, "compile": result.details},
                           "seconds": seconds}
        if not gates[name]["passed"] and name in ("G1", "G2"):
            break
    builds.dump(context.work_dir / "gates.json", gates)
    builds.dump(stage / "gates.json", gates)
    return gates
