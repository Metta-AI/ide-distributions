"""Who the credential is, asked fresh on every call (C6: no identity cache).

``softmax status`` (softmax-cli 0.26.38) has no ``--json``; it prints ``key: value`` lines from
``/whoami``, of which we read ``subject_type`` and ``subject_id``. The expiry comes from the
credential itself, because ``/whoami`` does not report it; a ``cga_`` agent token has none
(``expires_at`` None). For a ``cga_`` token the platform reports the agent's player as a
``player`` subject (metta ``observatory_api/auth.py`` ``_cogent_agent_principal``).

A credential the platform does not accept (a revoked agent token, a disabled player, an expired
session, or a token for another server) makes ``softmax status`` exit 1: ``/whoami`` treats an
unknown token as anonymous ("Token is invalid or expired"), and other routes answer 401
(softmax-cli: "returned HTTP 401"; coworld: "(401)"). That raises ``CredentialRejected``, with a
plain explanation and without the CLI's own advice ("Run softmax login" is not our recovery).
Preflight pauses on it at once. Output holding a Python traceback is never a rejection: it is an
uncaught error (connection refused, TLS, read timeout), and softmax-cli's traceback quotes
source lines, including its own "Not authenticated." message. Any other failure (network error, 5xx, unreadable output) is
an ordinary ``IdentityError`` ("whoami failed"): it says nothing about the credential, and
mutating calls still refuse (C6).
"""

import re
from datetime import datetime

from ide_research_core.softmax_tool.platform_cli import (
    IdentityError,
    PlatformCli,
    SoftmaxCliError,
    ToolMissing,
)
from ide_research_core.softmax_tool.protocol import Identity

__all__ = [
    "CredentialRejected",
    "IdentityError",
    "identity_problems",
    "parse_status",
    "require_player",
    "whoami",
]

_REJECTED = re.compile(
    r"invalid or expired|session expired|Not authenticated|Failed to authenticate"
    r"|\(40[13]\)|HTTP 40[13]\b"
)
_TRACEBACK = "Traceback (most recent call last)"
_CLI_ADVICE = re.compile(r"^(Next|Docs|Request id):.*$|\s*Run [^.\n]*\.?", re.MULTILINE)


class CredentialRejected(IdentityError):
    """Softmax answered and refused the credential (module docstring)."""


def _platform_words(error: SoftmaxCliError) -> str:
    """The CLI's failure text without its advice lines, on one line."""
    return " ".join(_CLI_ADVICE.sub("", str(error)).split())


def parse_status(text: str) -> tuple[str, str]:
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    subject_type, subject_id = fields.get("subject_type"), fields.get("subject_id")
    if not subject_type or not subject_id or subject_id == "-":
        raise IdentityError(f"softmax status printed no subject: {text.strip()[-500:]!r}")
    return subject_type, subject_id


def whoami(cli: PlatformCli) -> Identity:
    credential = cli.install_credential()  # CredentialError is an IdentityError
    try:
        result = cli.run("softmax", ["status"])
    except ToolMissing:
        raise  # an installation problem, not an identity one
    except SoftmaxCliError as error:
        text = str(error)
        if _TRACEBACK not in text and _REJECTED.search(text):
            raise CredentialRejected(
                f"Softmax did not accept the player credential at {cli.server}: it may be "
                "revoked, the player disabled, a session expired, or the server wrong. Replace "
                f"SOFTMAX_PLAYER_CREDENTIAL with a current credential, or fix COGAMES_API_URL "
                f"({_platform_words(error)})"
            ) from None
        raise IdentityError(f"whoami failed: {error}") from None
    subject_type, subject_id = parse_status(result.stdout)
    return Identity(
        subject_type=subject_type, player_id=subject_id, expires_at=credential.expires_at
    )


def identity_problems(identity: Identity, expected_player_id: str, now: datetime) -> list[str]:
    """Why ``identity`` may not act as ``expected_player_id`` now; empty when it may (C6).

    The single source of these checks: ``require_player`` and the promotion guards use it.
    """
    problems = []
    if identity.subject_type != "player":
        problems.append(f"credential is a {identity.subject_type!r}, not a player")
    if not expected_player_id:
        problems.append("RESEARCH_PLAYER_ID is not set")
    elif identity.player_id != expected_player_id:
        problems.append(
            f"credential player {identity.player_id!r} does not match expected {expected_player_id!r}"
        )
    if identity.expires_at is not None and identity.expires_at <= now:
        problems.append(f"credential expired at {identity.expires_at.isoformat()}")
    return problems


def require_player(cli: PlatformCli, expected_player_id: str, now: datetime) -> Identity:
    """``whoami`` and refuse unless it is ``expected_player_id`` as a Player with a live token
    (a ``cga_`` agent token never expires)."""
    if not expected_player_id:
        raise IdentityError("RESEARCH_PLAYER_ID is not set")
    identity = whoami(cli)
    problems = identity_problems(identity, expected_player_id, now)
    if problems:
        raise IdentityError("; ".join(problems))
    return identity
