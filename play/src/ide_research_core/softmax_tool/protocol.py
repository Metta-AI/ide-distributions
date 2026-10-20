"""The Softmax platform boundary: the only way core code touches Softmax."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class Identity:
    subject_type: str  # "player" or "user"; only "player" credentials are allowed (C6)
    player_id: str
    expires_at: datetime | None  # None: the credential does not expire (a cga_ agent token)


@dataclass(frozen=True)
class XpHandle:
    """One XP request. ``seed`` and ``arm`` (``"candidate"`` or ``"baseline"``) are set for a
    seeded request of a paired evaluation (A12: one request per seed and arm); they are None
    for a request tagged without them."""

    request_id: str
    experiment: str
    batch: int
    attempt: int
    seed: int | None = None
    arm: str | None = None


@dataclass(frozen=True)
class CreditDenied:
    reason: str


# The refusal's state, from the server's ``detail.reason`` (metta
# ``observatory_core.cogents.permits.check_permit``): the ask waits for the coach (also after an
# outgrown or used yes, when the server files a new ask), the coach said no, or the act is more
# than the credits left today, which no yes covers until the UTC day turns.
PERMIT_WAITING, PERMIT_DECLINED, PERMIT_OVER_CAP = "waiting", "declined", "over_cap"
# ``Softmax.permit_answer``: the coach's answer to one ask.
ANSWER_YES, ANSWER_NO, ANSWER_USED, ANSWER_WAITING = "yes", "no", "used", "waiting"
# The retry timing shared by XP relaunches (credit denials, the coach's refusals) and promotion
# submits the coach refused (A10): ``BACKOFF_BASE`` first (XP: or the worker's configured base),
# doubling per refusal, at most ``BACKOFF_CAP`` (``retry_backoff``).
BACKOFF_BASE = timedelta(minutes=30)
BACKOFF_CAP = timedelta(hours=8)
# An ask still unanswered this long after the coach was first asked gets one reminder, never more.
REMINDER_AFTER = timedelta(hours=24)


def retry_backoff(count: int, base: timedelta = BACKOFF_BASE) -> timedelta:
    """The wait after the ``count``-th consecutive refusal or denial: ``base``, doubling,
    at most ``BACKOFF_CAP``."""
    return min(base * 2 ** (count - 1), BACKOFF_CAP)


@dataclass(frozen=True)
class PermitRequired:
    """The server refused the act until the coach answers a permit ask (HTTP 403 with detail
    ``type: permit_required``): on Softmax hosting the coach's IDE Controls can ask before XP
    requests and league submissions (``askBefore``). ``reason`` is the server's message;
    ``ask`` the ask's record key (``asks/<ask>``) when the CLI output carried it; ``state`` one
    of ``PERMIT_WAITING``, ``PERMIT_DECLINED``, ``PERMIT_OVER_CAP``. The server files no second
    ask while one waits: the same act asked again names the same ask."""

    reason: str
    ask: str | None = None
    state: str = PERMIT_WAITING


@dataclass(frozen=True)
class LeagueNotChosen:
    """The server refused a league-scoped act (an XP request in a league, a league
    submission) because the coach has chosen no league for this player, or chose another one
    (HTTP 403 with the league-choice detail, metta ``observatory_core.cogents.league``).
    ``reason`` is the server's words."""

    reason: str


# A refusal the coach resolves in the IDE Controls (approve the ask, or choose the league).
CoachRefusal = PermitRequired | LeagueNotChosen


@dataclass(frozen=True)
class SubmissionState:
    """Where one of our submissions stands in a league (A9), read from our own league
    memberships, else our submissions list:

    - ``pending``: submitted and not yet admitted (submission ``pending``/``processing``, or a
      membership ``submitted``/``qualifying``, including substatus ``held``);
    - ``competing``: admitted; ``is_champion`` says whether it is our champion (an admitted
      membership becomes champion by default and earlier ones are ``benched``);
    - ``rejected``: the submission was rejected or withdrawn, or the membership disqualified;
    - ``unknown``: neither list names it, or it has a status we do not know (the promotion
      stays ``submitted`` and is read again).

    ``detail`` is the platform's status and substatus, for messages and logs."""

    stage: Literal["pending", "competing", "rejected", "unknown"]
    is_champion: bool = False
    detail: str = ""


