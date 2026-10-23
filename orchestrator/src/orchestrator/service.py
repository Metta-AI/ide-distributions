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

    def status(self, game=None):
        """Board-wide cards and waits, the host-wide budget, and one game's research
        (``game``; default: the host's ``RESEARCH_GAME``)."""
        cards = Board(self.context).card_list()
        by_state = {}
        for card in cards:
            by_state.setdefault(card["status"], []).append(card["id"])
        research = self.research.in_game(game)
        return {
            "cards": by_state,
            "pending": transitions(snapshot(self.context), self.research, IDE(self.context)),
            "game": research.context.env.get("RESEARCH_GAME"),
            "research": research.call("records", "summary"),
            "promotion": research.call("promotion", "status"),
            "control": research.control_read(),
            "plan": research.plan_read(),
            "budget": self.research.budget_read(),
        }
