# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/tests/test_strategy_basic.py
"""strategy_basic: unit checks, generated tables, assembly, telemetry budget, and the runtime's
rule selection / adaptation arithmetic against the Python reference (in the real engine when the
local handoff engine is built)."""
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[4] / "games/gota/compiler/tools"
sys.path.insert(0, str(TOOLS))
import strategy_basic as sb  # noqa: E402
import strategy_format as sf  # noqa: E402

RUNTIME = TOOLS.parent / "strategy" / "compiler" / "runtime"

# Canonical trivial fixture (two LLM components). The driver's committed fixture copies this text.
TRIVIAL = """# Strategy: trivial

## Primitives

### P.walkTo
- Summary: Walk toward a point.
- Spec: Host call `walkTo(x, y)`.
- Status: specified (2026-09-30)

## Knowledge

### K.position
- Summary: Our own position.
- Spec: Copy `selfX` and `selfY` into the outputs every tick.
- Uses: `P.walkTo`
- Outputs:
  - x -- own x in cm
  - y -- own y in cm
- Log: x, y every 24 ticks
- Checks:
  - Believed: logged position equals the replay position. Reads: `K.position`.x, `K.position`.y
- Status: specified (2026-09-30)

## Capabilities

### C.idle
- Summary: Stand still for a while.
- Spec: Walk to the position in `K.position` at the start. Report done after `hold` ticks. Abort when the cog is more than 500 cm from the start point.
- Uses: `K.position`
- Inputs: hold
- Params:
  - speed = 12 ticks [6, 48, 6] -- hand-set
- Done when:
  - waited -- `hold` ticks passed since the start
- Abort when:
  - lost -- more than 500 cm from the start point
- Checks:
  - Acted properly: done follows start by hold ticks. Reads: PWE.e, PWE.k
- Status: specified (2026-09-30)

## Strategy

### ST.roles
- Summary: One role.
- Spec: Every seat has the same role.
- Roles:
  - all = seats 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
- Checks:
  - Acted: every seat runs the rules. Reads: replay
- Status: specified (2026-09-30)

### ST.rules
- Summary: Always idle.
- Spec: The single rule below.
- `R.idle` [100]: ALWAYS DO `C.idle`(hold=`C.idle`.speed)
- Checks:
  - Acted: the rule always holds. Reads: PWD.r
- Status: specified (2026-09-30)

### ST.commitment
- Summary: No commitment.
- Spec: Pure priority selection.
- Params:
  - min_hold = 0 ticks -- hand-set
  - preempt_margin = 0 -- hand-set
  - interrupt_at = 1001 -- hand-set, off
- Checks:
  - Acted properly: the audit re-runs selection. Reads: PWD.h
- Status: specified (2026-09-30)
"""

TRIVIAL_UNITS = {
    "K.position": """SUB k_position__update()
  k_position__x = selfX
  k_position__y = selfY
END SUB
""",
    "C.idle": """SUB c_idle__start()
  c_idle__t0 = worldTick
  c_idle__x0 = k_position__x
  c_idle__y0 = k_position__y
  walkTo(c_idle__x0, c_idle__y0)
END SUB

SUB c_idle__tick()
  c_idle__status = 0
  c_idle__dx = k_position__x - c_idle__x0
  c_idle__dy = k_position__y - c_idle__y0
  IF c_idle__dx * c_idle__dx + c_idle__dy * c_idle__dy > 250000 THEN
    c_idle__status = 2
    c_idle__cond = c_idle__k_lost
  END IF
  IF c_idle__status = 0 AND worldTick - c_idle__t0 >= c_idle__in_hold THEN
    c_idle__status = 1
    c_idle__cond = c_idle__k_waited
  END IF
END SUB
""",
}

