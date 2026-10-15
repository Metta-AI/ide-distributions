from ide_research_core.runtime.briefing import BriefingInputs, build_briefing
from runtime.workspace import make_spec

TASK = "K-01"
INBOUND = [
    {
        "id": "I-mine",
        "kind": "steer",
        "urgency": "now",
        "body": "Stop pushing mid lane; full text here.",
        "responsible_task": TASK,
    },
    {
        "id": "I-urgent-elsewhere",
        "kind": "steer",
        "urgency": "now",
        "body": "Secret details of another card's steer.",
        "responsible_task": "K-other",
    },
    {
        "id": "I-quiet-elsewhere",
        "kind": "reply",
        "urgency": "normal",
        "body": "Unrelated reply.",
        "responsible_task": "K-third",
    },
]


def _inputs(**overrides):
    values = {
        "signals": ["credits low: 3 XP left"],
        "inbound": INBOUND,
        "open_proposals": ["Proposal P-1: retire the jungle topic"],
        "planning_backlog": ["proposed_topic from I-9: tower dives"],
    }
    return BriefingInputs(**(values | overrides))


def test_marker_and_shared_part(canonical):
    spec = make_spec(refs=[f"ar://hypothesis/{canonical.hypothesis_id}"])
    text = build_briefing(canonical.root, spec, TASK, _inputs())
    assert text.splitlines()[0] == f"<!-- autoresearch-briefing task={TASK} -->"
    for expected in (spec.title, spec.why, "investigation", "AGENTS.md", "credits low"):
        assert expected in text


def test_non_planning_omits_plan_and_proposals(canonical):
    spec = make_spec(refs=[f"ar://hypothesis/{canonical.hypothesis_id}"])
    text = build_briefing(canonical.root, spec, TASK, _inputs())
    assert "Focus on last hits" not in text
    assert "retire the jungle" not in text and "tower dives" not in text


def test_planning_includes_plan_proposals_and_backlog(canonical):
    spec = make_spec(mode="planning")
    text = build_briefing(canonical.root, spec, TASK, _inputs())
    assert "Focus on last hits" in text
    assert "retire the jungle" in text and "tower dives" in text


def test_slice_summarises_refs_and_includes_only_their_topics(canonical):
    spec = make_spec(
        refs=[f"ar://hypothesis/{canonical.hypothesis_id}", f"ar://claim/{canonical.claim_id}"]
    )
    text = build_briefing(canonical.root, spec, TASK, _inputs())
    assert "kill range" in text  # hypothesis statement as its one-line summary
    assert "Creep damage is 10 per hit." in text  # claim body as its summary
    assert "Secure more last hits" in text  # the hypothesis' Topic objective
    assert "errors" in text  # ... with its guardrails
    assert "Profile the top opponents" not in text  # a Topic no ref points at


def test_card_without_topic_refs_has_no_topics(canonical):
    spec = make_spec(refs=[f"ar://claim/{canonical.claim_id}"])
    text = build_briefing(canonical.root, spec, TASK, _inputs())
    assert "Secure more last hits" not in text and "Profile the top opponents" not in text


def test_inbound_responsible_in_full_others_urgent_marked_handled(canonical):
    text = build_briefing(canonical.root, make_spec(), TASK, _inputs())
    assert "Stop pushing mid lane; full text here." in text
    assert "I-urgent-elsewhere" in text and "handled by K-other" in text
    assert "Secret details" not in text
    assert "I-quiet-elsewhere" not in text and "Unrelated reply" not in text


def test_empty_inputs(canonical):
    text = build_briefing(canonical.root, make_spec(), TASK, BriefingInputs())
    assert text.startswith(f"<!-- autoresearch-briefing task={TASK} -->")


def test_urgent_inbound_with_no_responsible_card_names_the_planning_card(canonical):
    inbound = [{"id": "I-x", "kind": "steer", "urgency": "now", "body": "B"}]
    text = build_briefing(canonical.root, make_spec(), "K-me", _inputs(inbound=inbound))
    assert "handled by None" not in text and "handled by the planning card" in text
