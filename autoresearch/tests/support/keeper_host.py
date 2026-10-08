"""A profile home for runtime-job tests: bootstrap seeds a real canonical from the fakegame
seed and pushes to a bare ``origin``; a FileQueue board; FakeSoftmax; and simulated card
workers that claim a card, work in its worktree and end with ``card finish``. Tests drive the
real ``queue_keeper.run`` (and ``xp_poll.run``, ``health.run``) with explicit times."""

import datetime
import json
from collections import namedtuple
from pathlib import Path

from autoresearch.comms.stage import stage
from autoresearch.games.loader import load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json
from autoresearch.paths import Layout
from autoresearch.queue.card import Card, CardSpec
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.requests import write_request
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import queue_keeper
from autoresearch.runtime.card_finish import finish
from autoresearch.softmax_tool import cli as softmax_cli
from autoresearch.softmax_tool.fake import DEFAULT_PLAYER_ID, FakeSoftmax
from autoresearch.stats.design import EvaluationDesign, MetricSpec

AUTORESEARCH_DIR = Path(__file__).resolve().parents[2]
PROFILE = "autoresearch"
GAME_VERSION = "fake-1"  # FakeSoftmax's game version
Usage = namedtuple("Usage", "total used free")
PLENTY = Usage(0, 0, 100 * 1024**3)
HYPOTHESIS = {
    "statement": "Heroes attack before the creep is in kill range.",
    "prediction": "Waiting for kill range raises last hits.",
    "topics": [],
    "lever": "SK.last_hit",
    "state": "proposed",
}


BASELINE_PV = "autoresearch-fake:v0"
CANDIDATE_PV = "autoresearch-fake:v1"
LEAGUE = "fake-league"  # games/fakegame league_id
VERSION = {
    "parents": [],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-fake",
}
# The fakegame's outcome metrics; two seeds, so two candidate/baseline pairs (A12).
DESIGN = EvaluationDesign(
    GAME_VERSION,
    BASELINE_PV,
    (0, 1),
    ("random", "opp:v3"),
    (1, 2),
    (
        MetricSpec("score", "up", "primary", 1.0, None),
        MetricSpec("errors", "down", "agent_guardrail", None, 0.5),
    ),
    0.5,
    {},
    2,
    "rev1",
)


def evaluating_experiment(
    store: RecordStore, hypothesis: str, walk=("built", "uploaded", "evaluating")
) -> str:
    """Create an experiment bound to ``CANDIDATE_PV`` and walk it through ``walk``."""
    version = store.create("version", dict(VERSION))
    experiment = store.create(
        "experiment",
        {
            "hypothesis": hypothesis,
            "parents": [],
            "mode": "experimentation",
            "lever": "SK.last_hit",
            "baseline": "V-0",
            "change": {"components": ["SK.last_hit"], "description": "Wait for range."},
            "evaluation_design": DESIGN.to_dict(),
            "environment": {"game_id": "fakegame", "game_version": GAME_VERSION},
            "status": "designed",
        },
    )
    for step in walk:
        store.set_status(experiment, step)
        if step == "uploaded":
            binding = {"version_id": version, "platform_policy_version_id": CANDIDATE_PV}
            store.update(experiment, {"candidate_binding": binding})
    return experiment


def launch_all(sm: FakeSoftmax, store: RecordStore, experiment: str) -> list:
    """Create every XP request of batch 1, attempt 1 (as ``softmax xp create`` would)."""
    meta, _ = store.load(experiment)
    planned = softmax_cli.xp_specs(
        meta["evaluation_design"], meta["candidate_binding"], LEAGUE, 1, 1
    )
    return [sm.xp_create(experiment, 1, 1, spec, seed=s, arm=a) for s, a, spec in planned]


def no_hermes(argv: list[str], home: Path) -> None:
    """Config migrations runner for tests: Hermes is not installed."""


