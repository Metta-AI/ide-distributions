"""Protocol slice on the real Hermes Kanban dispatcher (Plan 01 Task 5.1, marker ``hermes``).

This test is the Kanban go/no-go evidence. It installs the distribution into a throwaway
host root (``conftest.install``), runs real card workers (``z-ai/glm-5.3-flash`` through
OpenRouter; it spends credit and FAILS without ``OPENROUTER_API_KEY``) dispatched by the real
gateway dispatcher, and drives queue-keeper as Hermes cron does. Card bodies
(``slice_cards/*.md``) tell each worker the exact commands to run.

Steps (the brief's Steps 1-5):

1. Install with ``AUTORESEARCH_GAME=fakegame``, ``AUTORESEARCH_SOFTMAX=fake``, a bare remote
   and ``OPENROUTER_MODEL``; bootstrap.
2. Seed canonical: hypothesis H-x, two reviewed experiments with eligible stored outcomes
   and committed candidates, the leaderboard champion on FakeSoftmax; commit three requests:
   investigation cards A and B (both edit H-x line 2) and promotion card C; run queue-keeper,
   which admits all three.
3. Resume the cron jobs and run ``hermes gateway run`` (its dispatcher starts A and B on its
   first tick, C when a slot frees; its cron runs queue-keeper every minute).
4. Assert: one of A/B merges and the other is sent to reconciliation (overlap H-x); outbox
   seqs contiguous; the merged card's request admitted with a receipt; the remote has
   ``main`` and ``card/*``; plugin tool events exist; C's promotion is ``published`` and
   FakeSoftmax has its one submission.
5. Faults, with the cron job paused and queue-keeper run as the cron would (env = the
   ``terminal.env_passthrough`` names of the profile ``.env``, amendment A4): cards F and D
   wait for a go file, P is pending. A wrong player id in the ``.env`` makes the next run
   pause (P unassigned) and D's running ``softmax submit`` is refused (``identity``). After
   the fix, ``crash_after=merge`` makes a run exit between a merge and its publication; the
   next run publishes that handoff exactly once and resumes P.

Test shims (environment only; the deliverable is the profile repo): the host root
``config.yaml`` (the installer's job, A3: no ``default_assignee``, ``max_in_progress: 2``, a
10 s dispatch tick), the fake credential with a ``player_id``, a parked unassigned planning
card so the planning cadence does not add an unscripted card, and the go files.
"""

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import yaml
from support.promotion_board import BASELINE_PV, LEAGUE, PV_A, VERSION, design_dict, design_sha256

from autoresearch.io import gitops
from autoresearch.io.docs import write_json
from autoresearch.io.locks import file_lock
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import write_request
from autoresearch.records.store import RecordStore
from autoresearch.runtime.bootstrap import set_env_lines
from autoresearch.runtime.ledger import canonical_lock
from autoresearch.runtime.preflight import env_file_value
from autoresearch.runtime.promotion import CHAMPION_PATH
from autoresearch.softmax_tool.fake import FakeSoftmax

pytestmark = pytest.mark.hermes

MODEL = "z-ai/glm-5.3-flash"
CARDS = Path(__file__).parent / "slice_cards"
PLAYER = "ply_fake"
WRONG_PLAYER = "ply_wrong"
CREDENTIAL = json.dumps(
    {"token": "ply_fake_token", "expires_at": "2099-01-01T00:00:00Z", "player_id": PLAYER}
)
PV_B = "autoresearch-fake:v2"
HANDOFF_WAIT_S = 20 * 60  # Step 3: all handoffs reach a final state within this
PHASE_WAIT_S = 15 * 60
POLL_S = 10
FINAL_STATES = {"published", "reconcile", "repair", "superseded"}
CRON_JOBS = ("queue-keeper", "xp-poll", "health")
HYPOTHESIS_BODY = (
    "Line 1: fixture hypothesis for the protocol slice.\n"
    "Line 2: not edited yet.\n"
    "Line 3: end of the fixture.\n"
)


# --- small helpers ----------------------------------------------------------------------


def hermes_python() -> str:
    """The interpreter Hermes runs cron scripts with (as in ``conftest.hermes_python``)."""
    first_line = Path(shutil.which("hermes")).resolve().read_text(errors="replace").splitlines()[0]
    return first_line.removeprefix("#!").strip()


def openrouter_usage(key: str) -> float:
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return float(json.loads(response.read())["data"]["usage"])


