"""``autoresearch stats ...`` through the real entry point (subprocess)."""

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict

import pytest
import yaml
from records.fixtures import EXP_DESIGNED

from ide_research_core.cli import main
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.stats import cli as stats_cli
from ide_research_core.stats.design import EvaluationDesign, MetricSpec
from ide_research_core.stats.ingest import EpisodeRow

BASE, CAND = "pv-base", "pv-cand"


def stats(root, *argv):
    proc = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "stats", *argv, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip() else proc.stderr


def write_rows(path, rows):
    path.write_text("".join(json.dumps(asdict(row)) + "\n" for row in rows), encoding="utf-8")
    return path


def league_rows():
    rows = []
    for seed in range(12):
        for policy, shift in (("pv-a", 0.0), ("pv-b", 1.0)):
            metrics = {"score": seed + shift, "errors": float(seed % 3)}
            rows.append(
                EpisodeRow(
                    f"{policy}-{seed}", "65", policy, seed, (0,), ("o",), metrics, "completed"
                )
            )
    rows.append(EpisodeRow("bad", "65", "pv-a", 99, (0,), ("o",), {"score": 1.0}, "completed"))
    rows.append(EpisodeRow("pv-a-0", "65", "pv-a", 0, (0,), ("o",), {}, "completed"))
    return rows


def test_ingest_league_then_noise(tmp_path):
    rows_file = write_rows(tmp_path / "rows.jsonl", league_rows())
    root = tmp_path / "ws"
    code, env = stats(
        root,
        "ingest",
        "--rows",
        str(rows_file),
        "--league",
        "L1",
        "--game-version",
        "65",
        "--game",
        "fakegame",
    )
    assert code == 0, env
    assert env["data"]["report"]["kept"] == 24
    assert env["data"]["report"]["duplicates"] == 1
    assert env["data"]["report"]["missing_metrics"] == ["errors"]
    stored = (root / "stats" / "rows" / "L1.jsonl").read_text().splitlines()
    assert len(stored) == 24

    code, env = stats(
        root,
        "noise",
        "--league",
        "L1",
        "--game-version",
        "65",
        "--metrics",
        "score,errors",
        "--evidence",
        "EV-1",
    )
    assert code == 0, env
    assert len(env["data"]["noise_rev"]) == 40
    record = yaml.safe_load((root / "stats" / "metric-noise.yaml").read_text())
    assert set(record["metrics"]) == {"score", "errors"} and record["evidence"] == ["EV-1"]


def test_ingest_league_requires_game_version_and_game(tmp_path):
    rows_file = write_rows(tmp_path / "rows.jsonl", league_rows())
    code, env = stats(
        tmp_path, "ingest", "--rows", str(rows_file), "--league", "L1", "--game", "fakegame"
    )
    assert code == 2 and env["ok"] is False


def test_noise_without_rows_is_failure(tmp_path):
    code, env = stats(
        tmp_path, "noise", "--league", "L9", "--game-version", "65", "--metrics", "score"
    )
    assert code == 1 and "L9" in env["error"]["message"]


def design_dict():
    metrics = (
        MetricSpec("speed", "up", "primary", 1.0, None),
        MetricSpec("errors", "down", "agent_guardrail", None, 0.5),
        MetricSpec("score", "up", "performance", None, None),
    )
    design = EvaluationDesign(
        "65", BASE, (0,), ("o",), tuple(range(20)), metrics, 0.5, {"score": 0.5}, 10, "rev1"
    )
    return design.to_dict()


@pytest.fixture
def experiment(tmp_path):
    store = RecordStore(Layout(tmp_path / "ws"))
    exp = store.create("experiment", {**EXP_DESIGNED, "evaluation_design": design_dict()})
    store.set_status(exp, "built")
    store.set_status(exp, "uploaded")
    store.update(
        exp, {"candidate_binding": {"version_id": "V-1", "platform_policy_version_id": CAND}}
    )
    return exp


