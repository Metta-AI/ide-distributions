import jsonschema
import pytest

from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.queue.card import CardSpec
from ide_research_core.queue.requests import read_requests, write_request

SPEC = CardSpec("", "Look at X", "investigation", "because", ["T-1"], "worker", "P2")
TARGET = "Q-01K6Z3V8QXABCDEFGHJKMNPQRS"


def test_write_create_validates_and_writes(tmp_path):
    layout = Layout(tmp_path)
    qid = write_request(layout, "create", spec=SPEC)
    assert qid.startswith("Q-")
    assert (layout.card_requests / f"{qid}.json").exists()


def test_write_reprioritize_and_drop(tmp_path):
    layout = Layout(tmp_path)
    write_request(layout, "reprioritize", target_request=TARGET, priority="P0")
    write_request(layout, "drop", target_request=TARGET, reason="stale")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"op": "create"},
        {"op": "reprioritize", "target_request": TARGET},
        {"op": "drop", "target_request": TARGET},
    ],
)
def test_write_rejects_missing_fields(tmp_path, kwargs):
    layout = Layout(tmp_path)
    with pytest.raises(jsonschema.ValidationError):
        write_request(layout, **kwargs)
    assert not layout.card_requests.exists()


def test_read_requests_reads_from_commit_not_working_tree(tmp_path):
    gitops.git(tmp_path, "init", "-q")
    gitops.git(tmp_path, "config", "user.email", "t@example.com")
    gitops.git(tmp_path, "config", "user.name", "t")
    layout = Layout(tmp_path)
    committed = write_request(layout, "create", spec=SPEC)
    sha = gitops.commit_all(tmp_path, "add request")
    uncommitted = write_request(layout, "drop", target_request=committed, reason="x")
    (layout.card_requests / f"{committed}.json").unlink()

    requests = read_requests(tmp_path, sha)
    assert [r["id"] for r in requests] == [committed]
    assert uncommitted not in [r["id"] for r in requests]


def test_read_requests_empty_when_no_directory(tmp_path):
    gitops.git(tmp_path, "init", "-q")
    gitops.git(tmp_path, "config", "user.email", "t@example.com")
    gitops.git(tmp_path, "config", "user.name", "t")
    sha = gitops.commit_all(tmp_path, "empty", allow_empty=True)
    assert read_requests(tmp_path, sha) == []
