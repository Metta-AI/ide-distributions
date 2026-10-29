"""``softmax_tool.upload``: build provenance -> version record -> upload -> binding (Task 3.3)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from records.fixtures import EXP_DESIGNED

from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext
from ide_research_core.softmax_tool.fake import FAULTS_ENV, FakeSoftmax
from ide_research_core.softmax_tool.platform_cli import IdentityError
from ide_research_core.softmax_tool.protocol import Refused
from ide_research_core.softmax_tool.upload import artifact_sha256, policy_name, upload

REPO = Path(__file__).resolve().parents[3]
STRATEGY_SHA = "a" * 64
SOURCE_COMMIT = "b" * 40


class Workspace:
    def __init__(self, tmp_path: Path, status: str = "built"):
        self.canonical = tmp_path / "canonical"
        self.worker = tmp_path / "worker"
        self.build = tmp_path / "build"
        self.canonical.mkdir()
        gitops.git(self.canonical, "init", "-q", "-b", "main")
        gitops.git(self.canonical, "remote", "add", "origin", str(tmp_path / "canonical.git"))
        self.worker.mkdir()
        gitops.git(self.worker, "init", "-q", "-b", "main")
        gitops.git(self.worker, "config", "user.email", "t@example.com")
        gitops.git(self.worker, "config", "user.name", "T")
        self.store = RecordStore(Layout(self.worker))
        self.experiment = self.store.create("experiment", dict(EXP_DESIGNED))
        for state in ("built", "uploaded")[: ("designed", "built", "uploaded").index(status)]:
            self.store.set_status(self.experiment, state)
        self.sm = FakeSoftmax(self.canonical, {})
        self.ctx = RunContext(tmp_path / "home", self.canonical, self.worker)

    def write_build(self, artifact: bytes = b"policy-bytes", **version) -> Path:
        self.build.mkdir(exist_ok=True)
        (self.build / "policy.bas").write_bytes(artifact)
        data = {
            "artifact": "policy.bas",
            "strategy_sha256": STRATEGY_SHA,
            "source_commit": SOURCE_COMMIT,
            "build_id": "build-1",
            "compiler": {"agent": "hermes", "model": "m"},
            "gates": {"G1": {"passed": True}, "G4": {"passed": False, "advisory": True}},
            "policy_sha256": hashlib.sha256(artifact).hexdigest(),
            **version,
        }
        data = {key: value for key, value in data.items() if value is not None}
        (self.build / "version.json").write_text(json.dumps(data))
        return self.build

    def meta(self) -> dict:
        return self.store.load(self.experiment)[0]

    def uploads(self) -> dict:
        path = self.canonical / ".supervisor" / "runtime" / "fake_softmax.json"
        return json.loads(path.read_text())["uploads"] if path.exists() else {}

    def run(self, sm=None, player="ply_fake") -> str:
        return upload(self.ctx, sm or self.sm, self.build, self.experiment, player_id=player, name="opening-rush")


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def refused(ws, sm=None) -> Refused:
    with pytest.raises(Refused) as caught:
        ws.run(sm)
    return caught.value


def test_upload_creates_version_event_and_binding(ws):
    ws.write_build()
    version_id = ws.run()

    version = ws.store.load(version_id)[0]
    assert version["artifact_sha256"] == hashlib.sha256(b"policy-bytes").hexdigest()
    assert version["strategy_sha256"] == STRATEGY_SHA
    assert version["source_commit"] == SOURCE_COMMIT
    assert version["policy_name"] == policy_name("opening-rush", "ply_fake")
    assert version["experiment"] == ws.experiment and version["player_id"] == "ply_fake"
    assert version["gates"]["G1"] == {"passed": True}
    assert version["build"]["build_id"] == "build-1"
    assert version["build"]["compiler"] == {"agent": "hermes", "model": "m"}

    (event_id,) = ws.store.find("version_event", version=version_id, kind="uploaded")
    platform_id = ws.store.load(event_id)[0]["data"]["policy_version_id"]
    assert list(ws.uploads()) == [platform_id]
    assert ws.uploads()[platform_id]["name"] == version["policy_name"]

    meta = ws.meta()
    assert meta["status"] == "uploaded"
    assert meta["candidate_binding"] == {
        "version_id": version_id,
        "platform_policy_version_id": platform_id,
    }


def test_policy_name_is_player_scoped():
    assert policy_name("opening-rush", "ply_fake") == "opening-rush-ply_fake"
    assert policy_name("opening-rush", "ply_fake") != policy_name("opening-rush", "ply_two")


@pytest.mark.parametrize("name", ["", "UPPER", "with spaces", "with:colon", "x" * 60])
def test_invalid_name_is_refused(name):
    with pytest.raises(Refused, match="policy name"):
        policy_name(name, "ply_fake")


def test_upload_from_status_uploaded_only_sets_binding(tmp_path):
    ws = Workspace(tmp_path, status="uploaded")
    ws.write_build()
    version_id = ws.run()
    assert ws.meta()["candidate_binding"]["version_id"] == version_id


def test_retry_after_success_returns_binding_without_reupload(ws):
    ws.write_build()
    first = ws.run()
    assert ws.run() == first
    assert len(ws.uploads()) == 1
    assert len(ws.store.find("version")) == 1


def test_retry_after_crash_in_upload_reuses_the_version_record(ws, monkeypatch):
    ws.write_build()

    def lost(artifact, name, *, player_id, artifact_type="file", operation_id=None):
        raise RuntimeError("network lost")

    monkeypatch.setattr(ws.sm, "upload", lost)
    with pytest.raises(RuntimeError, match="network lost"):
        ws.run()
    monkeypatch.undo()
    with pytest.raises(Refused) as pending:
        ws.run(sm=FakeSoftmax(ws.canonical, {}))
    assert pending.value.reason == "publication_pending"
    assert len(ws.store.find("version")) == 1
    assert len(ws.uploads()) == 0


def test_retry_after_event_before_binding_does_not_reupload(ws, monkeypatch):
    ws.write_build()

    def crash(*args, **kwargs):
        raise RuntimeError("crash before binding")

    monkeypatch.setattr(RecordStore, "update", crash)
    with pytest.raises(RuntimeError, match="crash before binding"):
        ws.run()
    monkeypatch.undo()
    assert ws.meta().get("candidate_binding") is None
    assert len(ws.uploads()) == 1
    version_id = ws.run()
    assert len(ws.uploads()) == 1  # the uploaded event was reused
    assert ws.meta()["candidate_binding"]["version_id"] == version_id


def test_refuses_failed_gate(ws):
    ws.write_build(gates={"G1": {"passed": True}, "G2": {"passed": False}})
    error = refused(ws)
    assert error.reason == "gates_failed" and "G2" in str(error)
    assert ws.store.find("version") == [] and ws.uploads() == {}


def test_refuses_build_status_not_passed(ws):
    ws.write_build(status="failed")
    assert refused(ws).reason == "gates_failed"


def test_refuses_missing_gates(ws):
    ws.write_build(gates={})
    assert refused(ws).reason == "gates_failed"


def test_refuses_missing_version_json(ws):
    ws.build.mkdir()
    assert refused(ws).reason == "bad_build"


def test_refuses_missing_artifact(ws):
    ws.write_build()
    (ws.build / "policy.bas").unlink()
    assert refused(ws).reason == "bad_build"


def test_refuses_artifact_hash_mismatch(ws):
    ws.write_build(policy_sha256="0" * 64)
    assert refused(ws).reason == "artifact_mismatch"


def test_refuses_artifact_outside_build_dir(ws):
    ws.write_build()
    (ws.build.parent / "escape.bas").write_bytes(b"x")
    version = json.loads((ws.build / "version.json").read_text())
    (ws.build / "version.json").write_text(json.dumps({**version, "artifact": "../escape.bas"}))
    assert refused(ws).reason == "bad_build"


def test_strategy_hash_from_source_hashes(ws):
    ws.write_build(
        strategy_sha256=None,
        source_path="strategy/STRATEGY.md",
        source_hashes={"strategy/STRATEGY.md": "c" * 64},
    )
    assert ws.store.load(ws.run())[0]["strategy_sha256"] == "c" * 64


def test_refuses_without_strategy_hash(ws):
    ws.write_build(strategy_sha256=None)
    assert refused(ws).reason == "bad_build"


def test_source_commit_defaults_to_worker_head(ws):
    gitops.git(ws.worker, "init", "-b", "main")
    gitops.git(ws.worker, "config", "user.email", "t@example.com")
    gitops.git(ws.worker, "config", "user.name", "T")
    gitops.commit_all(ws.worker, "seed")
    ws.write_build(source_commit=None)
    assert ws.store.load(ws.run())[0]["source_commit"] == gitops.head(ws.worker)


def test_refuses_wrong_status(tmp_path):
    ws = Workspace(tmp_path, status="designed")
    ws.write_build()
    assert refused(ws).reason == "experiment_status"


def test_refuses_different_build_once_bound(ws):
    ws.write_build()
    ws.run()
    ws.write_build(artifact=b"another policy")
    error = refused(ws)
    assert error.reason == "binding_conflict"
    assert len(ws.uploads()) == 1


def test_refuses_without_worker(ws):
    ws.write_build()
    ws.ctx = RunContext(ws.ctx.hermes_home, ws.canonical, None)
    assert refused(ws).reason == "no_worker"


@pytest.mark.parametrize("faults", ["identity=user", "player=ply_other"])
def test_identity_fault_blocks_upload_and_binding(ws, faults):
    ws.write_build()
    with pytest.raises(IdentityError):
        ws.run(sm=FakeSoftmax(ws.canonical, {FAULTS_ENV: faults}))
    assert ws.uploads() == {} and ws.meta().get("candidate_binding") is None


def test_directory_artifact_hash_is_order_independent(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    for root, names in ((one, ("b.txt", "a/x.txt")), (two, ("a/x.txt", "b.txt"))):
        for name in names:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(name)
    assert artifact_sha256(one) == artifact_sha256(two)
    (two / "b.txt").write_text("changed")
    assert artifact_sha256(one) != artifact_sha256(two)


def test_fakegame_build_output_uploads(ws, tmp_path):
    candidate = tmp_path / "candidate"
    (candidate / "strategy").mkdir(parents=True)
    (candidate / "strategy" / "STRATEGY.md").write_text("# Candidate\n")
    tool = REPO / "games" / "fakegame" / "tools" / "fake_build.py"
    proc = subprocess.run(
        [sys.executable, str(tool), str(candidate), "--out", str(ws.build), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["ok"] is True
    gitops.git(ws.worker, "init", "-b", "main")
    gitops.git(ws.worker, "config", "user.email", "t@example.com")
    gitops.git(ws.worker, "config", "user.name", "T")
    gitops.commit_all(ws.worker, "seed")
    version = ws.store.load(ws.run())[0]
    expected = hashlib.sha256(b"# Candidate\n").hexdigest()
    assert version["strategy_sha256"] == expected

@pytest.mark.parametrize('name', ['G4', 'G5'])
def test_advisory_error_rejected_even_with_passed_build_status(tmp_path, name):
    ws = Workspace(tmp_path)
    ws.write_build(status='passed', gates={name: {'outcome': 'errored', 'passed': False,
                                                'advisory': True}})
    with pytest.raises(Refused, match='build gates did not pass'):
        ws.run()
    assert ws.uploads() == {}


@pytest.mark.parametrize('name', ['G4', 'G5'])
def test_advisory_failure_upload_reader_accepts(tmp_path, name):
    from ide_research_core.softmax_tool.upload import _read_build
    ws = Workspace(tmp_path)
    ws.write_build(status='passed', gates={name: {'outcome': 'failed', 'passed': False,
                                                'advisory': True}})
    metadata, artifact = _read_build(ws.build)
    assert metadata['status'] == 'passed'
    assert artifact == ws.build / 'policy.bas'


def test_unbound_build_rejects_other_player_and_keeps_stable_retries(ws):
    from ide_research_core.softmax_tool.upload import upload_build

    ws.write_build()
    first = upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "opening-rush")
    with pytest.raises(Refused, match="workspace player"):
        upload_build(ws.ctx, ws.sm, ws.build, "ply_two", "opening-rush")
    assert upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "opening-rush") == first
    assert len(ws.uploads()) == 1


def test_unbound_new_artifact_keeps_lineage_but_gets_new_version(ws):
    from ide_research_core.softmax_tool.upload import upload_build

    ws.write_build()
    first = upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "opening-rush")
    ws.write_build(artifact=b"changed policy")
    second = upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "opening-rush")
    assert first != second
    assert len(ws.uploads()) == 2
    assert {item["name"] for item in ws.uploads().values()} == {"opening-rush-ply_fake"}


def test_failed_legacy_record_does_not_force_retry_of_conflicting_name(ws, monkeypatch):
    ws.write_build()
    with monkeypatch.context() as patch:
        patch.setattr("ide_research_core.softmax_tool.upload.policy_name",
                      lambda name, player: "ar-bootstrap-1637c5604a52")
        def conflict(artifact, name, *, player_id, artifact_type="file", operation_id=None):
            raise RuntimeError("409 name taken")
        patch.setattr(ws.sm, "upload", conflict)
        with pytest.raises(RuntimeError, match="409"):
            ws.run()
    legacy, = ws.store.find("version")
    recovered = ws.run()
    assert recovered != legacy
    assert ws.store.load(legacy)[0]["policy_name"] == "ar-bootstrap-1637c5604a52"
    assert ws.store.load(recovered)[0]["policy_name"] == "opening-rush-ply_fake"
    assert ws.run() == recovered
    assert len(ws.uploads()) == 1


@pytest.mark.parametrize("invalid", [
    {"status": "failed"},
    {"status": "rejected"},
    {"policy_sha256": "0" * 64},
    {"gates": {"G1": {"passed": False}}},
])
def test_upload_build_rejects_unchecked_or_changed_artifact(ws, invalid):
    from ide_research_core.softmax_tool.upload import upload_build

    ws.write_build(**invalid)
    with pytest.raises(Refused):
        upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "requested-change")
    assert ws.uploads() == {}
    assert ws.store.find("version") == []


@pytest.mark.parametrize("experiment", [False, True])
def test_research_worker_cannot_upload_directly(ws, experiment):
    from ide_research_core.softmax_tool.upload import upload_build

    (ws.ctx.hermes_home / "plugins/researcher_guard").mkdir(parents=True)
    ws.write_build()
    with pytest.raises(Refused) as error:
        if experiment:
            ws.run()
        else:
            upload_build(ws.ctx, ws.sm, ws.build, "ply_fake", "requested-change")
    assert error.value.reason == "coordinator_required"
    assert ws.uploads() == {}


@pytest.mark.parametrize("artifact_type", ["file", "oci-image"])
def test_build_publication_preserves_artifact_type_and_deduplicates(ws, monkeypatch, artifact_type):
    from ide_research_core.softmax_tool.upload import upload_build

    ws.write_build(artifact_type=artifact_type)
    calls = []
    original = ws.sm.upload

    def publish(artifact, name, *, player_id, artifact_type):
        calls.append((artifact_type, player_id))
        return original(artifact, name, player_id=player_id, artifact_type=artifact_type)

    monkeypatch.setattr(ws.sm, "upload", publish)
    selected = "ply_fake"
    first = upload_build(ws.ctx, ws.sm, ws.build, player_id=selected, name="build")
    assert upload_build(ws.ctx, ws.sm, ws.build, player_id=selected, name="build") == first
    assert calls == [(artifact_type, selected)]
    assert ws.store.load(first[0])[0]["player_id"] == selected
    assert ws.store.load(first[0])[0]["build"]["artifact_type"] == artifact_type


def test_unknown_artifact_type_is_rejected_before_upload(ws):
    ws.write_build(artifact_type="unknown")
    assert "artifact_type" in str(refused(ws))
    assert ws.uploads() == {}
