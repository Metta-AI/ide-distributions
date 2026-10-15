"""Full research cycle on the fake game (Plan 03 Task 6.0, marker ``hermes``).

The agent runs from an empty plan with the real doctrine and skills: no scripted card
bodies. It installs the distribution into a throwaway host root (``conftest.install``), runs
real card workers (``z-ai/glm-5.3`` through OpenRouter; it spends credit and FAILS without
``OPENROUTER_API_KEY``) dispatched by the real gateway, and lets the three shipped cron jobs
(queue-keeper, xp-poll, health) drive the runtime.

The one human input is a ``steer`` inbound (with a ``proposed_topic``) submitted before the
first run, as an operator would: it names the problem (errors) and points at the game's
documentation. Everything else (Topic, investigation, hypothesis, calibration, experiment
design, build, upload, XP launch, review, decision, promotion) is the agent's and the
runtime's work.

Within ``CYCLE_WAIT_S`` (90 min) the test asserts, from committed canonical state and the
fake platform:

- calibration wrote a noise record for the current game version;
- planning created a Topic and card requests (a planning request published);
- an investigation produced a hypothesis (an investigation request published);
- an experiment froze its evaluation design (``stats design``), was built with the fakegame
  build binding, uploaded to the fake platform, and launched paired per-seed XP (A12: one
  one-episode request per (seed, arm), both arms on every design seed);
- xp-poll requested the review; the review wrote a Result and a decision;
- on ``promote``: the promotion card submitted once and the promotion reached ``published``.

Credit fault: the fake platform starts with ``LOW_CREDITS`` credits, so the balance hits 0 in
the middle of the first XP launch. The experiment must go ``awaiting-credits``; the test then
restores the balance, and xp-poll's relaunch card must create only the missing requests (no
duplicate (seed, arm) on the fake platform).

Test shims (environment only; the deliverable is the profile repo): the host root
``config.yaml`` (the installer's job, A3), a fake credential that names its player, the
champion already on the fake leaderboard (built from the seed STRATEGY and submitted, as an
operator's first submission would be), the low credit balance and its later top-up, and
``AUTORESEARCH_CREDIT_BACKOFF_MINUTES=2`` so the relaunch fits in the time budget.

Run it with (see ``docs/plans/findings/fake-cycle.md``)::

    export OPENROUTER_API_KEY=...
    uv run pytest -m hermes tests/integration/test_fake_cycle.py -v -s

``FAKE_CYCLE_MAX_USD`` (default 6) stops the run when the key's usage grew by more.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_protocol_slice import Slice, committed, committed_dir, openrouter_usage

from autoresearch.comms.inbound import submit_inbound
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.io.locks import file_lock
from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore
from autoresearch.runtime.preflight import env_file_value
from autoresearch.runtime.promotion import CHAMPION_PATH

pytestmark = pytest.mark.hermes

MODEL = "z-ai/glm-5.3"
PLAYER = "ply_fake"
LEAGUE = "fake-league"  # games/fakegame league_id
CREDENTIAL = json.dumps(
    {"token": "ply_fake_token", "expires_at": "2099-01-01T00:00:00Z", "player_id": PLAYER}
)
CYCLE_WAIT_S = 90 * 60
POLL_S = 15
USAGE_EVERY_S = 120
LOW_CREDITS = 6  # three seed pairs, then the balance is 0 mid-launch
TOPPED_UP_CREDITS = 10_000
STEER_TEXT = (
    "Our policy makes too many errors per episode. GAME.md (section Mechanics) is the "
    "official documentation of this game: higher patience lowers errors and does not change "
    "score. Please take this through one full research cycle: make it a Topic, investigate it "
    "and write the hypothesis (the documentation is tier A evidence; no replay is needed for "
    "this documented mechanic), then design and run one experiment with a clear, large "
    "effect, review it, and promote the candidate if the pre-registered rule allows it."
)
PROPOSED_TOPIC = {
    "title": "Reduce errors",
    "objective": "Lower errors per episode without losing score.",
    "metrics": [{"name": "errors", "direction": "down"}],
    "priority": "P1",
}


# --- fake platform shims ------------------------------------------------------------------


def fake_state_path(canonical: Path) -> Path:
    return canonical / ".supervisor" / "runtime" / "fake_softmax.json"


def set_fake_credits(canonical: Path, credits: int) -> None:
    """Set the fake platform's balance (the shared state file, under its lock)."""
    runtime = canonical / ".supervisor" / "runtime"
    with file_lock(runtime / "fake_softmax.lock"):
        state = read_json(fake_state_path(canonical))
        state["credits"] = credits
        write_json(fake_state_path(canonical), state)


def fake_xp(canonical: Path) -> dict:
    return read_json(fake_state_path(canonical))["xp"]