# Situations, roles, commitment and two overlapping adaptations on one rule (+= then =). No belief
# log: 1 PWD + 3 PWE + 2 PWP already use the whole 64-event half budget.
RICH = TRIVIAL.replace("""- Log: x, y every 24 ticks
- Checks:
  - Believed: logged position equals the replay position. Reads: `K.position`.x, `K.position`.y""", """- Checks:
  - Result: the position is used. Reads: replay""").replace("## Capabilities", """## Situations

### S.even_phase
- Summary: The first half of every 48-tick window.
- Spec: Hold when `worldTick` mod 48 is below 24.
- Uses: `K.position`
- Checks:
  - Believed: matches the tick arithmetic. Reads: PWD.f
- Status: specified (2026-09-30)

## Capabilities

### C.home
- Summary: Walk home.
- Spec: Walk to `homeX`, `homeY` every tick. Never report done.
- Uses: `K.position`
- Done when: never
- Checks:
  - Acted: runs in the even phase. Reads: PWD.c
- Status: specified (2026-09-30)
""").replace("## Strategy", """## Strategy""").replace("""### ST.roles
- Summary: One role.
- Spec: Every seat has the same role.
- Roles:
  - all = seats 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15""", """### ST.roles
- Summary: Two halves.
- Spec: Low seats and high seats.
- Roles half:
  - low = seats 0,1,2,3,4,5,6,7
  - high = seats 8,9,10,11,12,13,14,15""").replace("""- `R.idle` [100]: ALWAYS DO `C.idle`(hold=`C.idle`.speed)""",
                                               """- `R.home` [500]: WHEN `S.even_phase` DO `C.home` AS half=low
- `R.idle` [100]: ALWAYS DO `C.idle`(hold=`C.idle`.speed)""").replace("""  - min_hold = 0 ticks -- hand-set
  - preempt_margin = 0 -- hand-set
  - interrupt_at = 1001 -- hand-set, off""", """  - min_hold = 20 ticks [0, 48, 4] -- hand-set
  - preempt_margin = 450 -- hand-set
  - interrupt_at = 900 -- hand-set""") + """
## Adaptations

### A.demote
- Summary: Demote going home at the start of each even phase.
- Spec: Fire while `S.even_phase` holds.
- Uses: `S.even_phase`, `R.home`
- Params:
  - drop = 450 -- hand-set
  - window = 30 ticks -- hand-set
- Effect: `R.home` -= drop FOR window
- Checks:
  - Acted: fires each phase. Reads: PWP.a
- Status: specified (2026-09-30)

### A.pin
- Summary: Pin going home briefly.
- Spec: Fire when `worldTick` mod 96 is below 2.
- Uses: `K.position`, `R.home`
- Effect: `R.home` = 950 FOR 10
- Checks:
  - Acted: fires every 96 ticks. Reads: PWP.n
- Status: specified (2026-09-30)
"""

RICH_UNITS = dict(TRIVIAL_UNITS) | {
    "S.even_phase": """SUB s_even_phase__eval()
  s_even_phase__on = 0
  IF worldTick MOD 48 < 24 THEN
    s_even_phase__on = 1
  END IF
END SUB
""",
    "C.home": """SUB c_home__start()
  c_home__starts = c_home__starts + 1
END SUB

SUB c_home__tick()
  walkTo(homeX, homeY)
  c_home__status = 0
END SUB
""",
    "A.demote": """SUB a_demote__eval()
  a_demote__fire = s_even_phase__on
END SUB
""",
    "A.pin": """SUB a_pin__eval()
  a_pin__fire = 0
  IF worldTick MOD 96 < 2 THEN
    a_pin__fire = 1
  END IF
END SUB
""",
}


