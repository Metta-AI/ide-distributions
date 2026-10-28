"""Compose board facts with canonical records for the model's two overview tools."""

from pathlib import Path

from .board import Board
from .gate import transitions
from .ide import IDE
from .research import Research
from .storage import snapshot

# Every researcher distribution ships this plugin; the coordinator does not.
RESEARCHER_MARKER = Path("plugins") / "researcher_guard"


def dispatch_profiles(root):
    """The root Hermes config's ``kanban.dispatch_profiles``: the assignees this host's
    dispatcher claims (a list or a comma-separated string), or None when it is not set."""
    # The plugin runs inside Hermes's own Python, which has no PyYAML; use Hermes's reader there.
    # Tests run in the profile venv, which has PyYAML but not Hermes.
    try:
        from hermes_cli.config import fast_safe_load as safe_load
    except ImportError:
        from yaml import safe_load

    path = root / "config.yaml"
    config = safe_load(path.read_text()) if path.is_file() else None
    value = ((config or {}).get("kanban") or {}).get("dispatch_profiles")
    if value is None:
        return None
    items = value.split(",") if isinstance(value, str) else value
    return [str(item).strip() for item in items if str(item).strip()]


def researchers(context, cards):
    """The researcher profiles cards can be assigned to, with each one's open cards.

    The host's dispatcher allowlist names them (hosting sets it); without one, every installed
    profile that ships the researcher's guard plugin is one."""
    profiles = context.home.parent
    names = dispatch_profiles(context.root)
    if names is None:
        names = sorted(
            entry.name for entry in profiles.iterdir() if (entry / RESEARCHER_MARKER).is_dir()
        ) if profiles.name == "profiles" else []
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
        # Every open and recent card, not card_list's default page of 20.
        cards = Board(self.context).card_list({"limit": 200, "order_by": "created-desc"})
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
