"""Build adapter for the shared G1–G5 gates; no duplicate gate implementation."""

from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp

from gota_host import BASELINE, cached_probe

from games.gota.gates import GateContext, g1_lint, g2_episode, g3_budget, g4_screen, g5_telemetry


def verify(build_id: str, *, context: GateContext | None = None) -> dict:
    import strategy_build as builds
    from strategy_format import lint_strategy, parse_strategy

    stage = builds.build_path(build_id, staged=True)
    order = builds.read(stage / "work_order.json")
    builds.assert_inputs(order)
    strategy = parse_strategy(builds.REPO / order["source_path"], backend="gota")
    errors = [d.to_dict() for d in lint_strategy(strategy) if d.level == "error"]
    policy = stage / "policy.bas"
    if not policy.is_file():
        raise ValueError("assemble the staged build before verify")
    if context is None:
        config = builds.read(builds.COMPILER / "config.json").get("gates", {})
        manifest = config.get("manifest")
        context = GateContext(
            stage / "verify",
            baseline=BASELINE,
            native_binary=cached_probe(),
            manifest=Path(manifest) if manifest else None,
        )
    context.work_dir.mkdir(parents=True, exist_ok=True)
    # Each repair round owns its evidence, including when callers reuse a context.
    context = replace(
        context,
        work_dir=Path(mkdtemp(prefix="attempt-", dir=context.work_dir)),
        native_binary=context.native_binary or cached_probe(),
        episode_dir=None,
        measurements={},
        compiler_map=builds.read(stage / "map.json"),
    )
    gates = {}
    for name, module in zip(
        ("G1", "G2", "G3", "G4", "G5"), (g1_lint, g2_episode, g3_budget, g4_screen, g5_telemetry)
    ):
        result = module.run(policy, context)
        gates[name] = {"passed": result.ok, "summary": result.details}
        if name == "G1" and errors:
            gates[name] = {"passed": False, "summary": {"lint": errors, "compile": result.details}}
        if not gates[name]["passed"] and name in ("G1", "G2"):
            for remaining in ("G1", "G2", "G3", "G4", "G5"):
                if remaining not in gates:
                    gates[remaining] = {"passed": False, "summary": f"not run: {name} failed"}
            break
    builds.dump(context.work_dir / "gates.json", gates)
    builds.dump(stage / "gates.json", gates)
    return gates
