"""Play's operations on the user's program, run in the profile venv as the profile's Player.

Every call goes through the shared ``softmax_tool`` client: the credential is installed into the
profile-local Softmax home before each CLI call, and every mutating call (upload, practice,
release) first runs ``whoami`` and refuses unless the credential is the Player
``RESEARCH_PLAYER_ID`` (``SoftmaxClient._require_player``). Nothing here keeps research state:
no build directory, version record, experiment or champion.

The coach's refusals come back as data, never as an error and never retried:
``{"outcome": "refused", "refusal": "permit_required" | "credit_denied" | "league_not_chosen",
"reason", "state"?, "ask"?}``. The same call made again sends the same request (practice reuses
its idempotency key; release looks up the league's submissions first), so asking again after the
coach answers is safe.
"""

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from ide_research_core.softmax_tool import xp
from ide_research_core.softmax_tool.client import SoftmaxClient
from ide_research_core.softmax_tool.platform_cli import (
    SoftmaxCliError,
    coach_refusal,
    describe_failure,
)
from ide_research_core.softmax_tool.protocol import (
    CreditDenied,
    LeagueNotChosen,
    PermitRequired,
    XpHandle,
)

from .games import GOTA

SHA256 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
POLICY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*:v[0-9]+")
SEATS = range(10)  # GotA's ten hero seats
MAX_EPISODES = 20
FETCH_TIMEOUT_S = 60
USER_AGENT = "softmax-play"

# url -> response bytes; raises urllib.error.HTTPError / URLError.
Fetch = Callable[[str], bytes]


class PlayError(RuntimeError):
    """A refused or failed operation: ``reason`` is a stable code, ``details`` is safe to show."""

    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}

    @property
    def envelope(self) -> dict:
        error = {"reason": self.reason, "message": str(self)}
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


