"""``research_core.run_failures``: which evidence classifies a failed run, and the holds."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_core import run_failures
from research_core.inference import ApiError, InferenceState

NOW = datetime(2026, 11, 2, 22, 0, tzinfo=UTC)
SESSION = "api_1_abcd"


def classify(state=None, raw=None, error=None, now=NOW):
    return run_failures.classify(
        session_id=SESSION,
        raw=raw or {},
        error=error,
        state=state or InferenceState(),
        installed_key_id=None,
        now=now,
    )


def last_error(reason: str, at: datetime = NOW, session_id: str = SESSION) -> InferenceState:
    return InferenceState(last_error=ApiError(session_id, reason, None, at))


def test_every_hermes_reason_in_the_table_has_a_class():
    for reason, error_class in run_failures.REASON_CLASSES.items():
        assert classify(last_error(reason)) == error_class


def test_last_error_of_another_session_or_from_long_before_the_end_is_ignored():
    assert classify(last_error("billing", session_id="api_other")) is None
    stale = NOW - run_failures.LAST_ERROR_WINDOW - timedelta(seconds=1)
    assert classify(last_error("rate_limit", at=stale)) is None


def test_last_error_from_before_the_run_started_is_ignored():
    raw = {"created_at": NOW.timestamp(), "updated_at": NOW.timestamp() + 30}
    before = NOW - timedelta(seconds=5)
    assert classify(last_error("billing", at=before), raw=raw) is None
    assert classify(last_error("billing", at=NOW + timedelta(seconds=20)), raw=raw) == (
        "budget_exhausted"
    )


def test_an_unmapped_reason_falls_through_to_the_block():
    state = InferenceState(
        blocked="provider_unavailable",
        until=NOW + timedelta(minutes=3),
        last_error=ApiError(SESSION, "unknown", None, NOW),
    )
    assert classify(state) == "provider_unavailable"


def test_a_lapsed_outage_block_is_no_evidence():
    state = InferenceState(blocked="provider_unavailable", until=NOW - timedelta(minutes=1))
    assert classify(state) is None


def test_hermes_iteration_budget_is_not_money():
    assert classify(raw={"turn_exit_reason": "budget_exhausted"}) is None
    assert classify(raw={"turn_exit_reason": "max_iterations_reached(90/90)"}) is None


def test_resume_after_is_the_hold_or_the_block_whichever_is_later():
    assert run_failures.resume_after(
        "rate_limited", InferenceState(), NOW, installed_key_id=None
    ) == NOW + timedelta(minutes=1)
    refill = NOW + timedelta(hours=2)
    budget = InferenceState(blocked="budget_exhausted", until=refill)
    assert (
        run_failures.resume_after("budget_exhausted", budget, NOW, installed_key_id=None) == refill
    )


def test_the_hold_doubles_per_resume_up_to_the_cap_and_a_budget_waits_for_the_refill():
    assert [run_failures.hold("rate_limited", n) for n in range(7)] == [
        timedelta(minutes=minutes) for minutes in (1, 2, 4, 8, 16, 30, 30)
    ]
    assert run_failures.hold("rate_limited", 10_000) == run_failures.MAX_HOLD
    refill = NOW + timedelta(hours=5)
    budget = InferenceState(blocked="budget_exhausted", until=refill)
    assert (
        run_failures.resume_after("budget_exhausted", budget, NOW, resumes=9, installed_key_id=None)
        == refill
    )


def test_last_error_matches_the_run_by_task_id_when_compression_rotated_the_session_id():
    rotated = InferenceState(
        last_error=ApiError("api_rotated", "billing", 402, NOW, task_id="api_run_start")
    )
    assert classify(rotated, raw={"session_id": "api_run_start"}) == "budget_exhausted"
    # Without a session id in the run status, the admission's Session stands in.
    by_admission = InferenceState(last_error=ApiError("api_rotated", "billing", 402, NOW, SESSION))
    assert classify(by_admission) == "budget_exhausted"
    other_run = InferenceState(last_error=ApiError("api_x", "billing", 402, NOW, task_id="api_y"))
    assert classify(other_run, raw={"session_id": "api_run_start"}) is None


@pytest.mark.parametrize(
    ("text", "error_class"),
    [
        ("HTTP 429: too fast", "rate_limited"),
        ("Error code: 503 - upstream", "provider_unavailable"),
        ("status 502", "provider_unavailable"),
        ("HTTP 413", "too_large"),
        ("error: 401", "misconfigured"),
        ("wrote 500 rows then crashed", None),
        ("the tool printed 429 lines", None),
        ("port 5003 refused", None),
    ],
)
def test_status_codes_in_error_text_need_a_marking_word(text, error_class):
    assert classify(error=text) == error_class
