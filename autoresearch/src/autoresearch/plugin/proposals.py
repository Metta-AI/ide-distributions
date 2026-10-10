"""The coach chat's pending inbound proposals, in plugin memory. STDLIB ONLY.

The chat model may only *propose* an inbound message (``propose_inbound``); plugin code sends
it when the coach's own next line is exactly ``confirm P-xxxx`` in the same session
(coach-chat-role.md §2.4, plan 3.7). This store is what makes that safe:

- **Memory only**, so there is no file a research worker could forge. A gateway restart drops
  every proposal; the model proposes again.
- **Bound to the session** that made it: ``take`` and ``cancel`` look only in their own
  session's proposals.
- **Code's words, not the model's.** ``render`` builds the text the coach sees only from the
  payload that will be sent (every field, strings JSON-quoted on one line), never from the
  model's ``summary``. ``show`` hands that text to the ``transform_llm_output`` hook, which
  appends it to the turn's reply, and only then is the proposal confirmable. A turn that ends
  with an empty reply never runs that hook (Hermes v0.21.6 ``agent/turn_finalizer.py``), so
  its proposal stays unshown until a later turn's reply carries it.
- **Taken once**: ``take`` removes the proposal under the lock, so two confirms racing on the
  gateway's thread pool (``gateway/run.py`` runs turns on a ``ThreadPoolExecutor``) send it
  once. A proposal expires ``EXPIRY_SECONDS`` after it was made.

``note`` queues plugin-written lines (``Sent P-xxxx as I-...``) that ``show`` returns with the
renderings, so what was sent also reaches the coach in code's words.
"""

import copy
import json
import re
import secrets
import threading
from dataclasses import dataclass

EXPIRY_SECONDS = 15 * 60
ID_PATTERN = r"P-[0-9a-f]{4,}"
_ID_BYTES = 3  # six hex digits
# Validated enum values (urgency, mode, priority, action) print bare when they look like one.
_BARE = re.compile(r"[A-Za-z0-9_-]{1,40}")
# Per kind: the fields rendered in this order, each with its label (None: the value alone).
_FIELDS = {
    "steer": (("urgency", "urgency"), ("text", None), ("proposed_topic", "proposed topic")),
    "task": (
        ("title", None), ("mode", "mode"), ("priority", "priority"), ("why", "why"),
        ("refs", "refs"),
    ),
    "reply": (("in_reply_to", "to"), ("choice", "choice"), ("text", "text")),
    "control": (("action", "action"),),
}
_ENUMS = {"urgency", "mode", "priority", "action", "in_reply_to"}


def _value(field: str, value) -> str:
    if field in _ENUMS and isinstance(value, str) and _BARE.fullmatch(value):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render(kind: str, payload: dict, answers: str | None = None) -> str:
    """One line naming every field of ``payload``: the known ones in a fixed order, then any
    other field. ``answers`` is the title of the message a reply answers (plugin-looked-up)."""
    parts = [kind]
    known = _FIELDS.get(kind, ())
    for field, label in known:
        if field not in payload or payload[field] is None:
            continue
        text = _value(field, payload[field])
        parts.append(f"{label} {text}" if label else text)
        if field == "in_reply_to" and answers:
            parts.append(f"answering {json.dumps(answers, ensure_ascii=False)}")
    names = {field for field, _ in known}
    parts += [f"{key} {_value(key, payload[key])}" for key in sorted(payload) if key not in names]
    return ", ".join(parts)


@dataclass
class Proposal:
    id: str
    session_id: str
    kind: str
    payload: dict
    summary: str  # the model's words: logged, never shown to the coach as the proposal
    created: float
    rendering: str
    shown_turn: str | None = None  # the turn whose reply carried the rendering

    @property
    def shown(self) -> bool:
        return self.shown_turn is not None

    def expired(self, now: float) -> bool:
        return now - self.created >= EXPIRY_SECONDS

    def line(self) -> str:
        return f"Proposal {self.id}: {self.rendering}. To send exactly this, reply: confirm {self.id}"


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Proposal]] = {}  # session id -> id -> proposal
        self._taken: dict[str, set[str]] = {}  # session id -> ids taken (sent or sending)
        self._notes: dict[str, list[str]] = {}  # session id -> lines for the next reply

    def _drop_expired(self, session_id: str, now: float) -> dict[str, Proposal]:
        pending = self._pending.setdefault(session_id, {})
        for proposal_id in [key for key, item in pending.items() if item.expired(now)]:
            del pending[proposal_id]
        return pending

    def propose(
        self, session_id: str, kind: str, payload: dict, summary: str, now: float,
        answers: str | None = None,
    ) -> Proposal:
        """Store a proposal (a deep copy of ``payload``: exactly what ``take`` returns)."""
        payload = copy.deepcopy(payload)
        with self._lock:
            pending = self._drop_expired(session_id, now)
            taken = self._taken.get(session_id, set())
            proposal_id = f"P-{secrets.token_hex(_ID_BYTES)}"
            while proposal_id in pending or proposal_id in taken:
                proposal_id = f"P-{secrets.token_hex(_ID_BYTES)}"
            proposal = Proposal(
                proposal_id, session_id, kind, payload, summary, now,
                render(kind, payload, answers),
            )
            pending[proposal_id] = proposal
            return proposal

    def take(self, session_id: str, proposal_id: str, now: float) -> Proposal | None:
        """The shown, unexpired proposal ``proposal_id`` of this session, removed so it is
        taken once; None otherwise."""
        with self._lock:
            pending = self._drop_expired(session_id, now)
            proposal = pending.get(proposal_id)
            if proposal is None or not proposal.shown:
                return None
            del pending[proposal_id]
            self._taken.setdefault(session_id, set()).add(proposal_id)
            return proposal

    def was_taken(self, session_id: str, proposal_id: str) -> bool:
        with self._lock:
            return proposal_id in self._taken.get(session_id, set())

    def cancel(self, session_id: str, proposal_id: str) -> bool:
        """Drop a pending proposal of this session (shown or not); True if there was one."""
        with self._lock:
            return self._pending.get(session_id, {}).pop(proposal_id, None) is not None

    def unshown(self, session_id: str, now: float) -> list[Proposal]:
        with self._lock:
            pending = self._drop_expired(session_id, now)
            return [item for item in pending.values() if not item.shown]

    def note(self, session_id: str, line: str) -> None:
        with self._lock:
            self._notes.setdefault(session_id, []).append(line)

    def show(self, session_id: str, turn_id: str, now: float) -> list[str]:
        """The lines to append to this turn's reply: queued notes, then each unshown
        proposal's ``line``, which marks it shown (confirmable) in ``turn_id``."""
        with self._lock:
            lines = self._notes.pop(session_id, [])
            for proposal in self._drop_expired(session_id, now).values():
                if not proposal.shown:
                    proposal.shown_turn = turn_id or "unknown"
                    lines.append(proposal.line())
            return lines
