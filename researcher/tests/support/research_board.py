"""A research workspace for the softmax and promotion tests.

A bare canonical repository and one seeded clone of it (standing for a researcher's or the
orchestrator's clone), a FakeSoftmax whose league shows ``BASELINE_PV`` as our champion and an
accepted promotion that records it as the loop's champion, and card worktrees made the way
the orchestrator's ``card_create`` and the Kanban dispatcher make them.
"""

import hashlib
import json
from pathlib import Path

from unit.records.fixtures import HYP, JOURNAL, JOURNAL_BODY
from ulid import ULID

from ide_research_core.io import gitops
from ide_research_core.io.docs import write_json
from ide_research_core.paths import Layout
from ide_research_core.promotion import CHAMPION_PATH, PROMOTIONS_DIR
from ide_research_core.records.store import RecordStore
from ide_research_core.softmax_tool.fake import DEFAULT_PLAYER_ID, FakeSoftmax
from ide_research_core.stats.design import EvaluationDesign, MetricSpec, design_sha256

LEAGUE = "fake-league"  # games/fakegame league_id
PLAYER = DEFAULT_PLAYER_ID
BASELINE_PV = "autoresearch-fake:v0"
PV_A = "autoresearch-fake:v1"
CHAMPION_V0 = "# Champion v0\n"
VERSION = {
    "parents": [],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-fake",
}


def design_dict(baseline: str = BASELINE_PV) -> dict:
    metrics = (
        MetricSpec("score", "up", "primary", 1.0, None),
        MetricSpec("errors", "down", "agent_guardrail", None, 0.5),
        MetricSpec("score_perf", "up", "performance", None, None),
    )
    design = EvaluationDesign(
        "fake-1", baseline, (0, 1), ("random", "opp:v3"), (1, 2), metrics, 0.5, {}, 2, "rev1"
    )
    return design.to_dict()


class Worker:
    """A card worker's worktree: branch ``card/<id>`` of the clone, at its ``main``."""

    def __init__(self, board: "ResearchBoard", card: str):
        self.board = board
        self.task_id = card
        self.worktree = board.canonical / ".worktrees" / card
        gitops.git(board.canonical, "worktree", "add", "-q", "-b", f"card/{card}",
                   str(self.worktree), "HEAD")  # fmt: skip

    @property
    def store(self) -> RecordStore:
        return RecordStore(Layout(self.worktree))

    def start(self) -> str:
        """Kept for the tests' shape: the worktree exists from ``admit``."""
        return self.task_id

    def write_journal(self) -> str:
        return self.store.create("journal", {**JOURNAL, "card": self.task_id}, JOURNAL_BODY)


class ResearchBoard:
    def __init__(self, tmp_path: Path, seed_champion: bool = True):
        self.tmp_path = tmp_path
        self.origin = tmp_path / "canonical.git"
        self.canonical = tmp_path / "canonical"
        gitops.git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.canonical.mkdir()
        root = self.canonical
        gitops.git(root, "init", "-q", "-b", "main")
        gitops.git(root, "config", "user.email", "t@example.com")
        gitops.git(root, "config", "user.name", "T")
        gitops.git(root, "remote", "add", "origin", str(self.origin))
        (root / ".gitignore").write_text(".supervisor/runtime/\n.worktrees/\n")
        (root / "AGENTS.md").write_text("# Doctrine\n")
        (root / CHAMPION_PATH).parent.mkdir(parents=True)
        (root / CHAMPION_PATH).write_text(CHAMPION_V0)
        self.store = RecordStore(Layout(root))
        self.hypothesis = self.store.create("hypothesis", dict(HYP), "Mechanism.")
        self.sm = FakeSoftmax(root, {})
        if seed_champion:
            self.sm.submit(BASELINE_PV, LEAGUE)
            self._accepted(BASELINE_PV)
        gitops.commit_all(root, "seed")
        gitops.git(root, "push", "-q", "origin", "main")
        gitops.git(root, "branch", "-q", "--set-upstream-to=origin/main", "main")

    def _accepted(self, policy: str) -> None:
        """What ``promotion accept`` records on a host whose league already has our champion."""
        promotion = f"P-{ULID()}"
        write_json(
            self.canonical / PROMOTIONS_DIR / f"{promotion}.json",
            {
                "id": promotion, "kind": "accepted", "version_id": None,
                "platform_policy_version_id": policy, "league_id": LEAGUE, "stage": "published",
            },
        )

    def admit(self, mode: str = "experimentation", refs=()) -> Worker:
        return Worker(self, f"t_{str(ULID()).lower()[-8:]}")

    def reviewed_experiment(
        self, platform_id: str = PV_A, decision: str = "promote", outcome: dict | None = None
    ) -> tuple[str, str]:
        """Commit and push a version, a reviewed experiment bound to ``platform_id`` with
        ``decision``, its rows file, its stored outcome (eligible unless ``outcome`` overrides
        fields) and its candidate strategy."""
        version = self.store.create("version", dict(VERSION))
        design = design_dict()
        experiment = self.store.create(
            "experiment",
            {
                "hypothesis": self.hypothesis,
                "parents": [],
                "mode": "experimentation",
                "lever": "SK.last_hit",
                "baseline": "V-0",
                "change": {"components": ["SK.last_hit"], "description": "Wait for range."},
                "evaluation_design": design,
                "environment": {"game_id": "fakegame", "game_version": "fake-1"},
                "status": "designed",
            },
        )
        self.store.set_status(experiment, "built")
        binding = {"version_id": version, "platform_policy_version_id": platform_id}
        self.store.update(experiment, {"status": "uploaded", "candidate_binding": binding})
        self.store.update(experiment, {"status": "evaluating"})
        self.store.update(
            experiment,
            {"status": "reviewed", "decision": {"outcome": decision, "reason": "fixture"}},
        )
        rows = self.canonical / "stats" / "rows" / f"{experiment}.jsonl"
        rows.parent.mkdir(parents=True, exist_ok=True)
        rows.write_text('{"episode_id": "e1"}\n')
        stored = {
            "experiment": experiment,
            "champion": BASELINE_PV,
            "candidate_platform_id": platform_id,
            "noise_rev": "rev1",
            "rows_sha256": hashlib.sha256(rows.read_bytes()).hexdigest(),
            "design_sha256": design_sha256(EvaluationDesign.from_dict(design)),
            "promote_eligible": True,
            "failures": [],
            "per_metric": {},
            **(outcome or {}),
        }
        write_json(self.canonical / "stats" / "outcomes" / f"{experiment}.json", stored)
        candidate = self.canonical / "strategy" / "candidates" / experiment / "STRATEGY.md"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(f"# Candidate for {experiment}\n")
        gitops.commit_all(self.canonical, f"experiment {experiment}")
        gitops.git(self.canonical, "push", "-q", "origin", "main")
        return experiment, version


def fake_state(board: ResearchBoard) -> dict:
    return json.loads((board.canonical / ".supervisor/runtime/fake_softmax.json").read_text())
