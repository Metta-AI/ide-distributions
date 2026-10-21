"""Hosted Experience Requests (XP) through ``coworld xp-request`` (coworld 0.1.57).

Tagging: coworld 0.1.57 has no tag or metadata field on an XP request. The request body's
``title`` carries the tag ``ar:<experiment>:b<batch>:a<attempt>:s<seed>:<arm>`` (experiment ids
are ULIDs, so the tag is globally unique; ``:s<seed>:<arm>`` is present for the seeded requests
of a paired evaluation and absent on older untagged-seed requests), and ``recover`` finds requests by parsing titles from
``xp-request list --mine``. The same tag is sent as ``idempotency_key``: the server returns the
existing request for a repeated key instead of creating a second one (observatory-api
``v2/experience_requests.py``), so even a create retried after a lost response cannot duplicate.

Credit denial: the server answers 402 when the owning account's credit allowance cannot admit the
request; the CLI prints ``Error: Request failed (402) for /v2/experience-requests: <detail>`` on
stderr and exits 1. That becomes ``CreditDenied``.

The coach's refusals (Softmax hosting, a ``cga_`` agent token): the server answers 403 when the
coach's IDE Controls ask before XP requests (a permit ask, ``PermitRequired``) or when the
request's league is not the one the coach chose (``LeagueNotChosen``); see
``platform_cli.coach_refusal`` for what the CLI prints. The server checks credits (402) first,
then the league choice, then the permit. The request is not created. Retrying the same
(experiment, batch, attempt, seed, arm) sends the same body and idempotency key, so the server
decides it against the same permit ask instead of filing a new one.

Artifacts: ``xp-request download`` writes each file under ``<dest>/<sha256(artifact:version)>/``
and appends a receipt per file to ``<dest>/manifest.jsonl`` with ``size`` and ``sha256``. The CLI
computes ``sha256`` while streaming and checks ``size`` against the server's declared size; the
server publishes no content hash of its own (``version`` is not one). We verify every file on disk
against the latest receipt for it.

Seeds (A12): the body's ``game_config_overrides`` (validated by the server against the Coworld
config schema; GotA has a top-level integer ``seed``) with ``{"seed": s}`` pins EVERY episode
of the request to seed ``s`` (observatory-api ``v2/experience_requests.py``). Without it each
episode gets a seed derived from (request id, job index). There is no per-episode seed list, so
a paired evaluation sends one request per (seed, arm). Roster ``random``/``top_n`` selectors are
sampled per request, so only fixed ``policy_ref`` opponents are identical across a pair.
"""

import hashlib
import json
import re
from pathlib import Path

from ide_research_core.softmax_tool.platform_cli import (
    PAGE_LIMIT,
    PlatformCli,
    SoftmaxCliError,
    coach_refusal,
    describe_failure,
)
from ide_research_core.softmax_tool.protocol import (
    CreditDenied,
    LeagueNotChosen,
    PermitRequired,
    XpHandle,
    XpStatus,
)

ARMS = ("candidate", "baseline")
TAG_PATTERN = re.compile(
    r"ar:(?P<experiment>[^:\s]+):b(?P<batch>\d+):a(?P<attempt>\d+)"
    r"(?::s(?P<seed>-?\d+):(?P<arm>candidate|baseline))?"
)
CREDIT_DENIAL = re.compile(r"\(402\)|HTTP 402")
REQUIRED_SPEC = ("league_id", "num_episodes", "roster")
OPTIONAL_SPEC = (
    "description",
    "notes",
    "variant_id",
    "episode_player_llm_spend_limit_usd",
    "game_config_overrides",
)
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


def tag(
    experiment: str, batch: int, attempt: int, seed: int | None = None, arm: str | None = None
) -> str:
    base = f"ar:{experiment}:b{batch}:a{attempt}"
    return base if seed is None else f"{base}:s{seed}:{arm}"


def parse_tag(title: str | None) -> tuple[str, int, int, int | None, str | None] | None:
    """(experiment, batch, attempt, seed, arm); seed and arm are None for an unseeded tag."""
    match = TAG_PATTERN.fullmatch(title or "")
    if match is None:
        return None
    seed = None if match["seed"] is None else int(match["seed"])
    return match["experiment"], int(match["batch"]), int(match["attempt"]), seed, match["arm"]


