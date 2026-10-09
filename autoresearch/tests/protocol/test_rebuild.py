"""Board rebuild after board loss, and restore from the workspace remote alone (Task 3.8,
Overview C12 with amendments A2, A10, A11). Real git with a bare ``origin``, FileQueue,
FakeSoftmax and simulated workers (``tests/support``)."""

import datetime
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import jsonschema
import pytest
import yaml
from support.board import Board, Worker, new_hypothesis
from support.keeper_host import PLENTY, no_hermes
from support.promotion_board import design_dict

from autoresearch.cli import main as cli_main
from autoresearch.games.loader import load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.file_queue import FileQueue
from autoresearch.queue.requests import load_schema
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion, queue_keeper
from autoresearch.runtime.admission import STALE_EPOCH, admit_and_create, board_epoch
from autoresearch.runtime.briefing import BriefingInputs
from autoresearch.runtime.promotion import CHAMPION_PATH, adopt, stage_champion, submit_promotion
from autoresearch.runtime.rebuild import needs_rebuild, rebuild
from autoresearch.softmax_tool import cli as softmax_cli
from autoresearch.softmax_tool.fake import DEFAULT_PLAYER_ID, FAULTS_ENV, FakeSoftmax

AUTORESEARCH_DIR = Path(__file__).resolve().parents[2]
GAME_VERSION = "fake-1"  # FakeSoftmax's game version
FAKE_LEAGUE = "fake-league"  # games/fakegame league_id (XP requests)
# Promotions submit to the game's league too: queue-keeper's champion check (A9) reads it.
LEAGUE = FAKE_LEAGUE
PROFILE = "autoresearch"
BASELINE_PV = "autoresearch-fake:v0"
CANDIDATE_PV = "autoresearch-fake:v1"
LEVER = "SK.last_hit"
VERSION = {
    "parents": [],
    "strategy_sha256": "1" * 64,
    "source_commit": "2" * 40,
    "artifact_sha256": "3" * 64,
    "policy_name": "autoresearch-fake",
}
NOW = datetime.datetime.now(datetime.UTC)


class Host(Board):
    """A canonical workspace with a bare ``origin``, a champion, a noise record for
    ``GAME_VERSION``, an open Topic and a FakeSoftmax (the platform, which outlives hosts)."""

    def __init__(self, tmp_path: Path, noise: bool = True):
        super().__init__(tmp_path)
        self.origin = tmp_path / "origin.git"
        gitops.git(tmp_path, "init", "--bare", "-b", "main", str(self.origin))
        gitops.git(self.canonical, "remote", "add", "origin", str(self.origin))
        (self.canonical / CHAMPION_PATH).write_text("# Champion v0\n")
        if noise:
            (self.canonical / "stats").mkdir()
            (self.canonical / "stats" / "metric-noise.yaml").write_text(
                yaml.safe_dump({"game_version": GAME_VERSION, "metrics": {}})
            )
        self.topic = self.store.create(
            "topic",
            {
                "title": "Last hits",
                "objective": "Secure more last hits.",
                "metrics": [{"name": "score", "direction": "up"}],
                "guardrails": [],
                "origin": {"kind": "human"},
                "priority": 1,
                "status": "open",
            },
        )
        gitops.commit_all(self.canonical, "seed champion, noise, topic")
        self.push()
        self.sm = FakeSoftmax(self.canonical, {})
        self.sm.submit(BASELINE_PV, LEAGUE)

    def push(self) -> None:
        refspecs = ["HEAD:refs/heads/main", "refs/heads/card/*:refs/heads/card/*"]
        assert gitops.push(self.canonical, "origin", refspecs)

    def admit_requested(self, refs=(), mode="investigation") -> Worker:
        """Admit a card whose request file is committed in canonical (as after its merge)."""
        worker = self.admit(refs=refs, mode=mode)
        path = self.canonical / "cards" / "requests" / f"{worker.request_id}.json"
        write_json(path, {"id": worker.request_id, "op": "create", "spec": asdict(worker.spec)})
        gitops.commit_paths(self.canonical, [path.relative_to(self.canonical).as_posix()], "req")
        return worker

    def experiment(
        self, status: str, binding: bool = True, design: dict | None = None, **fields
    ) -> tuple[str, str]:
        """An experiment walked to ``status`` (bound to a version from ``uploaded`` on)."""
        version = self.store.create("version", dict(VERSION))
        experiment = self.store.create(
            "experiment",
            {
                "hypothesis": self.hypothesis,
                "parents": [],
                "mode": "experimentation",
                "lever": LEVER,
                "baseline": "V-0",
                "change": {"components": [LEVER], "description": "Wait for range."},
                "evaluation_design": design or {"n_min": 10, "baseline_version": BASELINE_PV},
                "environment": {"game_id": "fakegame", "game_version": GAME_VERSION},
                "status": "designed",
            },
        )
        steps = {
            "designed": [],
            "built": ["built"],
            "uploaded": ["built", "uploaded"],
            "awaiting-credits": ["built", "uploaded", "awaiting-credits"],
            "evaluating": ["built", "uploaded", "evaluating"],
            "reviewed": ["built", "uploaded", "evaluating"],
        }[status]
        for step in steps:
            self.store.set_status(experiment, step)
            if step == "uploaded" and binding:
                pv = fields.pop("platform_id", CANDIDATE_PV)
                bound = {"version_id": version, "platform_policy_version_id": pv}
                self.store.update(experiment, {"candidate_binding": bound})
        if status == "reviewed":
            decision = {"outcome": fields.pop("outcome", "promote"), "reason": "Better."}
            self.store.update(experiment, {"status": "reviewed", "decision": decision})
        if fields:
            self.store.update(experiment, fields)
        gitops.commit_all(self.canonical, f"experiment {experiment} {status}")
        return experiment, version


