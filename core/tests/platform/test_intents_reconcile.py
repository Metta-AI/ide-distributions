"""Review focus 4: a platform call that crashes after the platform accepted it is reconciled.

The fake's ``fail_after=<op>`` fault raises after the operation persisted, which is what a
process death between the platform's answer and the store's commit looks like. The intent row
stays ``pending``; the next call reads the platform by the intent's key and sends nothing
twice (the fake counts every upload and submit that reached it).
"""

from __future__ import annotations

import pytest

from research_core.store import champion, intents

# The tool fixtures live in tests/tools/conftest.py; importing them makes them available here.
from tests.tools.conftest import ctx_factory, workspace_env  # noqa: F401
from tests.tools.test_version_tool import Lab


@pytest.fixture
def lab(ctx_factory) -> Lab:  # noqa: F811  (the imported fixture is injected by name)
    return Lab(ctx_factory)


def test_intents_reconcile_upload(lab):
    version = lab.create("Starter")
    crashed = lab.run("upload", {"version": "V-1"}, faults="fail_after=upload")
    assert crashed["ok"] is False and "injected fault after upload" in crashed["error"]
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    (pending,) = intents.pending(lab.store, "upload")
    assert pending["key"] == version["artifact"]["digest"] and pending["subject"] == "V-1"
    assert pending["request"]["file_hash"] == version["artifact"]["digest"]
    assert lab.read("V-1")["record"]["uploaded"] is None
    # Recovery reads the platform with the saved inputs, so the kept copy is not needed.
    (lab.coworld.documents / "artifacts" / version["artifact"]["digest"]).unlink()

    recovered = lab.upload("V-1")
    assert recovered["reconciled"] is True
    assert recovered["record"]["uploaded"]["policy_version_id"] == recovered["policy_version"]["id"]
    assert recovered["record"]["uploaded"]["digest_checked"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1
    assert intents.pending(lab.store) == []
    done = intents.read(lab.store, 1)
    assert done["state"] == "done" and done["result"]["reconciled"] is True
    assert len(lab.events("version.uploaded")) == 1
    # A third call is the no-op of an uploaded Version.
    assert lab.upload("V-1")["already_uploaded"] is True
    assert lab.platform().snapshot()["counts"]["upload"] == 1


def test_intents_reconcile_submit(lab):
    lab.create("Starter")
    lab.upload("V-1")
    crashed = lab.submit("V-1", user_requested=True, cites="msg-1", faults="fail_after=submit")
    assert crashed["ok"] is False and "injected fault after submit" in crashed["error"]
    assert lab.platform().snapshot()["counts"]["submit"] == 1
    (pending,) = intents.pending(lab.store, "submit")
    assert pending["key"] == "V-1:league_fake"
    assert lab.events("version.submitted") == []
    assert champion.read(lab.store, "league_fake") is None

    recovered = lab.submit("V-1", user_requested=True, cites="msg-1")
    assert recovered["ok"] is True, recovered
    assert recovered["reconciled"] is True
    assert lab.platform().snapshot()["counts"]["submit"] == 1
    (submitted,) = lab.events("version.submitted")
    assert submitted["reconciled"] is True and submitted["cites"] == "msg-1"
    assert submitted["submission_id"] == recovered["submission"]["id"]
    assert intents.read(lab.store, 2)["state"] == "done"
    assert champion.read(lab.store, "league_fake")["version_id"] == 1

    # Submitting again finds the done intent and the platform's submission: nothing is sent.
    again = lab.submit("V-1", user_requested=True, cites="msg-2")
    assert again["ok"] is True and again["already_submitted"] is True
    assert lab.platform().snapshot()["counts"]["submit"] == 1
    assert len(lab.events("version.submitted")) == 1
