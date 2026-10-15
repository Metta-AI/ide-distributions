"""Test helpers: a real git canonical workspace seeded with a few records, and card specs."""

from pathlib import Path

import yaml

from autoresearch.ids import new_id
from autoresearch.io import gitops
from autoresearch.io.docs import write_doc
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec

AUTORESEARCH_DIR = Path(__file__).resolve().parents[3]
GAME_VERSION = "65"


class Canonical:
    """Test handle on the canonical repo: its path, layout, and the ids it was seeded with."""

    def __init__(self, root: Path):
        self.root = root
        self.layout = Layout(root)
        self.topic_id = new_id("topic")
        self.other_topic_id = new_id("topic")
        self.hypothesis_id = new_id("hypothesis")
        self.claim_id = new_id("claim")

    def seed(self) -> None:
        gitops.git(self.root, "init", "-b", "main")
        gitops.git(self.root, "config", "user.email", "t@example.com")
        gitops.git(self.root, "config", "user.name", "T")
        (self.root / ".gitignore").write_text(".supervisor/runtime/\n.worktrees/\n")
        self.layout.agents_md.write_text("# Doctrine\n")
        self.layout.plan.write_text("# Plan\n\nFocus on last hits this week.\n")
        write_doc(
            self.layout.topics / f"{self.topic_id}--last-hits.md",
            {
                "id": self.topic_id,
                "title": "Increase last hits",
                "objective": "Secure more last hits without dying more.",
                "metrics": [{"name": "score", "direction": "up"}],
                "guardrails": [{"name": "errors", "direction": "down", "tolerance": 0.05}],
            },
            "Topic prose.",
        )
        write_doc(
            self.layout.topics / f"{self.other_topic_id}.md",
            {
                "id": self.other_topic_id,
                "title": "Unrelated scouting topic",
                "objective": "Profile the top opponents.",
                "metrics": [],
                "guardrails": [],
            },
            "",
        )
        write_doc(
            self.layout.hypotheses / f"{self.hypothesis_id}.md",
            {
                "id": self.hypothesis_id,
                "statement": "Heroes attack before the creep is in kill range.",
                "topics": [self.topic_id],
            },
            "",
        )
        write_doc(
            self.layout.claims / f"{self.claim_id}.md",
            {"id": self.claim_id, "kind": "mechanism", "topics": []},
            "Creep damage is 10 per hit.",
        )
        gitops.commit_all(self.root, "seed")

    def write_noise(self, game_version: str = GAME_VERSION) -> None:
        self.layout.stats.mkdir(parents=True, exist_ok=True)
        (self.layout.stats / "metric-noise.yaml").write_text(
            yaml.safe_dump({"game_version": game_version, "metrics": {}})
        )
        gitops.commit_all(self.root, f"noise {game_version}")


def make_spec(mode="investigation", refs=(), title="Find why we lose", priority="P2") -> CardSpec:
    return CardSpec(
        request_id=new_id("card_request"),
        title=title,
        mode=mode,
        why="Score dropped last week.",
        refs=list(refs),
        created_by="planner",
        priority=priority,
    )
