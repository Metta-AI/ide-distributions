"""Run the pinned ``softmax``/``coworld`` CLIs as the profile's Player, isolated from the host user.

Credential handling (C6, A4):

- ``SOFTMAX_PLAYER_CREDENTIAL`` is a JSON object ``{"token": "ply_...", "expires_at": "<ISO 8601>"}``,
  the same shape as one entry of ``player_sessions[<server>].cache`` in the file that
  ``coworld player use <ply_id>`` writes. The server's ``/whoami`` does not report token expiry, so
  the credential must carry it for preflight's expiry checks. A bare token is rejected.
- Before every CLI call the credential is written to
  ``$HERMES_HOME/local/softmax/home/.softmax/credentials.yaml`` (mode 0600) in softmax-cli 0.26.38's
  format (``softmax/auth.py``): ``tokens`` (user tokens per server) is left empty and the Player
  token is the active player session. With no user token in the file, an expired player session
  makes the CLI report "Not authenticated" instead of silently acting as a user.
- The CLIs run with ``HOME`` pointing at that directory, ``cwd`` inside it, and an environment
  built from scratch, so the host user's ``~/.softmax/credentials.yaml`` is never read or changed
  and the credential env var is not passed on. ``COWORLD_AGENT_GUIDANCE=0`` stops ``upload-policy``
  and ``submit`` from writing AGENTS.md guidance into the working directory.
- The executables are the ones installed next to the running interpreter (the profile venv), never
  a global binary.

``api_get_json`` is the one read that does not go through a CLI: the XP credit balance
(``GET /observatory/usage/me/credits``, readable with a Player credential since metta #27052,
2026-10-08) has no command in coworld 0.1.58 (``coworld credits`` is merged in metta but not
released). It sends the same Player token as a bearer header, with a User-Agent (Cloudflare
answers 403 "error code: 1010" to urllib's default one). Replace it with ``coworld credits
--json`` once a coworld release ships that command.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from autoresearch.io.docs import write_text

SERVER = "https://softmax.com/api"
CREDENTIAL_ENV = "SOFTMAX_PLAYER_CREDENTIAL"
SESSION_KEY = "autoresearch"  # the active-session key in the file; whoami reports the real player
DEFAULT_TIMEOUT_S = 120
PAGE_LIMIT = 200
HTTP_TIMEOUT_S = 30
USER_AGENT = "autoresearch"

Runner = Callable[..., subprocess.CompletedProcess]
# (url, headers) -> response body text; raises urllib.error.URLError (HTTPError) on failure.
HttpGet = Callable[[str, dict[str, str]], str]


def urllib_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
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
    expires_at: datetime


def parse_credential(value: str | None) -> PlayerCredential:
    expected = f'{CREDENTIAL_ENV} must be JSON {{"token": "ply_...", "expires_at": "<ISO 8601>"}}'
    if not value:
        raise CredentialError(f"{CREDENTIAL_ENV} is not set; {expected}")
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
        self._run = run
        self._http_get = http_get or urllib_get
        self._bin_dir = bin_dir or Path(sys.executable).parent
        self.home = Path(env["HERMES_HOME"]) / "local" / "softmax" / "home"

    @property
    def credentials_path(self) -> Path:
        return self.home / ".softmax" / "credentials.yaml"

    def install_credential(self) -> PlayerCredential:
        """Parse the credential and (re)write the profile-local credentials file from it."""
        credential = parse_credential(self._credential_value)
        session = {"token": credential.token, "expires_at": credential.expires_at.isoformat()}
        data = {
            "tokens": {},
            "player_sessions": {SERVER: {"active": SESSION_KEY, "cache": {SESSION_KEY: session}}},
        }
        for directory in (self.home, self.credentials_path.parent):
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        write_text(self.credentials_path, yaml.safe_dump(data, default_flow_style=False))
        self.credentials_path.chmod(0o600)
        return credential

    def _child_env(self) -> dict[str, str]:
        return {
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
        """Run ``<venv>/bin/<tool> <args>`` as the Player; raise unless the exit code is in ok_codes."""
        self.install_credential()
        executable = self._bin_dir / tool
        try:
            result = self._run(
                [str(executable), *args],
                input=input,
                capture_output=True,
                text=True,
                env=self._child_env(),
                cwd=str(self.home),
                timeout=timeout,
            )
        except FileNotFoundError:
            raise ToolMissing(f"{executable} is not installed (reinstall the profile venv)") from None
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

    def api_get_json(self, path: str):
        """``GET <SERVER><path>`` as the Player (module docstring); the parsed JSON body.
        Errors never include the token."""
        credential = parse_credential(self._credential_value)
        headers = {"Authorization": f"Bearer {credential.token}", "User-Agent": USER_AGENT}
        try:
            body = self._http_get(f"{SERVER}{path}", headers)
        except urllib.error.HTTPError as error:
            raise SoftmaxCliError(f"GET {path} answered HTTP {error.code}") from None
        except (urllib.error.URLError, OSError) as error:
            raise SoftmaxCliError(f"GET {path} failed: {error}") from None
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


def describe_failure(tool: str, args: Sequence[str], result: subprocess.CompletedProcess) -> str:
    output = (result.stderr or "").strip() or (result.stdout or "").strip()
    return f"{tool} {' '.join(args[:3])} exited {result.returncode}: {output[-2000:]}"