def write_strategy(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "STRATEGY.md"
    path.write_text(text)
    return path


def build(tmp_path: Path, text: str, units: dict) -> tuple[sf.Strategy, dict]:
    strategy = sf.parse_strategy(write_strategy(tmp_path, text))
    return strategy, sb.assemble(strategy, units, RUNTIME, "abc1234-1", "abc1234")


def unit_errors(strategy, comp_id, text) -> set[str]:
    return {d.code for d in sb.check_unit(strategy, comp_id, text)}


# ---------------------------------------------------------------- assembly

def test_assemble_is_deterministic_and_copies_runtime_verbatim(tmp_path):
    strategy, first = build(tmp_path, TRIVIAL, TRIVIAL_UNITS)
    _, second = build(tmp_path / "again", TRIVIAL, TRIVIAL_UNITS)
    assert first["policy"] == second["policy"]
    assert first["units"]["runtime.lib"] == (RUNTIME / "lib.bas").read_text()
    assert first["units"]["runtime.main"] == (RUNTIME / "main.bas").read_text()
    assert first["policy"].rstrip().endswith((RUNTIME / "main.bas").read_text().rstrip())
    unit = first["units"]["K.position"]
    assert unit.splitlines()[0] == f"' unit K.position {strategy.components['K.position'].text_hash} generated, do not edit"
    assert set(first["units"]) == {"K.position", "C.idle", "runtime.lib", "runtime.main", "generated.tables"}
    m = first["map"]
    assert m["codes"]["rule"] == {"R.idle": 1} and m["codes"]["capability"] == {"C.idle": 1}
    assert m["components"]["K.position"]["log_fields"] == [{"name": "x", "cells": 1}, {"name": "y", "cells": 1}]
    assert m["components"]["C.idle"]["unit_sha256"] == sb._sha(first["units"]["C.idle"])
    assert m["telemetry"]["lines"]["PWD"] == ["t", "r", "c", "i", "h", "p", "f"]


def test_agent_header_is_replaced(tmp_path):
    units = dict(TRIVIAL_UNITS, **{"K.position": "' unit K.position sha256:stale\n" + TRIVIAL_UNITS["K.position"]})
    strategy, out = build(tmp_path, TRIVIAL, units)
    assert out["units"]["K.position"].count("' unit ") == 1
    assert "stale" not in out["units"]["K.position"]


def test_generated_tables(tmp_path):
    strategy, out = build(tmp_path, RICH, RICH_UNITS)
    tables = out["units"]["generated.tables"]
    assert "c_idle__speed = 12 ' @tune 6 48 6" in tables
    assert "st_commitment__min_hold = 20 ' @tune 0 48 4" in tables
    assert "rt__ok(1) = (rt__flag(1) AND (st__role_half = 1))" in tables
    assert "rt__ok(2) = 1" in tables
    assert "rt__a_amt(1) = 0 - a_demote__drop" in tables and "rt__a_op(2) = 2" in tables
    assert "rt__a_dur(1) = a_demote__window" in tables and "rt__a_dur(2) = 10" in tables
    assert "c_idle__in_hold = c_idle__speed" in tables
    assert "telemetryOff" not in tables  # the public kill switch is never reset by generated code
    st = out["map"]["components"]["ST.rules"]
    assert st["unit"] == "generated.tables" and st["generated"] and st["unit_sha256"] == sb._sha(tables)
    assert out["map"]["components"]["P.walkTo"]["unit"] is None
    nots = sb._cond_expr(sf.parse_condition("WHEN NOT `S.a`"), {"S.a": 3})
    assert nots == "(1 - rt__flag(3))"


def test_missing_and_extra_units_fail(tmp_path):
    strategy = sf.parse_strategy(write_strategy(tmp_path, TRIVIAL))
    with pytest.raises(sb.BuildError) as missing:
        sb.assemble(strategy, {"K.position": TRIVIAL_UNITS["K.position"]}, RUNTIME, "x-1", "x")
    assert {d.code for d in missing.value.diagnostics} == {"unit-missing"}
    with pytest.raises(sb.BuildError) as extra:
        sb.assemble(strategy, dict(TRIVIAL_UNITS, **{"S.none": ""}), RUNTIME, "x-1", "x")
    assert "unit-extra" in {d.code for d in extra.value.diagnostics}
    assert isinstance(extra.value, ValueError)


# ---------------------------------------------------------------- unit checks

@pytest.mark.parametrize("text, code", [
    ("SUB k_position__update()\n  c_idle__t0 = 1\nEND SUB\n", "unit-namespace"),           # writes another unit
    ("SUB k_position__update()\n  scratch = selfX\nEND SUB\n", "unit-namespace"),            # bare global write
    ("SUB k_position__update()\n  k_position__x = scratch\nEND SUB\n", "unit-bare-name"),    # bare global read
    ("SUB k_position__update()\n  PRINT selfX\nEND SUB\n", "unit-print"),
    ("k_position__x = 1\nSUB k_position__update()\nEND SUB\n", "unit-structure"),          # top-level code
    ("SUB k_position__update()\n  DIM k_position__a(3)\nEND SUB\n", "unit-structure"),
    ("SUB k_position__other()\nEND SUB\n", "unit-abi"),                                     # missing update
    ("SUB k_position__update(a)\nEND SUB\n", "unit-abi"),
    ("SUB k_position__update()\n  rt__rule = 0\nEND SUB\n", "unit-namespace"),
    ("SUB helper()\nEND SUB\nSUB k_position__update()\nEND SUB\n", "unit-namespace"),
])
def test_check_unit_rejects(tmp_path, text, code):
    strategy = sf.parse_strategy(write_strategy(tmp_path, TRIVIAL))
    assert code in unit_errors(strategy, "K.position", text)


def test_check_unit_capability_rules(tmp_path):
    strategy = sf.parse_strategy(write_strategy(tmp_path, RICH))
    assert unit_errors(strategy, "C.idle", TRIVIAL_UNITS["C.idle"]) == set()
    writes_input = TRIVIAL_UNITS["C.idle"].replace("c_idle__t0 = worldTick", "c_idle__in_hold = 3")
    assert "unit-namespace" in unit_errors(strategy, "C.idle", writes_input)
    writes_param = TRIVIAL_UNITS["C.idle"].replace("c_idle__t0 = worldTick", "c_idle__speed = 3")
    assert "unit-namespace" in unit_errors(strategy, "C.idle", writes_param)
    reads_private = TRIVIAL_UNITS["C.idle"].replace("c_idle__t0 = worldTick", "c_idle__t0 = k_position__secret")
    assert "unit-namespace" in unit_errors(strategy, "C.idle", reads_private)
    calls_abi = TRIVIAL_UNITS["C.idle"].replace("  walkTo(c_idle__x0, c_idle__y0)", "  k_position__update()")
    assert "unit-call" in unit_errors(strategy, "C.idle", calls_abi)
    reads_unused_dep = TRIVIAL_UNITS["C.idle"].replace("c_idle__t0 = worldTick", "c_idle__t0 = s_even_phase__on")
    assert "unit-namespace" in unit_errors(strategy, "C.idle", reads_unused_dep)  # S.even_phase not in Uses
    no_status = "SUB c_home__start()\nEND SUB\nSUB c_home__tick()\nEND SUB\n"
    assert "unit-abi" in unit_errors(strategy, "C.home", no_status)


def test_skill_sub_calls_allowed_from_dependents(tmp_path):
    text = TRIVIAL.replace("## Capabilities", """## Skills

### SK.stand
- Summary: Stand at a point.
- Spec: Walk to the point.
- Uses: `P.walkTo`
- Code: skills/stand/skill.bas
- Checks:
  - Acted properly: walks there. Reads: replay
- Status: specified (2026-09-30)

## Capabilities""").replace("- Uses: `K.position`\n- Inputs", "- Uses: `K.position`, `SK.stand`\n- Inputs")
    skill = "' authored skill\r\nSUB sk_stand__go(sk_stand__x, sk_stand__y)\r\n  walkTo(sk_stand__x, sk_stand__y)\r\nEND SUB"
    (tmp_path / "skills" / "stand").mkdir(parents=True)
    (tmp_path / "skills" / "stand" / "skill.bas").write_bytes(skill.encode())
    unit = TRIVIAL_UNITS["C.idle"].replace("walkTo(c_idle__x0, c_idle__y0)", "sk_stand__go(c_idle__x0, c_idle__y0)")
    strategy, out = build(tmp_path, text, dict(TRIVIAL_UNITS, **{"C.idle": unit}))
    assert out["units"]["SK.stand"] == skill  # verbatim (CRLF, no final newline), no header
    assert "' ==== SK.stand ====\n" + skill + "\n\n" in out["policy"]
    assert strategy.components["SK.stand"].interface["subs"] == {"sk_stand__go": 2}
    assert out["map"]["skills"]["SK.stand"]["sha256"] == sb._sha(skill)


# ---------------------------------------------------------------- telemetry budget

def test_worst_case_counts(tmp_path):
    strategy = sf.parse_strategy(write_strategy(tmp_path, RICH))
    worst = sb.telemetry_worst_case(strategy)
    # Runtime-controlled values use exact digit bounds (rule, capability, held, flag word, adaptation
    # and rule codes, priorities clamped to 0..1000); the tick, inputs and the priority version keep 11.
    assert worst["lines"]["PWD"] == {"count": 1, "events": 19, "bytes": 90}
    assert worst["lines"]["PWE"]["events"] == 19
    assert worst["lines"]["PWP"] == {"count": 2, "events": 26, "bytes": 116}
    assert worst["lines"]["PWB"]["events"] == 0
    assert worst["events"] == 19 + 19 + 26 == 64
    trivial = sb.telemetry_worst_case(sf.parse_strategy(write_strategy(tmp_path / "t", TRIVIAL)))
    assert trivial["lines"]["PWB"] == {"count": 1, "events": 7, "bytes": 52}  # 3 + 2 values


def test_log_offsets_spread_coincident_logs(tmp_path):
    text = TRIVIAL.replace("## Capabilities", """### K.clock
- Summary: The tick.
- Spec: Copy `worldTick`.
- Outputs:
  - tick -- world tick
- Log: tick every 24 ticks
- Checks:
  - Believed: equals the tick. Reads: `K.clock`.tick
- Status: specified (2026-09-30)

## Capabilities""")
    strategy = sf.parse_strategy(write_strategy(tmp_path, text))
    offsets, (events, _) = sb.log_offsets(strategy)
    assert offsets["K.position"] != offsets["K.clock"]
    assert events == 7  # never both on one tick


# ---------------------------------------------------------------- reference model

COMMIT = {"min_hold": 6, "preempt_margin": 50, "interrupt_at": 900}


def test_reference_select_ties_and_idle():
    s = sb.reference_select(sb.SelectState(), [False, True, True], [0, 100, 100], 0, COMMIT)
    assert (s.rule, s.new, s.held) == (1, True, 0)  # tie -> lowest code
    s = sb.reference_select(s, [False, False, False], [0, 100, 100], 1, COMMIT)
    assert (s.rule, s.new, s.preempted) == (0, False, 1)  # condition false -> idle, preempted


def test_reference_select_commitment():
    s = sb.SelectState(rule=2, since=10)
    prio = [0, 200, 100, 950]
    held = sb.reference_select(s, [False, True, True, False], prio, 13, COMMIT)
    assert (held.rule, held.held) == (2, 1)  # better rule, but min_hold not reached
    switched = sb.reference_select(s, [False, True, True, False], prio, 16, COMMIT)
    assert (switched.rule, switched.new, switched.preempted) == (1, True, 2)
    margin = sb.reference_select(s, [False, True, True, False], [0, 149, 100, 0], 30, COMMIT)
    assert margin.rule == 2  # 149 < 100 + 50
    interrupt = sb.reference_select(s, [False, True, True, True], prio, 11, COMMIT)
    assert interrupt.rule == 3  # >= interrupt_at ignores min_hold
    ended = sb.reference_select(sb.SelectState(rule=2, since=10, ended=True), [False, False, True, False], prio, 11, COMMIT)
    assert (ended.rule, ended.new, ended.preempted) == (2, True, 0)  # same rule, new activation


def test_reference_priority_overlapping_effects():
    assert sb.reference_priority(500, [("add", -450)]) == 50
    assert sb.reference_priority(500, [("add", -450), ("set", 700)]) == 700
    assert sb.reference_priority(500, [("set", 700), ("add", -450)]) == 250
    assert sb.reference_priority(100, [("add", -450)]) == 0
    assert sb.reference_priority(900, [("add", 450)]) == 1000


# ---------------------------------------------------------------- the real engine

LINE = re.compile(r"^(PW[DPEBC]) v=2 (.*)$")


def parse_log(path: Path) -> list[tuple[str, dict]]:
    return parse_log_text(path.read_text())


def parse_log_text(text: str) -> list[tuple[str, dict]]:
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw)
        if m:
            fields = dict(item.split("=", 1) for item in m.group(2).split(" "))
            out.append((m.group(1), {k: [int(x) for x in v.split(",")] if "," in v or k in ("i", "f", "d") else int(v)
                                     for k, v in fields.items()}))
    return out


