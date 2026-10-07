import hashlib
import json
from pathlib import Path

import pytest

from autoresearch.softmax_tool import xp
from autoresearch.softmax_tool.client import SoftmaxClient
from autoresearch.softmax_tool.platform_cli import PlatformCli
from autoresearch.softmax_tool.protocol import CreditDenied, XpHandle, XpStatus

from .conftest import LEAGUE_ID, RECORDED, completed, recorded, synthetic

E = "E-01K6Z3V8QXAAAAAAAAAAAAAAAA"
SPEC = {
    "league_id": LEAGUE_ID,
    "num_episodes": 4,
    "roster": [{"player": {"random": True}}] * 10,
    "description": "candidate vs champion",
}
CREATED_ID = "xreq_00000000-0000-4000-8000-0000000000e1"


def cli(env, runner) -> PlatformCli:
    return PlatformCli(env, run=runner)


def listing(*titles: str | None, next_cursor: str | None = None) -> str:
    base = json.loads(recorded("xp_request_list_mine.json"))["entries"][0]
    entries = [
        {**base, "id": f"xreq_00000000-0000-4000-8000-{index:012d}", "title": title}
        for index, title in enumerate(titles)
    ]
    return json.dumps({"entries": entries, "next_cursor": next_cursor})


# --- tags --------------------------------------------------------------------


def test_tag_round_trip():
    assert xp.tag(E, 2, 3) == f"ar:{E}:b2:a3"
    assert xp.parse_tag(f"ar:{E}:b2:a3") == (E, 2, 3)
    assert xp.parse_tag("player-only XP probe") is None
    assert xp.parse_tag(None) is None


# --- create ------------------------------------------------------------------


def test_create_sends_private_league_request_tagged_and_idempotent(env, runner):
    runner.on(
        "coworld", "xp-request", "create", response=completed(synthetic("xp_request_create.json"))
    )
    handle = xp.create(cli(env, runner), E, 1, 1, SPEC)
    assert handle == XpHandle(CREATED_ID, E, 1, 1)
    argv, kwargs = runner.calls[0]
    assert argv == ["coworld", "xp-request", "create", "-", "--json"]
    body = json.loads(kwargs["input"])
    assert body == {
        "target": {"league_id": LEAGUE_ID},
        "roster": SPEC["roster"],
        "num_episodes": 4,
        "private": True,
        "title": f"ar:{E}:b1:a1",
        "idempotency_key": f"ar:{E}:b1:a1",
        "description": "candidate vs champion",
    }


def test_create_rejects_incomplete_or_unknown_spec(env, runner):
    with pytest.raises(ValueError, match="roster"):
        xp.create(cli(env, runner), E, 1, 1, {"league_id": LEAGUE_ID, "num_episodes": 1})
    with pytest.raises(ValueError, match="episodes"):
        xp.create(cli(env, runner), E, 1, 1, {**SPEC, "episodes": 3})
    assert runner.calls == []


def test_credit_denial_becomes_credit_denied(env, runner):
    runner.on(
        "coworld",
        "xp-request",
        "create",
        response=completed(stderr=synthetic("xp_request_create_402.stderr"), returncode=1),
    )
    result = xp.create(cli(env, runner), E, 1, 1, SPEC)
    assert isinstance(result, CreditDenied)
    assert "credit allowance" in result.reason


def test_other_create_failure_raises(env, runner):
    stderr = "Error: Request failed (429) for /v2/experience-requests: pending limit\n"
    runner.on("coworld", "xp-request", "create", response=completed(stderr=stderr, returncode=1))
    with pytest.raises(RuntimeError, match="429"):
        xp.create(cli(env, runner), E, 1, 1, SPEC)


# --- recover -----------------------------------------------------------------


def test_recover_on_recorded_listing_finds_nothing_untagged(env, runner):
    runner.on(
        "coworld", "xp-request", "list", response=completed(recorded("xp_request_list_mine.json"))
    )
    assert xp.recover(cli(env, runner), E) == []
    assert runner.argvs() == [
        ["coworld", "xp-request", "list", "--mine", "--limit", "200", "--json"]
    ]


