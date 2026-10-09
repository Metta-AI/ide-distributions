"""Validate seed records, currency, and immutable source citations."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from autoresearch.io.docs import read_doc
from autoresearch.kb.lint import analyze
from autoresearch.paths import Layout
from autoresearch.records.store import RecordStore

SEED = Path(__file__).resolve().parents[3] / "games/gota/seed"
ENGINE = "02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a"
STRATEGY_SHA = "857a1e84af3b116678052b63a0a607b6699b50a3a154bbea2816b93352696dd8"


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
    assert entry["repo"] in {"Metta-AI/polyworld", "Metta-AI/co-gas"}


def test_seed_records_and_kb_are_valid():
    count, problems = RecordStore(Layout(SEED)).validate_all()
    assert count >= 10
    assert problems == {}
    assert analyze(Layout(SEED)).findings == []


def test_every_claim_has_resolvable_immutable_evidence():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    for path in sorted((SEED / "kb/claims").glob("*.md")):
        meta, _ = read_doc(path)
        assert meta["evidence"], path
        for evidence in meta["evidence"]:
            ref = evidence["ref"]
            if evidence["tier"] == "S":
                check_citation(ref, sources)
                assert sources[ref]["commit"] == ENGINE
            else:
                assert evidence["tier"] == "D"
                record = RecordStore(Layout(SEED)).load(ref.removeprefix("ar://evidence/"))[0]
                assert hashlib.sha256((SEED / record["path"]).read_bytes()).hexdigest() == record["sha256"]


def test_lessons_are_tentative_and_trace_to_pinned_red_flags():
    sources = json.loads((SEED / "evidence/source-citations.json").read_text())
    lessons = list((SEED / "lessons").glob("L-*.md"))
    assert len(lessons) == 6
    for path in lessons:
        meta, body = read_doc(path)
        assert meta["status"] == "tentative"
        assert "transfer" in body.lower()
        for ref in meta["scar"]:
            check_citation(ref, sources)
            assert sources[ref]["commit"] == "6742feb08f8b119591d09261cadc581065fa698b"


def test_seed_currency_and_preserved_baseline():
    for path in (SEED / "kb/hubs").glob("*.md"):
        assert read_doc(path)[0]["game_version"] == "2026.10.8.1"
    assert hashlib.sha256((SEED / "strategy/STRATEGY.md").read_bytes()).hexdigest() == STRATEGY_SHA
    assert sorted(path.name for path in (SEED / "stats").iterdir()) == [".gitkeep"]
    assert read_doc(SEED / "plan.md")[0]["priorities"] == []


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