@dataclass(frozen=True)
class XpStatus:
    state: Literal["pending", "running", "completed", "failed"]
    episodes_completed: int
    episodes_total: int
    raw_status: str | None = None  # the platform's own status string, when there is one
    warning: str | None = None  # set when the platform status was not recognised


class Refused(RuntimeError):
    """A softmax tool guard refused an action (upload, submit). ``reason`` is a stable
    machine-readable code (for example ``stale_outcome``); the message says why."""

    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}

    def to_dict(self) -> dict:
        data = {"reason": self.reason, "message": str(self)}
        if self.details:
            data["details"] = self.details
        return data


class Softmax(Protocol):
    def whoami(self) -> Identity: ...
    def credits(self) -> float:
        """The XP credit balance the Player spends (the owning account's). Raises when it
        cannot be read."""
        ...

    def episode_cost(self) -> float | None:
        """Credits one XP episode has cost lately (our recent requests), or None when there
        is nothing to go by. Raises when it cannot be read."""
        ...

    def game_version(self, league_id: str) -> str:
        """The league's current game version (a dotted number such as ``2026.10.7.2``),
        stripped of surrounding whitespace. Every implementation normalizes it here."""
        ...

    def game_source_pin(self, league_id: str) -> str | None:
        """The game source commit (full sha) the league's current Coworld declares, or None
        when the manifest does not pin one."""
        ...

    def xp_create(
        self,
        experiment: str,
        batch: int,
        attempt: int,
        spec: dict,
        seed: int | None = None,
        arm: str | None = None,
        known: list[XpHandle] | None = None,
    ) -> XpHandle | CreditDenied | PermitRequired | LeagueNotChosen: ...
    def xp_status(self, handle: XpHandle) -> XpStatus: ...
    def permit_answer(self, ask: str) -> str | None:
        """The coach's answer to our permit ask ``ask`` (a key of our cogent's ``asks``):
        ``ANSWER_YES`` (not used yet), ``ANSWER_USED`` (a yes an act consumed), ``ANSWER_NO``,
        or ``ANSWER_WAITING``; None when it cannot be read (callers fall back to a backoff)."""
        ...

    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]: ...
    def xp_recover(self, experiment: str) -> list[XpHandle]: ...
    def upload(self, artifact: Path, name: str) -> str: ...
    def submit(
        self, policy_version_id: str, league_id: str
    ) -> str | PermitRequired | LeagueNotChosen:
        """The submission id, or the coach's refusal (nothing was submitted)."""
        ...

    def submissions(self, player_id: str) -> list[dict]: ...
    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None: ...
    def submission_state(
        self, player_id: str, league_id: str, policy_version_id: str
    ) -> SubmissionState:
        """Where our submission of ``policy_version_id`` stands in the league (read-only)."""
        ...

    def league_episodes(self, league_id: str, limit: int) -> Iterable[dict]:
        """Up to ``limit`` completed league episodes, read-only, in listing order (the real
        client yields them as they arrive). Each is a dict::

            {episode_id, round_id, seed, game_version, coworld_id,
             seats: [{position, policy_version_id, player_id, is_filler}],
             results: <the game's raw results.json object>}

        ``policy_version_id`` is the opaque ``<name>:v<N>`` label (None for a human seat).
        """
        ...

    def standings(self, league_id: str) -> list[dict]:
        """League standings, read-only: one row per (division, player)::

        {division_id, division_name, level, rank, player_id, player_name, score,
         rounds_played}
        """
        ...


def read_credits(sm: Softmax) -> float | None:
    """``sm.credits()``, or None when the balance cannot be read (A10: callers then fall back
    to the credit-denial backoff)."""
    try:
        return sm.credits()
    except Exception:  # noqa: BLE001 - any read failure only means "unknown"
        return None


def from_env(env: Mapping[str, str], canonical: Path) -> Softmax:
    """RESEARCH_SOFTMAX=fake selects the persistent fake; anything else is the real client."""
    if env.get("RESEARCH_SOFTMAX") == "fake":
        from ide_research_core.softmax_tool.fake import FakeSoftmax

        return FakeSoftmax(canonical, env)
    from ide_research_core.softmax_tool.client import SoftmaxClient

    return SoftmaxClient(env)
