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

    def status(self):
        cards = Board(self.context).card_list()
        by_state = {}
        for card in cards:
            by_state.setdefault(card["status"], []).append(card["id"])
        return {
            "cards": by_state,
            "pending": transitions(snapshot(self.context), self.research, IDE(self.context)),
            "research": self.research.call("records", "summary"),
            "promotion": self.research.call("promotion", "status"),
            "control": self.research.control_read(),
            "plan": self.research.plan_read(),
            "budget": self.research.budget_read(),
        }
