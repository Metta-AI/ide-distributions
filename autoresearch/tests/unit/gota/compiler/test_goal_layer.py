"""Goal source, PRINT, wire validation and evidence attribution without an engine."""

import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[4] / "games/gota/compiler/tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pw_intent
import strategy_audit as audit
import strategy_basic as sb
import strategy_format as sf
from strategy_audit_baseline import audit_seat
from strategy_audit_runtime import reconstruct
from test_strategy_basic import (
    RICH,
    RICH_UNITS,
    TRIVIAL,
    TRIVIAL_UNITS,
    build,
    write_strategy,
)

GOALS = """## Goals

### G.survive
- Claim: Stay alive.
- Priority: 100
- Provenance: Local test specification.
- Evidence: No gameplay evidence yet.
- Status: specified (2026-10-07)
- Checks:
  - Result: Survive the episode. Reads: replay

### G.farm
- Claim: Gain experience.
- Priority: 50
- Provenance: Local test specification.
- Evidence: No gameplay evidence yet.
- Status: idea

"""


def annotated(text=TRIVIAL):
    return text.replace("## Strategy", GOALS + "## Strategy").replace(
        "DO `C.idle`(hold=`C.idle`.speed)",
        "DO `C.idle`(hold=`C.idle`.speed) FOR G.survive, G.farm",
    )


def errors(tmp_path, text):
    strategy = sf.parse_strategy(write_strategy(tmp_path, text))
    return {d.code for d in sf.lint_strategy(strategy) if d.level == "error"}


def line(goals="G.survive,G.farm", *, rule=1, cap=1, tick=0):
    return f"PWD v=3 g={goals} t={tick} r={rule} c={cap} i=12,0,0 h=0 p=0 f=0"


def test_goal_parser_and_optional_annotations(tmp_path):
    strategy, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    assert not [d for d in sf.lint_strategy(strategy) if d.level == "error"]
    assert strategy.rules[0].goal_refs == ("G.survive", "G.farm")
    goal = strategy.components["G.survive"]
    assert not goal.llm and goal.code is None
    assert goal.fields["Claim"] == "Stay alive."
    assert goal.interface == {"outputs": {}, "params": []}
    plain, _ = build(tmp_path / "plain", TRIVIAL, TRIVIAL_UNITS)
    assert plain.rules[0].goal_refs == ()
    assert result["map"]["goals"]["G.survive"]["priority"] == 100


@pytest.mark.parametrize("replacement,code", [
    ("G.missing", "unresolved-ref"),
    ("C.idle", "rule-goals"),
    ("G.survive, G.survive", "rule-goals"),
    ("G.survive,", "rule-syntax"),
    ("G.survive,, G.farm", "rule-syntax"),
])
def test_rule_goal_references_are_strict(tmp_path, replacement, code):
    text = annotated().replace("FOR G.survive, G.farm", "FOR " + replacement)
    assert code in errors(tmp_path, text)


@pytest.mark.parametrize("old,new,code", [
    ("- Claim: Stay alive.", "", "missing-field"),
    ("- Provenance: Local test specification.", "- Provenance:", "missing-field"),
    ("- Priority: 100", "- Priority: high", "goal-priority"),
    ("- Priority: 100", "- Priority: -1", "goal-priority"),
    ("- Priority: 100", "- Priority: 1001", "goal-priority"),
    ("- Priority: 100", "- Priority: 100\n- Params:\n  - x = 1 -- invalid", "field"),
])
def test_goal_fields_are_metadata_only(tmp_path, old, new, code):
    assert code in errors(tmp_path, annotated().replace(old, new))


def test_role_selector_migrates_without_changing_adaptations(tmp_path):
    strategy, _ = build(tmp_path, annotated(RICH), RICH_UNITS)
    assert strategy.rules[0].roles == ("half", ("low",))
    assert strategy.components["A.pin"].effect.duration == 10
    assert "rule-syntax" in errors(tmp_path / "old", RICH.replace("AS half=low", "FOR half=low"))
    assert "rule-roles" in errors(tmp_path / "unknown", RICH.replace("AS half=low", "AS half=missing"))