def test_recover_follows_pages_and_matches_exact_experiment(env, runner):
    pages = {
        None: listing(f"ar:{E}:b1:a1", f"ar:{E}X:b1:a1", next_cursor="c2"),
        "c2": listing(None, f"ar:{E}:b1:a2", "ar:E-OTHER:b1:a1"),
    }

    def respond(argv, kwargs):
        cursor = argv[argv.index("--cursor") + 1] if "--cursor" in argv else None
        return completed(pages[cursor])

    runner.on("coworld", "xp-request", "list", response=respond)
    handles = xp.recover(cli(env, runner), E)
    assert [(h.experiment, h.batch, h.attempt) for h in handles] == [(E, 1, 1), (E, 1, 2)]
    assert runner.argvs()[1] == [
        "coworld",
        "xp-request",
        "list",
        "--mine",
        "--limit",
        "200",
        "--cursor",
        "c2",
        "--json",
    ]


def test_client_retry_reuses_existing_request_instead_of_creating(env, runner, tmp_path):
    runner.on("coworld", "xp-request", "list", response=completed(listing(f"ar:{E}:b1:a1")))
    runner.on(
        "coworld", "xp-request", "create", response=completed(synthetic("xp_request_create.json"))
    )
    sm = SoftmaxClient(env, tmp_path, run=runner)
    handle = sm.xp_create(E, 1, 1, SPEC)
    assert handle == XpHandle("xreq_00000000-0000-4000-8000-000000000000", E, 1, 1)
    assert not any(argv[:3] == ["coworld", "xp-request", "create"] for argv in runner.argvs())


def test_client_creates_when_no_existing_request_and_checks_identity_last(env, runner, tmp_path):
    runner.on("coworld", "xp-request", "list", response=completed(listing(f"ar:{E}:b1:a1")))
    runner.on(
        "coworld", "xp-request", "create", response=completed(synthetic("xp_request_create.json"))
    )
    handle = SoftmaxClient(env, tmp_path, run=runner).xp_create(E, 1, 2, SPEC)
    assert isinstance(handle, XpHandle)
    assert [argv[:3] for argv in runner.argvs()] == [
        ["coworld", "xp-request", "list"],
        ["softmax", "status"],
        ["coworld", "xp-request", "create"],
    ]


# --- status ------------------------------------------------------------------


def test_status_maps_recorded_completed_request(env, runner):
    runner.on("coworld", "xp-request", "get", response=completed(recorded("xp_request_get.json")))
    handle = XpHandle("xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5", E, 1, 1)
    assert xp.status(cli(env, runner), handle) == XpStatus("completed", 1, 1)
    assert runner.argvs() == [["coworld", "xp-request", "get", handle.request_id, "--json"]]


