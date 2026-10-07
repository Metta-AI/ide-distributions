"""Who the credential is, asked fresh on every call (C6: no identity cache).

``softmax status`` (softmax-cli 0.26.38) has no ``--json``; it prints ``key: value`` lines from
``/whoami``, of which we read ``subject_type`` and ``subject_id``. The expiry comes from the
credential itself, because ``/whoami`` does not report it.
"""

from datetime import datetime

from autoresearch.softmax_tool.platform_cli import (
    IdentityError,
    PlatformCli,
    SoftmaxCliError,
    ToolMissing,
)
from autoresearch.softmax_tool.protocol import Identity

__all__ = ["IdentityError", "identity_problems", "parse_status", "require_player", "whoami"]


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
        raise IdentityError(f"whoami failed: {error}") from None
    subject_type, subject_id = parse_status(result.stdout)
    return Identity(
        subject_type=subject_type, player_id=subject_id, expires_at=credential.expires_at
    )


def identity_problems(identity: Identity, expected_player_id: str, now: datetime) -> list[str]:
    """Why ``identity`` may not act as ``expected_player_id`` now; empty when it may (C6).

    The single source of these checks: ``require_player`` and preflight both use it.
    """
    problems = []
    if identity.subject_type != "player":
        problems.append(f"credential is a {identity.subject_type!r}, not a player")
    if not expected_player_id:
        problems.append("AUTORESEARCH_PLAYER_ID is not set")
    elif identity.player_id != expected_player_id:
        problems.append(
            f"credential player {identity.player_id!r} does not match expected {expected_player_id!r}"
        )
    if identity.expires_at <= now:
        problems.append(f"credential expired at {identity.expires_at.isoformat()}")
    return problems


def require_player(cli: PlatformCli, expected_player_id: str, now: datetime) -> Identity:
    """``whoami`` and refuse unless it is ``expected_player_id`` as a Player with a live token."""
    if not expected_player_id:
        raise IdentityError("AUTORESEARCH_PLAYER_ID is not set")
    identity = whoami(cli)
    problems = identity_problems(identity, expected_player_id, now)
    if problems:
        raise IdentityError("; ".join(problems))
    return identity