def committed(canonical: Path, relative: str) -> dict | None:
    content = gitops.show(canonical, "HEAD", relative)
    return None if content is None else json.loads(content)


def committed_dir(canonical: Path, relative: str) -> dict[str, dict]:
    listing = gitops.git(canonical, "ls-tree", "-r", "--name-only", "HEAD", f"{relative}/")
    return {
        path: committed(canonical, path) for path in listing.splitlines() if path.endswith(".json")
    }


def receipt(canonical: Path, request_id: str) -> dict | None:
    return committed(canonical, f".supervisor/ledger/requests/{request_id}.json")


def handoffs_of(canonical: Path, task_id: str) -> list[dict]:
    return list(committed_dir(canonical, f".supervisor/ledger/handoffs/{task_id}").values())


def outbox(canonical: Path) -> list[dict]:
    return [m for _, m in sorted(committed_dir(canonical, "comms/outbox").items())]


def render(name: str, wait: bool = False, **values: str) -> str:
    text = (CARDS / name).read_text()
    text = text.replace("@WAIT@", (CARDS / "wait.md").read_text() if wait else "")
    for key, value in values.items():
        text = text.replace(f"@{key.upper()}@", value)
    assert "@" not in text.replace("ar://", ""), text
    return text


class Slice:
    """The installed profile, its canonical workspace and the job runners."""

    def __init__(self, install):
        self.install = install
        self.board = install.profile
        self.home = install.profile_dir
        self.canonical = self.home / "workspace"
        self.gateway: subprocess.Popen | None = None
        self.gateway_log = install.root / "gateway.log"

    # -- environment --

    def set_env(self, **values: str) -> None:
        set_env_lines(self.home / ".env", values)

    def cron_env(self, **extra: str) -> dict:
        """What a cron script gets under the gateway (A4): HERMES_HOME is the profile and,
        from the profile .env, only the ``terminal.env_passthrough`` names."""
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        names = config["terminal"]["env_passthrough"]
        values = {n: env_file_value(self.home / ".env", n) for n in names}
        env = self.install.env(HERMES_HOME=str(self.home))
        env.update({name: value for name, value in values.items() if value is not None})
        env.update(extra)
        return env

    def keeper(self, *args: str, faults: str = "", expect_exit: int = 0) -> dict | None:
        """One queue-keeper run through the cron launcher (Hermes's interpreter, cwd
        ``scripts/``), retried while another run holds the run lock."""
        extra = {"AUTORESEARCH_FAKE_FAULTS": faults} if faults else {}
        for _ in range(30):
            result = subprocess.run(
                [hermes_python(), str(self.home / "scripts" / "queue_keeper.py"), *args],
                cwd=self.home / "scripts",
                env=self.cron_env(**extra),
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            assert result.returncode == expect_exit, (
                f"queue-keeper exited {result.returncode}, expected {expect_exit}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
            if expect_exit != 0:
                return None
            summary = json.loads(result.stdout.strip().splitlines()[-1])
            if not summary["skipped"]:
                return summary
            time.sleep(5)
        raise AssertionError("queue-keeper was skipped 30 times (run lock held)")

    # -- kanban --

    def show(self, task_id: str) -> dict:
        return json.loads(
            self.install.hermes("kanban", "--board", self.board, "show", task_id, "--json").stdout
        )

    def tasks(self) -> list[dict]:
        return json.loads(
            self.install.hermes("kanban", "--board", self.board, "list", "--json").stdout
        )

    def terminal_results(self, title: str) -> list[dict]:
        """The JSON envelopes our CLI printed in the terminal results of the worker sessions
        titled ``title`` (Hermes stores every tool result in the profile ``state.db``)."""
        connection = sqlite3.connect(self.home / "state.db")
        try:
            rows = connection.execute(
                "SELECT m.content FROM messages m JOIN sessions s ON s.id = m.session_id"
                " WHERE s.title = ? AND m.role = 'tool' AND m.tool_name = 'terminal'",
                (title,),
            ).fetchall()
        finally:
            connection.close()
        envelopes = []
        for (content,) in rows:
            try:
                envelopes.append(json.loads(json.loads(content)["output"]))
            except (ValueError, KeyError, TypeError):
                continue  # not one JSON envelope (a shell command's own output)
        return envelopes

    def park_planning(self) -> str:
        """Test shim: an unassigned planning card, never dispatched, so the planning cadence
        finds planning owned (C11) and adds no unscripted card to the slice."""
        spec = CardSpec("Q-SLICE-PARKED-PLANNING", "Parked planning (slice shim)", "planning",
                        "Never dispatched.", [], "slice-test", "P5")  # fmt: skip
        body = self.install.root / "parked.json"
        body.write_text(json.dumps(spec.__dict__))
        created = self.install.hermes(
            "kanban", "--board", self.board, "create", spec.title, "--body-file", str(body),
            "--idempotency-key", "slice:parked-planning", "--json",
        )  # fmt: skip
        return json.loads(created.stdout)["id"]

    # -- gateway --

    def start_gateway(self) -> None:
        self.gateway = subprocess.Popen(
            ["hermes", "gateway", "run", "-v"],
            env=self.install.env(),
            stdout=self.gateway_log.open("w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def stop_gateway(self) -> None:
        if self.gateway is not None and self.gateway.poll() is None:
            os.killpg(self.gateway.pid, signal.SIGTERM)
            try:
                self.gateway.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(self.gateway.pid, signal.SIGKILL)
        for task in self.tasks():  # workers outlive the gateway: stop any still running
            for run in self.show(task["id"])["runs"]:
                if run["ended_at"] is None and run.get("worker_pid"):
                    try:
                        os.kill(run["worker_pid"], signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def check_gateway(self) -> None:
        if self.gateway is not None and self.gateway.poll() is not None:
            raise AssertionError(
                f"gateway exited {self.gateway.returncode}:\n{self.gateway_log.read_text()[-4000:]}"
            )

    def cron(self, action: str) -> None:
        for job in CRON_JOBS:
            self.install.hermes("-p", self.board, "cron", action, job)

    def wait_until(self, what: str, done, timeout: float) -> float:
        start = time.monotonic()
        while not done():
            self.check_gateway()
            if time.monotonic() - start > timeout:
                states = {t["id"]: (t["title"], t["status"], t["assignee"]) for t in self.tasks()}
                raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}: {states}")
            time.sleep(POLL_S)
        return time.monotonic() - start

    def task_of(self, request_id: str) -> str:
        return receipt(self.canonical, request_id)["tasks"][-1]["task_id"]

    def go(self, request_id: str) -> None:
        scratch = self.canonical / ".worktrees" / request_id / ".scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "go").write_text("go\n")

    def fake(self) -> FakeSoftmax:
        return FakeSoftmax(self.canonical, {"AUTORESEARCH_PLAYER_ID": PLAYER})

    def usage(self) -> dict:
        connection = sqlite3.connect(self.home / "state.db")
        try:
            row = connection.execute(
                "SELECT count(*), sum(input_tokens), sum(output_tokens), sum(cache_read_tokens),"
                " sum(estimated_cost_usd) FROM sessions"
            ).fetchone()
        finally:
            connection.close()
        keys = ("sessions", "input_tokens", "output_tokens", "cache_read_tokens", "estimated_usd")
        return dict(zip(keys, row, strict=True))


# --- seeding (Step 2) ---------------------------------------------------------------------


def reviewed_experiment(store: RecordStore, canonical: Path, hypothesis: str, candidate: str,
                        baseline: str) -> tuple[str, str]:  # fmt: skip
    """A reviewed ``promote`` experiment bound to ``candidate`` with an eligible stored
    outcome evaluated against ``baseline``, and its candidate STRATEGY committed where the
    promotion card's branch will have it (``strategy/candidates/<E>/STRATEGY.md``)."""
    version = store.create("version", dict(VERSION))
    design = design_dict(baseline)
    experiment = store.create(
        "experiment",
        {
            "hypothesis": hypothesis, "parents": [], "mode": "experimentation",
            "lever": "SK.last_hit", "baseline": "V-0",
            "change": {"components": ["SK.last_hit"], "description": "Wait for range."},
            "evaluation_design": design,
            "environment": {"game_id": "fakegame", "game_version": "fake-1"},
            "status": "designed",
        },
    )  # fmt: skip
    store.set_status(experiment, "built")
    binding = {"version_id": version, "platform_policy_version_id": candidate}
    store.update(experiment, {"status": "uploaded", "candidate_binding": binding})
    store.update(experiment, {"status": "evaluating"})
    store.update(
        experiment, {"status": "reviewed", "decision": {"outcome": "promote", "reason": "slice"}}
    )
    rows = canonical / "stats" / "rows" / f"{experiment}.jsonl"
    rows.parent.mkdir(parents=True, exist_ok=True)
    rows.write_text('{"episode_id": "e1"}\n')
    write_json(
        canonical / "stats" / "outcomes" / f"{experiment}.json",
        {
            "experiment": experiment, "champion": baseline, "candidate_platform_id": candidate,
            "noise_rev": "rev1", "rows_sha256": hashlib.sha256(rows.read_bytes()).hexdigest(),
            "design_sha256": design_sha256(design), "promote_eligible": True, "failures": [],
            "per_metric": {},
        },
    )  # fmt: skip
    strategy = canonical / "strategy" / "candidates" / experiment / "STRATEGY.md"
    strategy.parent.mkdir(parents=True, exist_ok=True)
    strategy.write_text(f"# Candidate for {experiment}\n")
    return experiment, version


def request(canonical: Path, title: str, mode: str, why: str, refs: list[str], priority: str,
            order: int = 0) -> str:  # fmt: skip
    spec = CardSpec("", title, mode, why, refs, "slice-test", priority, order)
    return write_request(Layout(canonical), "create", spec=spec)


def commit_and_push(canonical: Path, message: str) -> None:
    with canonical_lock(canonical):
        gitops.commit_all(canonical, message)
        assert gitops.push(canonical, "origin", ["HEAD:refs/heads/main"])


# --- the test -------------------------------------------------------------------------------


def test_protocol_slice(install):
    key = env_file_value(install.profile_dir / ".env", "OPENROUTER_API_KEY")
    if not os.environ.get("OPENROUTER_API_KEY") or not key or key == "sk-or-fake":
        pytest.fail("the protocol slice needs OPENROUTER_API_KEY (go/no-go run: fail, not skip)")
    usage_before = openrouter_usage(key)
    started = time.monotonic()
    s = Slice(install)
    canonical = s.canonical
    report: dict = {"model": MODEL}
    try:
        # Step 1: install shims, then bootstrap through the cron launcher.
        s.set_env(OPENROUTER_MODEL=MODEL, SOFTMAX_PLAYER_CREDENTIAL=f"'{CREDENTIAL}'")
        (install.hermes_home / "config.yaml").write_text(
            yaml.safe_dump({"kanban": {"max_in_progress": 2, "dispatch_interval_seconds": 10}})
        )
        s.keeper("--bootstrap-only")
        profile_config = yaml.safe_load((s.home / "config.yaml").read_text())
        assert profile_config["model"]["default"] == MODEL  # OPENROUTER_MODEL applied

        # Step 2: fixtures and three card requests in canonical, then one keeper run.
        store = RecordStore(Layout(canonical))
        hypothesis = store.create(
            "hypothesis",
            {"statement": "Slice fixture.", "prediction": "One edit merges, one reconciles.",
             "topics": [], "lever": "SK.last_hit", "state": "proposed"},
            HYPOTHESIS_BODY,
        )  # fmt: skip
        assert (canonical / CHAMPION_PATH).exists()
        exp_c, ver_c = reviewed_experiment(store, canonical, hypothesis, PV_A, BASELINE_PV)
        exp_d, ver_d = reviewed_experiment(store, canonical, hypothesis, PV_B, PV_A)
        s.fake().submit(BASELINE_PV, LEAGUE)  # the leaderboard champion before any promotion
        doctrine = (s.home / "doctrine" / "VERSION").read_text().strip()
        h_ref = f"ar://hypothesis/{hypothesis}"
        cards = {}
        for name in ("A", "B"):
            why = render("investigation.md", card=name, hypothesis=hypothesis, doctrine=doctrine)
            cards[name] = request(
                canonical, f"Slice card {name}", "investigation", why, [h_ref], "P1"
            )
        why = render("promotion.md", card="C", experiment=exp_c, version=ver_c, doctrine=doctrine)
        cards["C"] = request(canonical, "Slice card C (promote)", "maintenance", why,
                             [f"ar://experiment/{exp_c}", f"ar://version/{ver_c}"], "P2")  # fmt: skip
        commit_and_push(canonical, "slice: fixtures and card requests A, B, C")
        s.park_planning()

        summary = s.keeper()
        assert summary["failures"] == {} and not summary["paused"], summary
        for name in "ABC":
            assert receipt(canonical, cards[name])["state"] == "admitted", name
        task = {name: s.task_of(cards[name]) for name in "ABC"}
        assert {s.show(t)["task"]["assignee"] for t in task.values()} == {s.board}
        assert summary["planning"] is None  # the parked planning card owns planning

        # Step 3: resume cron, run the gateway, wait for every handoff to be final.
        s.cron("resume")
        s.start_gateway()
        gateway_started = time.monotonic()

        def final(name: str) -> bool:
            states = [h["state"] for h in handoffs_of(canonical, task[name])]
            return bool(states) and states[-1] in FINAL_STATES

        def promotion_published() -> bool:
            promo = committed(canonical, ".supervisor/ledger/promotion.json")
            return promo is not None and promo["stage"] == "published"

        s.wait_until(
            "A, B and C handoffs final and C's promotion published",
            lambda: final("A") and final("B") and final("C") and promotion_published(),
            HANDOFF_WAIT_S,
        )
        report["step3_handoffs_final_s"] = round(time.monotonic() - gateway_started)

        # Step 4: assertions.
        both = {name: handoffs_of(canonical, task[name])[-1] for name in "AB"}
        merged = [n for n, h in both.items() if h["state"] == "published"]
        sent = [n for n, h in both.items() if h["state"] in ("reconcile", "superseded")]
        assert len(merged) == 1 and len(sent) == 1, both
        loser = both[sent[0]]
        assert loser.get("overlap") == [hypothesis], loser
        reconcile_request = loser["follow_up_request"]
        assert committed(canonical, f"cards/requests/{reconcile_request}.json") is not None, loser
        report["merged"], report["reconciled"] = merged[0], sent[0]

        winner = both[merged[0]]
        diff = ["diff", "--name-only", winner["base_sha"], winner["result_sha"]]
        requests_by_winner = gitops.git(canonical, *diff, "--", "cards/requests/").split()
        assert len(requests_by_winner) == 1, requests_by_winner
        follow_up = Path(requests_by_winner[0]).stem
        s.wait_until(
            "the merged card's request admitted",
            lambda: receipt(canonical, follow_up) is not None,
            180,
        )
        assert receipt(canonical, follow_up)["tasks"], receipt(canonical, follow_up)

        messages = outbox(canonical)
        assert [m["seq"] for m in messages] == list(range(1, len(messages) + 1))
        assert any(m["title"] == f"Slice highlight from card {merged[0]}" for m in messages)
        assert any(m["title"] == f"Slice card C promoted {exp_c}" for m in messages)

        remote_refs = gitops.git(install.remote, "for-each-ref", "--format=%(refname)").split()
        assert "refs/heads/main" in remote_refs
        for name in "ABC":
            assert f"refs/heads/card/{cards[name]}" in remote_refs, (name, remote_refs)

        events = [json.loads(line) for line in (canonical / ".supervisor" / "runtime" /
                                                "tool_events.jsonl").read_text().splitlines()]  # fmt: skip
        for name in "ABC":
            assert any(e["task"] == task[name] and e["tool"] == "terminal" for e in events), name

        promo = committed(canonical, ".supervisor/ledger/promotion.json")
        assert (promo["experiment"], promo["platform_policy_version_id"]) == (exp_c, PV_A)
        assert promo["holder_task"] == task["C"]
        submissions = [x["policy_version_id"] for x in s.fake().submissions(PLAYER)]
        assert submissions == [BASELINE_PV, PV_A], submissions  # the seed plus C's one
        assert (
            gitops.show(canonical, "HEAD", CHAMPION_PATH).decode() == f"# Candidate for {exp_c}\n"
        )
        report["step4_s"] = round(time.monotonic() - started)

        # Step 5: faults. From here queue-keeper runs only from the test (cron paused).
        s.cron("pause")
        with file_lock(
            canonical / ".supervisor" / "runtime" / "queue_keeper.run.lock", timeout_s=600
        ):
            pass  # a cron run in flight has ended

        def busy() -> bool:
            return any(t["status"] == "running" or (t["status"] == "ready" and t["assignee"])
                       for t in s.tasks())  # fmt: skip

        s.wait_until("the board to be idle", lambda: not busy(), PHASE_WAIT_S)
        cards["F"] = request(canonical, "Slice card F (fault)", "investigation",
                             render("note.md", wait=True, card="F", doctrine=doctrine),
                             [], "P0", 9)  # fmt: skip
        cards["D"] = request(canonical, "Slice card D (pause)", "maintenance",
                             render("promotion.md", wait=True, card="D", experiment=exp_d,
                                    version=ver_d, doctrine=doctrine),
                             [f"ar://experiment/{exp_d}", f"ar://version/{ver_d}"], "P0", 9)  # fmt: skip
        cards["P"] = request(canonical, "Slice card P (pending)", "investigation",
                             render("note.md", card="P", doctrine=doctrine), [], "P5")  # fmt: skip
        commit_and_push(canonical, "slice: card requests F, D, P")
        summary = s.keeper()
        assert not summary["paused"], summary
        for name in "FDP":
            task[name] = s.task_of(cards[name])
        s.wait_until(
            "F and D running (they wait for their go files)",
            lambda: all(s.show(task[n])["task"]["status"] == "running"
                        for n in "FD"),
            PHASE_WAIT_S,
        )  # fmt: skip
        pending = s.show(task["P"])["task"]
        assert (pending["status"], pending["assignee"]) == ("ready", s.board), pending

        # 5a: a wrong player id in .env: the next run pauses; the running worker is refused.
        s.set_env(AUTORESEARCH_PLAYER_ID=WRONG_PLAYER)
        summary = s.keeper()
        assert summary["paused"], summary
        pause = committed(canonical, ".supervisor/ledger/pause.json")
        assert task["P"] in pause["tasks"], pause
        assert s.show(task["P"])["task"]["assignee"] is None
        s.go(cards["D"])
        s.wait_until(
            "D to finish", lambda: s.show(task["D"])["task"]["status"] == "done", PHASE_WAIT_S
        )
        refusals = [
            e for e in s.terminal_results("Slice card D (pause)")
            if isinstance(e, dict) and e.get("ok") is False
        ]  # fmt: skip
        assert [e["error"]["reason"] for e in refusals] == ["identity"], refusals
        assert "identity changed since this worker started" in refusals[0]["error"]["message"]
        [d_handoff] = handoffs_of(canonical, task["D"])
        assert d_handoff["outcome"] == "blocked", d_handoff
        assert [x["policy_version_id"] for x in s.fake().submissions(PLAYER)] == [BASELINE_PV, PV_A]
        assert committed(canonical, ".supervisor/ledger/promotion.json")["experiment"] == exp_c
        s.go(cards["F"])
        s.wait_until(
            "F to finish", lambda: s.show(task["F"])["task"]["status"] == "done", PHASE_WAIT_S
        )
        s.set_env(AUTORESEARCH_PLAYER_ID=PLAYER)

        # 5b: crash between merge and publication, then exactly one publication.
        before = outbox(canonical)
        s.keeper(faults="crash_after=merge", expect_exit=70)
        handoffs = committed_dir(canonical, ".supervisor/ledger/handoffs")
        crashed = [(p, h) for p, h in handoffs.items() if h["state"] == "merged"]
        assert len(crashed) == 1, [(p, h["state"]) for p, h in handoffs.items()]
        crashed_path, crashed_handoff = crashed[0]
        staged = gitops.git(canonical, "diff", "--name-only", crashed_handoff["base_sha"],
                            crashed_handoff["result_sha"], "--", "comms/staged/").split()  # fmt: skip
        staged_ids = {Path(p).stem for p in staged}
        assert staged_ids, crashed_handoff
        assert outbox(canonical) == before
        assert not any(m["id"] in staged_ids for m in before)

        summary = s.keeper()
        assert summary["failures"] == {} and not summary["paused"], summary
        assert committed(canonical, crashed_path)["state"] == "published"
        s.keeper()
        messages = outbox(canonical)
        assert [m["seq"] for m in messages] == list(range(1, len(messages) + 1))
        for message_id in staged_ids:
            assert [m["id"] for m in messages].count(message_id) == 1, message_id
        assert committed(canonical, ".supervisor/ledger/pause.json") is None  # resumed
        assert s.show(task["P"])["task"]["assignee"] == s.board
        assert gitops.git(install.remote, "rev-parse", "main") == gitops.head(canonical)
        report["crashed_handoff"] = crashed_path
    finally:
        s.stop_gateway()
        report["wall_s"] = round(time.monotonic() - started)
        try:
            report["openrouter_usd"] = round(openrouter_usage(key) - usage_before, 4)
        except Exception as error:  # noqa: BLE001 - reporting only
            report["openrouter_usd"] = f"unavailable: {error}"
        try:
            report |= s.usage()
        except sqlite3.Error as error:
            report["usage"] = f"unavailable: {error}"
        print("\nSLICE REPORT " + json.dumps(report, sort_keys=True), flush=True)