def experiment_rows():
    rows = []
    for seed in range(20):
        jitter = 0.01 if seed % 2 else -0.01
        for policy, gain in ((BASE, 0.0), (CAND, 2.0)):
            metrics = {"speed": 5.0 + gain + jitter, "errors": 1.0, "score": 3.0 + gain / 4}
            rows.append(
                EpisodeRow(
                    f"{policy}-{seed}", "65", policy, seed, (0,), ("o",), metrics, "completed"
                )
            )
    rows.append(
        EpisodeRow(
            "seatless",
            "65",
            CAND,
            1,
            (1,),
            ("o",),
            {"speed": 1.0, "errors": 1.0, "score": 1.0},
            "completed",
        )
    )
    return rows


def test_ingest_experiment_then_evaluate(tmp_path, experiment):
    root = tmp_path / "ws"
    rows_file = write_rows(tmp_path / "rows.jsonl", experiment_rows())
    code, env = stats(root, "ingest", "--rows", str(rows_file), "--experiment", experiment)
    assert code == 0, env
    assert env["data"]["report"]["kept"] == 40 and env["data"]["report"]["wrong_seats"] == 1
    assert (root / "stats" / "rows" / f"{experiment}.jsonl").exists()

    code, env = stats(root, "evaluate", experiment, "--champion", BASE)
    assert code == 0, env
    assert env["data"]["promote_eligible"] is True and env["data"]["failures"] == []
    stored = json.loads((root / "stats" / "outcomes" / f"{experiment}.json").read_text())
    assert stored["promote_eligible"] is True and stored["champion"] == BASE
    assert stored["candidate_platform_id"] == CAND
    rows_bytes = (root / "stats" / "rows" / f"{experiment}.jsonl").read_bytes()
    assert stored["rows_sha256"] == hashlib.sha256(rows_bytes).hexdigest()
    canonical = json.dumps(design_dict(), sort_keys=True, separators=(",", ":"))
    assert stored["design_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()

    code, env = stats(root, "evaluate", experiment, "--champion", "pv-other")
    assert code == 0 and env["ok"] is True
    assert env["data"]["promote_eligible"] is False
    assert env["data"]["failures"] == ["baseline_not_champion"]


def test_evaluate_without_champion_outside_a_profile(tmp_path, experiment, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_GAME", raising=False)
    root = tmp_path / "ws"
    write_rows(tmp_path / "rows.jsonl", experiment_rows())
    stats(root, "ingest", "--rows", str(tmp_path / "rows.jsonl"), "--experiment", experiment)
    code, env = stats(root, "evaluate", experiment)
    assert code == 2
    assert env["error"]["message"] == "champion unknown: pass --champion"


def test_evaluate_without_rows_is_failure(tmp_path, experiment):
    code, env = stats(tmp_path / "ws", "evaluate", experiment, "--champion", BASE)
    assert code == 1 and "stats ingest" in env["error"]["message"]


def test_ingest_needs_exactly_one_target(tmp_path):
    rows_file = write_rows(tmp_path / "rows.jsonl", league_rows())
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ide_research_core",
            "stats",
            "ingest",
            "--rows",
            str(rows_file),
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2


def test_malformed_rows_file_is_bad_arguments(tmp_path):
    bad = tmp_path / "rows.jsonl"
    bad.write_text('{"episode_id": "x"}\n', encoding="utf-8")
    code, env = stats(
        tmp_path,
        "ingest",
        "--rows",
        str(bad),
        "--league",
        "L1",
        "--game-version",
        "65",
        "--game",
        "fakegame",
    )
    assert code == 2 and "line 1" in env["error"]["message"]


@pytest.mark.parametrize("league", ["../escape", "a/b", "..", ".hidden", ""])
def test_league_id_with_path_parts_is_bad_arguments(tmp_path, league):
    rows_file = write_rows(tmp_path / "rows.jsonl", league_rows())
    code, env = stats(
        tmp_path,
        "ingest",
        "--rows",
        str(rows_file),
        "--league",
        league,
        "--game-version",
        "65",
        "--game",
        "fakegame",
    )
    assert code == 2 and "league" in env["error"]["message"]
    code, env = stats(
        tmp_path, "noise", "--league", league, "--game-version", "65", "--metrics", "score"
    )
    assert code == 2 and "league" in env["error"]["message"]
    assert not (tmp_path / "stats").exists()


def test_non_numeric_metric_is_failure_envelope(tmp_path):
    rows = league_rows()
    rows[0].metrics["score"] = "lots"
    rows_file = write_rows(tmp_path / "rows.jsonl", rows)
    code, env = stats(
        tmp_path,
        "ingest",
        "--rows",
        str(rows_file),
        "--league",
        "L1",
        "--game-version",
        "65",
        "--game",
        "fakegame",
    )
    assert code == 1 and env["ok"] is False
    assert "score" in env["error"]["message"] and "line 1" in env["error"]["message"]


@pytest.fixture
def promotion(monkeypatch, tmp_path):
    """Run inside a profile whose league shows BASE as our champion (A9); returns the league
    ids ``current_champion`` was asked about."""
    asked = []

    def champion(ctx, sm, league_id):
        asked.append(league_id)
        return BASE

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AUTORESEARCH_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTORESEARCH_GAME", "fakegame")
    monkeypatch.setenv("AUTORESEARCH_SOFTMAX", "fake")
    monkeypatch.setattr(stats_cli, "current_champion", champion)
    return asked


def run_in_process(capsys, root, *argv):
    code = main(["stats", *argv, "--root", str(root)])
    return code, json.loads(capsys.readouterr().out)


def test_champion_from_promotion_and_mismatch(tmp_path, experiment, promotion, capsys):
    root = tmp_path / "ws"
    rows_file = write_rows(tmp_path / "rows.jsonl", experiment_rows())
    code, env = run_in_process(
        capsys, root, "ingest", "--rows", str(rows_file), "--experiment", experiment
    )
    assert code == 0, env
    code, env = run_in_process(capsys, root, "evaluate", experiment)
    assert code == 0 and env["data"]["champion"] == BASE and env["data"]["promote_eligible"]
    assert promotion == ["fake-league"]  # the game's league
    code, env = run_in_process(capsys, root, "evaluate", experiment, "--champion", BASE)
    assert code == 0
    code, env = run_in_process(capsys, root, "evaluate", experiment, "--champion", "pv-other")
    assert code == 1 and "champion mismatch" in env["error"]["message"]


def _ingested(tmp_path, capsys, experiment):
    root = tmp_path / "ws"
    rows_file = write_rows(tmp_path / "rows.jsonl", experiment_rows())
    code, env = run_in_process(
        capsys, root, "ingest", "--rows", str(rows_file), "--experiment", experiment
    )
    assert code == 0, env
    return root


def test_champion_override_needed_when_the_league_shows_none(
    tmp_path, experiment, promotion, capsys, monkeypatch
):
    root = _ingested(tmp_path, capsys, experiment)
    monkeypatch.setattr(stats_cli, "current_champion", lambda ctx, sm, league_id: None)
    code, env = run_in_process(capsys, root, "evaluate", experiment)
    assert code == 2 and "pass --champion" in env["error"]["message"]
    code, env = run_in_process(capsys, root, "evaluate", experiment, "--champion", BASE)
    assert code == 0 and env["data"]["champion"] == BASE


def test_softmax_failure_while_confirming_the_champion_is_exit_1(
    tmp_path, experiment, promotion, capsys, monkeypatch
):
    root = _ingested(tmp_path, capsys, experiment)

    def broken(ctx, sm, league_id):
        raise RuntimeError("whoami failed: token expired")

    monkeypatch.setattr(stats_cli, "current_champion", broken)
    code, env = run_in_process(capsys, root, "evaluate", experiment, "--champion", BASE)
    assert code == 1 and "cannot confirm the champion" in env["error"]["message"]


def test_relative_root_is_resolved_at_cli_entry(tmp_path, monkeypatch, capsys):
    """On the box, ``stats noise --root <relative>`` wrote the file, then crashed in
    noise_revision (the git path was doubled). The CLI makes ``--root`` absolute first."""
    rows_file = write_rows(tmp_path / "rows.jsonl", league_rows())
    monkeypatch.chdir(tmp_path)
    ingest = ["--league", "L1", "--game-version", "65"]
    assert main(["stats", "ingest", "--rows", str(rows_file), *ingest, "--game", "fakegame",
                 "--root", "ws"]) == 0
    capsys.readouterr()
    code = main(["stats", "noise", *ingest, "--metrics", "score", "--root", "ws"])
    env = json.loads(capsys.readouterr().out)
    assert code == 0, env
    assert len(env["data"]["noise_rev"]) == 40
