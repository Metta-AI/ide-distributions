"""The image's automatic sleep: idle timing, preparation and the platform's sleep report."""

import asyncio
import json
import sys
import types
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

IMAGE = Path(__file__).resolve().parents[2] / "image"


class Availability:
    """The image's inference gate; its real module needs the Hermes interpreter's dotenv."""


sys.modules.setdefault("inference", types.SimpleNamespace(Availability=Availability))
sys.path.insert(0, str(IMAGE))
import control  # noqa: E402

ROOT = "v2/workspaces/w-1"


class Workspace:
    """A seam reporting ``waiting_on``, a platform answering sleep reports, and the commands."""

    def __init__(self, answers: list[httpx.Response]):
        self.waiting_on: list[str] = []
        self.answers = answers
        self.reports: list[dict] = []
        self.commands: list[str] = []
        # While set, a report waits here: the platform has not answered yet.
        self.gate: asyncio.Event | None = None
        self.seam = httpx.AsyncClient(
            transport=httpx.MockTransport(self.status), base_url="http://seam"
        )
        player = httpx.AsyncClient(
            transport=httpx.MockTransport(self.report), base_url="http://platform/"
        )
        self.lifecycle = control.Lifecycle({}, player=player, pod="pod-a", root=ROOT)

        async def research(command: str) -> int:
            self.commands.append(command)
            return 0

        self.lifecycle.research = research

    def status(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "has_work": bool(self.waiting_on),
                "waiting_on": self.waiting_on,
                "restart_required": False,
            },
        )

    async def report(self, request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("POST", f"/{ROOT}/sleep")
        self.reports.append(json.loads(request.content))
        if self.gate is not None:
            await self.gate.wait()
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def accepted() -> httpx.Response:
    return httpx.Response(200, json={"kind": "accepted", "intent_revision": "rev-1"})


def refused(kind: str) -> httpx.Response:
    return httpx.Response(409, json={"detail": {"kind": kind, "detail": kind}})


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(control, "CHECK_S", 0.01)


def test_the_image_reports_sleep_after_idle_and_not_while_there_is_work(monkeypatch):
    async def scenario():
        workspace = Workspace([accepted()])
        workspace.waiting_on = ["admission:a-1:running"]
        watch = asyncio.create_task(workspace.lifecycle.watch(workspace.seam))
        monkeypatch.setattr(control, "IDLE_S", 0.05)
        await asyncio.sleep(0.2)
        # Work resets the idle timer on every check, so no sleep starts.
        assert workspace.commands == [] and workspace.reports == []
        reply = await workspace.lifecycle.status(
            control.ControlRequest(id=uuid4(), control="status"), workspace.seam
        )
        assert reply == {
            "ready": True,
            "ready_for_sleep": False,
            "detail": "Awake for admission:a-1:running",
            "controls": ["status", "sleep_request", "wake"],
        }
        workspace.waiting_on = []
        async with asyncio.timeout(5):
            while not workspace.reports:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        watch.cancel()
        assert workspace.commands == ["sleep"]
        assert workspace.reports == [{"pod": "pod-a"}]
        # The legacy poll still reads the prepared sleep.
        assert workspace.lifecycle.prepared and workspace.lifecycle.stopped is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("answers", "commands", "prepared", "stopped", "reports"),
    [
        ([accepted()], ["sleep"], True, None, 1),
        # Automatic sleep is off: undo the preparation and stop asking.
        (
            [refused("automatic_sleep_disabled")],
            ["sleep", "wake"],
            False,
            "automatic_sleep_disabled",
            1,
        ),
        # A successor pod owns the workspace: stop, and leave it to the successor.
        ([refused("stale_pod")], ["sleep"], True, "stale_pod", 1),
        # A concurrent command won: report once more.
        ([refused("intent_changed"), accepted()], ["sleep"], True, None, 2),
        # It won twice: treated as a failed report, undone and retried later.
        ([refused("intent_changed"), refused("intent_changed")], ["sleep", "wake"], False, None, 2),
        (
            [httpx.Response(404, json={"detail": {"kind": "unknown_workspace"}})],
            ["sleep"],
            True,
            "unknown_workspace",
            1,
        ),
        (
            [httpx.Response(403, json={"detail": "Forbidden"})],
            ["sleep", "wake"],
            False,
            "forbidden",
            1,
        ),
        # The platform failed: the idle sleep is undone so messages are served, then retried.
        ([httpx.Response(503)], ["sleep", "wake"], False, None, 1),
        ([httpx.ConnectError("refused")], ["sleep", "wake"], False, None, 1),
    ],
)
def test_each_sleep_answer_is_handled(answers, commands, prepared, stopped, reports):
    async def scenario():
        workspace = Workspace(answers)
        await workspace.lifecycle.sleep()
        assert workspace.commands == commands
        assert workspace.lifecycle.prepared is prepared
        assert workspace.lifecycle.stopped == stopped
        assert len(workspace.reports) == reports
        retried = stopped is None and not prepared
        assert workspace.lifecycle.idle_due() is False
        assert (workspace.lifecycle.next_attempt > 0) is retried

    asyncio.run(scenario())


