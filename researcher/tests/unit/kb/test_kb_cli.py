import datetime
import json
import subprocess
import sys

from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import write_request
from autoresearch.records.store import RecordStore

from .conftest import C_GOOD, C_GRADE, C_VERIF, HUB, kb_id
from .test_staleness import _commit, game_repo  # noqa: F401  (pytest fixture)

# kb_small claims C-1..C-5 are live and checked at version 65.


def run_kb(root, *args):
    result = subprocess.run(
        [sys.executable, "-m", "autoresearch", "kb", *args, "--root", str(root)],
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


def test_hubs_with_request(kb_layout):
    code, out = run_kb(kb_layout.root, "hubs", "--size-limit-tokens", "10", "--request")
    assert code == 0
    assert [f["entity"] for f in out["data"]["findings"]] == [HUB]
    assert len(out["data"]["requested"]) == 1
    _, again = run_kb(kb_layout.root, "hubs", "--size-limit-tokens", "10", "--request")
    assert again["data"]["requested"] == []


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
        [sys.executable, "-m", "autoresearch", "kb", "stale", "--root", str(kb_layout.root)],
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
    assert out["data"] == {"renewed": [C_GOOD], "requested": [C_VERIF]}


def test_compact_skips_claim_with_pending_reverification(tmp_path):
    store = RecordStore(Layout(tmp_path))
    long_ago = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400)

    def claim(stale):
        meta = {
            "kind": "mechanism",
            "status": "belief",
            "confidence": 0.5,
            "evidence": [],
            "revise_if": "x",
            "topics": [],
            "last_checked_version": "65",
        }
        if stale:
            at = long_ago.isoformat()
            meta |= {
                "status": "stale",
                "status_reason": "game version changed: 65 -> 66",
                "status_history": [{"from": "belief", "to": "stale", "reason": "r", "at": at}],
            }
        return store.create("claim", meta, "A claim.")

    guarded, unguarded = claim(True), claim(True)
    for _ in range(8):
        claim(False)
    spec = CardSpec(
        "",
        "Re-verify",
        "maintenance",
        "changed [reverify aaa..bbb].",
        [f"ar://claim/{guarded}"],
        "kb-staleness",
        "P2",
    )
    write_request(Layout(tmp_path), "create", spec=spec)
    code, out = run_kb(tmp_path, "compact")
    assert code == 0
    assert out["data"] == {"archived": [unguarded]}


def test_mutating_kb_commands_are_marked_runtime_only():
    import argparse

    from autoresearch.cli import build_parser
    from autoresearch.kb.cli import RUNTIME_ONLY

    parser = build_parser()
    group = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    kb = group.choices["kb"]
    commands = next(a for a in kb._actions if isinstance(a, argparse._SubParsersAction))
    helps = {action.dest: action.help for action in commands._choices_actions}
    for name in ("apply", "index", "stale", "renew", "compact"):
        assert helps[name].startswith(RUNTIME_ONLY), name
    for name in ("lint", "hubs"):
        assert RUNTIME_ONLY not in helps[name]
