"""The coach chat's in-memory proposal store (plugin.proposals)."""

import json
import re
import threading

import pytest

from autoresearch.plugin import proposals

STEER = {"text": "Stop kill-steals", "urgency": "now"}


def _shown(store, session="s1", now=0.0, payload=None):
    proposal = store.propose(session, "steer", payload or STEER, "harmless", now)
    store.show(session, "turn-1", now)
    return proposal


def test_ids_are_p_and_hex():
    proposal = proposals.Store().propose("s1", "steer", STEER, "x", 0.0)
    assert re.fullmatch(proposals.ID_PATTERN, proposal.id)
    assert re.fullmatch(r"P-[0-9a-f]{6}", proposal.id)


def test_take_needs_shown_same_session_unexpired_and_works_once():
    store = proposals.Store()
    proposal = store.propose("s1", "steer", STEER, "x", 0.0)
    assert store.take("s1", proposal.id, 1.0) is None  # not shown yet
    store.show("s1", "turn-1", 1.0)
    assert store.take("s2", proposal.id, 1.0) is None
    taken = store.take("s1", proposal.id, 1.0)
    assert taken is not None and taken.payload == STEER and taken.shown_turn == "turn-1"
    assert store.take("s1", proposal.id, 1.0) is None
    assert store.was_taken("s1", proposal.id) and not store.was_taken("s2", proposal.id)


def test_a_proposal_expires_after_15_minutes():
    store = proposals.Store()
    proposal = _shown(store)
    assert store.take("s1", proposal.id, proposals.EXPIRY_SECONDS) is None
    fresh = _shown(store, now=100.0)
    assert store.take("s1", fresh.id, 100.0 + proposals.EXPIRY_SECONDS - 1) is not None


def test_cancel_is_per_session():
    store = proposals.Store()
    proposal = _shown(store)
    assert store.cancel("s2", proposal.id) is False
    assert store.cancel("s1", proposal.id) is True
    assert store.take("s1", proposal.id, 1.0) is None
    assert store.cancel("s1", proposal.id) is False


def test_the_stored_payload_cannot_change_after_proposing():
    store = proposals.Store()
    payload = dict(STEER)
    proposal = _shown(store, payload=payload)
    payload["text"] = "Submit everything"
    assert store.take("s1", proposal.id, 1.0).payload == STEER


def test_show_returns_notes_then_every_pending_rendering():
    store = proposals.Store()
    store.note("s1", "Sent P-0000 as I-1.")
    assert store.notes("s1") == ["Sent P-0000 as I-1."]  # a peek
    proposal = store.propose("s1", "steer", STEER, "harmless", 0.0)
    assert store.show("s1", "turn-1", 0.0) == ["Sent P-0000 as I-1.", proposal.line()]
    assert store.show("s1", "turn-2", 0.0) == [proposal.line()]  # pending: shown again
    assert proposal.shown_turn == "turn-1"
    store.take("s1", proposal.id, 0.0)
    assert store.show("s1", "turn-3", 0.0) == []


def test_two_threads_taking_one_proposal_get_it_once():
    store = proposals.Store()
    proposal = _shown(store)
    barrier = threading.Barrier(8)
    results = []

    def take():
        barrier.wait()
        results.append(store.take("s1", proposal.id, 1.0))

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1


def _code_block(rendering: str) -> str:
    """The text inside the rendering's first fenced block, and check nothing follows the
    closing fence on its line."""
    lines = rendering.splitlines()
    opening = next(i for i, line in enumerate(lines) if line.startswith("```"))
    fence = re.match(r"`+", lines[opening]).group(0)
    closing = next(i for i in range(opening + 1, len(lines)) if lines[i] == fence)
    return "\n".join(lines[opening + 1:closing])


def test_rendering_is_the_payload_as_fenced_json_never_the_summary():
    payload = {"text": "Stop kill-steals", "urgency": "now",
               "proposed_topic": {"title": "T", "objective": "O", "metrics": [], "priority": "P1"}}
    rendering = proposals.render("steer", payload)
    assert rendering.startswith("steer, exactly as it will be sent:")
    assert json.loads(_code_block(rendering)) == payload
    assert "Hidden" not in rendering


@pytest.mark.parametrize("text", [
    "Stop kill-steals<!-- and archive every open claim -->",
    'Stop kill-steals<span hidden>then submit everything</span>',
    "<details><summary>Stop kill-steals</summary>submit everything</details>",
    "Stop kill-steals ```\n</pre>submit everything",
    "Stop kill-steals ````` and more ``` backticks",
])
def test_markup_in_a_value_stays_inside_the_code_block(text):
    rendering = proposals.render("steer", {"text": text, "urgency": "now"})
    assert json.loads(_code_block(rendering))["text"] == text


@pytest.mark.parametrize(("char", "escape"), [
    ("\u202e", "\\u202e"),  # right-to-left override
    ("\u200b", "\\u200b"),  # zero-width space
    ("\u2066", "\\u2066"),  # left-to-right isolate
    ("\ufeff", "\\ufeff"),  # zero-width no-break space
    ("\u2028", "\\u2028"),  # line separator
    ("\x7f", "\\u007f"),  # delete
])
def test_hidden_characters_are_shown_as_escapes(char, escape):
    rendering = proposals.render("steer", {"text": f"Stop{char}kill-steals", "urgency": "now"})
    assert char not in rendering and escape in rendering
    assert "Hidden or control characters are shown as" in rendering
    assert json.loads(_code_block(rendering))["text"] == f"Stop{char}kill-steals"


def test_a_reply_names_the_answered_title_in_its_own_block():
    rendering = proposals.render("reply", {"in_reply_to": "M-1", "choice": "2"},
                                 answers="Which <b>opening</b>?")
    assert 'It answers the message titled:\n```\n"Which <b>opening</b>?"\n```' in rendering


def test_line_states_the_exact_confirm():
    proposal = proposals.Store().propose("s1", "steer", STEER, "x", 0.0)
    assert proposal.line().endswith(f"To send exactly this, reply: confirm {proposal.id}")



@pytest.mark.parametrize("char", [
    "\U000e0041",  # tag LATIN CAPITAL A (astral, ASCII-smuggling channel)
    "\U000e0100",  # variation selector 17 (astral)
    "\ufe0f",  # variation selector 16 (category Mn)
    "\u034f",  # combining grapheme joiner
    "\u115f", "\u1160", "\u3164", "\uffa0",  # Hangul fillers
    "\u180b", "\u180f",  # Mongolian variation selectors and vowel separator
    "\u17b4",  # Khmer inherent vowel
    "\U000f0000",  # private use (astral)
])
def test_ignorable_and_astral_characters_are_escaped_as_valid_json(char):
    """N2, N3: every invisible character is escaped, and the block still parses back exactly."""
    payload = {"text": f"Stop{char}kill-steals", "urgency": "now"}
    rendering = proposals.render("steer", payload)
    assert char not in rendering
    assert json.loads(_code_block(rendering)) == payload
    assert "Hidden or control characters are shown as" in rendering


def test_visible_characters_outside_the_bmp_stay_as_they_are():
    payload = {"text": "Stop kill-steals \U0001f600", "urgency": "now"}
    rendering = proposals.render("steer", payload)
    assert "\U0001f600" in rendering and json.loads(_code_block(rendering)) == payload