def test_a_requested_sleep_stays_prepared_and_retries_its_report(monkeypatch):
    """The owner asked to pause: a failed report keeps the preparation and tries again."""
    monkeypatch.setattr(control, "REPORT_BACKOFF_S", (0.01,))

    async def scenario():
        workspace = Workspace([httpx.Response(502), httpx.ConnectError("refused"), accepted()])
        reply = await workspace.lifecycle.status(
            control.ControlRequest(id=uuid4(), control="sleep_request"), workspace.seam
        )
        assert reply["detail"] == "Preparing for sleep" and reply["ready_for_sleep"] is False
        await workspace.lifecycle.cycle
        assert workspace.commands == ["sleep"] and len(workspace.reports) == 3
        reply = await workspace.lifecycle.status(
            control.ControlRequest(id=uuid4(), control="status"), workspace.seam
        )
        assert reply == {
            "ready": True,
            "ready_for_sleep": True,
            "detail": "",
            "controls": ["status", "sleep_request", "wake"],
        }

    asyncio.run(scenario())


def send(workspace: Workspace, kind: str):
    return workspace.lifecycle.status(
        control.ControlRequest(id=uuid4(), control=kind), workspace.seam
    )


def test_wake_undoes_a_prepared_sleep_and_restarts_the_idle_timer():
    """A Resume reached the pod after its Pause was reported: the image serves again."""

    async def scenario():
        workspace = Workspace([accepted()])
        await send(workspace, "sleep_request")
        await workspace.lifecycle.cycle
        assert workspace.lifecycle.prepared and workspace.commands == ["sleep"]
        before = workspace.lifecycle.last_busy
        reply = await send(workspace, "wake")
        assert reply["ready_for_sleep"] is False and "wake" in reply["controls"]
        assert workspace.commands == ["sleep", "wake"]
        assert not workspace.lifecycle.requested and not workspace.lifecycle.idle_due()
        assert workspace.lifecycle.last_busy > before

    asyncio.run(scenario())


def test_wake_stops_an_in_flight_sleep_report():
    """A wake that races the report cancels it and its retries: nothing reports afterwards."""

    async def scenario():
        workspace = Workspace([accepted(), accepted()])
        workspace.gate = asyncio.Event()
        await send(workspace, "sleep_request")
        async with asyncio.timeout(5):
            while not workspace.reports:
                await asyncio.sleep(0.01)
        cycle = workspace.lifecycle.cycle
        reply = await send(workspace, "wake")
        assert cycle.cancelled()
        assert reply["ready_for_sleep"] is False
        assert workspace.commands == ["sleep", "wake"]
        workspace.gate.set()
        await asyncio.sleep(0.05)
        assert len(workspace.reports) == 1 and not workspace.lifecycle.prepared

    asyncio.run(scenario())