def test_generated_goal_labels_add_no_globals_or_arrays(tmp_path):
    strategy, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    _, plain = build(tmp_path / "plain", TRIVIAL, TRIVIAL_UNITS)
    for key in ("globals", "arrays", "array_cells"):
        assert result["budget"][key] == plain["budget"][key]
    assert sb.scan_basic(result["policy"]).globals == sb.scan_basic(plain["policy"]).globals
    assert result["budget"]["telemetry"]["events"] == plain["budget"]["telemetry"]["events"]
    assert result["budget"]["telemetry"]["bytes"] > plain["budget"]["telemetry"]["bytes"]
    tables = result["units"]["generated.tables"]
    printer = tables.split("SUB st__pwd_print()", 1)[1].split("END SUB", 1)[0]
    assert "IF rt__rule = 1 THEN" in printer
    assert 'PRINT "PWD v=3 g=G.survive,G.farm t="' in printer
    assert 'PRINT "PWD v=3 g=- t="' in printer
    events, nbytes = sb.line_cost(sb.pwd_items(strategy, strategy.rules[0].goal_refs))
    assert f"rt__pe = rt__pe + {events}" in printer
    assert f"rt__pb = rt__pb + {nbytes}" in printer
    _, again = build(tmp_path / "again", annotated(), TRIVIAL_UNITS)
    assert again["map"] == result["map"] and again["policy"] == result["policy"]
    assert result["map"]["rules"][0]["goal_refs"] == ["G.survive", "G.farm"]
    assert result["map"]["telemetry"]["decision_version"] == 3
    assert result["map"]["telemetry"]["lines"]["PWD"][0] == "g"


def test_goal_metadata_does_not_change_compiled_gameplay(tmp_path):
    _, original = build(tmp_path, annotated(), TRIVIAL_UNITS)
    changed = annotated().replace("- Priority: 100", "- Priority: 900").replace(
        "Stay alive.", "Stay safe.")
    _, result = build(tmp_path / "changed", changed, TRIVIAL_UNITS)
    assert result["policy"] == original["policy"]
    assert result["map"]["goals"] != original["map"]["goals"]


def test_worst_case_uses_longest_goal_list_and_rejects_overflow(tmp_path):
    extra = ("### G." + "x" * 480 + "\n- Claim: Test.\n- Priority: 1\n"
             "- Provenance: Test.\n- Status: idea\n\n## Strategy")
    text = annotated().replace("## Strategy", extra).replace(
        "FOR G.survive, G.farm", "FOR G.survive, G.farm, G." + "x" * 480)
    assert "telemetry-budget" in errors(tmp_path, text)
    strategy = sf.parse_strategy(write_strategy(tmp_path / "rich", annotated(RICH)))
    costs = [sb.line_cost(sb.pwd_items(strategy, goals)) for goals in
             [(), *(rule.goal_refs for rule in strategy.rules)]]
    assert sb.telemetry_worst_case(strategy)["lines"]["PWD"]["bytes"] == max(c[1] for c in costs)


def test_reader_validates_and_decodes_goals(tmp_path):
    _, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    assert pw_intent.parse_v2_line(line(), result["map"])["goal_refs"] == ["G.survive", "G.farm"]
    assert pw_intent.parse_telemetry_line(line(), result["map"])[0]["goal_refs"] == ["G.survive", "G.farm"]
    idle = pw_intent.parse_v2_line(line("-", rule=0, cap=0), result["map"])
    assert idle["goal_refs"] == []
    _, plain = build(tmp_path / "plain", TRIVIAL, TRIVIAL_UNITS)
    v2 = "PWD v=2 t=0 r=1 c=1 i=12,0,0 h=0 p=0 f=0"
    assert pw_intent.parse_v2_line(v2, plain["map"])["v"] == 2
    with pytest.raises(pw_intent.IntentError):
        pw_intent.parse_v2_line(v2, result["map"])
    with pytest.raises(pw_intent.IntentError):
        pw_intent.parse_v2_line(line(), plain["map"])


@pytest.mark.parametrize("goals", ["G.missing", "G.farm,G.survive", "G.survive",
                                    "G.survive,G.survive", "-", "G.survive,", "1,2"])
def test_reader_rejects_bad_labels(tmp_path, goals):
    _, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    with pytest.raises(pw_intent.IntentError):
        pw_intent.parse_v2_line(line(goals), result["map"])


def test_read_log_reconstruct_and_audit_attribution(tmp_path):
    strategy, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    mapping = result["map"]
    (tmp_path / "player-0.log").write_text(
        "PWE v=2 t=0 c=1 e=1 k=0\n" + line() +
        "\nPWP v=2 t=0 a=0 r=1 o=100 n=100 p=0\n")
    events, issues, _ = audit.read_log(tmp_path, 0, mapping)
    assert not issues
    states = {(t, 0): {"hp": 100} for t in range(3)}
    runtime = reconstruct(strategy, mapping, events, states, 0, 2)
    assert runtime["ticks"][1]["goal_refs"] == ["G.survive", "G.farm"]
    assert runtime["windows"][0]["goal_refs"] == ["G.survive", "G.farm"]
    evidence = audit.Evidence(mapping)
    evidence.attach_runtime("fake", 0, runtime)
    evidence.add("C.idle.1", "fake", 0, 1, "unmeasurable", "unsupported_test_predicate")
    assert evidence.rows[0]["goal_refs"] == ["G.survive", "G.farm"]
    traces = audit.goal_traces(mapping, runtime, [])
    assert traces["G.survive"]["decision_ticks"] == 2
    assert traces["G.survive"]["claim_status"] == "unmeasurable"
    assert traces["G.survive"]["reason"] == "no_evaluator_for_goal_claim"
    assert audit.goal_traces(mapping, runtime, [{"reason": "malformed_telemetry"}])[
        "G.survive"]["trace_status"] == "unmeasurable"
    (tmp_path / "player-0.log").write_text(line("G.missing") + "\n")
    assert audit.read_log(tmp_path, 0, mapping)[1][0]["reason"] == "malformed_telemetry"


