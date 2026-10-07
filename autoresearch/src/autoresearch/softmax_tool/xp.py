"""Hosted Experience Requests (XP) through ``coworld xp-request`` (coworld 0.1.57).

Tagging: coworld 0.1.57 has no tag or metadata field on an XP request. The request body's
``title`` carries the tag ``ar:<experiment>:b<batch>:a<attempt>`` (experiment ids are ULIDs, so
the tag is globally unique), and ``recover`` finds requests by parsing titles from
``xp-request list --mine``. The same tag is sent as ``idempotency_key``: the server returns the
existing request for a repeated key instead of creating a second one (observatory-api
``v2/experience_requests.py``), so even a create retried after a lost response cannot duplicate.

Credit denial: the server answers 402 when the owning account's credit allowance cannot admit the
request; the CLI prints ``Error: Request failed (402) for /v2/experience-requests: <detail>`` on
stderr and exits 1. That becomes ``CreditDenied``.

Artifacts: ``xp-request download`` writes each file under ``<dest>/<sha256(artifact:version)>/``
and appends a receipt per file to ``<dest>/manifest.jsonl`` with ``size`` and ``sha256``. The CLI
computes ``sha256`` while streaming and checks ``size`` against the server's declared size; the
server publishes no content hash of its own (``version`` is not one). We verify every file on disk
against the latest receipt for it.
"""

import hashlib
import json
import re
from pathlib import Path

from autoresearch.softmax_tool.platform_cli import (
    PAGE_LIMIT,
    PlatformCli,
    SoftmaxCliError,
    describe_failure,
)
from autoresearch.softmax_tool.protocol import CreditDenied, XpHandle, XpStatus

TAG_PATTERN = re.compile(r"ar:(?P<experiment>[^:\s]+):b(?P<batch>\d+):a(?P<attempt>\d+)")
CREDIT_DENIAL = re.compile(r"\(402\)|HTTP 402")
REQUIRED_SPEC = ("league_id", "num_episodes", "roster")
OPTIONAL_SPEC = ("description", "notes", "variant_id", "episode_player_llm_spend_limit_usd")
# ExperienceRequestStatus in observatory-core v2/models.py (metta 1301d30f33): pending,
# submitted, running, completed, failed, cancelled. coworld also spells "canceled" elsewhere.
STATE_BY_SERVER_STATUS = {
    "pending": "pending",
    "submitted": "pending",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "failed",
    "canceled": "failed",
}
UNKNOWN_STATUS_STATE = "pending"  # non-terminal: keep polling, never decide on an unknown status
DOWNLOAD_INCOMPLETE_EXIT = 2
DOWNLOAD_TIMEOUT_S = 900


class ArtifactsIncomplete(SoftmaxCliError):
    """Some requested evidence is still pending or unavailable; poll again later."""


class ArtifactVerificationError(SoftmaxCliError):
    """A downloaded file is missing or does not match its receipt."""


def tag(experiment: str, batch: int, attempt: int) -> str:
    return f"ar:{experiment}:b{batch}:a{attempt}"


def parse_tag(title: str | None) -> tuple[str, int, int] | None:
    match = TAG_PATTERN.fullmatch(title or "")
    if match is None:
        return None
    return match["experiment"], int(match["batch"]), int(match["attempt"])


def request_body(experiment: str, batch: int, attempt: int, spec: dict) -> dict:
    """The ``V2CreateExperienceRequestRequest`` body for ``spec``.

    ``spec`` keys: ``league_id``, ``num_episodes``, ``roster`` (the platform's roster entries, e.g.
    ``{"player": {"policy_ref": "name:v3"}, "slot": 0}``), and optionally ``description``,
    ``notes``, ``variant_id``, ``episode_player_llm_spend_limit_usd``.
    """
    missing = [key for key in REQUIRED_SPEC if key not in spec]
    unknown = sorted(set(spec) - set(REQUIRED_SPEC) - set(OPTIONAL_SPEC))
    if missing or unknown:
        raise ValueError(f"XP spec: missing {missing}, unknown {unknown}")
    target = {"league_id": spec["league_id"]}
    if "variant_id" in spec:
        target["variant_id"] = spec["variant_id"]
    request_tag = tag(experiment, batch, attempt)
    body = {
        "target": target,
        "roster": spec["roster"],
        "num_episodes": int(spec["num_episodes"]),
        "private": True,
        "title": request_tag,
        "idempotency_key": request_tag,
    }
    for key in ("description", "notes", "episode_player_llm_spend_limit_usd"):
        if key in spec:
            body[key] = spec[key]
    return body


