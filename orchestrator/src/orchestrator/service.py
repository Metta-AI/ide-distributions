"""Compose board facts with canonical records for the model's two overview tools."""

from .board import Board
from .gate import transitions
from .ide import IDE
from .research import Research
from .storage import snapshot


class Service:
    def __init__(self, context):
        self.context = context
        self.research = Research(context)

    def board_reconcile(self):
        state = snapshot(self.context)
        pending = transitions(state, self.research, IDE(self.context), True)
        actionable_cards = {event["task_id"] for event in state["events"]}
        return {
            "events": state["events"],
            "pending": pending,
            "cards": [card for card in state["cards"] if card["id"] in actionable_cards],
        }

    def status(self):
        cards = Board(self.context).card_list()
        by_state = {}
        for card in cards:
            by_state.setdefault(card["status"], []).append(card["id"])
        return {
            "cards": by_state,
            "research": self.research.call("records", "summary"),
            "promotion": self.research.call("promotion", "status"),
            "control": self.research.control_read(),
            "plan": self.research.plan_read(),
            "budget": self.research.budget_read(),
        }
