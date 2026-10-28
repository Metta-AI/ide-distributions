"""Compose board facts with canonical records for the model's two overview tools."""

from pathlib import Path

from .board import Board
from .gate import transitions
from .ide import IDE
from .research import Research
from .storage import snapshot

# Every researcher distribution ships this plugin; the coordinator does not.
RESEARCHER_MARKER = Path("plugins") / "researcher_guard"


def researchers(context, cards):
    """The researcher profiles cards can be assigned to, with each one's open cards.

    Hosting names them in ``SOFTMAX_WORKER_PROFILES``; elsewhere, every installed profile
    that ships the researcher's guard plugin is one."""
    profiles = context.home.parent
    named = context.env.get("SOFTMAX_WORKER_PROFILES", "")
    names = [name.strip() for name in named.split(",") if name.strip()]
    if not names and profiles.name == "profiles":
        names = sorted(
            entry.name for entry in profiles.iterdir() if (entry / RESEARCHER_MARKER).is_dir()
        )
    roster = []
    for name in names:
        own = [card for card in cards if card.get("assignee") == name]
        roster.append({
            "name": name,
            "installed": (profiles / name / "config.yaml").is_file(),
            "running": [card["id"] for card in own if card["status"] == "running"],
            "queued": [card["id"] for card in own if card["status"] in ("ready", "todo")],
        })
    return roster


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
            "researchers": researchers(self.context, cards),
            "game": research.context.env.get("RESEARCH_GAME"),
            "pending": transitions(snapshot(self.context), self.research, IDE(self.context)),
            "research": research.call("records", "summary"),
            "champion_submission": submission,
            "control": research.control_read(),
            "plan": research.plan_read(),
            "budget": self.research.budget_read(),
        }