@pytest.fixture
def host(tmp_path) -> Host:
    root = tmp_path / "old"
    root.mkdir()
    return Host(root)


@pytest.fixture
def game():
    return load_game(AUTORESEARCH_DIR, "fakegame")


def _lose_board(host: Host, tmp_path: Path) -> FileQueue:
    """The board is gone (a new, missing board directory); canonical survives."""
    host.queue = FileQueue(tmp_path / "lost-board")
    return host.queue


def _rebuild(host: Host, queue=None) -> list:
    return rebuild(host.ctx(), queue or host.queue, host.sm, game_version=GAME_VERSION, now=NOW)


def _queue_keys(queue: FileQueue) -> dict:
    return read_json(queue.directory / "index.json")["keys"]


# --- detection (C12, A2) ---


def test_needs_rebuild_when_the_board_is_missing(host, tmp_path):
    host.admit_requested()
    assert needs_rebuild(host.ctx(), host.queue) is False
    assert needs_rebuild(host.ctx(), _lose_board(host, tmp_path)) is True


def test_needs_rebuild_when_no_task_of_an_open_request_is_on_the_board(host, tmp_path):
    host.admit_requested()
    queue = _lose_board(host, tmp_path)
    queue.ensure_board()  # an empty board with the same slug
    assert needs_rebuild(host.ctx(), queue) is True


def test_no_rebuild_without_open_requests(host, tmp_path):
    worker = host.admit()
    worker.start()
    worker.write_journal()
    worker.finish()
    host.process()
    queue = _lose_board(host, tmp_path)
    queue.ensure_board()
    assert needs_rebuild(host.ctx(), queue) is False


# --- one recovery action per request receipt ---


