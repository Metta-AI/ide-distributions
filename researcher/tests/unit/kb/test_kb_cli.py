import json
import subprocess
import sys

from .conftest import C_GOOD, C_GRADE, C_VERIF, HUB, kb_id
from .test_staleness import _commit, game_repo  # noqa: F401  (pytest fixture)

# kb_small claims C-1..C-5 are live and checked at version 65.


def run_kb(root, *args):
    result = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "kb", *args, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, json.loads(result.stdout)


def test_lint_reports_findings(kb_layout):
    code, out = run_kb(kb_layout.root, "lint")
    assert code == 0 and out["ok"]
    assert out["data"]["count"] == len(out["data"]["findings"]) > 0
    assert {"code", "entity", "message"} <= set(out["data"]["findings"][0])


def test_apply_fixes_grade_and_logs(kb_layout):
    code, out = run_kb(kb_layout.root, "apply")
    assert code == 0 and out["ok"]
    assert any(path.startswith("kb/log/") for path in out["data"]["written"])
    _, after = run_kb(kb_layout.root, "lint")
    assert C_GRADE not in [
        f["entity"] for f in after["data"]["findings"] if f["code"] == "STATUS_GRADE_MISMATCH"
    ]


def test_index_writes_views(kb_layout):
    code, out = run_kb(kb_layout.root, "index")
    assert code == 0
    assert "kb/index.md" in out["data"]["written"]
    assert (kb_layout.root / "kb" / "index.md").exists()


def test_stale_marks_older_claims(kb_layout):
    code, out = run_kb(kb_layout.root, "stale", "--version", "66")
    assert code == 0
    assert out["data"]["marked"] == [kb_id("C", n) for n in range(1, 6)]


def test_compact_runs(kb_layout):
    code, out = run_kb(kb_layout.root, "compact")
    assert code == 0 and out["data"] == {"archived": []}


def test_hubs_reports_findings(kb_layout):
    code, out = run_kb(kb_layout.root, "hubs", "--size-limit-tokens", "10")
    assert code == 0
    assert [f["entity"] for f in out["data"]["findings"]] == [HUB]


def test_renew_with_unreadable_repo_is_a_business_failure(kb_layout, tmp_path):
    code, out = run_kb(
        kb_layout.root,
        "renew",
        "--game-repo",
        str(tmp_path / "missing"),
        "--old",
        "a",
        "--new",
        "b",
        "--version",
        "66",
    )
    assert code == 1 and not out["ok"]


def test_bad_arguments_exit_2(kb_layout):
    result = subprocess.run(
        [sys.executable, "-m", "ide_research_core", "kb", "stale", "--root", str(kb_layout.root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_renew_via_cli(kb_layout, game_repo):  # noqa: F811
    repo, old = game_repo
    new = _commit(repo, tower=7)  # inside C_VERIF's cited lines, outside C_GOOD's
    code, out = run_kb(
        kb_layout.root,
        "renew",
        "--game-repo",
        str(repo),
        "--old",
        old,
        "--new",
        new,
        "--version",
        "66",
    )
    assert code == 0
    assert out["data"] == {"renewed": [C_GOOD], "reverify": [C_VERIF]}