def fetch_public(url: str) -> bytes:
    """GET without a credential: published player files are anyone's to download."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def refused(refusal: CreditDenied | PermitRequired | LeagueNotChosen) -> dict:
    if isinstance(refusal, CreditDenied):
        return {"outcome": "refused", "refusal": "credit_denied", "reason": refusal.reason}
    if isinstance(refusal, LeagueNotChosen):
        return {"outcome": "refused", "refusal": "league_not_chosen", "reason": refusal.reason}
    return {
        "outcome": "refused",
        "refusal": "permit_required",
        "reason": refusal.reason,
        "state": refusal.state,
        "ask": refusal.ask,
    }


class Program:
    def __init__(
        self,
        env: Mapping[str, str],
        client: SoftmaxClient | None = None,
        fetch: Fetch = fetch_public,
    ):
        if not env.get("PLAY_WORKSPACE"):
            raise PlayError("not_configured", "PLAY_WORKSPACE is not set")
        self.workspace = Path(env["PLAY_WORKSPACE"])
        self.player = env.get("RESEARCH_PLAYER_ID", "")
        self.game = GOTA
        self.client = client or SoftmaxClient(env)
        self._fetch = fetch

    # --- starter --------------------------------------------------------

    def starter_fetch(self, player: str | None = None) -> dict:
        """Download the coworld's starter player file into ``starter/`` and check its sha256."""
        coworld = self.client._league_coworld(self.game.league_id)
        entries = [p for p in (coworld.get("manifest") or {}).get("player") or [] if p.get("file")]
        wanted = player or self.game.starter_player
        entry = next((p for p in entries if p.get("id") == wanted), None)
        if entry is None and player is None and entries:
            entry = entries[0]  # softmax.com's rule: the `baseline` entry, else the first
        if entry is None:
            raise PlayError(
                "starter_not_found",
                f"the coworld has no player file {wanted!r}",
                {"available": [p.get("id") for p in entries]},
            )
        digest = str(entry["file"]).removeprefix("sha256:")
        if not SHA256.fullmatch(digest):
            raise PlayError(
                "starter_unavailable", f"player file is not content-addressed: {entry['file']}"
            )
        url = f"{self.client._cli.server}/observatory/v2/coworlds/{coworld['id']}/player-files/{digest}"
        try:
            data = self._fetch(url)
        except urllib.error.HTTPError as error:
            raise PlayError(
                "starter_unavailable",
                f"GET {url} answered HTTP {error.code}",
                {"url": url, "status": error.code},
            ) from None
        except (urllib.error.URLError, OSError) as error:
            raise PlayError(
                "starter_unavailable", f"GET {url} failed: {error}", {"url": url}
            ) from None
        if sha256(data) != digest:
            raise PlayError(
                "sha256_mismatch",
                "the downloaded bytes do not match the published sha256",
                {"url": url, "expected": digest, "actual": sha256(data)},
            )
        target = self.workspace / "starter" / f"{entry['id']}{self.game.suffix}"
        _write_bytes(target, data)
        return {
            "coworld_id": coworld["id"],
            "player": entry["id"],
            "name": entry.get("name"),
            "sha256": digest,
            "size_bytes": len(data),
            "path": str(target),
        }

    # --- upload ---------------------------------------------------------

    def program_upload(self, path: str, name: str) -> dict:
        """``coworld upload-policy --file`` the program file; a new ``<name>:v<N>`` each call."""
        file = self._program_file(path)
        policy = self._policy_name(name)
        data = file.read_bytes()
        label = self.client.upload(file, policy)  # whoami first
        return {"policy_version": label, "name": policy, "sha256": sha256(data), "path": str(file)}

    def _program_file(self, path: str) -> Path:
        file = Path(path)
        file = (file if file.is_absolute() else self.workspace / file).resolve()
        if not file.is_relative_to(self.workspace.resolve()):
            raise PlayError(
                "bad_arguments", f"{path} is outside the program workspace {self.workspace}"
            )
        if file.suffix != self.game.suffix or not file.is_file():
            raise PlayError("bad_arguments", f"{path} is not a {self.game.suffix} program file")
        return file

    def _policy_name(self, name: str) -> str:
        """The same player-scoped lineage rule as ``softmax_tool.upload.policy_name``."""
        if not self.player:
            raise PlayError("not_configured", "RESEARCH_PLAYER_ID is not set")
        limit = 64 - len(self.player) - 1
        if not NAME.fullmatch(name) or len(name) > limit:
            raise PlayError(
                "bad_arguments",
                f"name must be lowercase words separated by hyphens, at most {limit} characters",
            )
        return f"{name}-{self.player}"

    # --- practice -------------------------------------------------------

    def practice_request(
        self,
        policy_version: str,
        episodes: int = 1,
        seats: list[int] | None = None,
        attempt: int = 1,
    ) -> dict:
        """One private practice request (``POST /v2/experience-requests``); the platform fills
        the other seats from the league."""
        _check_version(policy_version)
        seats = [0] if seats is None else seats
        if not seats or len(set(seats)) != len(seats) or not set(seats) <= set(SEATS):
            raise PlayError("bad_arguments", "seats must be distinct GotA seats 0-9")
        if not 1 <= episodes <= MAX_EPISODES or attempt < 1:
            raise PlayError("bad_arguments", f"episodes must be 1-{MAX_EPISODES}; attempt positive")
        key = f"practice:{policy_version}:a{attempt}"
        body = {
            "target": {"league_id": self.game.league_id},
            "roster": [{"slot": seat, "player": {"policy_ref": policy_version}} for seat in seats],
            "num_episodes": episodes,
            "private": True,
            "title": key,
            "idempotency_key": key,
        }
        self.client._require_player()
        args = ["xp-request", "create", "-", "--json"]
        result = self.client._cli.run("coworld", args, input=json.dumps(body), ok_codes=(0, 1))
        out = {"policy_version": policy_version, "league_id": self.game.league_id}
        if result.returncode != 0:
            stderr = result.stderr or ""
            if xp.CREDIT_DENIAL.search(stderr):
                return {**out, **refused(CreditDenied(reason=xp._error_detail(stderr)))}
            refusal = coach_refusal(stderr)
            if refusal is not None:
                return {**out, **refused(refusal)}
            raise SoftmaxCliError(describe_failure("coworld", args, result))
        try:
            request_id = str(json.loads(result.stdout)["id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SoftmaxCliError("xp-request create printed no request id") from None
        return {
            **out,
            "outcome": "requested",
            "request_id": request_id,
            "episodes": episodes,
            "seats": seats,
        }

    def practice_status(self, request_id: str) -> dict:
        """The request's state; once completed, its results files downloaded into
        ``practice/<request id>/``."""
        if not re.fullmatch(r"xreq_[A-Za-z0-9_-]+", request_id):
            raise PlayError("bad_arguments", "request_id must be an xreq_ id from practice_request")
        handle = XpHandle(request_id, "practice", 1, 0, None, None)
        status = xp.status(self.client._cli, handle)
        out = {
            "request_id": request_id,
            "state": status.state,
            "episodes_completed": status.episodes_completed,
            "episodes_total": status.episodes_total,
            "raw_status": status.raw_status,
            "warning": status.warning,
        }
        if status.state != "completed":
            return out
        dest = self.workspace / "practice" / request_id
        try:
            files = xp.artifacts(self.client._cli, handle, dest)
        except xp.ArtifactsIncomplete as error:
            return {**out, "results": None, "results_pending": str(error)}
        return {**out, "results": [str(path) for path in files if path.name.endswith(".json")]}

    # --- release --------------------------------------------------------

    def program_release(self, policy_version: str) -> dict:
        """``coworld submit <name>:v<N> --league L --player P``, once: an existing submission of
        the same version to the league is reported as released."""
        _check_version(policy_version)
        league = self.game.league_id
        out = {"policy_version": policy_version, "league_id": league}
        self.client._require_player()
        found = [
            row
            for row in self.client.submissions(self.player)
            if row["policy_version_id"] == policy_version and row["league_id"] == league
        ]
        if found:
            return {
                **out,
                "outcome": "released",
                "submission_id": found[-1]["submission_id"],
                "already": True,
            }
        result = self.client.submit(policy_version, league)
        if isinstance(result, (PermitRequired, LeagueNotChosen)):
            return {**out, **refused(result)}
        return {**out, "outcome": "released", "submission_id": result, "already": False}


def _check_version(policy_version: str) -> None:
    if not POLICY_VERSION.fullmatch(policy_version):
        raise PlayError(
            "bad_arguments", "policy_version must be the <name>:v<N> program_upload returned"
        )


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
