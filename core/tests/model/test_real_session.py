"""Tool calls through a real Session on a real Hermes gateway (``real_session.py``).

Costs real money, so it runs only on request: ``-m model``, ``OPENROUTER_API_KEY`` and
``HERMES_BIN``. ``in_image.sh`` runs it in the pinned Hermes image (README, "Develop and test").

Environment, all optional:
  IDE_TOOL_CALL_MODELS   the one model every profile runs (default Kimi K3.1 Pro)
  IDE_TOOL_CALL_CASES    comma-separated substrings of case ids
  IDE_TOOL_CALL_REPEATS  runs per case (default 5)
  IDE_TOOL_CALL_PARALLEL runs at a time (default 4)
  IDE_TOOL_CALL_CONTEXT  a path: also write the captured real context there, for the direct
                         tests (``test_write_actions.py``) to send
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from research_core.tools import catalog
from tests.model import harness
from tests.model.real_session import CASES, RealGateway, hermes_bin_from_env
from tests.model.write_actions import RECOVERY_DEPTHS, Case

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
    pytest.mark.skipif(not hermes_bin_from_env(), reason="HERMES_BIN is not set"),
]
DEFAULT_MODEL = "moonshotai/kimi-k3.1-pro"
DEFAULT_REPEATS = 5
# Proposed from the 2026-11-04 measurement on Kimi K3.1 Pro: first try 109/110, after one refusal
# 40/40, after three 40/40, and the one miss was a fault of the case, since fixed. Each interval's
# lower end is at least 91%, so 95% is a rate this path has shown it can hold, with room for a
# model's ordinary slips. At the default repeats the check fails at 49/55 on the first try or
# 17/20 on a recovery depth, the first counts the sample can tell apart from 95%.
AGREED_RATE = 0.95
MIN_SAMPLE = 20  # the default repeats give 55 first-try calls and 20 per recovery depth
MODEL = (harness.env_list("IDE_TOOL_CALL_MODELS") or [DEFAULT_MODEL])[0]


def selected() -> list[Case]:
    wanted = harness.env_list("IDE_TOOL_CALL_CASES")
    cases = [c for c in CASES if not wanted or any(w in c.id for w in wanted)]
    repeats = int(os.environ.get("IDE_TOOL_CALL_REPEATS") or DEFAULT_REPEATS)
    return [case for case in cases for _ in range(repeats)]


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    real = RealGateway(
        root=tmp_path_factory.mktemp("model-gateway"),
        hermes_bin=hermes_bin_from_env(),
        openrouter_key=harness.KEY,
        model=MODEL,
    )
    real.start()
    harness.RESULTS.schemas = "shipped, through a real Session"
    print(f"\ngateway log: {real.host.log}\nworkspace: {real.host.workspace}")
    yield real
    real.stop()


@pytest.fixture(scope="module")
def captured(gateway) -> dict[str, harness.SessionContext]:
    """One real Session per role, run once, and what Hermes stored for it."""
    contexts = {}
    for role in catalog.ROLES:
        session_id = gateway.open_session(role)
        gateway.run_to_completion(role, session_id, "Reply with READY and stop.")
        contexts[role] = gateway.stored_prompt_and_tools(role, session_id)
    return contexts


def test_a_real_session_gets_the_built_prompt_and_tools(captured):
    """The real prompt carries what the stand-in leaves out; direct calls can send this capture."""
    for role, context in captured.items():
        assert "[briefing]" in context.system, role
        assert "## Order of authority" in context.system, role  # the SOUL
        assert "working-a-task" in context.system or "goals-and-tasks" in context.system, role
        names = {tool["function"]["name"] for tool in context.hermes_tools}
        assert "skill_view" in names, role
        assert not names & {spec.name for spec in catalog.tools_for(role)}, role
    path = os.environ.get("IDE_TOOL_CALL_CONTEXT")
    if path:
        Path(path).write_text(json.dumps({r: asdict(c) for r, c in captured.items()}))


def test_a_fresh_sessions_frozen_tools_show_no_drift(gateway, captured):
    """``tools/schema_drift.py`` against a real ``state.db``: a Session opened now agrees."""
    drift_tool = harness._load(
        Path(__file__).parents[3] / "tools", "schema_drift.py", "schema_drift"
    )
    for role in catalog.ROLES:
        frozen_lists = drift_tool.read_frozen_lists(gateway.state_db(role))
        assert frozen_lists, role
        for frozen in frozen_lists:
            assert [str(d) for d in drift_tool.compare(role, frozen.tools)] == [], role


@pytest.fixture(scope="module")
def calls(request, gateway):
    def run_one(case: Case) -> harness.Result:
        return gateway.run_case(MODEL, case)

    return harness.start_calls(request.session, test_write_action_through_a_real_session, run_one)


@pytest.mark.parametrize("case", selected(), ids=lambda case: case.id)
def test_write_action_through_a_real_session(case: Case, calls, request):
    """Records the run. One run decides nothing: ``test_the_agreed_rate`` below does the judging,
    and the summary lists every call that was not valid."""
    result = calls[request.node.nodeid].result()
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(f"{case.id}: {result.detail}")


@pytest.mark.parametrize("refusals", [0, *RECOVERY_DEPTHS])
def test_the_agreed_rate(refusals: int):
    """The parent task's done-when: the model tests pass at an agreed rate through a real session.

    Fails when the valid rate is below ``AGREED_RATE`` by more than the sample's noise explains.
    """
    results = [r for r in harness.RESULTS.items if harness.refusals_in(r.case) == refusals]
    valid, total = harness.judged_rate(results)
    if total < MIN_SAMPLE:
        pytest.skip(
            f"{total} judged calls after {refusals} refusals; a decision needs {MIN_SAMPLE}"
        )
    low, high = harness.wilson_interval(valid, total)
    assert not harness.clearly_below(valid, total, AGREED_RATE), (
        f"after {refusals} refusals: {valid}/{total}, interval {low:.2f}-{high:.2f}, "
        f"agreed rate {AGREED_RATE}"
    )
