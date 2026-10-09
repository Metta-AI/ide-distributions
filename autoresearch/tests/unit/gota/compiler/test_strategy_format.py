# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/tests/test_strategy_format.py
"""strategy_format: the STRATEGY.md grammar, hashing and lint rules the compiler depends on."""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[4] / "games/gota/compiler/tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_format as sf
from test_strategy_basic import RICH, TRIVIAL, write_strategy


def lint_codes(text: str, tmp_path: Path) -> set[str]:
    strategy = sf.parse_strategy(write_strategy(tmp_path, text))
    return {d.code for d in sf.lint_strategy(strategy) if d.level == "error"}


def test_trivial_parses_clean(tmp_path):
    strategy = sf.parse_strategy(write_strategy(tmp_path, TRIVIAL))
    assert [d for d in sf.lint_strategy(strategy) if d.level == "error"] == []
    assert list(strategy.components) == ["P.walkTo", "K.position", "C.idle", "ST.roles", "ST.rules", "ST.commitment"]
    pos = strategy.components["K.position"]
    assert pos.prefix == "k_position" and pos.code == 1 and pos.llm
    assert [(o.name, o.cells) for o in pos.outputs] == [("x", 1), ("y", 1)]
    assert pos.log == sf.LogSpec(("x", "y"), 24)
    idle = strategy.components["C.idle"]
    assert idle.inputs == ("hold",)
    assert [(c.name, c.kind, c.code) for c in idle.conditions] == [("waited", "done", 1), ("lost", "abort", 2)]
    assert idle.interface == {"outputs": {}, "params": ["speed"], "inputs": ["hold"],
                              "conditions": {"waited": 1, "lost": 2}}
    assert strategy.commitment == {"min_hold": 0, "preempt_margin": 0, "interrupt_at": 1001}
    rule = strategy.rules[0]
    assert (rule.id, rule.priority, rule.condition, rule.capability, rule.code) == ("R.idle", 100, ("always",), "C.idle", 1)
    assert rule.args == {"hold": sf.Arg("ref", ref="C.idle", name="speed")}
    assert strategy.roles == {"role": {"all": tuple(range(16))}}


def test_rich_parses_codes_roles_effects(tmp_path):
    strategy = sf.parse_strategy(write_strategy(tmp_path, RICH))
    assert [d for d in sf.lint_strategy(strategy) if d.level == "error"] == []
    codes = strategy.codes()
    assert codes["rule"] == {"R.home": 1, "R.idle": 2}
    assert codes["situation"] == {"S.even_phase": 1}
    assert codes["adaptation"] == {"A.demote": 1, "A.pin": 2}
    assert strategy.components["A.demote"].effect == sf.Effect("R.home", "-=", "drop", "window")
    assert strategy.components["A.pin"].effect == sf.Effect("R.home", "=", 950, 10)
    assert strategy.components["ST.commitment"].param("min_hold").tunable


def test_condition_precedence():
    assert sf.parse_condition("WHEN `S.a` OR `S.b` AND NOT `S.c`") == \
        ("or", ("sit", "S.a"), ("and", ("sit", "S.b"), ("not", ("sit", "S.c"))))
    assert sf.parse_condition("WHEN NOT (`S.a` OR `S.b`)") == ("not", ("or", ("sit", "S.a"), ("sit", "S.b")))
    with pytest.raises(ValueError):
        sf.parse_condition("WHEN `S.a` AND")
    with pytest.raises(ValueError):
        sf.parse_condition("WHEN `K.a`")


def test_hash_covers_compiled_fields_only(tmp_path):
    base = sf.parse_strategy(write_strategy(tmp_path / "a", TRIVIAL)).components["K.position"]
    meta = TRIVIAL.replace("- Status: specified (2026-09-30)\n\n## Capabilities",
                           "- Status: tested (2026-10-01)\n- Rationale: Because.\n- Evidence: link\n\n## Capabilities")
    assert meta != TRIVIAL
    same = sf.parse_strategy(write_strategy(tmp_path / "b", meta)).components["K.position"]
    assert same.text_hash == base.text_hash
    check_text = TRIVIAL.replace("logged position equals", "the logged position equals")
    assert sf.parse_strategy(write_strategy(tmp_path / "c", check_text)).components["K.position"].text_hash == base.text_hash
    reads = TRIVIAL.replace("Reads: `K.position`.x, `K.position`.y", "Reads: `K.position`.x")
    assert sf.parse_strategy(write_strategy(tmp_path / "d", reads)).components["K.position"].text_hash != base.text_hash
    spec = TRIVIAL.replace("into the outputs every tick", "into the outputs each tick")
    changed = sf.parse_strategy(write_strategy(tmp_path / "e", spec)).components["K.position"]
    assert changed.text_hash != base.text_hash
    assert "Rationale" not in same.compiled_text and "Status" not in same.compiled_text


@pytest.mark.parametrize("old, new, code", [
    ("  - speed = 12 ticks [6, 48, 6] -- hand-set", "  - speed = 12; other = 3", "params-syntax"),
    ("- Params:\n  - speed = 12 ticks [6, 48, 6] -- hand-set", "- Params: speed = 12 -- hand-set", "params-syntax"),
    ("  - speed = 12 ticks [6, 48, 6] -- hand-set", "  - speed = 12 ticks [6, 48, 6]", "params-syntax"),
    ("(hold=`C.idle`.speed)", "", "rule-args"),
    ("(hold=`C.idle`.speed)", "(hold=`C.idle`.nothing)", "rule-args"),
    ("(hold=`C.idle`.speed)", "(hold=that_heart)", "rule-syntax"),
    ("- Uses: `K.position`\n- Inputs", "- Uses: `K.missing`\n- Inputs", "unresolved-ref"),
    ("  - all = seats 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15", "  - all = seats 0,1,2", "roles-syntax"),
    ("- Spec: The single rule below.\n", "", "missing-field"),
    ("### K.position", "### K.pos__ition", "id"),
    ("- Log: x, y every 24 ticks", "- Log: x, z every 24 ticks", "log-syntax"),
    ("  - waited -- `hold` ticks passed since the start", "  - waited: hold ticks", "done-when-syntax"),
])
def test_grammar_errors(tmp_path, old, new, code):
    assert old in TRIVIAL
    assert code in lint_codes(TRIVIAL.replace(old, new), tmp_path)


