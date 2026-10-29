"""The ``upload`` tool: exact bytes once, the hash check, notices from wakes, failures, and
recovery of an attempt whose answer was lost (B.13): the saved inputs, the read-only lookup,
the one automatic retry, unresolved answers and the user's request."""

from __future__ import annotations

import json

import pytest

from research_core import gitops
from research_core.platform.errors import PlatformError
from research_core.platform.fake import FakePlatform
from research_core.platform.protocol import PolicyVersion
from research_core.store import intents, notices, sessions
from research_core.tools import version as version_tool
from tests.tools.test_version_tool import HARNESS, Lab, commit_policy, policy_bytes

LOST = "fail_after=upload"  # the platform stored the bytes; the answer never arrived


@pytest.fixture
def lab(ctx_factory) -> Lab:
    return Lab(ctx_factory)


def test_upload_sends_exact_bytes_once(lab):
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    result = lab.upload("V-1")
    record = result["record"]
    assert result["reconciled"] is False and result["attempt"] == 1
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
    intent = intents.read(lab.store, 1)
    assert intent["state"] == "done" and intent["key"] == digest and intent["subject"] == "V-1"

    again = lab.upload("V-1", role="researcher", session_id="s9")
    assert again["already_uploaded"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    assert len(lab.events("version.uploaded")) == 1


def test_upload_saves_the_exact_inputs_on_the_intent(lab):
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    kept = lab.coworld.documents / "artifacts" / digest
    lab.upload("V-1")
    intent = intents.read(lab.store, 1)
    assert intent["request"] == {
        "name": "cow_fake-main",
        "player_id": "ply_fake",
        "file_hash": digest,
        "file_size": kept.stat().st_size,
        "attributes": {},
    }
    # The inputs stay on the row through finish, next to the platform's answer.
    assert intent["result"]["request"] == intent["request"]
    assert intent["result"]["id"] == lab.read("V-1")["record"]["uploaded"]["policy_version_id"]


def test_upload_lost_answer_is_recovered_by_lookup_without_the_artifact(lab):
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    crashed = lab.run("upload", {"version": "V-1"}, faults=LOST)
    assert crashed["ok"] is False and lab.platform().snapshot()["counts"]["upload"] == 1
    (pending,) = intents.pending(lab.store, "upload")
    assert pending["request"]["file_hash"] == digest
    # The kept copy is gone: the saved inputs are all the recovery needs.
    (lab.coworld.documents / "artifacts" / digest).unlink()

    recovered = lab.upload("V-1")
    assert recovered["reconciled"] is True
    assert recovered["record"]["uploaded"]["digest_checked"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["result"]["reconciled"] is True
    assert done["attempt"] == 1 and done["request"]["file_hash"] == digest
    assert notices.unread(lab.wstore) == []


@pytest.mark.parametrize(
    "fault", ["lookup=503", "lookup=403", "lookup=unavailable", "platform=down"]
)
def test_upload_lookup_failure_leaves_the_upload_unresolved(lab, monkeypatch, fault):
    monkeypatch.setattr(version_tool, "sleep", lambda _s: None)
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    lab.run("upload", {"version": "V-1"}, faults=LOST)

    result = lab.run("upload", {"version": "V-1"}, faults=fault)
    assert result["ok"] is False
    assert (
        "unresolved" in result["error"] and "I-1" in result["error"] and digest in result["error"]
    )
    context = result["context"]
    assert context["intent"] == "I-1" and context["unresolved"] is True
    assert context["upload_inputs"]["file_hash"] == digest and context["lookup"]["error"]
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    assert intents.read(lab.store, 1)["state"] == "pending"
    assert lab.read("V-1")["record"]["uploaded"] is None
    (notice,) = notices.unread(lab.wstore)
    assert notice["refs"] == ["V-1"] and notice["urgency"] == "high"
    assert "V-1 upload is unresolved (I-1)" in notice["text"]
    # The same answer again does not repeat the notice.
    lab.run("upload", {"version": "V-1"}, faults=fault)
    assert len(notices.unread(lab.wstore)) == 1


def test_upload_definitive_no_match_sends_again_and_records_the_attempt(lab):
    version = lab.create("Starter")
    digest = version["artifact"]["digest"]
    lab.run("upload", {"version": "V-1"}, faults=LOST)
    # The platform answers that no such version exists (the fake forgets what it holds): the
    # bytes go once more, as attempt 2 of the same intent.
    sent = lab.upload("V-1", faults="lookup=not_found")
    assert sent["reconciled"] is False and sent["attempt"] == 2
    assert lab.platform().snapshot()["counts"]["upload"] == 2
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["attempt"] == 2
    assert done["result"]["failures"] == [{"lookup": "not_found"}]
    assert done["request"]["file_hash"] == digest
    assert notices.unread(lab.wstore) == []


def test_upload_second_no_match_is_unresolved_until_the_user_asks(lab):
    lab.create("Starter")
    lab.run("upload", {"version": "V-1"}, faults=LOST)
    again = lab.run("upload", {"version": "V-1"}, faults=f"lookup=not_found;{LOST}")
    assert again["ok"] is False and "injected fault after upload" in again["error"]
    assert lab.platform().snapshot()["counts"]["upload"] == 2
    pending = intents.read(lab.store, 1)
    assert pending["state"] == "pending" and pending["attempt"] == 2

    blocked = lab.run("upload", {"version": "V-1"}, faults="lookup=not_found")
    assert blocked["ok"] is False and "automatic retry" in blocked["error"]
    assert blocked["context"]["unresolved"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 2
    assert intents.read(lab.store, 1)["attempt"] == 2
    (notice,) = notices.unread(lab.wstore)
    assert notice["refs"] == ["V-1"]

    asked = lab.run("upload", {"version": "V-1", "user_requested": True}, faults="lookup=not_found")
    assert asked["ok"] is True and asked["attempt"] == 3
    assert lab.platform().snapshot()["counts"]["upload"] == 3
    done = intents.read(lab.store, 1)
    assert done["state"] == "done"
    assert done["result"]["failures"] == [{"lookup": "not_found"}, {"user_requested": True}]


def test_upload_user_requested_sends_again_even_when_the_lookup_is_down(lab):
    lab.create("Starter")
    lab.run("upload", {"version": "V-1"}, faults=LOST)
    result = lab.run("upload", {"version": "V-1", "user_requested": True}, faults="lookup=503")
    assert result["ok"] is True and result["reconciled"] is False and result["attempt"] == 2
    assert lab.platform().snapshot()["counts"]["upload"] == 2
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["result"]["failures"] == [{"user_requested": True}]
    assert notices.unread(lab.wstore) == []


def test_upload_done_intent_the_platform_no_longer_shows_is_unresolved(lab):
    lab.create("One")
    gitops.commit_all(lab.code, "A comment-only change", allow_empty=True)
    lab.create("Two, same bytes")
    lab.upload("V-1")
    # The platform loses the version (say, deleted on the website). The intent for these bytes
    # is done, so V-2 is not sent from its absence alone.
    platform = lab.platform()
    state = platform.snapshot()
    state["policies"].clear()
    platform.state_path.write_text(json.dumps(state))

    result = lab.run("upload", {"version": "V-2"})
    assert result["ok"] is False and "accepted the bytes" in result["error"]
    assert result["context"]["intent"] == "I-1" and result["context"]["unresolved"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    assert intents.read(lab.store, 1)["state"] == "done"
    assert lab.read("V-2")["record"]["uploaded"] is None
    (notice,) = notices.unread(lab.wstore)
    assert notice["refs"] == ["V-2"]


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
    assert failed["result"]["platform_hash"] == "deadbeef" and failed["result"]["digest"] == digest

    monkeypatch.setattr(FakePlatform, "upload_file_policy", real)
    # The next call looks the saved inputs up first. The fake stored the real hash, so the
    # version is found and adopted as attempt 2 (recording the finding); nothing is sent again.
    retried = lab.upload("V-1")
    assert retried["reconciled"] is True
    assert retried["record"]["uploaded"]["digest_checked"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["attempt"] == 2
    assert done["result"]["failures"][0]["lookup"] == "found"


def test_upload_platform_error_fails_the_intent(lab):
    lab.create("Starter")
    # The write goes once per call, never retried (a retry could send twice when the error
    # came after the request reached the platform), so one 503 fails the call.
    result = lab.run("upload", {"version": "V-1"}, faults="upload=503:2")
    assert result["ok"] is False
    assert result["context"]["status"] == 503 and result["context"]["retryable"] is True
    assert "upload failed" in result["error"]
    assert intents.read(lab.store, 1)["state"] == "failed"
    assert lab.platform().snapshot()["counts"]["upload"] == 0
    # The next call reads the platform first (no such version) and sends once more: the one
    # automatic retry of this intent.
    second = lab.run("upload", {"version": "V-1"}, faults="upload=503:2")
    assert second["ok"] is False and second["context"]["status"] == 503
    failed = intents.read(lab.store, 1)
    assert failed["state"] == "failed" and failed["attempt"] == 2
    (earlier,) = failed["result"]["failures"]
    assert earlier["status"] == 503 and earlier["lookup"] == "not_found"
    # A third absence is not sent on the tool's own initiative; the user's request is.
    third = lab.run("upload", {"version": "V-1"})
    assert third["ok"] is False and "automatic retry" in third["error"]
    assert intents.read(lab.store, 1)["state"] == "failed"
    asked = lab.run("upload", {"version": "V-1", "user_requested": True})
    assert asked["ok"] is True and asked["attempt"] == 3
    assert lab.platform().snapshot()["counts"]["upload"] == 1


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
    assert intents.find(lab.store, kind="upload", key=version["artifact"]["digest"]) is None


def test_upload_refusal_carries_the_route(lab, monkeypatch):
    lab.create("Starter")

    def down(self, **kwargs):
        raise PlatformError("no route", route="POST /x", status=404, request_id="req-1")

    monkeypatch.setattr(FakePlatform, "upload_file_policy", down)
    result = lab.run("upload", {"version": "V-1"})
    assert result["ok"] is False
    assert result["context"]["route"] == "POST /x" and result["context"]["request_id"] == "req-1"
