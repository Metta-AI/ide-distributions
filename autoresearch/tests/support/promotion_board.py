"""A promotion-ready board for the softmax tool tests (Plan 03 Tasks 3.3–3.4).

A ``Board`` with a bare ``origin``, a seeded champion STRATEGY, a FakeSoftmax whose
leaderboard champion is ``BASELINE_PV``, and helpers that commit a reviewed experiment with a
stored ``stats evaluate`` outcome and admit the card that promotes it."""

import hashlib
import json
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.queue.file_queue import FileQueue
from autoresearch.runtime.promotion import CHAMPION_PATH
from autoresearch.softmax_tool.fake import DEFAULT_PLAYER_ID, FakeSoftmax
from autoresearch.stats.design import EvaluationDesign, MetricSpec
from support.board import Board, Worker

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
        "fake-1", baseline, (0, 1), ("random", "opp:v3"), (1, 2), metrics, 0.5, {}, 12, "rev1"
    )
    return design.to_dict()


def design_sha256(design: dict) -> str:
    text = json.dumps(design, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SubmitBoard(Board):
    def __init__(self, tmp_path: Path, queue_dir: Path | None = None, seed_champion=True):
        super().__init__(tmp_path)
        if queue_dir is not None:
            self.queue = FileQueue(queue_dir)
        self.origin = tmp_path / "origin.git"
        gitops.git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        gitops.git(self.canonical, "remote", "add", "origin", str(self.origin))
        (self.canonical / CHAMPION_PATH).write_text(CHAMPION_V0)
        gitops.commit_all(self.canonical, "seed champion")
        assert gitops.push(self.canonical, "origin", ["HEAD:refs/heads/main"])
        self.sm = FakeSoftmax(self.canonical, {})
        if seed_champion:
            self.sm.submit(BASELINE_PV, LEAGUE)

    def reviewed_experiment(
        self, platform_id: str = PV_A, decision: str = "promote", outcome: dict | None = None
    ) -> tuple[str, str]:
        """Commit a version, a reviewed experiment bound to ``platform_id`` with ``decision``,
        its rows file and its stored outcome (eligible unless ``outcome`` overrides fields)."""
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
            "design_sha256": design_sha256(design),
            "promote_eligible": True,
            "failures": [],
            "per_metric": {},
            **(outcome or {}),
        }
        write_json(self.canonical / "stats" / "outcomes" / f"{experiment}.json", stored)
        gitops.commit_all(self.canonical, f"experiment {experiment}")
        return experiment, version

    def promoter(self, experiment: str) -> Worker:
        """Admit and start the promotion card; commit the candidate STRATEGY on its branch."""
        worker = self.admit(refs=[f"ar://experiment/{experiment}"], mode="maintenance")
        worker.start()
        path = worker.worktree / "strategy" / "candidates" / experiment / "STRATEGY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Candidate for {experiment}\n")
        gitops.commit_all(worker.worktree, f"candidate {experiment}")
        return worker
