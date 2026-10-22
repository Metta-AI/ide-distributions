"""Run the pinned ``softmax``/``coworld`` CLIs as the profile's Player, isolated from the host user.

Credential handling (C6, A4). ``SOFTMAX_PLAYER_CREDENTIAL`` holds one of two Player credentials:

- A ``cga_...`` agent token, given bare. The Softmax hosting runtime provisions one per hosted
  player (metta ``packages/observatory-hosted-worker``). The platform resolves it to the
  player's PLAYER principal; it has no expiry and stops working when the agent is revoked or
  the player disabled. It is written as the server's token (``tokens[<server>]``), the same
  shape the hosting runtime writes to its own ``~/.softmax/credentials.yaml``.
- A Player session: JSON ``{"token": "ply_...", "expires_at": "<ISO 8601>"}``, the same shape
  as one entry of ``player_sessions[<server>].cache`` in the file that ``coworld player use
  <ply_id>`` writes. It lasts about 24 hours, and the server's ``/whoami`` does not report the
  expiry, so the credential must carry it for the identity expiry checks. It is written as the
  active player session, with ``tokens`` empty: with no user token in the file, an expired
  session makes the CLI report "Not authenticated" instead of silently acting as a user.

Any other shape (a bare ``ply_`` token, JSON without ``expires_at``) is rejected.

- Before every CLI call the credential is written to
  ``$HERMES_HOME/local/softmax/home/.softmax/credentials.yaml`` (mode 0600) in softmax-cli 0.26.38's
  format (``softmax/auth.py``), keyed by the server.
- Server: ``COGAMES_API_URL`` (what the hosting runtime writes to the profile ``.env``, and
  what softmax-cli itself reads), else ``https://softmax.com/api``; a trailing ``/`` is
  dropped, as the CLIs do. Every call ends with ``--server <server>``, because coworld 0.1.58
  ignores ``COGAMES_API_URL`` and both CLIs look the token up by that server.
- The CLIs run with ``HOME`` pointing at that directory, ``cwd`` inside it, and an environment
  built from scratch, so the host user's ``~/.softmax/credentials.yaml`` is never read or changed
  and the credential env var is not passed on. ``COWORLD_AGENT_GUIDANCE=0`` stops ``upload-policy``
  and ``submit`` from writing AGENTS.md guidance into the working directory.
- The executables are the ones installed next to the running interpreter (the profile venv), never
  a global binary.

``api_get_json`` handles reads without a pinned CLI command: latest player league replay,
permit records, and the XP credit balance
(``GET /observatory/usage/me/credits``, readable with a Player credential since metta #27052,
2026-10-08) has no command in coworld 0.1.58 (``coworld credits`` is merged in metta but not
released). It sends the same credential token as a bearer header to the configured server
(``self.server``, as the CLIs get with ``--server``), with a User-Agent (Cloudflare answers 403
"error code: 1010" to urllib's default one), and never follows a redirect (urllib would copy
the ``Authorization`` header to the redirect target). Replace it with ``coworld credits
--json`` once a coworld release ships that command.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from ide_research_core.io.docs import write_text
from ide_research_core.softmax_tool.protocol import (
    PERMIT_DECLINED,
    PERMIT_OVER_CAP,
    PERMIT_WAITING,
    CoachRefusal,
    LeagueNotChosen,
    PermitRequired,
)

DEFAULT_SERVER = "https://softmax.com/api"
SERVER_ENV = "COGAMES_API_URL"
CREDENTIAL_ENV = "SOFTMAX_PLAYER_CREDENTIAL"
# A registered agent's token: "cga_" + urlsafe base64 (metta observatory_core credential_tokens).
AGENT_TOKEN = re.compile(r"cga_[A-Za-z0-9_\-=]+")
SESSION_KEY = "autoresearch"  # the active-session key in the file; whoami reports the real player
DEFAULT_TIMEOUT_S = 120
PAGE_LIMIT = 200
HTTP_TIMEOUT_S = 30
USER_AGENT = "autoresearch"
# The coach's refusals (``coach_refusal``). A ``permit_required`` 403 carries a JSON object as
# its detail, which coworld's ``_detail`` does not print as one, so it prints the whole body.
PERMIT_DENIAL = re.compile(r"\(403\).*permit_required", re.DOTALL)
PERMIT_BODY = re.compile(r'\{\s*"detail"\s*:')
LEAGUE_REFUSAL = re.compile(
    r"\(403\): (?P<reason>(?:Your coach has not chosen a league|This cogent plays )[^\n]*)"
)

Runner = Callable[..., subprocess.CompletedProcess]
# (url, headers) -> response body text; raises urllib.error.URLError (HTTPError) on failure.
HttpGet = Callable[[str, dict[str, str]], str]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into an HTTPError, so the bearer token never follows one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    with _OPENER.open(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode("utf-8")


class SoftmaxCliError(RuntimeError):
    """A CLI call failed or printed something we could not interpret."""


class IdentityError(RuntimeError):
    """The credential cannot be confirmed as the expected, unexpired Player."""


class ToolMissing(SoftmaxCliError):
    """The pinned ``softmax``/``coworld`` executable is not installed in the profile venv."""


class CredentialError(IdentityError):
    """``SOFTMAX_PLAYER_CREDENTIAL`` is missing or malformed. Messages never include its value.

    An ``IdentityError``, so every call (mutating or not) fails as an identity problem.
    """


@dataclass(frozen=True)
class PlayerCredential:
    token: str = field(repr=False)
    expires_at: datetime | None  # None: a cga_ agent token, which does not expire


def server_from_env(env: Mapping[str, str]) -> str:
    return (env.get(SERVER_ENV) or DEFAULT_SERVER).rstrip("/")


def parse_credential(value: str | None) -> PlayerCredential:
    expected = (
        f"{CREDENTIAL_ENV} must be a cga_ agent token, or JSON "
        '{"token": "ply_...", "expires_at": "<ISO 8601>"}'
    )
    if not value:
        raise CredentialError(f"{CREDENTIAL_ENV} is not set; {expected}")
    value = value.strip()
    if value.startswith("cga_"):
        if not AGENT_TOKEN.fullmatch(value):
            raise CredentialError(f"{CREDENTIAL_ENV} is not a well-formed cga_ agent token")
        return PlayerCredential(token=value, expires_at=None)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise CredentialError(f"{CREDENTIAL_ENV} is not JSON; {expected}") from None
    if not isinstance(data, dict) or not data.get("token") or not data.get("expires_at"):
        raise CredentialError(f"{CREDENTIAL_ENV} lacks token or expires_at; {expected}")
    try:
        expires_at = datetime.fromisoformat(str(data["expires_at"]))
    except ValueError:
        raise CredentialError(f"{CREDENTIAL_ENV} expires_at is not ISO 8601") from None
    if expires_at.tzinfo is None:
        raise CredentialError(f"{CREDENTIAL_ENV} expires_at must include a timezone")
    return PlayerCredential(token=str(data["token"]), expires_at=expires_at)


class PlatformCli:
    """One credential, one isolated home, one injected runner (``subprocess.run`` by default)."""

    def __init__(
        self,
        env: Mapping[str, str],
        run: Runner = subprocess.run,
        bin_dir: Path | None = None,
        http_get: HttpGet | None = None,
    ):
        if not env.get("HERMES_HOME"):
            raise SoftmaxCliError("HERMES_HOME is not set; the softmax tool needs the profile home")
        self._credential_value = env.get(CREDENTIAL_ENV)
        self._certificate_env = {key: value for key, value in env.items() if key == "SSL_CERT_FILE"}
        self._run = run
        self._http_get = http_get or urllib_get
        self._bin_dir = bin_dir or Path(sys.executable).parent
        self.server = server_from_env(env)
        self.home = Path(env["HERMES_HOME"]) / "local" / "softmax" / "home"

    @property
    def credentials_path(self) -> Path:
        return self.home / ".softmax" / "credentials.yaml"

    def install_credential(self) -> PlayerCredential:
        """Parse the credential and (re)write the profile-local credentials file from it."""
        credential = parse_credential(self._credential_value)
        if credential.expires_at is None:
            data = {"tokens": {self.server: credential.token}, "player_sessions": {}}
        else:
            session = {"token": credential.token, "expires_at": credential.expires_at.isoformat()}
            sessions = {"active": SESSION_KEY, "cache": {SESSION_KEY: session}}
            data = {"tokens": {}, "player_sessions": {self.server: sessions}}
        for directory in (self.home, self.credentials_path.parent):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        write_text(self.credentials_path, yaml.safe_dump(data, default_flow_style=False))
        self.credentials_path.chmod(0o600)
        return credential

    def _child_env(self) -> dict[str, str]:
        return {
            **self._certificate_env,
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "NO_COLOR": "1",
            "COLUMNS": "200",
            "COWORLD_AGENT_GUIDANCE": "0",
            "PYTHONIOENCODING": "utf-8",
        }

    def run(
        self,
        tool: str,
        args: Sequence[str],
        *,
        input: str | None = None,
        ok_codes: Sequence[int] = (0,),
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> subprocess.CompletedProcess:
        """Run ``<venv>/bin/<tool> <args> --server <server>`` as the Player; raise unless the
        exit code is in ok_codes. A call that runs past ``timeout`` is a ``SoftmaxCliError``."""
        self.install_credential()
        executable = self._bin_dir / tool
        try:
            result = self._run(
                [str(executable), *args, "--server", self.server],
                input=input,
                capture_output=True,
                text=True,
                env=self._child_env(),
                cwd=str(self.home),
                timeout=timeout,
            )
        except FileNotFoundError:
            raise ToolMissing(
                f"{executable} is not installed (reinstall the profile venv)"
            ) from None
        except subprocess.TimeoutExpired:
            # argv never holds the token (it is only in the credentials file).
            command = " ".join([tool, *args[:3]])
            raise SoftmaxCliError(f"{command} timed out after {timeout:g} s") from None
        if result.returncode not in ok_codes:
            raise SoftmaxCliError(describe_failure(tool, args, result))
        return result

    def run_json(self, tool: str, args: Sequence[str], **kwargs):
        result = self.run(tool, [*args, "--json"], **kwargs)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise SoftmaxCliError(
                f"{tool} {' '.join(args)} printed non-JSON output: {error}"
            ) from None

    def api_get_text(self, path: str) -> str:
        """``GET <self.server><path>`` as the Player (module docstring); the response body.
        Errors never include the token."""
        credential = parse_credential(self._credential_value)
        headers = {"Authorization": f"Bearer {credential.token}", "User-Agent": USER_AGENT}
        try:
            body = self._http_get(f"{self.server}{path}", headers)
        except urllib.error.HTTPError as error:
            raise SoftmaxCliError(f"GET {path} answered HTTP {error.code}") from None
        except (urllib.error.URLError, OSError) as error:
            raise SoftmaxCliError(f"GET {path} failed: {error}") from None
        return body

    def api_get_json(self, path: str):
        body = self.api_get_text(path)
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise SoftmaxCliError(f"GET {path} returned non-JSON: {error}") from None

    def run_json_pages(self, tool: str, args: Sequence[str]) -> list[dict]:
        """Every ``entries`` row of a cursor-paginated ``--json`` listing."""
        entries: list[dict] = []
        cursor: str | None = None
        while True:
            page_args = [*args, "--cursor", cursor] if cursor else list(args)
            page = self.run_json(tool, page_args)
            entries.extend(page["entries"])
            cursor = page.get("next_cursor")
            if not cursor:
                return entries


def coach_refusal(stderr: str) -> CoachRefusal | None:
    """The coach's refusal in a failed ``coworld`` call's stderr, or None for any other failure.

    coworld 0.1.58 (``api_client._raise_for_status``, rendered by softmax-cli's
    ``AgentFriendlyGroup``) prints a 403 as one of:

    - ``Error: Access denied (403): <detail>``, when the detail is a string. The league choice
      (metta ``observatory_core.cogents.league``) is ``Your coach has not chosen a league for
      this cogent yet; ...`` or ``This cogent plays <league>; your coach chose it, and ... is
      refused.``: ``LeagueNotChosen``.
    - ``Error: Access denied (403) for <path>. <response body>. <hint>``, when the detail is an
      object. The permit refusal (``permits.refusal_detail``) is
      ``{"detail": {"type": "permit_required", "message", "reason", "ask": <record>}, ...}``:
      ``PermitRequired`` with its ``message``, the ask's ``key`` and the state its ``reason``
      names (``permit_state``).
    """
    if PERMIT_DENIAL.search(stderr):
        return _permit_required(stderr)
    match = LEAGUE_REFUSAL.search(stderr)
    return None if match is None else LeagueNotChosen(match["reason"].strip())


def _permit_required(stderr: str) -> PermitRequired:
    detail: dict = {}
    found = PERMIT_BODY.search(stderr)
    if found is not None:
        try:
            body, _ = json.JSONDecoder().raw_decode(stderr, found.start())
        except ValueError:
            body = {}
        if isinstance(body, dict) and isinstance(body.get("detail"), dict):
            detail = body["detail"]
    message = detail.get("message")
    if not isinstance(message, str) or not message.strip():
        message = next((line for line in stderr.splitlines() if line.strip()), stderr)
        message = message.removeprefix("Error: ").strip()
    record = detail.get("ask") if isinstance(detail.get("ask"), dict) else {}
    ask = record.get("key")
    why = (record.get("body") or {}).get("why") if isinstance(record.get("body"), dict) else None
    return PermitRequired(
        reason=message.strip(),
        ask=ask if isinstance(ask, str) else None,
        state=permit_state(detail.get("reason"), why),
    )


def permit_state(reason: object, why: object = None) -> str:
    """The refusal's state from the server's ``detail.reason`` and the ask's ``why`` (metta
    ``permits.check_permit``): ``the coach said no; ...`` is declined; ``<n> credits is more than
    the <m> left today`` (a yes that today's cap does not cover) or a new ask whose ``why`` says
    ``a yes will not cover it until the UTC day turns`` is over the cap; anything else (``the ask
    waits for the coach``, a used or outgrown yes, an ask naming another act) waits."""
    reason = reason if isinstance(reason, str) else ""
    if reason.startswith("the coach said no"):
        return PERMIT_DECLINED
    if "left today" in reason or (isinstance(why, str) and "until the UTC day turns" in why):
        return PERMIT_OVER_CAP
    return PERMIT_WAITING


def describe_failure(tool: str, args: Sequence[str], result: subprocess.CompletedProcess) -> str:
    output = (result.stderr or "").strip() or (result.stdout or "").strip()
    return f"{tool} {' '.join(args[:3])} exited {result.returncode}: {output[-2000:]}"
