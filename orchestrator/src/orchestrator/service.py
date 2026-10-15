"""Compose board facts with canonical records for the model's two overview tools."""

import json

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
        branches = []
        actionable_cards = {event["task_id"] for event in state["events"]}
        for card in state["cards"]:
            if (
                card["id"] in actionable_cards
                and card["branch_name"]
                and card["status"] in {"done", "blocked", "ready"}
            ):
                merged = self.research.call("workspace", "merged", "--branch", card["branch_name"])
                # An untouched queued branch is also an ancestor of main. It is not completed work.
                marker = "\n\nCard context:\n```json\n"
                context = (
                    json.loads(card["body"].rsplit(marker, 1)[1].split("\n```", 1)[0])
                    if marker in (card["body"] or "")
                    else {}
                )
                moved = context.get("base_sha") and merged["card_sha"] != context["base_sha"]
                if (card["status"] == "done" and not merged["merged"]) or (
                    card["status"] != "done" and merged["merged"] and moved
                ):
                    branches.append(
                        {
                            "card": card["id"],
                            "status": card["status"],
                            "branch": card["branch_name"],
                            **merged,
                        }
                    )
        return {"events": state["events"], "pending": pending, "branches": branches}

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
        }