def seed_champion(s: Slice) -> str:
    """Build the seed STRATEGY with the fakegame build binding, upload it and submit it: the
    leaderboard champion before any promotion (A9)."""
    build_dir = s.install.root / "champion-build"
    subprocess.run(
        [sys.executable, str(s.home / "games" / "fakegame" / "tools" / "fake_build.py"),
         str(s.canonical / CHAMPION_PATH), "--out", str(build_dir), "--json"],
        check=True, capture_output=True,
    )  # fmt: skip
    fake = s.fake()
    policy = fake.upload(build_dir / "policy.json", "autoresearch-champion")
    fake.submit(policy, LEAGUE)
    return policy


# --- canonical readers --------------------------------------------------------------------


def records(canonical: Path, entity: str) -> list[dict]:
    store = RecordStore(Layout(canonical))
    return [store.load(record_id)[0] for record_id in store.find(entity)]


def receipts(canonical: Path) -> list[dict]:
    return list(committed_dir(canonical, ".supervisor/ledger/requests").values())


def published_modes(canonical: Path) -> set[str]:
    return {r.get("mode") for r in receipts(canonical) if r["state"] == "published"}


def review_requested(canonical: Path, experiment: str) -> bool:
    for request in committed_dir(canonical, "cards/requests").values():
        if f"[review:{experiment}:" in ((request.get("spec") or {}).get("why") or ""):
            return True
    return False


class Cycle:
    """Stage tracking: each stage records the seconds since the gateway started."""

    def __init__(self, s: Slice, report: dict):
        self.s = s
        self.canonical = s.canonical
        self.report = report
        self.stages: dict[str, int] = report.setdefault("stages", {})
        self.started = time.monotonic()
        self.experiment: dict | None = None
        self.credits_restored = False

    def reach(self, stage: str) -> None:
        if stage not in self.stages:
            self.stages[stage] = round(time.monotonic() - self.started)
            print(f"[fake-cycle] {self.stages[stage]:>5}s {stage}", flush=True)

    def observe(self) -> bool:
        """Update stages from committed state; True when the cycle is over."""
        canonical = self.canonical
        if committed(canonical, ".supervisor/ledger/promotion.json") is not None:
            self.reach("promotion_acquired")
        noise = canonical / "stats" / "metric-noise.yaml"
        if noise.exists() and yaml.safe_load(noise.read_text()).get("game_version") == "fake-1":
            self.reach("noise_record")
        modes = published_modes(canonical)
        if records(canonical, "topic") and "planning" in modes:
            self.reach("planning_topic_and_cards")
        if records(canonical, "hypothesis") and "investigation" in modes:
            self.reach("investigation_hypothesis")
        experiments = [e for e in records(canonical, "experiment") if e.get("evaluation_design")]
        if not experiments:
            return False
        self.experiment = experiment = experiments[0]
        self.reach("design_frozen")
        if experiment.get("candidate_binding"):
            self.reach("built_and_uploaded")
        if experiment.get("xp_requests"):
            self.reach("xp_launched")
        if experiment["status"] == "awaiting-credits":
            self.reach("awaiting_credits")
            if not self.credits_restored:
                set_fake_credits(canonical, TOPPED_UP_CREDITS)
                self.credits_restored = True
                self.reach("credits_restored")
        if self.credits_restored and experiment["status"] == "evaluating":
            self.reach("relaunched")
        if review_requested(canonical, experiment["id"]):
            self.reach("review_requested")
        decision = experiment.get("decision") or {}
        if experiment["status"] == "reviewed" and decision.get("outcome"):
            self.reach(f"reviewed_{decision['outcome']}")
            if decision["outcome"] != "promote":
                return True
        promotion = committed(canonical, ".supervisor/ledger/promotion.json")
        if promotion is not None and promotion["stage"] == "published":
            self.reach("promotion_published")
            return True
        return False


