import pytest

from research_core.platform.errors import CreditsRefused, IdentityUnavailable, PlatformError
from research_core.platform.protocol import PolicyVersion, XpRequest, with_retries


def test_retryable_defaults():
    assert PlatformError("x", status=503).retryable is True
    assert PlatformError("x", status=404).retryable is False
    assert PlatformError("x").retryable is True  # a connection error has no status
    assert CreditsRefused("no credits", balance=0.0).retryable is False
    assert IdentityUnavailable("user").retryable is False
    assert "User identity not provisioned" in str(IdentityUnavailable("user"))


def test_with_retries_retries_then_succeeds():
    calls = []
    slept = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise PlatformError("down", status=503, route="GET /x")
        return "ok"

    assert with_retries(fn, sleep=slept.append) == "ok"
    assert len(calls) == 3 and slept == [1.0, 4.0]


def test_with_retries_gives_up_with_the_last_error():
    slept = []

    def fn():
        raise PlatformError("down", status=502, route="GET /x", request_id="r1")

    with pytest.raises(PlatformError) as info:
        with_retries(fn, sleep=slept.append)
    assert info.value.request_id == "r1" and len(slept) == 2


def test_with_retries_does_not_retry_non_retryable():
    calls = []

    def fn():
        calls.append(1)
        raise CreditsRefused("no", balance=1.0)

    with pytest.raises(CreditsRefused):
        with_retries(fn, sleep=lambda s: None)
    assert len(calls) == 1


def test_dataclass_helpers():
    assert PolicyVersion(id="pv", name="best", version=3).label == "best:v3"
    assert XpRequest(id="x", status="completed", episode_count=2).terminal
    assert not XpRequest(id="x", status="running", episode_count=2).terminal