def replay_reference(strategy: sf.Strategy, seat: int, lines: list[tuple[str, dict]]) -> int:
    """Re-run selection and adaptation arithmetic from the logged inputs; assert the runtime agreed.
    Returns the number of ticks checked."""
    defaults = {r.code: r.priority for r in strategy.rules}
    prio = [0] + [defaults[c] for c in sorted(defaults)]
    adaptations = {c.code: c for c in strategy.of_kind("A")}
    active: dict[int, bool] = {code: False for code in adaptations}
    by_tick: dict[int, list[tuple[str, dict]]] = {}
    for kind, f in lines:
        by_tick.setdefault(f["t"], []).append((kind, f))
    roles = strategy.roles
    state = sb.SelectState()
    flags = [0]
    logged_rule = logged_held = None
    checked = 0
    for t in range(0, max(by_tick) + 1):
        events = by_tick.get(t, [])
        for kind, f in events:
            if kind == "PWP" and f["a"] > 0:
                code = f["a"]
                active[code] = not active[code]
                rule_code = strategy.rules[[r.id for r in strategy.rules].index(adaptations[code].effect.rule)].code
                effects = []
                for a_code in sorted(adaptations):
                    comp = adaptations[a_code]
                    if active[a_code] and comp.effect.rule == strategy.rules[rule_code - 1].id:
                        amount = comp.effect.amount if isinstance(comp.effect.amount, int) else comp.param(comp.effect.amount).value
                        effects.append(("set", amount) if comp.effect.op == "=" else
                                       ("add", -amount if comp.effect.op == "-=" else amount))
                expected = sb.reference_priority(defaults[rule_code], effects)
                assert (f["r"], f["o"], f["n"]) == (rule_code, prio[rule_code], expected), (t, f)
                prio[rule_code] = expected
        pwd = [f for kind, f in events if kind == "PWD"]
        if pwd:
            flags = pwd[0]["f"]
            logged_rule, logged_held = pwd[0]["r"], pwd[0]["h"]
        ok = [False]
        for rule in strategy.rules:
            holds = _eval(rule.condition, strategy, flags)
            if rule.roles:
                set_name, names = rule.roles
                holds = holds and any(seat in roles[set_name][n] for n in names)
            ok.append(holds)
        state = sb.reference_select(state, ok, prio, t, strategy.commitment)
        pwe = [(f["e"], f["c"]) for kind, f in events if kind == "PWE"]
        assert ((4, strategy.rules[state.preempted - 1].code) in [(e, r) for e, r in pwe]) == bool(state.preempted) \
            or not state.preempted, (t, pwe)
        assert any(e == 1 for e, _ in pwe) == state.new, (t, pwe, state)
        assert (state.rule, state.held) == (logged_rule, logged_held), (t, state, logged_rule, logged_held)
        if any(e in (2, 3) for e, _ in pwe):
            state = sb.SelectState(rule=state.rule, since=state.since, ended=True, held=state.held)
        checked += 1
    return checked