def test_fake_cycle(install):
    key = env_file_value(install.profile_dir / ".env", "OPENROUTER_API_KEY")
    if not os.environ.get("OPENROUTER_API_KEY") or not key or key == "sk-or-fake":
        pytest.fail("the fake cycle needs OPENROUTER_API_KEY (fail, not skip)")
    max_usd = float(os.environ.get("FAKE_CYCLE_MAX_USD", "6"))
    usage_before = openrouter_usage(key)
    started = time.monotonic()
    s = Slice(install)
    canonical = s.canonical
    report: dict = {"model": MODEL}
    cycle = Cycle(s, report)
    try:
        # Install shims, bootstrap, the champion on the fake leaderboard, low credits.
        s.set_env(
            OPENROUTER_MODEL=MODEL,
            SOFTMAX_PLAYER_CREDENTIAL=f"'{CREDENTIAL}'",
            AUTORESEARCH_CREDIT_BACKOFF_MINUTES="2",
        )
        (install.hermes_home / "config.yaml").write_text(
            yaml.safe_dump({"kanban": {"max_in_progress": 2, "dispatch_interval_seconds": 10}})
        )
        s.keeper("--bootstrap-only")
        assert yaml.safe_load((s.home / "config.yaml").read_text())["model"]["default"] == MODEL
        report["champion"] = champion = seed_champion(s)
        set_fake_credits(canonical, LOW_CREDITS)

        # The one human input: a steer with a proposed Topic (routed to the planning backlog).
        payload = {"text": STEER_TEXT, "urgency": "next", "proposed_topic": PROPOSED_TOPIC}
        report["steer"] = submit_inbound(canonical, "steer", payload, author="operator")

        s.cron("resume")
        s.start_gateway()
        cycle.started = time.monotonic()
        last_usage_check = 0.0
        while True:
            s.check_gateway()
            try:
                if cycle.observe():
                    break
            except (OSError, ValueError, KeyError) as error:  # a file mid-write: poll again
                print(f"[fake-cycle] observe: {type(error).__name__}: {error}", flush=True)
            elapsed = time.monotonic() - cycle.started
            if elapsed > CYCLE_WAIT_S:
                raise AssertionError(f"cycle not done in {CYCLE_WAIT_S}s: {cycle.stages}")
            if elapsed - last_usage_check > USAGE_EVERY_S:
                last_usage_check = elapsed
                spent = openrouter_usage(key) - usage_before
                if spent > max_usd:
                    raise AssertionError(f"spent ${spent:.2f} > ${max_usd}: {cycle.stages}")
            time.sleep(POLL_S)
        report["cycle_s"] = round(time.monotonic() - cycle.started)

        # Assertions on the committed state.
        stages = cycle.stages
        for stage in ("noise_record", "planning_topic_and_cards", "investigation_hypothesis",
                      "design_frozen", "built_and_uploaded", "xp_launched", "awaiting_credits",
                      "relaunched", "review_requested"):  # fmt: skip
            assert stage in stages, (stage, stages)
        experiment = cycle.experiment
        design = experiment["evaluation_design"]
        report["experiment"] = experiment["id"]
        report["decision"] = experiment["decision"]
        report["n_seeds"] = len(design["seeds"])
        store = RecordStore(Layout(canonical))

        # Build and upload: a version with build provenance, bound to the fake platform id.
        binding = experiment["candidate_binding"]
        version = store.load(binding["version_id"])[0]
        assert version.get("strategy_sha256"), version
        uploads = read_json(fake_state_path(canonical))["uploads"]
        assert "parameters" in uploads[binding["platform_policy_version_id"]], uploads

        # Paired per-seed XP (A12), no duplicate (seed, arm) after the credit relaunch.
        ours = [r for r in fake_xp(canonical).values() if r["experiment"] == experiment["id"]]
        pairs = [(r["seed"], r["arm"]) for r in ours]
        assert len(pairs) == len(set(pairs)), sorted(pairs)
        assert set(pairs) == {(seed, arm) for seed in design["seeds"]
                              for arm in ("candidate", "baseline")}  # fmt: skip
        assert all(r["episodes"] == 1 and r["game_seed"] == r["seed"] for r in ours)
        policies = {r["arm"]: r["policy"] for r in ours}
        assert policies == {"candidate": binding["platform_policy_version_id"],
                            "baseline": champion}  # fmt: skip
        recorded = [(r["seed"], r["arm"]) for r in experiment["xp_requests"]]
        assert sorted(recorded) == sorted(pairs)

        # Review: a Result linked to the experiment, and the decision with its reason.
        results = [r for r in records(canonical, "result") if r["experiment"] == experiment["id"]]
        assert results, "no Result for the experiment"
        assert experiment["decision"]["outcome"] in ("promote", "hold", "refute", "invalid")
        assert experiment["decision"].get("reason")

        # Promotion, when the decision is promote.
        if experiment["decision"]["outcome"] != "promote":
            report["promotion"] = "not exercised (decision is not promote)"
            pytest.fail(f"cycle ended without promote: {experiment['decision']}")
        promotion = committed(canonical, ".supervisor/ledger/promotion.json")
        assert promotion["stage"] == "published", promotion
        assert promotion["experiment"] == experiment["id"]
        submissions = [x["policy_version_id"] for x in s.fake().submissions(PLAYER)]
        assert submissions == [champion, binding["platform_policy_version_id"]], submissions
        candidate = f"strategy/candidates/{experiment['id']}/STRATEGY.md"
        assert gitops.show(canonical, "HEAD", CHAMPION_PATH) == gitops.show(
            canonical, promotion["candidate_sha"], candidate
        )
    finally:
        s.stop_gateway()
        report["wall_s"] = round(time.monotonic() - started)
        try:
            report["openrouter_usd"] = round(openrouter_usage(key) - usage_before, 4)
        except Exception as error:  # noqa: BLE001 - reporting only
            report["openrouter_usd"] = f"unavailable: {error}"
        try:
            report |= s.usage()
        except Exception as error:  # noqa: BLE001 - reporting only
            report["usage"] = f"unavailable: {error}"
        try:
            report["cards"] = [{"title": t["title"], "status": t["status"]} for t in s.tasks()]
        except Exception as error:  # noqa: BLE001 - reporting only
            report["cards"] = f"unavailable: {error}"
        print("\nFAKE CYCLE REPORT " + json.dumps(report, sort_keys=True), flush=True)
