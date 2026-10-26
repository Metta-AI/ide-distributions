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
        (``game``; omit for the neutral workspace)."""
        cards = Board(self.context).card_list()
        by_state = {}
        for card in cards:
            by_state.setdefault(card["status"], []).append(card["id"])
        research = self.research.in_game(game)
        submission = research.call("champion", "pending")
        if submission is not None:
            submission = research.call("champion", "status", "--league", submission["league_id"])
        return {
            "cards": by_state,
            "game": research.context.env.get("RESEARCH_GAME"),
            "pending": transitions(snapshot(self.context), self.research, IDE(self.context)),
            "research": research.call("records", "summary"),
            "champion_submission": submission,
            "control": research.control_read(),
            "plan": research.plan_read(),
            "budget": self.research.budget_read(),
        }