def _check_seed(spec: dict, seed: int | None, arm: str | None) -> None:
    """A seeded request names its arm and its spec pins exactly that seed (the tag must say
    what the request runs)."""
    if seed is None:
        return
    if arm not in ARMS:
        raise ValueError(f"a seeded XP request needs arm in {ARMS}, got {arm!r}")
    pinned = (spec.get("game_config_overrides") or {}).get("seed")
    if pinned != seed:
        raise ValueError(f"XP spec game_config_overrides.seed is {pinned!r}, tag seed is {seed}")


def request_body(
    experiment: str,
    batch: int,
    attempt: int,
    spec: dict,
    seed: int | None = None,
    arm: str | None = None,
) -> dict:
    """The ``V2CreateExperienceRequestRequest`` body for ``spec``.

    ``spec`` keys: ``league_id``, ``num_episodes``, ``roster`` (the platform's roster entries, e.g.
    ``{"player": {"policy_ref": "name:v3"}, "slot": 0}``), and optionally ``description``,
    ``notes``, ``variant_id``, ``episode_player_llm_spend_limit_usd``,
    ``game_config_overrides`` (e.g. ``{"seed": 7}``; must match ``seed`` when it is given).
    """
    missing = [key for key in REQUIRED_SPEC if key not in spec]
    unknown = sorted(set(spec) - set(REQUIRED_SPEC) - set(OPTIONAL_SPEC))
    if missing or unknown:
        raise ValueError(f"XP spec: missing {missing}, unknown {unknown}")
    _check_seed(spec, seed, arm)
    target = {"league_id": spec["league_id"]}
    if "variant_id" in spec:
        target["variant_id"] = spec["variant_id"]
    request_tag = tag(experiment, batch, attempt, seed, arm)
    body = {
        "target": target,
        "roster": spec["roster"],
        "num_episodes": int(spec["num_episodes"]),
        "private": True,
        "title": request_tag,
        "idempotency_key": request_tag,
    }
    for key in (
        "description",
        "notes",
        "episode_player_llm_spend_limit_usd",
        "game_config_overrides",
    ):
        if key in spec:
            body[key] = spec[key]
    return body


def create(
    cli: PlatformCli,
    experiment: str,
    batch: int,
    attempt: int,
    spec: dict,
    seed: int | None = None,
    arm: str | None = None,
) -> XpHandle | CreditDenied | PermitRequired | LeagueNotChosen:
    """Create the request. Callers check identity and run ``recover`` first (see SoftmaxClient).

    The tag is also the idempotency key, so the platform never creates a second request for the
    same (experiment, batch, attempt, seed, arm), even after a failed or cancelled one: to retry,
    the caller bumps ``attempt``.
    """
    body = request_body(experiment, batch, attempt, spec, seed, arm)
    args = ["xp-request", "create", "-", "--json"]
    result = cli.run("coworld", args, input=json.dumps(body), ok_codes=(0, 1))
    if result.returncode != 0:
        if CREDIT_DENIAL.search(result.stderr or ""):
            return CreditDenied(reason=_error_detail(result.stderr))
        refusal = coach_refusal(result.stderr or "")
        if refusal is not None:
            return refusal
        raise SoftmaxCliError(describe_failure("coworld", args, result))
    try:
        request_id = json.loads(result.stdout)["id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise SoftmaxCliError(
            f"xp-request create printed no request id: {result.stdout.strip()[-500:]!r}"
        ) from None
    return XpHandle(request_id, experiment, batch, attempt, seed, arm)


def _error_detail(stderr: str) -> str:
    first = next((line for line in stderr.splitlines() if line.strip()), stderr)
    return first.removeprefix("Error: ").split(": ", 1)[-1].strip()


def recover(cli: PlatformCli, experiment: str) -> list[XpHandle]:
    """Every existing request tagged for ``experiment``, ordered by (batch, attempt, seed, arm)."""
    args = ["xp-request", "list", "--mine", "--limit", str(PAGE_LIMIT)]
    rows = cli.run_json_pages("coworld", args)
    handles = []
    for row in rows:
        parsed = parse_tag(row.get("title"))
        if parsed is not None and parsed[0] == experiment:
            handles.append(XpHandle(row["id"], *parsed))
    return sorted(handles, key=handle_order)


def handle_order(handle: XpHandle) -> tuple:
    """Sort key: (batch, attempt), unseeded first, then seed, arm, request id."""
    seeded = handle.seed is not None
    return (
        handle.batch,
        handle.attempt,
        seeded,
        handle.seed or 0,
        handle.arm or "",
        handle.request_id,
    )


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
