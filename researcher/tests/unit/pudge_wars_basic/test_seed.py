"""Validate the Pudge Wars Basic seed: records, currency, starter identity and source citations."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from ide_research_core.io.docs import read_doc
from ide_research_core.kb.lint import analyze
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore

GAME = Path(__file__).resolve().parents[3] / "games/pudge-wars-basic"
SEED = GAME / "seed"
ENGINE = "cd8111a5ee4f2fdc38c027615b33421f6c7d2536"
REPO = "Metta-AI/polyworld-pudge-wars"
GAME_VERSION = "0.1.8"
# The package's `base` player file (manifest player-files/<sha256>), shipped as the starter.
STARTER_SHA = "f38ba2929c91f985d73afa5a54ec3e5da582e5e71122069cbc2847b19fc48511"


def check_citation(ref, sources):
    match = re.fullmatch(r"(.+):(\d+)-(\d+)@([0-9a-f]{40})", ref)
    assert match, ref
    path, start, end, commit = match.groups()
    assert int(start) <= int(end), ref
    entry = sources[ref]
    assert entry["path"] == path and entry["commit"] == commit
    assert entry["start"] == int(start) and entry["end"] == int(end)
    assert len(entry["text"].splitlines()) == int(end) - int(start) + 1
    assert hashlib.sha256(entry["text"].encode()).hexdigest() == entry["sha256"]
    assert entry["repo"] == REPO


def test_seed_records_and_kb_are_valid():
    count, problems = RecordStore(Layout(SEED)).validate_all()
    assert count >= 20
    assert problems == {}
    assert analyze(Layout(SEED)).findings == []


def test_claims_are_bounded_and_have_resolvable_immutable_evidence():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    claims = sorted((SEED / "kb/claims").glob("*.md"))
    assert 8 <= len(claims) <= 15
    for path in claims:
        meta, body = read_doc(path)
        assert body.strip(), path
        assert meta["evidence"], path
        assert meta["last_checked_version"] == GAME_VERSION
        for evidence in meta["evidence"]:
            ref = evidence["ref"]
            if evidence["tier"] == "S":
                assert meta["kind"] == "mechanism" and meta["status"] == "verified"
                check_citation(ref, sources)
                assert sources[ref]["commit"] == ENGINE
            else:
                assert evidence["tier"] == "D" and meta["status"] == "belief"
                record = RecordStore(Layout(SEED)).load(ref.removeprefix("ar://evidence/"))[0]
                assert hashlib.sha256((SEED / record["path"]).read_bytes()).hexdigest() == record["sha256"]


def test_lessons_are_tentative_and_trace_to_pinned_upstream_scars():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    lessons = list((SEED / "lessons").glob("L-*.md"))
    assert len(lessons) == 6
    for path in lessons:
        meta, body = read_doc(path)
        assert meta["status"] == "tentative"
        assert meta["recurrences"] == {"count": 0, "refs": []}
        assert "transfer" in body.lower()
        assert meta["scar"]
        for ref in meta["scar"]:
            check_citation(ref, sources)
            assert sources[ref]["commit"] == ENGINE


def test_seed_currency_and_starter_identity():
    for path in (SEED / "kb/hubs").glob("*.md"):
        assert read_doc(path)[0]["game_version"] == GAME_VERSION
    assert {read_doc(path)[0]["title"] for path in (SEED / "kb/hubs").glob("*.md")} == {
        "mechanics",
        "league-meta",
        "our-policy",
    }
    policy = (SEED / "strategy/policy.bas").read_bytes()
    assert hashlib.sha256(policy).hexdigest() == STARTER_SHA
    assert STARTER_SHA in (SEED / "strategy/STRATEGY.md").read_text()
    assert sorted(path.name for path in (SEED / "stats").iterdir()) == [".gitkeep"]
    assert read_doc(SEED / "plan.md")[0]["priorities"] == []


def test_seed_starter_matches_shipped_baseline_when_present():
    baseline = GAME / "baselines/base.bas"
    if not baseline.exists():
        pytest.skip("baselines/base.bas is not shipped yet")
    assert baseline.read_bytes() == (SEED / "strategy/policy.bas").read_bytes()


def test_citations_match_upstream_source_when_available():
    """Optional: re-read every excerpt from a local clone named by PUDGE_WARS_SOURCE."""
    import os

    root = os.environ.get("PUDGE_WARS_SOURCE")
    if not root:
        pytest.skip("set PUDGE_WARS_SOURCE to a polyworld-pudge-wars clone at the pinned commit")
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    for entry in sources.values():
        data = (Path(root) / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["file_sha256"], entry["path"]
        lines = data.decode().splitlines(keepends=True)[entry["start"] - 1 : entry["end"]]
        assert "".join(lines).rstrip("\n") == entry["text"].rstrip("\n")


@pytest.mark.parametrize("ref", ["x:1-2@short", "x:4-2@" + ENGINE])
def test_citation_rejects_invalid_identity_or_range(ref):
    with pytest.raises(AssertionError):
        check_citation(ref, {})


def test_citation_rejects_tampered_source_excerpt():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    ref = next(iter(sources))
    sources[ref]["text"] += "tampered"
    with pytest.raises(AssertionError):
        check_citation(ref, sources)


def test_citation_rejects_foreign_repository():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    ref = next(iter(sources))
    sources[ref]["repo"] = "Metta-AI/polyworld"
    with pytest.raises(AssertionError):
        check_citation(ref, sources)
