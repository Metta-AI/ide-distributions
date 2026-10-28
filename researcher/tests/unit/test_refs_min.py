import pytest
from support.canonical import Canonical

from ide_research_core.io import gitops
from ide_research_core.io.docs import write_doc
from ide_research_core.refs import ResolveError, resolve


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


def test_plan_and_file(canonical):
    assert b"Focus on last hits" in resolve(canonical.root, "ar://plan").content
    assert resolve(canonical.root, "ar://file/AGENTS.md").content == b"# Doctrine\n"


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


def test_claim_in_both_locations_resolves_to_kb_claims(canonical):
    source = canonical.layout.claims / f"{canonical.claim_id}.md"
    canonical.layout.archive.mkdir(parents=True, exist_ok=True)
    (canonical.layout.archive / source.name).write_text(source.read_text())
    gitops.commit_all(canonical.root, "interrupted archive")
    resolved = resolve(canonical.root, f"ar://claim/{canonical.claim_id}")
    assert resolved.path == f"kb/claims/{canonical.claim_id}.md"