@pytest.mark.parametrize(
    "server_status, state",
    [
        ("pending", "pending"),
        ("submitted", "pending"),
        ("running", "running"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("cancelled", "failed"),
    ],
)
def test_status_mapping(env, runner, server_status, state):
    body = json.loads(synthetic("xp_request_create.json"))
    body.update(status=server_status, completed_count=1, episode_count=4)
    runner.on("coworld", "xp-request", "get", response=completed(json.dumps(body)))
    assert xp.status(cli(env, runner), XpHandle(CREATED_ID, E, 1, 1)) == XpStatus(state, 1, 4)


# --- artifacts ---------------------------------------------------------------


def receipt(episode: str, artifact_id: str, folder: str, filename: str, content: bytes) -> dict:
    return {
        "kind": "artifact",
        "episode_request_id": episode,
        "artifact_id": artifact_id,
        "version": "v1",
        "filename": filename,
        "encoding": "identity",
        "state": "completed",
        "path": f"{folder}/{filename}",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "http_status": None,
    }


def downloader(files: dict[str, bytes], rows: list[dict], returncode: int = 0):
    """Acts like ``coworld xp-request download``: writes files and appends receipts."""

    def respond(argv, kwargs):
        dest = Path(argv[argv.index("-d") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        for relative, content in files.items():
            (dest / relative).parent.mkdir(parents=True, exist_ok=True)
            (dest / relative).write_bytes(content)
        with (dest / "manifest.jsonl").open("a") as log:
            for row in rows:
                log.write(json.dumps(row) + "\n")
        return completed(f"Download receipts: {dest / 'manifest.jsonl'}\n", returncode=returncode)

    return respond


def test_artifacts_verify_recorded_results_receipt(env, runner, tmp_path):
    rows = [json.loads(line) for line in recorded("download_manifest.jsonl").splitlines()]
    results_row = next(row for row in rows if row["filename"] == "results.json")
    files = {results_row["path"]: (RECORDED / "download_results.json").read_bytes()}
    runner.on("coworld", "xp-request", "download", response=downloader(files, [results_row]))
    dest = tmp_path / "artifacts"
    handle = XpHandle("xreq_b0c4ff36-2d9e-4ddb-8224-9ba8fa0c2fd5", E, 1, 1)
    paths = xp.artifacts(cli(env, runner), handle, dest)
    assert paths == [dest / results_row["path"]]
    assert json.loads(paths[0].read_text())["total_xp"][0] == 2327
    assert runner.argvs() == [
        [
            "coworld",
            "xp-request",
            "download",
            handle.request_id,
            "--include",
            "results",
            "--include",
            "replay",
            "-d",
            str(dest),
        ]
    ]


def test_artifacts_detect_hash_mismatch(env, runner, tmp_path):
    good = receipt("ereq_1", "a1:results:None", "f1", "results.json", b'{"scores": [1]}')
    runner.on(
        "coworld",
        "xp-request",
        "download",
        response=downloader({"f1/results.json": b'{"scores": [2]}'}, [good]),
    )
    with pytest.raises(xp.ArtifactVerificationError, match="sha256"):
        xp.artifacts(cli(env, runner), XpHandle(CREATED_ID, E, 1, 1), tmp_path / "a")


def test_artifacts_detect_missing_file(env, runner, tmp_path):
    good = receipt("ereq_1", "a1:results:None", "f1", "results.json", b"{}")
    runner.on("coworld", "xp-request", "download", response=downloader({}, [good]))
    with pytest.raises(xp.ArtifactVerificationError, match="missing"):
        xp.artifacts(cli(env, runner), XpHandle(CREATED_ID, E, 1, 1), tmp_path / "a")


def test_artifacts_pending_evidence_raises_incomplete(env, runner, tmp_path):
    pending = {
        **receipt("ereq_1", "a1:replay:None", "f2", "replay.replay", b""),
        "state": "pending",
    }
    runner.on("coworld", "xp-request", "download", response=downloader({}, [pending], returncode=2))
    with pytest.raises(xp.ArtifactsIncomplete):
        xp.artifacts(cli(env, runner), XpHandle(CREATED_ID, E, 1, 1), tmp_path / "a")


def test_artifacts_rerun_uses_latest_receipt_per_artifact(env, runner, tmp_path):
    dest = tmp_path / "a"
    old = {**receipt("ereq_1", "a1:replay:None", "f2", "replay.replay", b""), "state": "pending"}
    dest.mkdir()
    (dest / "manifest.jsonl").write_text(json.dumps(old) + "\n")
    replay = receipt("ereq_1", "a1:replay:None", "f2", "replay.replay", b"REPLAY")
    results = receipt("ereq_1", "a1:results:None", "f1", "results.json", b"{}")
    files = {"f2/replay.replay": b"REPLAY", "f1/results.json": b"{}"}
    runner.on("coworld", "xp-request", "download", response=downloader(files, [replay, results]))
    paths = xp.artifacts(cli(env, runner), XpHandle(CREATED_ID, E, 1, 1), dest)
    assert paths == [dest / "f1/results.json", dest / "f2/replay.replay"]
