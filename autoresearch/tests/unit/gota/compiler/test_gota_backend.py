"""GotA adapter integration and bounded storage regression tests."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "games/gota/compiler/tools"))
import strategy_basic as sb
import strategy_format as sf
from test_strategy_basic import RUNTIME, TRIVIAL, TRIVIAL_UNITS


def strategy(tmp_path):
    path = tmp_path / "STRATEGY.md"
    path.write_text(TRIVIAL.replace(",10,11,12,13,14,15", ""))
    return sf.parse_strategy(path, backend="gota")


def test_ten_seats_and_live_host_ids(tmp_path):
    value = strategy(tmp_path)
    assert not [d for d in sf.lint_strategy(value) if d.level == "error"]
    tables = sb.generate_tables(value, {})
    assert "selfId = 100" in tables and "selfId = 109" in tables
    assert "selfId = 0 " not in tables


def test_real_generated_artifact_compiles(tmp_path):
    import gota_host as host

    from games.gota.gates import GateContext, g1_lint, g3_budget

    result = sb.assemble(strategy(tmp_path), TRIVIAL_UNITS, RUNTIME, "abcdef12-1", "a" * 40)
    policy = tmp_path / "generated.bas"
    policy.write_text(result["policy"])
    context = GateContext(
        tmp_path / "gate", native_binary=host.cached_probe(), seeds=(1,), max_ticks=3000
    )
    compiled = g1_lint.run(policy, context)
    assert compiled.ok, compiled.details
    measured = g3_budget.run(policy, context)
    assert measured.ok, measured.details
    assert result["budget"]["array_cells"] > 153920
    assert result["budget"]["globals"] > 78
    assert result["map"]["policy_mode"] == "structured"


def test_reserve_overflow(tmp_path):
    import legacy_packing as packing

    with pytest.raises(ValueError, match="128"):
        packing.pack("\n".join(f"c_x__scratch{i} = 0" for i in range(129)))


def test_baseline_accesses_disjoint_and_exact(tmp_path):
    import legacy_packing as packing

    baseline = (ROOT / "tests/fixtures/gota/v13-live.bas").read_text()
    packed, mapping = packing.pack(baseline)
    assert sb.scan_basic(packed, backend="gota").arrays["pvid"] == 661
    assert "pvId(pvN)" in packed
    assert mapping["arrays"]["rt__def"] == [64, 64]
    assert mapping["arrays"]["rt__a_dur"] == [479, 5]
    assert len(mapping["fixed"]) == 49
    assert set(mapping["fixed"].values()) == set(range(484, 533))


def test_unknown_array_and_oversized_binding_fail():
    import legacy_packing as packing

    with pytest.raises(ValueError, match="array"):
        packing.pack("DIM c_x__items(3)")
    with pytest.raises(ValueError, match="bound"):
        packing.pack("DIM sk_base__pvId(64)", {"sk_base__pvId": "pvId"})


def test_binding_and_strings_are_preserved():
    import legacy_packing as packing

    text = 'DIM sk_base__pvId(63)\nsk_base__pvId(0)=5\nc_x__n=2\nPRINT "c_x__n"\n'
    packed, mapping = packing.pack(text, {"sk_base__pvId": "pvId"})
    assert "pvId(0)=5" in packed
    assert 'PRINT "c_x__n"' in packed
    assert "pvId(533)=2" in packed
    assert mapping["bindings"] == {"sk_base__pvid": "pvid"}


@pytest.mark.parametrize(
    "source",
    [
        "DIM pvId(63): x=1",
        "DIM x(1), pvId(63)",
        "SUB x(): END SUB",
        "rt__def(-1)=0",
        "rt__a_on(5)=0",
        "pvId(64)=0",
        "pvId(-1)=0",
    ],
)
def test_unsafe_flattening_fails(source):
    import legacy_packing as packing

    with pytest.raises(ValueError):
        packing.pack(source)


def test_scoped_parameters_and_nested_indices():
    import legacy_packing as packing

    text = "c_x__n=3\nSUB c_x__f(c_x__n)\nc_x__n=c_x__n+1\nEND SUB\n"
    packed, _ = packing.pack(text)
    assert "SUB c_x__f(c_x__n)\nc_x__n=c_x__n+1\nEND SUB" in packed
    packed, _ = packing.pack('rt__def(rt__prio(rt__i))=rt__rule\nPRINT "rt__rule"')
    assert "pvId(64+(pvId(128+(pvId(492)))))=pvId(522)" in packed
    assert 'PRINT "rt__rule"' in packed


def test_com_and_wrong_seat_count_rejected(tmp_path):
    value = strategy(tmp_path)
    path = value.path
    path.write_text(TRIVIAL)
    assert any(
        d.code == "roles-syntax" for d in sf.lint_strategy(sf.parse_strategy(path, backend="gota"))
    )
    # Directly adding the parsed component exercises the backend lint boundary,
    # independent of historical COM grammar.
    from copy import deepcopy

    component = deepcopy(value.components["K.position"])
    component.id, component.kind = "COM.test", "COM"
    value.components[component.id] = component
    assert any(d.code == "gota-com" for d in sf.lint_strategy(value))


def test_source_budget_is_actual_generated_bytes(tmp_path):
    value = strategy(tmp_path)
    units = dict(TRIVIAL_UNITS)
    # The unit checker permits arbitrary namespaced SUB names. Long identifiers
    # survive comment/indent compaction and must count against the actual source.
    units["K.position"] += "\nSUB k_position__" + "x" * 65536 + "()\nEND SUB\n"
    with pytest.raises(sb.BuildError, match="source_bytes"):
        sb.assemble(value, units, RUNTIME, "abcdef12-1", "a" * 40)


def test_shared_gate_order_and_advisory_screen(tmp_path, monkeypatch):
    import gota_gates
    import strategy_build as builds

    from games.gota.gates import GateContext, GateResult

    value = strategy(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "policy.bas").write_text("x=1")
    builds.dump(stage / "map.json", {})
    builds.dump(stage / "work_order.json", {"source_path": str(value.path), "source_hashes": {}})
    monkeypatch.setattr(builds, "build_path", lambda *a, **kw: stage)
    monkeypatch.setattr(builds, "assert_inputs", lambda order: None)
    calls = []

    def run(name):
        def gate(artifact, context):
            calls.append(name)
            return GateResult(True, {"regressions": ["glory"]} if name == "G4" else {})

        return gate

    for name, module in zip(
        ("G1", "G2", "G3", "G4", "G5"),
        (
            gota_gates.g1_lint,
            gota_gates.g2_episode,
            gota_gates.g3_budget,
            gota_gates.g4_screen,
            gota_gates.g5_telemetry,
        ),
    ):
        monkeypatch.setattr(module, "run", run(name))
    result = gota_gates.verify("abcdef12-1", context=GateContext(tmp_path))
    assert calls == ["G1", "G2", "G3", "G4", "G5"]
    assert all(row["passed"] for row in result.values())
    monkeypatch.setattr(
        gota_gates.g4_screen, "run", lambda *a: GateResult(False, {"error": "missing"})
    )
    assert not gota_gates.verify("abcdef12-1", context=GateContext(tmp_path))["G4"]["passed"]

    def swapped_native(artifact, context):
        context.measurements['changed'] = {'native_build': {'different': True}}
        return GateResult(True, {})

    monkeypatch.setattr(gota_gates.g1_lint, 'run', swapped_native)
    with pytest.raises(ValueError, match='different native build'):
        gota_gates.verify('abcdef12-1', context=GateContext(tmp_path))


def test_source_inventory_includes_backend_and_shared_gate_closure(tmp_path):
    import strategy_build as builds

    path = tmp_path / "STRATEGY.md"
    (tmp_path / "packing.json").write_text("{}")
    paths = builds.source_files(path)
    for suffix in (
        "gota_host.py",
        "gota_gates.py",
        "pw_episodes.py",
        "pw_metrics.py",
        "g5_telemetry.py",
        "gate_probe.nim",
        "gota_episodes.py",
        "packing.json",
        "v13-live.bas",
    ):
        assert any(p.name == suffix for p in paths), suffix


def test_backend_host_schema_matches_cached_live_sources():
    import re

    import gota_host as host

    engine = ROOT / "games/gota/tools/.cache/polyworld"
    game = engine / "examples/gods_of_the_arena"
    bots = (game / "bots.nim").read_text()
    names = set(
        re.findall(r'"([A-Za-z][A-Za-z0-9]+)"', bots.split("HeroDataNames:")[1].split("var\n")[0])
    )
    init = bots.split("proc initHeroHost(")[1].split("let draftHeroProc")[0]
    names.update(re.findall(r'addData\("([A-Za-z][A-Za-z0-9]+)"', init))
    names.update(re.findall(r'\("(\w+Layer)"', init))
    for filename, typename in [
        ("events.nim", "ActionError"),
        ("content.nim", "HeroClass"),
        ("terrains.nim", "TerrainKind"),
    ]:
        text = (game / filename).read_text().split(typename + "*")[1].split("= enum", 1)[1]
        block = re.split(r"\n\s*\n|\n  \w", text)[0]
        names.update(re.findall(r"\b[A-Za-z]\w*\b", block))
    assert host.HOST_DATA == {name.lower() for name in names}
    parser = (engine / "tmp/coworld/deps/bassy/src/bassy.nim").read_text()
    reserved = parser.split("proc isReserved(")[1].split("proc prepareTokens")[0]
    assert host.KEYWORDS == set(re.findall(r'"(\w+)"', reserved))


def test_actual_packed_v13_storage_compiles(tmp_path):
    import gota_host as host
    import legacy_packing as packing

    from games.gota.gates import GateContext, g1_lint

    source, _ = packing.pack(host.BASELINE.read_text())
    artifact = tmp_path / "packed-v13.bas"
    artifact.write_text(source)
    result = g1_lint.run(artifact, GateContext(tmp_path, native_binary=host.cached_probe()))
    assert result.ok, result.details
    assert {key: result.details[key] for key in ("globals", "arrays", "array_cells")} == {
        "globals": 251,
        "arrays": 32,
        "array_cells": 4061,
    }
    # Token identity, with only the inclusive pvId bound changed. This is a
    # mechanical source proof, not an episode or action-equivalence claim.
    before = [sb._tokens(line) for line in host.BASELINE.read_text().splitlines()]
    after = [
        sb._tokens(line) for line in source.replace("DIM pvId(660)", "DIM pvId(63)").splitlines()
    ]
    assert [line for line in before if line] == [line for line in after if line]


def test_bindings_fail_on_duplicate_unknown_or_wrong_kind():
    import legacy_packing as packing

    for source, bindings in [
        ("a=1\nb=2", {"a": "pvN", "b": "pvN"}),
        ("a=1", {"absent": "pvN"}),
        ("a=1", {"a": "pvId"}),
        ("a=1\npvN=2", {"a": "pvN"}),
        ("rt__rule=1", {"rt__rule": "pvN"}),
    ]:
        with pytest.raises(ValueError):
            packing.pack(source, bindings)


def test_g5_parses_ten_goal_traces_and_rejects_missing_or_malformed(tmp_path):
    from test_goal_layer import GOALS

    from games.gota.gates import GateContext, g5_telemetry

    value = strategy(tmp_path)
    value.path.write_text(
        value.path.read_text()
        .replace("## Primitives", GOALS + "\n## Primitives")
        .replace(
            "DO `C.idle`(hold=`C.idle`.speed)", "DO `C.idle`(hold=`C.idle`.speed) FOR G.survive"
        )
    )
    value = sf.parse_strategy(value.path, backend="gota")
    mapping = sb.assemble(value, TRIVIAL_UNITS, RUNTIME, "abcdef12-1", "a" * 40)["map"]
    (tmp_path / "logs").mkdir()
    for seat in range(10):
        (tmp_path / "logs" / f"policy_agent_{seat}.log").write_text(
            "PWD v=3 g=G.survive t=0 r=1 c=1 i=12,0,0 h=0 p=0 f=0\n"
        )
    context = GateContext(tmp_path, episode_dir=tmp_path, compiler_map=mapping)
    result = g5_telemetry.compiler_logs(context)
    assert result.ok, result.details
    assert len(result.details["seats"]) == 10
    last = tmp_path / "logs/policy_agent_9.log"
    last.write_text(last.read_text().replace("G.survive", "G.unknown"))
    assert not g5_telemetry.compiler_logs(context).ok
    last.unlink()
    assert not g5_telemetry.compiler_logs(context).ok


def test_assemble_build_rejects_obsolete_gota_bindings(tmp_path, monkeypatch):
    import pw_cli
    import strategy_build as builds

    value = strategy(tmp_path)
    packing = tmp_path / "packing.json"
    builds.dump(packing, {"baseline_bindings": {"k_position__x": "pvN"}})
    stage = tmp_path / "stage"
    (stage / "units").mkdir(parents=True)
    for name, text in TRIVIAL_UNITS.items():
        (stage / "units" / f"{name}.bas").write_text(text)
    order = {
        "source_path": "STRATEGY.md",
        "source_commit": "a" * 40,
        "source_hashes": {p.name: builds.digest(p) for p in (value.path, packing)},
    }
    builds.dump(stage / "work_order.json", order)
    monkeypatch.setattr(builds, "REPO", tmp_path)
    monkeypatch.setattr(builds, "build_path", lambda *a, **kw: stage)
    with pytest.raises(pw_cli.UsageError, match="packing.json is obsolete"):
        builds.assemble_build("abcdef12-1")
    builds.dump(packing, {"baseline_bindings": {}})
    with pytest.raises(pw_cli.UsageError, match="changed since prepare"):
        builds.assemble_build("abcdef12-1")


def test_metadata_overflow_fails_in_actual_assembly(tmp_path):
    units = dict(TRIVIAL_UNITS)
    units["K.position"] = units["K.position"].replace(
        "END SUB", "\n".join(f"k_position__extra{i}=0" for i in range(513)) + "\nEND SUB"
    )
    with pytest.raises(sb.BuildError, match="globals"):
        sb.assemble(strategy(tmp_path), units, RUNTIME, "abcdef12-1", "a" * 40)


def test_host_doc_names_and_keywords_match_code():
    import gota_host as host

    text = (ROOT / "games/gota/compiler/docs/policy-surface.md").read_text()
    for name in host.HOST_DATA | host.KEYWORDS:
        assert name in text.split(), name


@pytest.mark.parametrize(
    "text",
    ["[]", "{}", "null", "{", '{"baseline_bindings": []}', '{"baseline_bindings": {"x": 3}}'],
)
def test_invalid_packing_file_is_actionable(tmp_path, text):
    import pw_cli
    import strategy_build as builds

    (tmp_path / "packing.json").write_text(text)
    with pytest.raises(pw_cli.UsageError, match="packing.json"):
        builds.packing_bindings(tmp_path / "STRATEGY.md")


def test_cached_probe_rejects_missing_and_stale(tmp_path, monkeypatch):
    import os

    import gota_host as host

    monkeypatch.setattr(host, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="cached native gate probe required"):
        host.cached_probe()
    tools = tmp_path / "games/gota/tools"
    (tools / "bin").mkdir(parents=True)
    binary = tools / "bin/gate_probe-801749e5"
    binary.write_bytes(b"fixture binary")
    os.utime(binary, (1, 1))
    for name in ("gate_probe.nim", "replay_outcome.nim", "build_nim.sh"):
        (tools / name).write_text("new source")
    with pytest.raises(FileNotFoundError, match="receipt.json"):
        host.cached_probe()


@pytest.mark.parametrize(
    'parameter,bindings,declaration,assignment',
    [
        ('pvN', {'k_position__x': 'pvN'}, '', 'k_position__x=3'),
        ('pvId', {}, '', 'k_position__x=3'),
        ('efId', {'k_position__memory': 'efId'}, 'DIM k_position__memory(63)\n',
         'k_position__memory(0)=3'),
    ],
)
def test_assembly_rejects_parameter_capture_by_physical_storage(
    tmp_path, parameter, bindings, declaration, assignment,
):
    value = strategy(tmp_path)
    units = dict(TRIVIAL_UNITS)
    units['K.position'] = declaration + f'''SUB k_position__update()
 k_position__x=0
 k_position__helper(7)
 k_position__y=k_position__x
END SUB
SUB k_position__helper({parameter})
 {assignment}
END SUB
'''
    assert not sb.check_unit(value, 'K.position', units['K.position'])
    # Structured names remain namespaced; physical baseline names are no longer aliases.
    result = sb.assemble(value, units, RUNTIME, 'abcdef12-1', 'a' * 40)
    assert f'SUB k_position__helper({parameter})' in result['policy']


def test_assembly_preserves_parameter_shadowing_source_symbol(tmp_path):
    units = dict(TRIVIAL_UNITS)
    units['K.position'] += '''
SUB k_position__helper(k_position__x)
k_position__x=k_position__x+1
END SUB
'''
    result = sb.assemble(strategy(tmp_path), units, RUNTIME, 'abcdef12-1', 'a' * 40)
    assert 'SUB k_position__helper(k_position__x)\nk_position__x=k_position__x+1\nEND SUB' in result['policy']
    assert 'k_position__x = selfX' in result['policy']


@pytest.mark.parametrize('supplied_context', [False, True])
@pytest.mark.parametrize('first_failure', ['G2', 'G3'])
def test_verify_retries_use_fresh_episode_and_same_attempt_logs(
    tmp_path, monkeypatch, supplied_context, first_failure,
):
    import subprocess

    import gota_gates
    import gota_host
    import strategy_build as builds

    from games.gota.gates import GateContext, GateResult

    value = strategy(tmp_path)
    stage = tmp_path / 'stage'
    stage.mkdir()
    artifact = stage / 'policy.bas'
    artifact.write_text('candidate=1\n')
    mapping = sb.assemble(value, TRIVIAL_UNITS, RUNTIME, 'abcdef12-1', 'a' * 40)['map']
    builds.dump(stage / 'map.json', mapping)
    builds.dump(stage / 'work_order.json', {'source_path': str(value.path), 'source_hashes': {}})
    compiler = tmp_path / 'compiler'
    manifest = tmp_path / 'manifest.json'
    builds.dump(compiler / 'config.json', {'gates': {'manifest': str(manifest)}})
    monkeypatch.setattr(builds, 'COMPILER', compiler)
    monkeypatch.setattr(builds, 'build_path', lambda *a, **kw: stage)
    monkeypatch.setattr(builds, 'assert_inputs', lambda order: None)
    monkeypatch.setattr(gota_gates.g1_lint, 'run', lambda *a: GateResult(True, {}))
    monkeypatch.setattr(gota_gates.g4_screen, 'run', lambda *a: GateResult(True, {}))
    episodes, telemetry = [], []

    def episode_runner(command, **kwargs):
        directory = Path(command[command.index('--output-dir') + 1])
        directory.mkdir(parents=True)
        episodes.append(directory)
        if first_failure == 'G2' and len(episodes) == 1:
            raise subprocess.CalledProcessError(1, command)
        (directory / 'logs').mkdir()
        for seat in range(10):
            player = directory / 'players' / str(seat)
            player.mkdir(parents=True)
            (player / 'file').write_bytes(artifact.read_bytes())
            (directory / 'logs' / f'policy_agent_{seat}.log').write_text(
                'PWD v=2 t=0 r=1 c=1 i=12,0,0 h=0 p=0 f=0\n'
                f'Player slot {seat} completed.\n')
        (directory / 'logs/game.stdout.log').write_text(
            'scripts: 10/10 active, 10 decisions\nreplay saved: fixture (10 actions)\n')
        builds.dump(directory / 'results.json', {'scores': [0] * 10, 'total_xp': [0] * 10, 'ticks': 1})

    def budget(candidate, context):
        assert context.measurements == {}
        context.measurements['current-candidate'] = {'text': candidate.read_text(), 'native_build': {}}
        return GateResult(not (first_failure == 'G3' and len(episodes) == 1), {})

    real_g5 = gota_gates.g5_telemetry.run

    def g5(candidate, context):
        assert context.episode_dir == episodes[-1]
        assert context.measurements['current-candidate']['text'] == candidate.read_text()
        result = real_g5(candidate, context)
        telemetry.append((context.episode_dir, result))
        return result

    from games.gota.tools import native_build
    monkeypatch.setattr(native_build, 'validate', lambda *args, **kwargs: {})
    monkeypatch.setattr(gota_gates.g2_episode.subprocess, 'run', episode_runner)
    monkeypatch.setattr(gota_gates.g3_budget, 'run', budget)
    monkeypatch.setattr(gota_gates.g5_telemetry, 'run', g5)
    caller = GateContext(tmp_path / 'caller', manifest=manifest,
                         native_binary=gota_host.cached_probe(), episode_dir=tmp_path / 'old',
                         measurements={'old': 'evidence'}, compiler_map={'old': 'map'})
    context = caller if supplied_context else None
    first = gota_gates.verify('abcdef12-1', context=context)
    assert not first[first_failure]['passed']
    artifact.write_text('candidate=2\n')
    second = gota_gates.verify('abcdef12-1', context=context)
    assert all(row['passed'] for row in second.values()), second
    assert len(episodes) == 2 and episodes[0] != episodes[1]
    assert all(path.exists() for path in episodes)
    assert telemetry[-1][0] == episodes[1] and telemetry[-1][1].ok
    assert (episodes[1] / 'players/0/file').read_text() == 'candidate=2\n'
    if first_failure == 'G3':
        assert (episodes[0] / 'players/0/file').read_text() == 'candidate=1\n'
    assert all((path.parent / 'gates.json').is_file() for path in episodes)
    assert caller.episode_dir == tmp_path / 'old'
    assert caller.measurements == {'old': 'evidence'}
    assert caller.compiler_map == {'old': 'map'}