def test_layering_and_usage(tmp_path):
    # a Situation that nothing uses, and a Capability no rule uses, are errors
    extra = TRIVIAL.replace("## Capabilities", """## Situations

### S.lonely
- Summary: Nothing uses this.
- Spec: Hold when `K.position` x is above 0.
- Uses: `K.position`
- Checks:
  - Believed: it holds. Reads: PWD.f
- Status: idea (2026-09-30)

## Capabilities""")
    assert "unused" in lint_codes(extra, tmp_path / "a")
    # Knowledge cannot use a Capability
    bad = TRIVIAL.replace("- Uses: `P.walkTo`", "- Uses: `P.walkTo`, `C.idle`")
    assert "layer" in lint_codes(bad, tmp_path / "b")
    # referenced in Spec but missing from Uses
    primitive = TRIVIAL.replace("Walk to the position in `K.position`", "Walk to `K.position` with `P.walkTo`")
    assert lint_codes(primitive, tmp_path / "c") == {"layer"}  # a Capability reaches primitives through Skills
    no_uses = TRIVIAL.replace("- Uses: `K.position`\n- Inputs", "- Inputs")
    assert "uses-missing" in lint_codes(no_uses, tmp_path / "d")


def test_reads_must_be_logged(tmp_path):
    bad = TRIVIAL.replace("Reads: PWE.e, PWE.k", "Reads: PWE.e, `K.position`.z")
    assert "unlogged-read" in lint_codes(bad, tmp_path / "a")
    bad_key = TRIVIAL.replace("Reads: PWE.e, PWE.k", "Reads: PWE.zz")
    assert "reads-syntax" in lint_codes(bad_key, tmp_path / "b")


def test_telemetry_budget_is_a_lint_error(tmp_path):
    big = TRIVIAL.replace("  - y -- own y in cm", "  - y -- own y in cm\n  - trail[40] -- recent x values") \
                 .replace("- Log: x, y every 24 ticks", "- Log: x, y, trail every 24 ticks")
    assert "telemetry-budget" in lint_codes(big, tmp_path)


def test_com_component_parses(tmp_path):
    com = TRIVIAL.replace("## Strategy", """## Communication

### COM.grenade_out
- Summary: Shout when a grenade leaves the hand.
- Spec: Shout the literal text "Grenade out!" on the tick the capability releases a grenade.
- Uses: `C.idle`
- Directions: send
- Outputs:
  - shouts -- shouts sent this tick
- Log: shouts
- Checks:
  - Result: teammates move. Reads: PWC.s
- Status: specified (2026-09-30)

## Strategy""")
    strategy = sf.parse_strategy(write_strategy(tmp_path / "a", com))
    assert [d for d in sf.lint_strategy(strategy) if d.level == "error"] == []
    comp = strategy.components["COM.grenade_out"]
    assert comp.llm and comp.code == 1 and comp.log == sf.LogSpec(("shouts",), 0)
    assert comp.directions == ("send",) and comp.interface["directions"] == ["send"]
    timed = com.replace("- Log: shouts\n", "- Log: shouts every 24 ticks\n")
    assert "log-syntax" in lint_codes(timed, tmp_path / "b")
    assert "missing-field" in lint_codes(com.replace("- Directions: send\n", ""), tmp_path / "c")
    assert "directions-syntax" in lint_codes(com.replace("- Directions: send", "- Directions: out"), tmp_path / "d")


@pytest.mark.parametrize("old, new", [
    ("  - x -- own x in cm", "  - status -- clashes with the ABI"),
    ("  - speed = 12 ticks [6, 48, 6] -- hand-set", "  - k_speed = 12 -- hand-set"),
    ("  - speed = 12 ticks [6, 48, 6] -- hand-set", "  - speed = 3000000000 -- too big"),
    ("- Inputs: hold", "- Inputs: in_hold"),
])
def test_reserved_names_and_int32(tmp_path, old, new):
    codes = lint_codes(TRIVIAL.replace(old, new), tmp_path)
    assert codes & {"reserved-name", "params-syntax"}


def test_effect_amount_bounds(tmp_path):
    from test_strategy_basic import RICH as rich
    assert lint_codes(rich, tmp_path / "a") == set()
    big = rich.replace("  - drop = 450 -- hand-set", "  - drop = 450 [0, 5000, 50] -- hand-set")
    assert "effect-syntax" in lint_codes(big, tmp_path / "b")
    negative_set = rich.replace("- Effect: `R.home` = 950 FOR 10", "- Effect: `R.home` = -5 FOR 10")
    assert "effect-syntax" in lint_codes(negative_set, tmp_path / "c")


def test_ste_warnings(tmp_path):
    text = TRIVIAL.replace("Copy `selfX` and `selfY` into the outputs every tick.",
                           "Copy the values; it should be fast.")
    strategy = sf.parse_strategy(write_strategy(tmp_path, text))
    warnings = {d.code for d in sf.lint_strategy(strategy) if d.level == "warning"}
    assert "ste" in warnings
