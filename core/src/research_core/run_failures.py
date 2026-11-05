"""Why a chat run failed, and what ``research serve`` does about it.

When a chat message's run ends ``failed``, the Coordinator's one outcome transition
(``coordinator.Coordinator._record_outcome``, reached from the dispatcher and the reconcile
loop alike) calls :func:`classify` and acts on the class:

==========================  ==========================================  ===================
Class                       Hermes reasons (``FailoverReason``)          The admission
==========================  ==========================================  ===================
``budget_exhausted``        ``billing`` (402 credits, 403 key limit)     ``waiting``
``rate_limited``            ``rate_limit``, ``upstream_rate_limit``      ``waiting``
``provider_unavailable``    ``overloaded``, ``server_error``, ``timeout``  ``waiting``
``too_large``               ``context_overflow``, ``payload_too_large``  ``failed``
``misconfigured``           ``auth``, ``auth_permanent``,                ``failed``
                            ``model_not_found``, ``model_entitlement``,
                            ``provider_policy_blocked``
==========================  ==========================================  ===================

A failure with no class stays ``failed`` with Hermes's error text, as before. A ``waiting``
admission resumes on its own (see :func:`resume_after` and :func:`continuation`). Only the
admission that started the run waits; one steered into it ends ``failed`` with the class, since
the resume continues the whole conversation.

The evidence, in order (:func:`classify`):

1. the inference state's ``last_error`` (``research_core.inference``), when it names this
   run and fell inside it, near its end. The profile plugin's ``api_request_error`` hook
   writes it from Hermes's own classifier, so this is Hermes's verdict, not a guess. It names
   the run by ``task_id``, the session id the run started with (the run status's
   ``session_id``); its ``session_id`` is the agent's live id, which compression rotates, so
   it is only a fallback;
2. the inference state's block, while it blocks (``budget_exhausted``/``provider_unavailable``);
3. the run's ``turn_exit_reason`` (Hermes sets it on failed runs that ended in the loop, such
   as ``context_compression_exhausted``). Note: Hermes's ``budget_exhausted`` exit reason is
   the *iteration* budget, not money, and has no class here;
4. the run's error text, for the few unambiguous phrases OpenRouter and Hermes use.

Standard library only, like ``research_core.inference``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from research_core.inference import InferenceState

ErrorClass = Literal[
    "budget_exhausted", "rate_limited", "provider_unavailable", "too_large", "misconfigured"
]

# Classes whose admission waits and resumes; every other class fails.
WAITING_CLASSES: frozenset[str] = frozenset(
    {"budget_exhausted", "rate_limited", "provider_unavailable"}
)

REASON_CLASSES: dict[str, ErrorClass] = {
    "billing": "budget_exhausted",
    "output_limit": "too_large",
    "rate_limit": "rate_limited",
    "upstream_rate_limit": "rate_limited",
    "overloaded": "provider_unavailable",
    "server_error": "provider_unavailable",
    "timeout": "provider_unavailable",
    "context_overflow": "too_large",
    "payload_too_large": "too_large",
    "auth": "misconfigured",
    "auth_permanent": "misconfigured",
    "model_not_found": "misconfigured",
    "model_entitlement": "misconfigured",
    "provider_policy_blocked": "misconfigured",
}

# ``turn_exit_reason`` prefixes (Hermes ``agent/turn_*.py``) that name a class.
EXIT_REASON_CLASSES: tuple[tuple[str, ErrorClass], ...] = (
    ("context_compression", "too_large"),
    ("compaction_handoff_not_actionable", "too_large"),
    ("ollama_runtime_context", "too_large"),
    ("all_retries_exhausted_no_response", "provider_unavailable"),
)


# An HTTP status code in error text counts only after a word that marks it as one, so a bare
# number elsewhere in the text ("500 tokens") does not.
def _status(codes: str) -> str:
    return rf"\b(?:http|status|error|code)\W{{0,3}}(?:{codes})\b"


# Phrases in a failed run's error text, checked in this order.
TEXT_CLASSES: tuple[tuple[re.Pattern[str], ErrorClass], ...] = (
    (
        re.compile(r"key limit exceeded|insufficient credits|credits exhausted|\bbilling\b", re.I),
        "budget_exhausted",
    ),
    (re.compile(r"rate.?limit|too many requests|" + _status("429"), re.I), "rate_limited"),
    (
        re.compile(
            r"context length|context window|maximum context|payload too large|" + _status("413"),
            re.I,
        ),
        "too_large",
    ),
    (
        re.compile(
            _status("401") + r"|unauthori[sz]ed|invalid api key|model not found|no endpoints found",
            re.I,
        ),
        "misconfigured",
    ),
    (
        re.compile(r"overloaded|service unavailable|bad gateway|" + _status("50[0234]"), re.I),
        "provider_unavailable",
    ),
)

# ``last_error`` counts for a run only if it came at most this long before the run ended:
# Hermes ends a run right after the model call it gives up on, while an earlier error that
# Hermes recovered from says nothing about why the run failed later.
LAST_ERROR_WINDOW = timedelta(minutes=2)

# The hold before a waiting admission's first resume, per class. It doubles with each resume
# up to MAX_HOLD, so a lasting rate limit does not resend the whole conversation every minute.
# The inference gate can hold longer: a budget block until a new key, an outage block until
# its ``until``.
MAX_HOLD = timedelta(minutes=30)
HOLDS: dict[str, timedelta] = {
    "budget_exhausted": timedelta(minutes=5),
    "rate_limited": timedelta(minutes=1),
    "provider_unavailable": timedelta(minutes=5),
}

REASON_TEXT: dict[str, str] = {
    "budget_exhausted": "the workspace ran out of model budget",
    "rate_limited": "the model provider was rate-limiting requests",
    "provider_unavailable": "the model provider was unavailable",
}

CONTINUATION_PREFIX = "Your previous run stopped because "


def _run_time(raw: dict, key: str) -> datetime | None:
    """A run status timestamp (Hermes sends epoch seconds) as an aware datetime."""
    value = raw.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    return None


def _from_last_error(
    state: InferenceState, session_id: str, raw: dict, now: datetime
) -> ErrorClass | None:
    error = state.last_error
    if error is None:
        return None
    run_session = raw.get("session_id") or session_id
    names = {run_session, session_id}
    if error.task_id not in names and error.session_id not in names:
        return None
    started = _run_time(raw, "created_at")
    ended = _run_time(raw, "updated_at") or now
    if started is not None and error.at < started:
        return None
    if ended - error.at > LAST_ERROR_WINDOW:
        return None
    return REASON_CLASSES.get(error.reason)


def classify(
    *,
    session_id: str,
    raw: dict,
    error: str | None,
    state: InferenceState,
    installed_key_id: str | None,
    now: datetime,
) -> ErrorClass | None:
    """The class of a failed run, from the evidence in the module docstring; ``None`` if none.

    ``session_id`` is the admission's Session, ``raw`` the run's status payload
    (``RunStatus.raw``), ``error`` its error text, ``installed_key_id`` the lease file's key
    (a budget block holds only while the spent key is still installed).
    """
    found = _from_last_error(state, session_id, raw, now)
    if found is not None:
        return found
    if state.blocking(now, installed_key_id=installed_key_id) and state.blocked is not None:
        return state.blocked
    exit_reason = str(raw.get("turn_exit_reason") or "")
    for prefix, error_class in EXIT_REASON_CLASSES:
        if exit_reason.startswith(prefix):
            return error_class
    for pattern, error_class in TEXT_CLASSES:
        if error and pattern.search(error):
            return error_class
    return None


def hold(error_class: str, resumes: int) -> timedelta:
    """The class's hold after ``resumes`` earlier resumes: doubled per resume, capped."""
    return min(HOLDS[error_class] * 2 ** min(resumes, 16), MAX_HOLD)


def resume_after(
    error_class: str,
    state: InferenceState,
    now: datetime,
    resumes: int = 0,
    *,
    installed_key_id: str | None,
) -> datetime:
    """The earliest time a waiting admission may resume: its :func:`hold`, or the inference
    state's ``until`` while it blocks (the refill time for a budget block), whichever is later.
    The dispatcher also waits for the inference gate, so a budget block still holds past this
    time until a new key arrives."""
    earliest = now + hold(error_class, resumes)
    blocking = state.blocking(now, installed_key_id=installed_key_id)
    if blocking and state.until is not None and state.until > earliest:
        return state.until
    return earliest


def continuation(error_class: str) -> str:
    """The run input that resumes a waiting message: not the user's text again, which would
    repeat the failed run's work and the user's message in the conversation."""
    return (
        f"{CONTINUATION_PREFIX}{REASON_TEXT[error_class]}. Continue the user's last request from "
        "where you stopped; check what you already recorded before repeating any write."
    )
