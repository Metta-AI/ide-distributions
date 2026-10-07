import datetime
import json

import pytest

from autoresearch.io import gitops
from autoresearch.io.docs import read_doc
from autoresearch.kb.staleness import (
    code_citations,
    mark_stale,
    renew_code_citations,
    version_older,
)
from autoresearch.records.store import RecordStore

from .conftest import C_ARCH, C_CONTRA, C_GOOD, C_GRADE, C_ORPHAN, C_REFUTED, C_VERIF

NOW = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.UTC)


def log_entries(layout):
    return [read_doc(path)[0] for path in sorted(layout.kb_log.glob("*.md"))]


@pytest.mark.parametrize(
    ("a", "b", "older"),
    [
        ("65", "66", True),
        ("9", "10", True),  # numeric, not string, comparison
        ("1.9.2", "1.10.0", True),
        ("v66", "v65", False),
        ("66", "66", False),
        ("1.2", "1.2.1", True),
        ("beta", "gamma", True),  # no digits: any difference counts as older
        ("beta", "beta", False),
    ],
)
def test_version_older(a, b, older):
    assert version_older(a, b) is older


def test_mark_stale_marks_live_claims_once(kb_layout):
    marked = mark_stale(kb_layout, "66", NOW)
    # C_REFUTED is refuted; C_ARCH is archived.
    assert marked == sorted([C_GOOD, C_GRADE, C_VERIF, C_CONTRA, C_ORPHAN])
    store = RecordStore(kb_layout)
    meta, _ = store.load(C_GOOD)
    assert meta["status"] == "stale"
    assert "65" in meta["status_reason"] and "66" in meta["status_reason"]
    assert meta["status_history"][-1]["from"] == "verified"
    assert meta["status_history"][-1]["at"] == NOW.isoformat()
    assert store.load(C_REFUTED)[0]["status"] == "refuted"
    assert store.load(C_ARCH)[0]["status_reason"] == "game version 64"
    assert len(log_entries(kb_layout)) == 5
    assert mark_stale(kb_layout, "66", NOW) == []
    assert len(log_entries(kb_layout)) == 5


def test_mark_stale_same_version_is_noop(kb_layout):
    assert mark_stale(kb_layout, "65", NOW) == []


# --- code-citation renewal ---


def test_code_citations_formats():
    meta = {
        "evidence": [
            {
                "ref": "src/combat.py:10-20@abc123",
                "tier": "S",
                "direct": True,
                "stance": "supports",
            },
            {"ref": "src:src/tower.py#L5-L9", "tier": "S", "direct": True, "stance": "supports"},
            {"ref": "src/one.py:7@abc", "tier": "S", "direct": True, "stance": "supports"},
            {"ref": "docs/armor.md", "tier": "A", "direct": True, "stance": "supports"},
            {"ref": "src/x.py:1-2@abc", "tier": "B", "direct": True, "stance": "supports"},
        ]
    }
    assert code_citations(meta) == [
        ("src/combat.py", 10, 20),
        ("src/tower.py", 5, 9),
        ("src/one.py", 7, 7),
    ]


def _lines(n, changed=None):
    return "".join(f"line {i}{' CHANGED' if i == changed else ''}\n" for i in range(1, n + 1))


@pytest.fixture
def game_repo(tmp_path):
    repo = tmp_path / "game"
    (repo / "src").mkdir(parents=True)
    gitops.git(repo, "init", "-q")
    gitops.git(repo, "config", "user.email", "t@t")
    gitops.git(repo, "config", "user.name", "t")
    (repo / "src" / "combat.py").write_text(_lines(30))
    (repo / "src" / "tower.py").write_text(_lines(30))
    old = gitops.commit_all(repo, "v65")
    return repo, old


def _commit(repo, combat=None, tower=None):
    if combat:
        (repo / "src" / "combat.py").write_text(_lines(30, changed=combat))
    if tower:
        (repo / "src" / "tower.py").write_text(_lines(30, changed=tower))
    return gitops.commit_all(repo, "v66")


def _requests(layout):
    return [json.loads(p.read_text()) for p in sorted(layout.card_requests.glob("*.json"))]


def test_unchanged_lines_renew_and_unstale(kb_layout, game_repo):
    repo, old = game_repo
    new = _commit(repo, combat=25, tower=1)  # outside 10-20 and 5-9
    mark_stale(kb_layout, "66", NOW)
    report = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert report.renewed == sorted([C_GOOD, C_VERIF]) and report.requested == []
    store = RecordStore(kb_layout)
    for claim_id in (C_GOOD, C_VERIF):
        assert store.load(claim_id)[0]["last_checked_version"] == "66"
    mechanism, _ = store.load(C_GOOD)
    assert mechanism["status"] == "verified"
    assert mechanism["status_history"][-1]["from"] == "stale"
    # Behaviour claims keep stale: unchanged source does not show unchanged behaviour.
    assert store.load(C_VERIF)[0]["status"] == "stale"
    assert store.load(C_GRADE)[0]["status"] == "stale"  # no code citation
    assert _requests(kb_layout) == []
    again = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert again.renewed == [] and again.requested == []


def test_changed_lines_create_reverification_request(kb_layout, game_repo):
    repo, old = game_repo
    new = _commit(repo, tower=7)  # inside src/tower.py:5-9 (C_VERIF)
    report = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert report.renewed == [C_GOOD]
    assert report.requested == [C_VERIF]
    (request,) = _requests(kb_layout)
    spec = request["spec"]
    assert request["op"] == "create"
    assert spec["priority"] == "P2" and spec["mode"] == "maintenance"
    assert spec["refs"] == [f"ar://claim/{C_VERIF}"]
    store = RecordStore(kb_layout)
    assert store.load(C_VERIF)[0]["last_checked_version"] == "65"


def test_insertion_inside_range_counts_as_change(kb_layout, game_repo):
    repo, old = game_repo
    text = _lines(30).splitlines(keepends=True)
    text.insert(7, "inserted\n")  # between lines 7 and 8 of tower.py
    (repo / "src" / "tower.py").write_text("".join(text))
    new = gitops.commit_all(repo, "insert")
    report = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert report.requested == [C_VERIF]


def test_deleted_file_counts_as_change(kb_layout, game_repo):
    repo, old = game_repo
    (repo / "src" / "tower.py").unlink()
    new = gitops.commit_all(repo, "delete")
    report = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert report.requested == [C_VERIF]


def test_rerun_does_not_duplicate_requests(kb_layout, game_repo):
    repo, old = game_repo
    new = _commit(repo, tower=7)
    renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    again = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert again.requested == []
    (request,) = _requests(kb_layout)
    assert f"[reverify {old}..{new}]" in request["spec"]["why"]


def test_file_absent_at_old_sha_counts_as_change(kb_layout, game_repo):
    repo, _ = game_repo
    (repo / "src" / "tower.py").unlink()
    old = gitops.commit_all(repo, "no tower")
    (repo / "src" / "tower.py").write_text(_lines(30))
    new = gitops.commit_all(repo, "tower back")
    report = renew_code_citations(kb_layout, repo, old, new, current_version="66", now=NOW)
    assert report.requested == [C_VERIF] and report.renewed == [C_GOOD]