def test_each_receipt_gets_exactly_one_recovery_action(host, tmp_path):
    admitted = host.admit_requested()
    handed_off = host.admit_requested()
    handed_off.start()
    handed_off.write_journal()
    handed_off.finish()
    published = host.admit_requested()
    published.start()
    published.write_journal()
    published.finish()
    host.process()
    old_epoch = board_epoch(host.ledger)
    queue = _lose_board(host, tmp_path)

    specs = _rebuild(host)

    epoch = board_epoch(host.ledger)
    assert epoch != old_epoch
    board_file = host.committed(host.ledger / "board.json")
    assert board_file["epoch"] == epoch and board_file["rebuilding"] is False
    readmitted = [spec for spec in specs if spec.request_id == admitted.request_id]
    assert len(readmitted) == 1
    receipt = admitted.receipt()
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "admitted"
    assert [task["board_epoch"] for task in receipt["tasks"]] == [old_epoch, epoch]
    new_task = receipt["tasks"][-1]["task_id"]
    assert _queue_keys(queue)[f"req:{admitted.request_id}:{epoch}"] == new_task
    cards = {card.spec.request_id: card for card in queue.list()}
    assert admitted.request_id in cards
    assert handed_off.request_id not in cards and published.request_id not in cards
    assert handed_off.receipt()["state"] == "published"  # host.process() published both
    assert gitops.git(host.canonical, "status", "--porcelain") == ""


def test_handed_off_request_gets_no_card_and_is_adopted(host, tmp_path, game):
    worker = host.admit_requested()
    worker.start()
    new_hypothesis(worker)
    worker.write_journal()
    worker.finish()  # handoff committed; never processed on the lost board
    queue = _lose_board(host, tmp_path)

    specs = _rebuild(host)

    assert worker.request_id not in {spec.request_id for spec in specs}
    [outcome] = host.process(queue=queue)  # earlier-epoch rule: adopted as is
    assert outcome.state == "published"
    assert worker.receipt()["state"] == "published"


def test_rebuild_lifecycle_cards(host, tmp_path):
    host.store.update(host.hypothesis, {"topics": [host.topic]})
    designed, _ = host.experiment("designed")
    built, _ = host.experiment("built")
    uploaded, version = host.experiment("uploaded")
    unbound, _ = host.experiment("uploaded", binding=False)
    awaiting, _ = host.experiment("awaiting-credits", lifecycle={"launch_attempts": 2})
    later = (NOW + datetime.timedelta(hours=1)).isoformat(timespec="seconds")
    backing_off, _ = host.experiment(
        "awaiting-credits", lifecycle={"launch_attempts": 1, "next_launch_after": later}
    )
    evaluated, _ = host.experiment("evaluating")
    host.sm.xp_status(host.sm.xp_create(evaluated, 1, 1, {"num_episodes": 2}))
    running, _ = host.experiment("evaluating")  # no completed XP yet
    promote, _ = host.experiment("reviewed")
    hold, _ = host.experiment("reviewed", outcome="hold")
    covered, _ = host.experiment("designed")
    host.admit_requested(refs=[f"ar://experiment/{covered}"])
    _lose_board(host, tmp_path)

    specs = _rebuild(host)

    runtime_specs = [spec for spec in specs if spec.created_by == "runtime"]
    by_marker = {spec.why.rsplit("[", 1)[-1].rstrip("]"): spec for spec in runtime_specs}
    epoch = board_epoch(host.ledger)
    expected = {
        f"build:{designed}@{epoch}",
        f"upload:{built}@{epoch}",
        f"launch:{uploaded}:1",  # launch_attempts + 1: the attempt about to be made
        f"launch:{awaiting}:3",
        f"review:{evaluated}:n0",  # n: the XP requests in the record (none recorded here)
        f"promote:{promote}@{epoch}",
        f"planning:rebuild:{epoch}",
    }
    assert set(by_marker) == expected
    for experiment in (unbound, backing_off, running, hold, covered):
        assert not any(experiment in marker for marker in by_marker)
    launch = by_marker[f"launch:{uploaded}:1"]
    assert "xp recover" in launch.why and launch.mode == "experimentation"
    assert "--attempt 1" in launch.why
    assert launch.refs == [f"ar://experiment/{uploaded}", f"ar://version/{version}"]
    assert by_marker[f"promote:{promote}@{epoch}"].mode == "maintenance"
    # The review card names the hypothesis and its Topics, so the review may update them (D-23).
    assert by_marker[f"review:{evaluated}:n0"].refs[-2:] == [
        f"ar://hypothesis/{host.hypothesis}",
        f"ar://topic/{host.topic}",
    ]
    for spec in runtime_specs:
        request = host.follow_up(spec.request_id)
        assert request is not None and request["spec"]["created_by"] == "runtime"


