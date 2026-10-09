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
  expiry, so the credential must carry it for preflight's expiry checks. It is written as the
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
"""
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from autoresearch.io.docs import write_text

DEFAULT_SERVER = "https://softmax.com/api"
SERVER_ENV = "COGAMES_API_URL"
CREDENTIAL_ENV = "SOFTMAX_PLAYER_CREDENTIAL"
# A registered agent's token: "cga_" + urlsafe base64 (metta observatory_core credential_tokens).
AGENT_TOKEN = re.compile(r"cga_[A-Za-z0-9_\-=]+")
SESSION_KEY = "autoresearch"  # the active-session key in the file; whoami reports the real player
DEFAULT_TIMEOUT_S = 120
PAGE_LIMIT = 200

Runner = Callable[..., subprocess.CompletedProcess]


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
        self, env: Mapping[str, str], run: Runner = subprocess.run, bin_dir: Path | None = None
    ):
        if not env.get("HERMES_HOME"):
            raise SoftmaxCliError("HERMES_HOME is not set; the softmax tool needs the profile home")
        self._credential_value = env.get(CREDENTIAL_ENV)
        self._run = run
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
        exit code is in ok_codes."""
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