def _eval(cond, strategy, flags) -> bool:
    kind = cond[0]
    if kind == "always":
        return True
    if kind == "sit":
        code = strategy.components[cond[1]].code - 1
        return bool(flags[code // 31] >> (code % 31) & 1)
    if kind == "not":
        return not _eval(cond[1], strategy, flags)
    left, right = _eval(cond[1], strategy, flags), _eval(cond[2], strategy, flags)
    return left and right if kind == "and" else left or right


@pytest.mark.parametrize("interrupt_at", [900, 1001])  # 900: A.pin's 950 interrupts; 1001: hold and margin decide
def test_runtime_matches_reference_in_engine(tmp_path, interrupt_at):
    import pw_cli
    import pw_intent
    try:
        pw_intent._handoff(pw_intent.pe.DEFAULT_TAG)
    except pw_cli.EnvironmentMissing:
        pytest.skip("handoff engine not built (paintbot_pw_lab/tools/build_tools.sh)")
    text = RICH.replace("  - interrupt_at = 900 -- hand-set", f"  - interrupt_at = {interrupt_at} -- hand-set")
    strategy, out = build(tmp_path / "src", text, RICH_UNITS)
    policy = tmp_path / "policy.bas"
    policy.write_text(out["policy"])
    pw_intent.record_episode(policy, policy, 3, 0, tmp_path / "rec", ticks=300)
    for seat in (0, 9):  # one low-role and one high-role seat
        log = tmp_path / "rec" / f"player-{seat}.log"
        text = log.read_text()
        assert "BASIC error" not in text
        lines = parse_log(log)
        kinds = {kind for kind, _ in lines}
        assert {"PWD", "PWE", "PWP"} <= kinds
        snapshot = [f for kind, f in lines if kind == "PWP" and f["a"] == 0]
        assert [(f["r"], f["o"], f["p"]) for f in snapshot] == [(1, 500, 0), (2, 100, 0)]
        assert replay_reference(strategy, seat, lines) >= 290
        pwd_ticks = [f["t"] for kind, f in lines if kind == "PWD"]
        assert all(b - a <= 24 for a, b in zip(pwd_ticks, pwd_ticks[1:]))


def test_belief_lines_in_engine(tmp_path):
    import pw_cli
    import pw_intent
    try:
        pw_intent._handoff(pw_intent.pe.DEFAULT_TAG)
    except pw_cli.EnvironmentMissing:
        pytest.skip("handoff engine not built (paintbot_pw_lab/tools/build_tools.sh)")
    strategy, out = build(tmp_path / "src", TRIVIAL, TRIVIAL_UNITS)
    policy = tmp_path / "policy.bas"
    policy.write_text(out["policy"])
    pw_intent.record_episode(policy, policy, 5, 0, tmp_path / "rec", ticks=100)
    lines = parse_log(tmp_path / "rec" / "player-0.log")
    beliefs = [f for kind, f in lines if kind == "PWB"]
    assert [f["t"] for f in beliefs] == [0, 24, 48, 72, 96]
    assert all(f["k"] == 1 and len(f["d"]) == 2 for f in beliefs)
    assert replay_reference(strategy, 0, lines) >= 95


SHOUTS = TRIVIAL.replace("## Strategy", """## Communication

### COM.grenade_out
- Summary: Shout when a capability starts.
- Spec: Shout the literal text "Grenade out!" on the tick an activation starts.
- Uses: `C.idle`
- Directions: send
- Checks:
  - Result: teammates hear it. Reads: PWC.s
- Status: specified (2026-09-30)

## Strategy""")

SHOUT_UNIT = """SUB com_grenade_out__send()
  com_grenade_out__sent = 0
  IF rt__since = worldTick AND rt__cap > 0 THEN
    shout(strNew("Grenade out!"))
    com_grenade_out__sent = 1
  END IF
END SUB
"""


def test_send_only_com_budget_and_abi(tmp_path):
    strategy, out = build(tmp_path, SHOUTS, dict(TRIVIAL_UNITS, **{"COM.grenade_out": SHOUT_UNIT}))
    worst = out["budget"]["telemetry"]["lines"]["PWC"]
    assert worst == {"count": 1, "events": 6, "bytes": sb._pwc_cost(0, 1, 1)[1]}
    assert "com_grenade_out__recv" not in out["units"]["generated.tables"]
    with_recv = SHOUT_UNIT + "SUB com_grenade_out__recv()\nEND SUB\n"
    assert "unit-abi" in unit_errors(strategy, "COM.grenade_out", with_recv)


def _record(tmp_path, text, units, ticks, edit=None):
    import pw_cli
    import pw_intent
    try:
        pw_intent._handoff(pw_intent.pe.DEFAULT_TAG)
    except pw_cli.EnvironmentMissing:
        pytest.skip("handoff engine not built (paintbot_pw_lab/tools/build_tools.sh)")
    strategy, out = build(tmp_path / "src", text, units)
    policy = tmp_path / "policy.bas"
    policy.write_text(edit(out["policy"]) if edit else out["policy"])
    pw_intent.record_episode(policy, policy, 5, 0, tmp_path / "rec", ticks=ticks)
    return strategy, (tmp_path / "rec" / "player-0.log").read_text()


def test_shout_and_pwc_in_engine(tmp_path):
    strategy, text = _record(tmp_path, SHOUTS, dict(TRIVIAL_UNITS, **{"COM.grenade_out": SHOUT_UNIT}), 60)
    assert "BASIC error" not in text
    starts = [f["t"] for kind, f in parse_log_text(text) if kind == "PWE" and f["e"] == 1]
    sends = [f for kind, f in parse_log_text(text) if kind == "PWC"]
    assert starts and [f["t"] for f in sends] == starts
    assert all((f["m"], f["s"], f["w"], f["d"]) == (1, 1, 0, [0]) for f in sends)


def test_kill_switch_silences_every_line(tmp_path):
    _, text = _record(tmp_path, RICH, RICH_UNITS, 120,
                      edit=lambda policy: policy.replace("' ==== runtime.main ====\n",
                                                         "' ==== runtime.main ====\ntelemetryOff = 1\n"))
    assert "BASIC error" not in text
    assert not [line for line in text.splitlines() if line.startswith("PW")]
