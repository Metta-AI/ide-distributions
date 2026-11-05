"""Recovery from refused calls inside one turn, on a real Hermes gateway (``forcing_proxy.py``).

Costs real money, so it runs only on request, like ``test_real_session.py``, and takes the same
environment. Each case's first requests are answered with the broken call, the real tool refuses it
and the real hooks and loop guard answer; the model's next call is real and is judged.
"""

from __future__ import annotations

import os

import pytest

from research_core.plugin.repeats import drop_repeated_refusals
from tests.model import harness
from tests.model.forcing_proxy import FORCED_ID_PREFIX, ForcingProxy
from tests.model.real_session import HOST_LABEL, RECOVERY_CASES, RealGateway, hermes_bin_from_env
from tests.model.test_real_session import AGREED_RATE, DEFAULT_REPEATS, MIN_SAMPLE, MODEL
from tests.model.write_actions import RECOVERY_DEPTHS, Case

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
    pytest.mark.skipif(not hermes_bin_from_env(), reason="HERMES_BIN is not set"),
]


def selected() -> list[Case]:
    wanted = harness.env_list("IDE_TOOL_CALL_CASES")
    cases = [c for c in RECOVERY_CASES if not wanted or any(w in c.id for w in wanted)]
    repeats = int(os.environ.get("IDE_TOOL_CALL_REPEATS") or DEFAULT_REPEATS)
    return [case for case in cases for _ in range(repeats)]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    real = RealGateway(
        root=tmp_path_factory.mktemp("mid-turn-gateway"),
        hermes_bin=hermes_bin_from_env(),
        openrouter_key=harness.KEY,
        model=MODEL,
        forcing=ForcingProxy(key=harness.KEY),
    )
    try:
        real.start()
        harness.RESULTS.schemas = "shipped, refused calls forced inside one real turn"
        yield real
    finally:
        real.stop()
        real.forcing.stop()


@pytest.fixture(scope="module")
def calls(request, gateway):
    def run_one(case: Case) -> harness.Result:
        return gateway.run_case(MODEL, case)

    with harness.started_calls(request.session, test_recovery_in_one_turn, run_one) as futures:
        yield futures


@pytest.mark.parametrize("case", selected(), ids=lambda case: case.id)
def test_recovery_in_one_turn(case: Case, calls, request):
    """Records the run; ``test_the_agreed_rate`` below does the judging."""
    result = calls[request.node.nodeid].result()
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(f"{case.id}: {result.detail}")


def test_requests_drop_repeats_but_the_stored_history_keeps_them(gateway):
    case = next(c for c in RECOVERY_CASES if c.refusals == max(RECOVERY_DEPTHS))
    session_id = gateway.open_session(case.role)
    gateway.run_to_completion(case.role, session_id, gateway.force_refusals(case, case.instruction))

    stored = gateway.runs.session_messages(session_id, profile=case.role)
    forced = [m for m in stored if str(m.get("tool_call_id")).startswith(FORCED_ID_PREFIX)]
    assert len(forced) == case.refusals, "the stored history holds every refused call"
    assert gateway.forcing.requests, "the proxy saw the requests"
    for body in gateway.forcing.requests:
        assert drop_repeated_refusals(body["messages"]) == body["messages"]


@pytest.mark.parametrize("refusals", RECOVERY_DEPTHS)
def test_the_agreed_rate(refusals: int):
    results = [
        r
        for r in harness.RESULTS.items
        if r.host == HOST_LABEL and harness.refusals_in(r.case) == refusals
    ]
    valid, total = harness.judged_rate(results)
    if total < MIN_SAMPLE and harness.env_list("IDE_TOOL_CALL_CASES"):
        pytest.skip(f"narrowed run: {total} judged calls, a decision needs {MIN_SAMPLE}")
    assert total >= MIN_SAMPLE, f"after {refusals} refusals: only {total} judged calls"
    assert valid / total >= AGREED_RATE, (
        f"after {refusals} refusals: {valid}/{total}, agreed rate {AGREED_RATE}"
    )