def test_rebuild_requests_calibration_without_a_noise_record(tmp_path):
    root = tmp_path / "bare-host"
    root.mkdir()
    host = Host(root, noise=False)
    specs = _rebuild(host)
    [calibration] = [s for s in specs if f"[calibrate:{GAME_VERSION}]" in s.why]
    assert calibration.mode == "maintenance" and calibration.priority == "P1"


def test_rebuild_without_calibration_when_noise_is_current(host):
    assert not any("calibrate:" in spec.why for spec in _rebuild(host))


def test_rebuild_after_a_crash_resumes_the_same_epoch(host, tmp_path, monkeypatch):
    admitted = host.admit_requested()
    queue = _lose_board(host, tmp_path)
    from autoresearch.runtime import rebuild as rebuild_module

    def crash(*args, **kwargs):
        raise RuntimeError("crash during rebuild")

    monkeypatch.setattr(rebuild_module, "_lifecycle_cards", crash)
    with pytest.raises(RuntimeError):
        _rebuild(host)
    epoch = board_epoch(host.ledger)
    assert host.committed(host.ledger / "board.json")["rebuilding"] is True
    assert needs_rebuild(host.ctx(), queue) is True  # unfinished rebuild
    monkeypatch.undo()

    _rebuild(host)

    assert board_epoch(host.ledger) == epoch
    assert len(admitted.receipt()["tasks"]) == 2
    assert [c.spec.request_id for c in queue.list()].count(admitted.request_id) == 1
    assert needs_rebuild(host.ctx(), queue) is False


def test_admission_leaves_an_earlier_epoch_receipt_to_rebuild(host, tmp_path, game):
    admitted = host.admit_requested()
    queue = _lose_board(host, tmp_path)
    write_json(host.ledger / "board.json", {"epoch": "e9", "rebuilding": False})
    gitops.commit_all(host.canonical, "a newer epoch")
    result = admit_and_create(
        host.ctx(), queue, admitted.spec, PROFILE, BriefingInputs(), game, GAME_VERSION
    )
    assert not result.ok and result.reasons == [STALE_EPOCH]
    assert queue.list() == []


def test_uncommitted_handoff_debris_does_not_block_readmission(host, tmp_path):
    admitted = host.admit_requested()
    debris = host.ledger / "handoffs" / admitted.task_id / "r-lost.json"
    write_json(debris, {"state": "committed"})  # a crash left it on disk, never committed
    queue = _lose_board(host, tmp_path)
    assert needs_rebuild(host.ctx(), queue) is True

    specs = _rebuild(host)

    assert [spec.request_id for spec in specs].count(admitted.request_id) == 1
    assert len(admitted.receipt()["tasks"]) == 2
    assert needs_rebuild(host.ctx(), queue) is False  # no rebuild on every run


def _help_requests(host: Host, text: str) -> list[dict]:
    outbox = host.canonical / "comms" / "outbox"
    messages = [read_json(path) for path in sorted(outbox.glob("*.json"))]
    return [
        m
        for m in messages
        if m.get("kind") == "help_request" and (text in m["title"] or text in m.get("about", {}).values())
    ]


def test_admitted_request_without_a_committed_spec_is_closed_with_one_help_request(host, tmp_path):
    lost = host.admit()  # admitted, but its cards/requests file was never committed
    queue = _lose_board(host, tmp_path)
    queue.ensure_board()
    assert needs_rebuild(host.ctx(), queue) is True

    specs = _rebuild(host)

    assert lost.request_id not in {spec.request_id for spec in specs}
    receipt = lost.receipt()
    jsonschema.validate(receipt, load_schema("receipt.json"))
    assert receipt["state"] == "rejected" and lost.request_id in receipt["reason"]
    assert len(_help_requests(host, lost.request_id)) == 1
    assert needs_rebuild(host.ctx(), queue) is False  # settled for good: no repeat rebuild
    assert gitops.git(host.canonical, "status", "--porcelain") == ""


