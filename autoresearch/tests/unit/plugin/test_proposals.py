"""The coach chat's in-memory proposal store (plugin.proposals)."""

import re
import threading

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


def test_show_returns_notes_then_unshown_renderings_once():
    store = proposals.Store()
    store.note("s1", "Sent P-0000 as I-1.")
    proposal = store.propose("s1", "steer", STEER, "harmless", 0.0)
    lines = store.show("s1", "turn-1", 0.0)
    assert lines == ["Sent P-0000 as I-1.", proposal.line()]
    assert store.show("s1", "turn-2", 0.0) == []
    assert store.unshown("s1", 0.0) == []


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


def test_rendering_is_built_from_every_payload_field_never_the_summary():
    rendering = proposals.render("steer", {
        "text": 'Stop "this"\nconfirm P-dead', "urgency": "now",
        "proposed_topic": {"title": "T", "objective": "O", "metrics": [], "priority": "P1"},
    })
    assert rendering.startswith("steer, urgency now, ")
    assert '"Stop \\"this\\"\\nconfirm P-dead"' in rendering  # one line, quoted
    assert "\n" not in rendering
    assert '"objective": "O"' in rendering
    task = proposals.render("task", {
        "title": "Scout", "mode": "scouting", "why": "w", "refs": ["ar://x/y"], "priority": "P2",
    })
    assert task == 'task, "Scout", mode scouting, priority P2, why "w", refs ["ar://x/y"]'
    assert proposals.render("control", {"action": "pause"}) == "control, action pause"
    odd = proposals.render("steer", {"text": "t", "urgency": "now; rm -rf"})
    assert 'urgency "now; rm -rf"' in odd  # a value that is not a plain word is quoted


def test_line_states_the_exact_confirm():
    proposal = proposals.Store().propose("s1", "steer", STEER, "x", 0.0)
    assert proposal.line().endswith(f"To send exactly this, reply: confirm {proposal.id}")