def create(
    cli: PlatformCli, experiment: str, batch: int, attempt: int, spec: dict
) -> XpHandle | CreditDenied:
    """Create the request. Callers check identity and run ``recover`` first (see SoftmaxClient).

    The tag is also the idempotency key, so the platform never creates a second request for the
    same (experiment, batch, attempt), even after a failed or cancelled one: to retry, the caller
    bumps ``attempt``.
    """
    body = request_body(experiment, batch, attempt, spec)
    args = ["xp-request", "create", "-", "--json"]
    result = cli.run("coworld", args, input=json.dumps(body), ok_codes=(0, 1))
    if result.returncode != 0:
        if CREDIT_DENIAL.search(result.stderr or ""):
            return CreditDenied(reason=_error_detail(result.stderr))
        raise SoftmaxCliError(describe_failure("coworld", args, result))
    try:
        request_id = json.loads(result.stdout)["id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise SoftmaxCliError(
            f"xp-request create printed no request id: {result.stdout.strip()[-500:]!r}"
        ) from None
    return XpHandle(request_id, experiment, batch, attempt)


def _error_detail(stderr: str) -> str:
    first = next((line for line in stderr.splitlines() if line.strip()), stderr)
    return first.removeprefix("Error: ").split(": ", 1)[-1].strip()


def recover(cli: PlatformCli, experiment: str) -> list[XpHandle]:
    """Every existing request tagged for ``experiment``, ordered by (batch, attempt)."""
    args = ["xp-request", "list", "--mine", "--limit", str(PAGE_LIMIT)]
    rows = cli.run_json_pages("coworld", args)
    handles = []
    for row in rows:
        parsed = parse_tag(row.get("title"))
        if parsed is not None and parsed[0] == experiment:
            handles.append(XpHandle(row["id"], experiment, parsed[1], parsed[2]))
    return sorted(handles, key=lambda handle: (handle.batch, handle.attempt, handle.request_id))


def status(cli: PlatformCli, handle: XpHandle) -> XpStatus:
    """Map the platform status; an unknown one stays non-terminal and carries a warning."""
    detail = cli.run_json("coworld", ["xp-request", "get", handle.request_id])
    raw_status = str(detail.get("status"))
    state = STATE_BY_SERVER_STATUS.get(raw_status)
    warning = None
    if state is None:
        state = UNKNOWN_STATUS_STATE
        warning = f"unknown XP request status {raw_status!r}; treated as {state}"
    return XpStatus(
        state,
        int(detail["completed_count"]),
        int(detail["episode_count"]),
        raw_status=raw_status,
        warning=warning,
    )


def artifacts(cli: PlatformCli, handle: XpHandle, dest: Path) -> list[Path]:
    """Download results and replays into ``dest`` and verify each file against its receipt."""
    dest = Path(dest)
    args = ["xp-request", "download", handle.request_id]
    args += ["--include", "results", "--include", "replay", "-d", str(dest)]
    result = cli.run(
        "coworld", args, ok_codes=(0, DOWNLOAD_INCOMPLETE_EXIT), timeout=DOWNLOAD_TIMEOUT_S
    )
    manifest = dest / "manifest.jsonl"
    if not manifest.exists():
        raise ArtifactVerificationError(f"XP {handle.request_id} download wrote no {manifest}")
    receipts = latest_receipts(manifest)
    waiting = sorted(r.get("filename") or r.get("id") for r in receipts if not _completed(r))
    if result.returncode == DOWNLOAD_INCOMPLETE_EXIT or waiting:
        raise ArtifactsIncomplete(f"XP {handle.request_id} evidence not ready: {waiting}")
    completed = [receipt for receipt in receipts if _completed(receipt)]
    if not completed:
        raise ArtifactVerificationError(f"XP {handle.request_id} download produced no artifacts")
    return sorted(verify_receipt(dest, receipt) for receipt in completed)


def _completed(receipt: dict) -> bool:
    return receipt.get("kind") == "artifact" and receipt.get("state") == "completed"


def latest_receipts(manifest: Path) -> list[dict]:
    """The last receipt per artifact (or unavailable selector) in the append-only log."""
    latest: dict[str, dict] = {}
    for number, line in enumerate(manifest.read_text().splitlines(), start=1):
        if line.strip():
            row = json.loads(line)
            key = row.get("artifact_id") or row.get("id")
            if not key:
                raise ArtifactVerificationError(f"{manifest}:{number} has no artifact_id or id")
            latest[key] = row
    return list(latest.values())


def verify_receipt(dest: Path, receipt: dict) -> Path:
    path = dest / receipt["path"]
    if not path.resolve().is_relative_to(dest.resolve()):
        raise ArtifactVerificationError(f"receipt path escapes {dest}: {receipt['path']!r}")
    if not path.is_file():
        raise ArtifactVerificationError(f"downloaded file missing: {receipt['path']}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != receipt["sha256"] or path.stat().st_size != receipt["size"]:
        raise ArtifactVerificationError(
            f"sha256/size mismatch for {receipt['path']}: receipt {receipt['sha256']} "
            f"({receipt['size']} B), file {digest} ({path.stat().st_size} B)"
        )
    return path
