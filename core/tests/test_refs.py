import pytest

from research_core import refs

CASES = [
    ("G-3", "record", "G-3", "goal", 3, None),
    ("T-42#failure", "record", "T-42", "task", 42, "failure"),
    ("X-5#methods", "record", "X-5", "experiment", 5, "methods"),
    ("C-120", "record", "C-120", "claim", 120, None),
    ("V-7", "record", "V-7", "version", 7, None),
    ("D-2", "record", "D-2", "directive", 2, None),
    ("I-9", "record", "I-9", "intent", 9, None),
    ("ply_f39295d0-ac38-4a62-ac4f-e1d62d7dc9d1", "player", None, None, None, None),
    ("league_3c60897b-25cf-4b37-9d1a-8554c1198f28", "league", None, None, None, None),
    ("div_abc", "division", None, None, None, None),
    ("ereq_fake_00001", "episode", None, None, None, None),
    ("xreq_12ab", "xp_request", None, None, None, None),
    ("sub_9f", "submission", None, None, None, None),
    ("cow_pudge-wars-basic", "coworld", None, None, None, None),
    ("2f1e4c6a-1111-4222-8333-444455556666", "policy_version", None, None, None, None),
    ("my-policy:v3", "policy_label", None, None, None, None),
    (
        "https://softmax.com/leagues/league_3c60897b-25cf-4b37-9d1a-8554c1198f28",
        "league",
        None,
        None,
        None,
        None,
    ),
    (
        "https://softmax.com/api/observatory/v2/episode-requests/ereq_01",
        "episode",
        None,
        None,
        None,
        None,
    ),
    (
        "https://softmax.com/policies/2f1e4c6a-1111-4222-8333-444455556666",
        "policy_version",
        None,
        None,
        None,
        None,
    ),
    ("https://softmax.com/docs/skill.md", "url", None, None, None, None),
    ("hello world", "unknown", None, None, None, None),
    ("G-0", "unknown", None, None, None, None),
    ("g-3", "unknown", None, None, None, None),
    ("GX-1", "unknown", None, None, None, None),
]


@pytest.mark.parametrize("text,kind,value,record_kind,number,fragment", CASES)
def test_parse(text, kind, value, record_kind, number, fragment):
    ref = refs.parse(text)
    assert ref.kind == kind
    if value is not None:
        assert ref.value == value
    assert ref.record_kind == record_kind
    assert ref.number == number
    assert ref.fragment == fragment
    assert ref.display == text


def test_parse_uuid_is_lowercased():
    assert refs.parse("2F1E4C6A-1111-4222-8333-444455556666").value.startswith("2f1e")


def test_parse_many_finds_every_kind_once():
    text = (
        "Compare V-7 with V-7 against the champion of league_abc "
        "(see https://softmax.com/divisions/div_gold and C-12#evidence-3, policy best:v2, "
        "episode ereq_1)."
    )
    found = refs.parse_many(text)
    kinds = [(r.kind, r.value) for r in found]
    assert kinds.count(("record", "V-7")) == 1
    assert ("league", "league_abc") in kinds
    assert ("division", "div_gold") in kinds
    assert ("record", "C-12") in kinds
    assert ("policy_label", "best:v2") in kinds
    assert ("episode", "ereq_1") in kinds


def test_parse_many_ignores_plain_words():
    assert refs.parse_many("nothing to see here") == []
