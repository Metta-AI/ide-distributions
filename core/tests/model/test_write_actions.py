"""Write actions with required fields, on several models and OpenRouter hosts, in full-toolset mode.

Costs real money, so it runs only on request: ``-m model``, ``OPENROUTER_API_KEY`` and
``IDE_HERMES_SRC`` (README, "Develop and test"). ``write_actions.py`` holds the cases and says
which write actions are covered, how a case is run and how its call is judged. The run ends with
a table of results per model and host (``harness.summary``), printed by this folder's
``conftest.py``.

Environment, all optional but the first:
  IDE_HERMES_SRC         the pinned Hermes's source tree, for its schema sanitizers (required)
  IDE_TOOL_CALL_MODELS   comma-separated models (default: MEASUREMENT's models)
  IDE_TOOL_CALL_HOSTS    comma-separated host labels for every model (default: MEASUREMENT's,
                         else ``shipped``); see ``harness.provider_for``
  IDE_TOOL_CALL_CASES    comma-separated substrings; only cases whose id contains one run
                         (ids look like ``orchestrator-claim.write``, ``...-recovery``)
  IDE_TOOL_CALL_REPEATS  calls per case (default 1)
  IDE_TOOL_CALL_SCHEMAS  ``module:attribute`` naming another ``harness.SchemaSet``
"""

from __future__ import annotations

import os

import pytest

from tests.model import harness
from tests.model.write_actions import CASES, MEASUREMENT, Case, run_case

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set"),
]


def selected() -> list[tuple[str, str, Case]]:
    models = harness.env_list("IDE_TOOL_CALL_MODELS") or list(MEASUREMENT)
    hosts = harness.env_list("IDE_TOOL_CALL_HOSTS")
    wanted = harness.env_list("IDE_TOOL_CALL_CASES")
    cases = [c for c in CASES if not wanted or any(w in c.id for w in wanted)]
    repeats = int(os.environ.get("IDE_TOOL_CALL_REPEATS") or 1)
    return [
        (model, host, case)
        for model in models
        for host in hosts or MEASUREMENT.get(model, [harness.SHIPPED])
        for case in cases
        for _ in range(repeats)
    ]


@pytest.mark.parametrize(
    ("model", "host", "case"),
    selected(),
    ids=lambda value: value.id if isinstance(value, Case) else str(value).rsplit("/", 1)[-1],
)
def test_write_action_first_call_is_valid(model: str, host: str, case: Case):
    harness.hermes_sanitizers()  # fails loudly without IDE_HERMES_SRC
    schemas = harness.schema_set()
    harness.RESULTS.schemas = schemas.label
    result = run_case(model, host, case, schemas)
    harness.RESULTS.items.append(result)
    if result.outcome == "host_error":
        pytest.skip(f"{model} on {host}: {result.detail}")
    assert result.outcome == "valid", f"{result.outcome}: {result.detail}"