class Host:
    def __init__(self, tmp_path: Path, faults: str = "", remote: bool = True):
        self.tmp_path = tmp_path
        self.home = tmp_path / "profiles" / PROFILE
        self.home.mkdir(parents=True)
        self.origin = tmp_path / "origin.git"
        gitops.git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        self.canonical = self.home / "workspace"
        self.env = {
            "HERMES_HOME": str(self.home),
            "AUTORESEARCH_GAME": "fakegame",
            "AUTORESEARCH_PLAYER_ID": DEFAULT_PLAYER_ID,
            "AUTORESEARCH_SOFTMAX": "fake",
            "AUTORESEARCH_SKIP_OPENROUTER_CHECK": "1",
        }
        if remote:
            self.env["AUTORESEARCH_WORKSPACE_REMOTE"] = str(self.origin)
        self.queue = FileQueue(self.home / "board")
        self.now = datetime.datetime.now(datetime.UTC)
        self.game = load_game(AUTORESEARCH_DIR, "fakegame")
        self.set_faults(faults)

    @property
    def ctx(self) -> RunContext:
        return RunContext(self.home, self.canonical, None, None, None)

    @property
    def ledger(self) -> Path:
        return self.canonical / ".supervisor" / "ledger"

    @property
    def store(self) -> RecordStore:
        return RecordStore(Layout(self.canonical))

    def set_faults(self, faults: str) -> None:
        self.env["AUTORESEARCH_FAKE_FAULTS"] = faults
        # The fake platform's clock follows the test's time (credential expiry).
        self.sm = FakeSoftmax(self.canonical, self.env, clock=lambda: self.now)

    def tick(self, now: datetime.datetime, **kwargs) -> queue_keeper.KeeperRun:
        self.now = now
        kwargs.setdefault("runner", no_hermes)
        kwargs.setdefault("disk_free", lambda _path: PLENTY)
        return queue_keeper.run(
            self.ctx, self.queue, self.sm, self.game, now, env=self.env, **kwargs
        )

    # --- reading canonical ---

    def head(self) -> str:
        return gitops.head(self.canonical)

    def remote_head(self) -> str:
        return gitops.git(self.origin, "rev-parse", "main")

    def committed(self, path: Path) -> dict | None:
        content = gitops.show(self.canonical, "HEAD", path.relative_to(self.canonical).as_posix())
        return None if content is None else json.loads(content)

    def receipt(self, request_id: str) -> dict | None:
        return self.committed(self.ledger / "requests" / f"{request_id}.json")

    def outbox(self) -> list[dict]:
        outbox = self.canonical / "comms" / "outbox"
        return [read_json(path) for path in sorted(outbox.glob("*.json"))]

    def subjects(self) -> list[str]:
        return gitops.git(self.canonical, "log", "--reverse", "--format=%s").splitlines()

    def requests(self) -> list[dict]:
        listing = gitops.git(self.canonical, "ls-tree", "--name-only", "HEAD", "cards/requests/")
        return [json.loads(gitops.show(self.canonical, "HEAD", p)) for p in listing.split()]

    def clean(self) -> bool:
        return gitops.git(self.canonical, "status", "--porcelain") == ""

    # --- writing canonical as the runtime would (runtime-written requests) ---

    def runtime_request(self, op: str = "create", **fields) -> str:
        request_id = write_request(Layout(self.canonical), op, **fields)
        path = f"cards/requests/{request_id}.json"
        gitops.commit_paths(self.canonical, [path], f"test request {request_id}")
        return request_id

    # --- workers ---

    def claim(self, mode: str | None = None) -> "CardRun":
        """Claim the best pending card assigned to our profile (as the dispatcher would),
        or, with ``mode``, the first pending card of that mode."""
        worker = PROFILE
        if mode is not None:
            card = next(c for c in self.queue.list("pending") if c.spec.mode == mode)
            assert card.assignee == PROFILE
            worker = f"worker-{card.task_id}"
            self.queue.assign(card.task_id, worker)
        claimed = self.queue.claim_next(worker)
        assert claimed is not None
        return CardRun(self, claimed)


class CardRun:
    """One claimed card run in its own worktree (branched from canonical HEAD)."""

    def __init__(self, host: Host, card: Card):
        self.host = host
        self.card = card
        self.run_id = card.runs[-1].run_id
        self.worktree = host.canonical / ".worktrees" / card.spec.request_id
        branch = f"card/{card.spec.request_id}"
        gitops.git(host.canonical, "worktree", "add", "-b", branch, str(self.worktree))

    @property
    def task_id(self) -> str:
        return self.card.task_id

    @property
    def ctx(self) -> RunContext:
        return RunContext(self.host.home, self.host.canonical, self.worktree, self.task_id,
                          self.run_id)  # fmt: skip

    @property
    def store(self) -> RecordStore:
        return RecordStore(Layout(self.worktree))

    def request(self, title: str, mode: str, refs=(), priority: str = "P2") -> str:
        spec = CardSpec("", title, mode, f"Requested by {self.task_id}.", list(refs),
                        self.task_id, priority)  # fmt: skip
        return write_request(Layout(self.worktree), "create", spec=spec)

    def stage_note(self, title: str, body: str = "Body.", **fields) -> str:
        return stage(Layout(self.worktree), "note", title, body, [], **fields)

    def hypothesis(self) -> str:
        return self.store.create("hypothesis", dict(HYPOTHESIS), "A hypothesis.\n")

    def journal(self) -> str:
        meta = {"iteration": 1, "mode": self.card.spec.mode, "card": self.task_id,
                "doctrine_version": 1}  # fmt: skip
        return self.store.create("journal", meta, "Did things.\n\n## Decisions\n\n- none\n")

    def finish(self, outcome: str = "done") -> dict:
        self.journal()
        result = finish(self.ctx, outcome)
        assert result.ok, result.problems
        self.host.queue.finish(self.task_id, self.run_id, outcome)
        return result.record
