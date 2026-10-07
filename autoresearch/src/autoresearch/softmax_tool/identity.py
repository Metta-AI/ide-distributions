"""Who the credential is, asked fresh on every call (C6: no identity cache).

``softmax status`` (softmax-cli 0.26.38) has no ``--json``; it prints ``key: value`` lines from
``/whoami``, of which we read ``subject_type`` and ``subject_id``. The expiry comes from the
credential itself, because ``/whoami`` does not report it.
"""

from datetime import datetime

from autoresearch.softmax_tool.platform_cli import PlatformCli, SoftmaxCliError
from autoresearch.softmax_tool.protocol import Identity


class IdentityError(RuntimeError):
    """The credential cannot be confirmed as the expected, unexpired Player."""


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
    credential = cli.install_credential()
    try:
        result = cli.run("softmax", ["status"])
    except SoftmaxCliError as error:
        raise IdentityError(f"whoami failed: {error}") from None
    subject_type, subject_id = parse_status(result.stdout)
    return Identity(
        subject_type=subject_type, player_id=subject_id, expires_at=credential.expires_at
    )


def require_player(cli: PlatformCli, expected_player_id: str, now: datetime) -> Identity:
    """``whoami`` and refuse unless it is ``expected_player_id`` as a Player with a live token."""
    if not expected_player_id:
        raise IdentityError("AUTORESEARCH_PLAYER_ID is not set")
    identity = whoami(cli)
    if identity.subject_type != "player":
        raise IdentityError(f"credential is a {identity.subject_type!r}, not a player")
    if identity.player_id != expected_player_id:
        raise IdentityError(
            f"credential player {identity.player_id!r} does not match expected {expected_player_id!r}"
        )
    if identity.expires_at <= now:
        raise IdentityError(f"credential expired at {identity.expires_at.isoformat()}")
    return identity
