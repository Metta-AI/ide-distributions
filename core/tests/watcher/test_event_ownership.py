"""Every event kind has exactly one owner (design B.13 "Event ownership"; amendment A5.4)."""

from __future__ import annotations

import pytest

from research_core.store import events
from research_core.watcher.handlers import (
    FUNCTION_KINDS,
    HANDLER_PACKAGE,
    HANDLERS,
    MODEL_KINDS,
    STEP_MODULES,
    UNWATCHED_KINDS,
    WAKE_KINDS,
)
from research_core.watcher.wakes import TEMPLATES_DIR

# The design's wake table (Appendix H) minus change.completed (A3.6), plus the kinds A5.4 and
# the recovery, wake and store events added: kind -> wake kind, or None for a no-model handler.
EXPECTED_WAKES = {
    "coworld.setup_needed": "setup",
    "task.completed": "review",
    "task.reviewed": None,
    "task.asked": "management",
    "task.proposed": "management",
    "task.proposed_submission": "management",
    "merge.failed": "management",
    "submission.candidate": "management",
    "task.wait_resolved": None,
    "capability.failed": None,
    "repair.failed_twice": None,
    "goal.needs_ideation": "management",
    "goal.park": "management",
    "session.dead": None,
    "task.stuck": None,
    "workspace.resumed": None,
    "task.abandoned": None,
    "task.failed": None,
    "champion.changed": "management",
    "store.unmarked_write": "management",
    "store.schema_rebuilt": "management",
    "store.schema_newer": "management",
    "recovery.failed": "management",
    "wake.failed": "management",
    "anti_stall": "anti_stall",
}


def test_handlers_and_unwatched_kinds_partition_the_event_kinds():
    assert set(HANDLERS) | UNWATCHED_KINDS == events.KINDS
    assert not set(HANDLERS) & UNWATCHED_KINDS
    assert "change.completed" not in HANDLERS and "change.completed" not in events.KINDS
    assert MODEL_KINDS | FUNCTION_KINDS == set(HANDLERS) and not MODEL_KINDS & FUNCTION_KINDS


def test_wake_table_matches_the_design():
    assert {kind: entry.wake for kind, entry in HANDLERS.items()} == EXPECTED_WAKES


def test_each_handler_is_a_wake_or_a_watcher_function():
    for kind, entry in HANDLERS.items():
        assert entry.kind == kind
        if entry.model:
            assert entry.handler is None and entry.wake in WAKE_KINDS, kind
            continue
        assert entry.wake is None, kind
        package, _, module = entry.module_name.rpartition(".")
        # Checked by name, not imported: the step modules land in parallel with this map.
        assert package == HANDLER_PACKAGE and module in STEP_MODULES, kind
        assert entry.function_name.startswith("handle"), kind


def test_resolve_refuses_a_model_wake():
    with pytest.raises(ValueError, match="management wake"):
        HANDLERS["task.asked"].resolve()


def test_every_wake_kind_has_a_template_that_keeps_wakes_out_of_conversations():
    for kind in WAKE_KINDS:
        text = (TEMPLATES_DIR / f"{kind}.md").read_text(encoding="utf-8")
        for placeholder in ("{event_kind}", "{subject}", "{payload_json}", "{coworld_id}"):
            assert placeholder in text, (kind, placeholder)
        assert "ui.notify" in text and "user conversation" in text, kind
    assert "pending_request" in (TEMPLATES_DIR / "setup.md").read_text(encoding="utf-8")
