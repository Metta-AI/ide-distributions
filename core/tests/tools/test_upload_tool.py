"""The ``upload`` tool: exact bytes once, the hash check, notices from wakes, failures."""

from __future__ import annotations

import pytest

from research_core import gitops
from research_core.platform.errors import PlatformError
from research_core.platform.fake import FakePlatform
from research_core.platform.protocol import PolicyVersion
from research_core.store import intents, notices, sessions
from research_core.tools import version as version_tool
from tests.tools.test_version_tool import HARNESS, Lab, commit_policy, policy_bytes


@pytest.fixture
def lab(ctx_factory) -> Lab:
    return Lab(ctx_factory)


def test_upload_sends_exact_bytes_once(lab):
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    result = lab.upload("V-1")
    record = result["record"]
    assert result["reconciled"] is False
    assert record["uploaded"]["policy_version_id"] == result["policy_version"]["id"]
    assert record["uploaded"]["digest_checked"] is True
    assert record["submission_status"] == "uploaded"
    assert result["policy_version"]["label"] == "cow_fake-main:v1"
    state = lab.platform().snapshot()
    (row,) = state["policies"].values()
    assert row["content_hash"] == digest
    assert row["tags"] == {"version": "V-1", "commit": version["commit_sha"]}
    assert state["counts"]["upload"] == 1
    assert [e["policy_version_id"] for e in lab.events("version.uploaded")] == [row["id"]]
    (intent,) = [i for i in [intents.read(lab.store, 1)]]
    assert intent["state"] == "done" and intent["key"] == digest and intent["subject"] == "V-1"

    again = lab.upload("V-1", role="researcher", session_id="s9")
    assert again["already_uploaded"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    assert len(lab.events("version.uploaded")) == 1


def test_upload_hash_mismatch(lab, monkeypatch):
    lab.create("Starter")
    real = FakePlatform.upload_file_policy

    def tampered(self, *, name, content, tags=None):
        found = real(self, name=name, content=content, tags=tags)
        return PolicyVersion(found.id, found.name, found.version, "deadbeef", found.player_id)

    monkeypatch.setattr(FakePlatform, "upload_file_policy", tampered)
    result = lab.run("upload", {"version": "V-1"})
    assert result["ok"] is False
    digest = lab.read("V-1")["record"]["artifact"]["digest"]
    assert "deadbeef" in result["error"] and digest in result["error"]
    assert lab.read("V-1")["record"]["uploaded"] is None
    failed = intents.read(lab.store, 1)
    assert failed["state"] == "failed"
    assert failed["result"] == {"platform_hash": "deadbeef", "digest": digest}

    monkeypatch.setattr(FakePlatform, "upload_file_policy", real)
    retried = lab.upload("V-1")
    assert retried["record"]["uploaded"]["digest_checked"] is True
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["attempt"] == 2


def test_upload_platform_error_fails_the_intent(lab, monkeypatch):
    monkeypatch.setattr(version_tool, "sleep", lambda _s: None)
    lab.create("Starter")
    result = lab.run("upload", {"version": "V-1"}, faults="upload=503:3")
    assert result["ok"] is False
    assert result["context"]["status"] == 503 and result["context"]["retryable"] is True
    assert "upload failed" in result["error"]
    assert intents.read(lab.store, 1)["state"] == "failed"
    assert lab.platform().snapshot()["counts"]["upload"] == 0
    # The fourth try (a new tool call) goes through and the intent is retried.
    ok = lab.upload("V-1", faults="upload=503:3")
    assert ok["record"]["uploaded"]["policy_version_id"]
    assert intents.read(lab.store, 1)["attempt"] == 2


def test_upload_identical_bytes_name_the_version_that_has_them(lab):
    lab.create("One")
    gitops.commit_all(lab.code, "A comment-only change", allow_empty=True)
    lab.create("Two, same bytes")
    lab.upload("V-1")
    result = lab.run("upload", {"version": "V-2"})
    assert result["ok"] is False
    assert "already uploaded as V-1" in result["error"] and "submit V-1" in result["error"]
    assert result["context"]["uploaded_as"] == "V-1"
    assert lab.platform().snapshot()["counts"]["upload"] == 1


def test_upload_notice_only_from_a_wake(lab):
    lab.create("One")
    commit_policy(lab.code, 3, 6, "Patience 6")
    lab.create("Two")
    sessions.register(lab.wstore, HARNESS, session_id="w1", profile="orchestrator", kind="wake")
    sessions.register(lab.wstore, HARNESS, session_id="c1", profile="orchestrator", kind="chat")
    lab.upload("V-1", session_id="c1")
    assert notices.unread(lab.wstore) == []
    lab.upload("V-2", session_id="w1")
    (notice,) = notices.unread(lab.wstore)
    assert notice["text"] == "Uploaded V-2 as cow_fake-main:v2" and notice["refs"] == ["V-2"]


def test_upload_refuses_when_the_kept_artifact_is_gone(lab):
    version = lab.create("Starter")
    kept = lab.coworld.documents / "artifacts" / version["artifact"]["digest"]
    kept.write_bytes(policy_bytes(1, 1))
    changed = lab.run("upload", {"version": "V-1"})
    assert changed["ok"] is False and "no longer matches" in changed["error"]
    kept.unlink()
    gone = lab.run("upload", {"version": "V-1"})
    assert gone["ok"] is False and "is gone" in gone["error"]
    assert lab.platform().snapshot()["counts"]["upload"] == 0


def test_upload_refusal_carries_the_route(lab, monkeypatch):
    lab.create("Starter")

    def down(self, **kwargs):
        raise PlatformError("no route", route="POST /x", status=404, request_id="req-1")

    monkeypatch.setattr(FakePlatform, "upload_file_policy", down)
    result = lab.run("upload", {"version": "V-1"})
    assert result["ok"] is False
    assert result["context"]["route"] == "POST /x" and result["context"]["request_id"] == "req-1"
