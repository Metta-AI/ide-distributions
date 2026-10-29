"""Platform errors (design C.4): temporary by default, retried with backoff, then waited on."""

from __future__ import annotations

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class PlatformError(Exception):
    """A platform call failed. ``retryable`` says whether backoff may help."""

    def __init__(
        self,
        detail: str,
        *,
        route: str = "",
        status: int | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
    ):
        self.detail = detail
        self.route = route
        self.status = status
        self.request_id = request_id
        if retryable is None:
            retryable = status is None or status in RETRYABLE_STATUSES
        self.retryable = retryable
        super().__init__(detail)

    def to_dict(self) -> dict:
        return {
            "error": self.detail,
            "route": self.route,
            "status": self.status,
            "request_id": self.request_id,
            "retryable": self.retryable,
        }


class CreditsRefused(PlatformError):
    """The platform refused a paid request for lack of credits (HTTP 402)."""

    def __init__(
        self,
        detail: str,
        *,
        balance: float | None = None,
        held: float | None = None,
        next_refill_at: str | None = None,
        **kwargs,
    ):
        kwargs.setdefault("status", 402)
        kwargs.setdefault("retryable", False)
        super().__init__(detail, **kwargs)
        self.balance = balance
        self.held = held
        self.next_refill_at = next_refill_at

    def to_dict(self) -> dict:
        return {
            **super().to_dict(),
            "balance": self.balance,
            "held": self.held,
            "next_refill_at": self.next_refill_at,
        }


class IdentityUnavailable(PlatformError):
    """An identity the call needs is not provisioned in this workspace (the User identity)."""

    def __init__(self, identity: str = "user", **kwargs):
        kwargs.setdefault("retryable", False)
        super().__init__(
            f"{identity.capitalize()} identity not provisioned for this workspace", **kwargs
        )
        self.identity = identity


class NotAvailable(PlatformError):
    """The platform has no route for this operation (for example a policy file download)."""

    def __init__(self, detail: str, **kwargs):
        kwargs.setdefault("retryable", False)
        super().__init__(detail, **kwargs)