def test_rebuild_readmits_the_latest_recovery_request(host, tmp_path):
    holder = _dead_holder(host)
    [action] = promotion.reconcile(host.ctx(), host.queue, host.sm)
    assert action.kind == "recovery_requested"
    recovery = host.admit_follow_up(action.request_id)  # admitted on the old board, never ran
    queue = _lose_board(host, tmp_path)
    assert needs_rebuild(host.ctx(), queue) is True

    specs = _rebuild(host)

    assert [spec.request_id for spec in specs].count(action.request_id) == 1
    epoch = board_epoch(host.ledger)
    assert [t["board_epoch"] for t in recovery.receipt()["tasks"]] == ["e1", epoch]
    requests = [card.spec.request_id for card in queue.list()]
    assert requests.count(action.request_id) == 1 and holder.request_id not in requests
    assert len(promotion.load(host.canonical).recovery_requests) == 1  # no second attempt
    assert needs_rebuild(host.ctx(), queue) is False


# --- restore from the workspace remote alone ---


class RestoredHost(Board):
    """A Board over a canonical cloned from the remote into an empty home (no seeding)."""

    def __init__(self, home: Path, canonical: Path, queue: FileQueue):
        self.home = home
        self.canonical = canonical
        self.ledger = canonical / ".supervisor" / "ledger"
        self.queue = queue
        self.store = RecordStore(Layout(canonical))

    def ctx(self, worker=None, card_id=None, run_id=None) -> RunContext:
        return RunContext(
            hermes_home=self.home,
            canonical=self.canonical,
            worker=worker,
            card_id=card_id,
            run_id=run_id,
        )


def _card_worker(board: Board, request_id: str) -> Worker:
    """The worker of an already admitted card (assigned to its request id to claim it)."""
    [card] = [c for c in board.queue.list() if c.spec.request_id == request_id]
    board.queue.assign(card.task_id, request_id)
    return Worker(board, card.spec, card.task_id)


def _dead_holder(host: Host) -> Worker:
    """A promotion card that submitted its candidate (stage ``submitted``) and then died
    before ``card finish``."""
    experiment, version = host.experiment("reviewed")
    holder = host.admit_requested(refs=[f"ar://experiment/{experiment}"], mode="maintenance")
    holder.start()
    candidate_path = f"strategy/candidates/{experiment}/STRATEGY.md"
    candidate = holder.worktree / candidate_path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# Candidate v1\n")
    gitops.commit_all(holder.worktree, "candidate")
    outcome = submit_promotion(
        holder.ctx(), host.queue, host.sm, experiment, version, candidate_path, LEAGUE
    )
    assert outcome.ok, outcome.reason
    holder.end_run("crashed")
    assert promotion.load(host.canonical).stage == "submitted"
    return holder


def _launched_before_loss(host: Host) -> str:
    """(d): an ``awaiting-credits`` experiment (launch_attempts 1) whose relaunch, attempt 2,
    created every XP request on the platform, but whose record update was lost with the host."""
    awaiting, _ = host.experiment(
        "awaiting-credits", design=design_dict(), lifecycle={"launch_attempts": 1}
    )
    meta, _ = host.store.load(awaiting)
    planned = softmax_cli.xp_specs(
        meta["evaluation_design"], meta["candidate_binding"], FAKE_LEAGUE, 1, 1
    )
    for seed, arm, spec in planned:
        host.sm.xp_create(awaiting, 1, 2, spec, seed=seed, arm=arm)
    assert len(host.sm.xp_recover(awaiting)) == len(planned)
    return awaiting


def _old_host_before_loss(host: Host) -> dict:
    """Canonical state (a)-(f) of the restore test, pushed to ``origin``."""
    # (a) a published request
    published = host.admit_requested()
    published.start()
    published.write_journal()
    published.finish()
    host.process()
    # (b) an admitted request with no handoff
    admitted = host.admit_requested()
    # (c) a request whose handoff is committed but unpublished
    handed_off = host.admit_requested()
    handed_off.start()
    new_hypothesis(handed_off)
    handed_off.write_journal()
    handed_off.finish()
    # (d) an awaiting-credits experiment whose relaunch reached the platform
    awaiting = _launched_before_loss(host)
    # (e) a promotion in submitted whose holder never finished
    holder = _dead_holder(host)
    # (f) open Topics: host.topic
    host.push()
    return {
        "published": published,
        "admitted": admitted,
        "handed_off": handed_off,
        "awaiting": awaiting,
        "promotion": promotion.load(host.canonical).id,
        "holder": holder,
    }