def test_unannotated_rules_keep_plain_goal_label_in_goal_build(tmp_path):
    strategy, result = build(tmp_path, annotated(RICH), RICH_UNITS)
    cap = result["map"]["codes"]["capability"]["C.home"]
    event = pw_intent.parse_v2_line(
        f"PWD v=3 g=- t=0 r=1 c={cap} i=0,0,0 h=0 p=0 f=1", result["map"])
    assert event["goal_refs"] == []
    printer = result["units"]["generated.tables"].split("SUB st__pwd_print()", 1)[1]
    assert re.search(r'IF rt__rule = 1 THEN\s+PRINT "PWD v=3 g=- t="', printer)
    assert strategy.rules[0].goal_refs == ()


def test_declared_goals_without_rule_labels_keep_v2_policy(tmp_path):
    text = TRIVIAL.replace("## Strategy", GOALS + "## Strategy")
    _, result = build(tmp_path, text, TRIVIAL_UNITS)
    _, plain = build(tmp_path / "plain", TRIVIAL, TRIVIAL_UNITS)
    assert result["policy"] == plain["policy"]
    assert result["map"]["telemetry"] == plain["map"]["telemetry"]
    assert result["map"]["goals"]["G.survive"]["claim"] == "Stay alive."


def test_backticked_goals_and_role_condition_share_rule(tmp_path):
    text = annotated(RICH).replace("AS half=low", "AS half=low FOR `G.survive`")
    strategy, result = build(tmp_path, text, RICH_UNITS)
    assert strategy.rules[0].goal_refs == ("G.survive",)
    assert strategy.rules[0].condition == ("sit", "S.even_phase")
    assert result["map"]["rules"][0]["role_set"] == "half"


def test_goal_checks_stay_unmeasurable_even_with_identical_reference(tmp_path):
    strategy, result = build(tmp_path, annotated(), TRIVIAL_UNITS)
    evidence = audit.Evidence(result["map"])
    evidence.checks = {key: check for key, check in evidence.checks.items()
                       if check["component"].startswith("G.")}

    class EmptyTable:
        def itertuples(self):
            return iter(())

    class FakeEpisode:
        episode_id = "fake"

        def __getitem__(self, key):
            assert key == "visibility"
            return EmptyTable()

    runtime = {"ticks": [], "windows": [], "issues": []}
    audit_seat(FakeEpisode(), 0, {}, [], runtime, [], strategy,
               result["map"], result["map"], evidence)
    assert evidence.rows[0]["status"] == "unmeasurable"
    assert evidence.rows[0]["reason"] == "no_evaluator_for_goal_claim"
    assert evidence.rows[0]["goal_refs"] == ["G.survive"]


def test_goal_check_read_requires_wire_labels(tmp_path):
    text = annotated().replace("Reads: replay", "Reads: PWD.g")
    assert not errors(tmp_path, text)
    assert "reads-syntax" in errors(tmp_path / "plain", text.replace(" FOR G.survive, G.farm", ""))


def test_full_policy_print_branches_have_exact_budget_counters(tmp_path):
    _, result = build(tmp_path, annotated(RICH), RICH_UNITS)
    printer = result["policy"].split("SUB st__pwd_print()", 1)[1].split("END SUB", 1)[0]
    # Independent accounting over emitted BASIC, including the goal and idle literals.
    widths = {"worldTick": 11, "rt__rule": 1, "rt__cap": 1, "rt__held": 1,
              "rt__in0": 11, "rt__in1": 11, "rt__in2": 11,
              "rt__pver": 11, "rt__fw(0)": 1}
    costs = []
    for match in re.finditer(
        r'PRINT ([^\n]+)\n\s+rt__pe = rt__pe \+ (\d+)\n\s+rt__pb = rt__pb \+ (\d+)',
        printer,
    ):
        items = match[1].split("; ")
        nbytes = 1 + sum(len(item[1:-1]) if item.startswith('"') else widths[item]
                         for item in items)
        assert int(match[2]) == len(items) + 1
        assert int(match[3]) == nbytes
        costs.append((len(items) + 1, nbytes))
    assert len(costs) == 3  # Two rule branches plus the idle fallback.
    budget = result["budget"]["telemetry"]["lines"]["PWD"]
    assert budget["bytes"] == max(cost[1] for cost in costs)
    assert budget["events"] == max(cost[0] for cost in costs)
