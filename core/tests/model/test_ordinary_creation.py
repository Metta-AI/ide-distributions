"""Task creation from a plain request through a real Session, with nothing forced.

Costs real money, so it runs only on request, like ``test_real_session.py``, and takes the same
environment. Forced refusals are fault injection (``test_mid_turn_recovery.py``); here every refusal
is the real tool's answer to a call the model chose, so this measures ordinary creation and natural
recovery. Its results carry ``real_session.ORDINARY_LABEL``, so no ``test_the_agreed_rate`` counts
them.
"""

from __future__ import annotations

import os

import pytest

from tests.model import harness
from tests.model.real_session import (
    ORDINARY_CASES,
    ORDINARY_LABEL,
    RealGateway,
    hermes_bin_from_env,
)
from tests.model.test_real_session import AGREED_RATE, DEFAULT_REPEATS, MIN_SAMPLE, MODEL
from tests.model.write_actions import Case

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
    pytest.mark.skipif(not hermes_bin_from_env(), reason="HERMES_BIN is not set"),
]


def selected() -> list[Case]:
    wanted = harness.env_list("IDE_TOOL_CALL_CASES")
    cases = [c for c in ORDINARY_CASES if not wanted or any(w in c.id for w in wanted)]
    repeats = int(os.environ.get("IDE_TOOL_CALL_REPEATS") or DEFAULT_REPEATS)
    return [case for case in cases for _ in range(repeats)]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    real = RealGateway(
        root=tmp_path_factory.mktemp("ordinary-gateway"),
        hermes_bin=hermes_bin_from_env(),
        openrouter_key=harness.KEY,
        model=MODEL,
    )
    try:
        real.start()
        harness.RESULTS.schemas = "shipped, through a real Session, nothing forced"
        yield real
    finally:
        real.stop()


@pytest.fixture(scope="module")
def calls(request, gateway):
    def run_one(case: Case) -> harness.Result:
        return gateway.run_case(MODEL, case)

    with harness.started_calls(request.session, test_ordinary_creation, run_one) as futures:
        yield futures


@pytest.mark.parametrize("case", selected(), ids=lambda case: case.id)
def test_ordinary_creation(case: Case, calls, request):
    """Records the run; ``test_ordinary_first_try_rate`` below does the judging."""
    result = calls[request.node.nodeid].result()
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(f"{case.id}: {result.detail}")


def test_ordinary_first_try_rate():
    results = [r for r in harness.RESULTS.items if r.host == ORDINARY_LABEL]
    valid, total = harness.judged_rate(results)
    if total < MIN_SAMPLE and harness.env_list("IDE_TOOL_CALL_CASES"):
        pytest.skip(f"narrowed run: {total} judged calls, a decision needs {MIN_SAMPLE}")
    assert total >= MIN_SAMPLE, f"only {total} judged calls"
    assert total / len(results) >= AGREED_RATE, (
        f"{len(results) - total} of {len(results)} runs ended without a verdict, too many to trust "
        "the rate"
    )
    assert valid / total >= AGREED_RATE, f"{valid}/{total}, agreed rate {AGREED_RATE}"
