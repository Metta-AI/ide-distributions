import pytest
from runtime.workspace import Canonical

from autoresearch.comms.resolver import ResolveError, resolve
from autoresearch.io import gitops
from autoresearch.io.docs import write_doc, write_json


@pytest.fixture
def canonical(tmp_path):
    handle = Canonical(tmp_path)
    handle.seed()
    return handle


def test_resolves_topic_at_head(canonical):
    resolved = resolve(canonical.root, f"ar://topic/{canonical.topic_id}")
    assert resolved.path == f"topics/{canonical.topic_id}--last-hits.md"
    assert b"Increase last hits" in resolved.content
    assert resolved.rev == gitops.head(canonical.root)


def test_resolves_older_rev_after_change(canonical):
    old = gitops.head(canonical.root)
    path = canonical.layout.hypotheses / f"{canonical.hypothesis_id}.md"
    write_doc(path, {"id": canonical.hypothesis_id, "statement": "Changed."}, "")
    gitops.commit_all(canonical.root, "change")
    uri = f"ar://hypothesis/{canonical.hypothesis_id}"
    assert b"Changed." in resolve(canonical.root, uri).content
    older = resolve(canonical.root, uri, rev=old)
    assert b"kill range" in older.content and older.rev == old


def test_reads_git_not_the_working_tree(canonical):
    new_topic = canonical.layout.topics / "T-01K6Z3V8QX0000000000000000.md"
    new_topic.write_text("---\nid: x\n---\n")
    with pytest.raises(ResolveError):
        resolve(canonical.root, "ar://topic/T-01K6Z3V8QX0000000000000000")


def test_plan_file_and_handoff(canonical):
    write_json(canonical.layout.ledger / "handoffs" / "K-1" / "r-1.json", {"state": "committed"})
    gitops.commit_all(canonical.root, "handoff")
    assert b"Focus on last hits" in resolve(canonical.root, "ar://plan").content
    assert resolve(canonical.root, "ar://file/AGENTS.md").content == b"# Doctrine\n"
    handoff = resolve(canonical.root, "ar://handoff/K-1/r-1")
    assert handoff.path == ".supervisor/ledger/handoffs/K-1/r-1.json"


def test_anchor_resolves_the_document(canonical):
    resolved = resolve(canonical.root, f"ar://claim/{canonical.claim_id}#d1")
    assert b"Creep damage" in resolved.content


@pytest.mark.parametrize(
    "uri",
    [
        "ar://widget/W-1",
        "ar://topic/T-01K6Z3V8QX0000000000000000",
        "ar://file/missing.md",
        "ar://handoff/K-1/r-9",
        "topic/T-1",
        "ar://topic/",
    ],
)
def test_unresolvable_raises(canonical, uri):
    with pytest.raises(ResolveError):
        resolve(canonical.root, uri)


def test_type_must_match_id(canonical):
    with pytest.raises(ResolveError):
        resolve(canonical.root, f"ar://topic/{canonical.hypothesis_id}")


def test_archived_claim_resolves(canonical):
    source = canonical.layout.claims / f"{canonical.claim_id}.md"
    canonical.layout.archive.mkdir(parents=True, exist_ok=True)
    source.rename(canonical.layout.archive / source.name)
    gitops.commit_all(canonical.root, "archive claim")
    resolved = resolve(canonical.root, f"ar://claim/{canonical.claim_id}")
    assert resolved.path == f"kb/archive/{canonical.claim_id}.md"
    assert b"Creep damage" in resolved.content