def _submissions(sm: FakeSoftmax, platform_id: str) -> list[dict]:
    player = sm.whoami().player_id
    return [s for s in sm.submissions(player) if s["policy_version_id"] == platform_id]


def _softmax_cli(monkeypatch, capsys, restored: "RestoredHost", worker: Worker, *argv) -> dict:
    """Run ``autoresearch softmax ...`` in-process as the card's worker would."""
    env = {
        "HERMES_HOME": str(restored.home),
        "AUTORESEARCH_WORKSPACE": str(restored.canonical),
        "HERMES_KANBAN_WORKSPACE": str(worker.worktree),
        "HERMES_KANBAN_TASK": worker.task_id,
        "HERMES_KANBAN_RUN_ID": worker.run_id,
        "AUTORESEARCH_SOFTMAX": "fake",
        "AUTORESEARCH_GAME": "fakegame",
        "AUTORESEARCH_PLAYER_ID": DEFAULT_PLAYER_ID,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(FAULTS_ENV, raising=False)
    capsys.readouterr()
    code = cli_main(["softmax", *argv])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["ok"], payload
    return payload["data"]


class Keeper:
    """Runs the real queue-keeper over a restored host until it settles."""

    def __init__(self, restored: "RestoredHost", sm: FakeSoftmax, game):
        self.restored, self.sm, self.game = restored, sm, game
        self.env = {
            "HERMES_HOME": str(restored.home),
            "AUTORESEARCH_PLAYER_ID": DEFAULT_PLAYER_ID,
            "AUTORESEARCH_GAME": "fakegame",
            "AUTORESEARCH_SKIP_OPENROUTER_CHECK": "1",
        }

    def run_once(self) -> queue_keeper.KeeperRun:
        result = queue_keeper.run(
            self.restored.ctx(),
            self.restored.queue,
            self.sm,
            self.game,
            datetime.datetime.now(datetime.UTC),
            env=self.env,
            profile=PROFILE,
            runner=no_hermes,
            disk_free=lambda _path: PLENTY,
        )
        assert result.exit_code == 0 and result.failures == {}, result.failures
        return result

    def snapshot(self) -> tuple:
        """Canonical HEAD and every card's (task, state, assignee): what a run can change."""
        cards = tuple(sorted((c.task_id, c.state, c.assignee) for c in self.restored.queue.list()))
        return gitops.head(self.restored.canonical), cards

    def run_until_quiescent(self, limit: int = 10) -> list[queue_keeper.KeeperRun]:
        runs = []
        for _ in range(limit):
            before = self.snapshot()
            runs.append(self.run_once())
            if self.snapshot() == before:
                return runs
        raise AssertionError(f"queue-keeper did not settle in {limit} runs")


def test_restore_from_the_remote_alone(host, tmp_path, game, monkeypatch, capsys):
    old = _old_host_before_loss(host)

    home = tmp_path / "new-home"
    home.mkdir()
    canonical = home / "workspace"
    gitops.git(tmp_path, "clone", "-q", str(host.origin), str(canonical))
    gitops.git(canonical, "config", "user.email", "t@example.com")
    gitops.git(canonical, "config", "user.name", "T")
    # The platform outlives the host: the fake platform's state moves to the new home.
    state = Path(".supervisor") / "runtime" / "fake_softmax.json"
    (canonical / state).parent.mkdir(parents=True)
    shutil.copy(host.canonical / state, canonical / state)
    platform = FakeSoftmax(canonical, {})
    restored = RestoredHost(home, canonical, FileQueue(home / "board"))
    keeper = Keeper(restored, platform, game)

    runs = keeper.run_until_quiescent()

    old_epoch = "e1"
    epoch = board_epoch(restored.ledger)
    assert epoch != old_epoch
    assert sum(len(run.rebuilt) > 0 for run in runs) == 1  # one rebuild
    cards = restored.queue.list()
    by_request = {}
    for card in cards:
        by_request.setdefault(card.spec.request_id, []).append(card)

    # (a) not re-admitted
    assert old["published"].request_id not in by_request
    # (b) re-admitted once, with an epoch-suffixed key
    admitted = old["admitted"].request_id
    assert len(by_request[admitted]) == 1
    receipt = restored.committed(restored.receipt_path(admitted))
    assert [task["board_epoch"] for task in receipt["tasks"]] == [old_epoch, epoch]
    keys = _queue_keys(restored.queue)
    assert keys[f"req:{admitted}:{epoch}"] == by_request[admitted][0].task_id
    assert by_request[admitted][0].assignee == PROFILE
    # (c) adopted and published with no new card
    handed_off = old["handed_off"]
    assert handed_off.request_id not in by_request
    record = restored.committed(
        restored.canonical / handed_off.handoff_path().relative_to(host.canonical)
    )
    assert record["state"] == "published"
    assert restored.committed(restored.receipt_path(handed_off.request_id))["state"] == "published"
    # (d) one launch card for attempt 2 that says to run xp recover first. Its scripted
    #     worker runs `softmax xp recover`, then `softmax xp create`: nothing is created twice.
    awaiting = old["awaiting"]
    [launch] = [c for c in cards if f"[launch:{awaiting}:2]" in c.spec.why]
    assert launch.assignee == PROFILE
    assert launch.spec.why.index("xp recover") < launch.spec.why.index("xp create")
    on_platform = len(platform.xp_recover(awaiting))
    launcher = _card_worker(restored, launch.spec.request_id)
    launcher.start()
    recovered = _softmax_cli(monkeypatch, capsys, restored, launcher, "xp", "recover", awaiting)
    assert len(recovered) == on_platform and {r["attempt"] for r in recovered} == {2}
    created = _softmax_cli(
        monkeypatch, capsys, restored, launcher, "xp", "create", awaiting,
        "--batch", "1", "--attempt", "2",
    )  # fmt: skip
    assert created["created"] == [] and len(created["existing"]) == on_platform
    assert len(platform.xp_recover(awaiting)) == on_platform  # no duplicate XP request
    # (e) one promotion-recovery card; no resubmit
    pid = old["promotion"]
    recovery = [c for c in cards if f"promote-recover:{pid}:1" in c.spec.title]
    assert len(recovery) == 1 and recovery[0].spec.priority == "P0"
    assert old["holder"].request_id not in by_request  # covered by the promotion (A11)
    assert len(_submissions(platform, CANDIDATE_PV)) == 1
    # (f) one planning card, with the open Topic in its refs
    planning = [c for c in cards if c.spec.mode == "planning"]
    assert len(planning) == 1 and f"ar://topic/{host.topic}" in planning[0].spec.refs

    # The recovery card runs as its worker would; queue-keeper publishes the champion.
    worker = _card_worker(restored, recovery[0].spec.request_id)
    worker.start()
    adopted = adopt(worker.ctx(), pid)
    stage_champion(worker.ctx(), adopted)
    worker.write_journal()
    worker.finish()
    keeper.run_until_quiescent()

    current = promotion.load(restored.canonical)
    assert current.id == pid and current.stage == "published"
    candidate = gitops.show(
        restored.canonical, current.candidate_sha, current.candidate_path
    ).decode()
    assert (restored.canonical / CHAMPION_PATH).read_text() == candidate
    assert len(_submissions(platform, CANDIDATE_PV)) == 1  # never resubmitted

    # A second queue-keeper run adds nothing.
    before = keeper.snapshot()
    outbox = sorted(p.name for p in (restored.canonical / "comms" / "outbox").glob("*.json"))
    run = keeper.run_once()
    assert keeper.snapshot() == before
    assert run.rebuilt == [] and run.planning is None
    assert sorted(p.name for p in (restored.canonical / "comms" / "outbox").glob("*.json")) == (
        outbox
    )
    assert gitops.git(restored.canonical, "status", "--porcelain") == ""
