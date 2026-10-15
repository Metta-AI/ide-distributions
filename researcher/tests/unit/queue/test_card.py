import pytest

from ide_research_core.queue.card import kanban_priority


@pytest.mark.parametrize(
    ("priority", "order", "expected"),
    [("P0", 0, 50), ("P5", 0, 0), ("P2", 3, 33), ("P1", 9, 49)],
)
def test_kanban_priority(priority, order, expected):
    assert kanban_priority(priority, order) == expected


@pytest.mark.parametrize("order", [-1, 10])
def test_kanban_priority_rejects_bad_order(order):
    with pytest.raises(ValueError):
        kanban_priority("P1", order)
