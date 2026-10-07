"""A test driver for queue-keeper's card-affecting steps, in the Task 3.9 order (Overview C1):

    promotion reconcile -> rebuild if needed -> process handoffs (with publication) ->
    route inbound -> admit requests -> signals -> planning request -> orphan detection

Signals run just before the planning request so a ``queue_growth`` signal can request
planning in the same run (System Design §8).

The real ``queue_keeper`` (Task 3.9) adds bootstrap, preflight and pause, pushes, KB apply,
skill sweeping and proposal re-raise around these steps. This driver runs only the protocol
steps so restore and timeline tests can run "queue-keeper until quiescent"; Task 3.9 can
reuse it for its scripted timelines.
"""

import datetime
import functools
from collections.abc import Callable
from dataclasses import dataclass, field

from autoresearch.runtime.rebuild import needs_rebuild, rebuild
from autoresearch.runtime.signals import QUEUE_GROWTH, SignalConfig, compute_signals

from autoresearch.comms.publisher import publish_hook
from autoresearch.games.loader import Game
from autoresearch.io import gitops
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import read_requests
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion
from autoresearch.runtime.admission import AdmissionResult, admit_and_create, receipt_path
from autoresearch.runtime.briefing import BriefingInputs
from autoresearch.runtime.ledger import committed_json, ledger_of
from autoresearch.runtime.merge import find_orphan_done, process_handoffs
from autoresearch.runtime.planning import planning_request
from autoresearch.runtime.routing import route_all
from autoresearch.softmax_tool.protocol import Softmax


@dataclass
class KeeperRun:
    """What one driver run did."""

    rebuilt: list[CardSpec] = field(default_factory=list)
    handoffs: list = field(default_factory=list)
    admitted: list[AdmissionResult] = field(default_factory=list)
    planning: CardSpec | None = None
    signals: list = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


@dataclass
class KeeperDriver:
    ctx: RunContext
    queue: Queue
    sm: Softmax
    game: Game
    game_version: str
    profile: str = "autoresearch"
    allow_no_remote: bool = True
    clock: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC)
    signal_config: SignalConfig = field(default_factory=SignalConfig)

    def admit_requests(self) -> list[AdmissionResult]:
        """Admit every committed create request with no receipt yet, and finish admission of
        those still ``admitted`` (runtime files, assignment). Receipts whose task is from an
        earlier board epoch are left to rebuild (admission reports ``stale_epoch``)."""
        canonical = self.ctx.canonical
        results = []
        for request in read_requests(canonical, "HEAD"):
            if request.get("op") != "create":
                continue
            receipt = committed_json(canonical, receipt_path(ledger_of(canonical), request["id"]))
            if receipt is not None and receipt["state"] != "admitted":
                continue
            spec = CardSpec(**request["spec"])
            results.append(
                admit_and_create(
                    self.ctx,
                    self.queue,
                    spec,
                    self.profile,
                    BriefingInputs(),
                    self.game,
                    self.game_version,
                )
            )
        return results

    def run_once(self) -> KeeperRun:
        ctx, queue, sm = self.ctx, self.queue, self.sm
        now = self.clock()
        run = KeeperRun()
        promotion.reconcile(ctx, queue, sm)
        if needs_rebuild(ctx, queue):
            run.rebuilt = rebuild(ctx, queue, sm, game_version=self.game_version, now=now)
        publish = functools.partial(publish_hook, allow_no_remote=self.allow_no_remote)
        store = RecordStore(Layout(ctx.canonical))
        run.handoffs = process_handoffs(ctx, queue, store, publish=publish)
        route_all(ctx, queue, allow_no_remote=self.allow_no_remote)
        run.admitted = self.admit_requests()
        run.signals = compute_signals(
            ctx, queue, now, self.signal_config, allow_no_remote=self.allow_no_remote
        )
        growth = any(signal.code == QUEUE_GROWTH for signal in run.signals)
        run.planning = planning_request(ctx, queue, now) or (
            planning_request(ctx, queue, now, growth=True) if growth else None
        )
        run.orphans = find_orphan_done(queue, ledger_of(ctx.canonical))
        return run

    def snapshot(self) -> tuple:
        """Canonical HEAD and every card's (task, state, assignee): what a run can change."""
        cards = tuple(
            sorted((card.task_id, card.state, card.assignee) for card in self.queue.list())
        )
        return gitops.head(self.ctx.canonical), cards

    def run_until_quiescent(self, limit: int = 10) -> list[KeeperRun]:
        """Run until a run changes neither canonical nor the board; fail after ``limit``."""
        runs = []
        for _ in range(limit):
            before = self.snapshot()
            runs.append(self.run_once())
            if self.snapshot() == before:
                return runs
        raise AssertionError(f"queue-keeper did not settle in {limit} runs")
