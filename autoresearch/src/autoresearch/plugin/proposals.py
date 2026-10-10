"""The coach chat's pending inbound proposals, in plugin memory. STDLIB ONLY.

The chat model may only *propose* an inbound message (``propose_inbound``); plugin code sends
it when the coach's own next line is exactly ``confirm P-xxxx`` in the same session
(coach-chat-role.md §2.4, plan 3.7). This store is what makes that safe:

- **Memory only**, so there is no file a research worker could forge. A gateway restart drops
  every proposal; the model proposes again.
- **Bound to the session** that made it: ``take`` and ``cancel`` look only in their own
  session's proposals.
- **Code's words, not the model's.** ``render`` builds the text the coach sees only from the
  payload that will be sent, never from the model's ``summary``: the payload as JSON in a
  fenced code block (a fence longer than any backtick run in it, so no Markdown or HTML in a
  value renders or hides anything), with control and format characters (bidi overrides,
  zero-width) shown as ``\\uXXXX`` escapes. ``show`` hands that text to the
  ``transform_llm_output`` hook, which appends it to the turn's reply, and only then is the
  proposal confirmable. Every pending proposal is appended again to each later reply, so a
  model cannot follow it with a fake rendering of the same id. A turn that ends
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
import unicodedata
from dataclasses import dataclass

EXPIRY_SECONDS = 15 * 60
ID_PATTERN = r"P-[0-9a-f]{4,}"
_ID_BYTES = 3  # six hex digits
# Characters a Markdown or HTML client could hide or reorder: controls, format characters (bidi
# overrides, zero-width), private use, surrogates and line/paragraph separators.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})
_BACKTICKS = re.compile(r"`+")


def visible(text: str) -> str:
    """``text`` with every hidden character but a newline replaced by its ``\\uXXXX`` escape."""
    out = []
    for char in text:
        if char != "\n" and unicodedata.category(char) in _HIDDEN_CATEGORIES:
            code = ord(char)
            out.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
        else:
            out.append(char)
    return "".join(out)


def fenced(text: str, info: str = "") -> str:
    """``text`` in a fenced code block whose fence is longer than any backtick run in it, so
    nothing in it renders as Markdown or HTML or closes the block early."""
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text}\n{fence}"


def render(kind: str, payload: dict, answers: str | None = None) -> str:
    """The payload exactly as it will be sent (JSON, hidden characters escaped) in a code block,
    and for a reply the title of the message it answers (plugin-looked-up), also fenced."""
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    parts = [f"{kind}, exactly as it will be sent:", fenced(visible(raw), "json")]
    hidden = visible(raw) != raw
    if answers is not None:
        title = json.dumps(answers, ensure_ascii=False)
        parts += ["It answers the message titled:", fenced(visible(title))]
        hidden = hidden or visible(title) != title
    if hidden:
        parts.append("Hidden or control characters are shown as \\uXXXX escapes.")
    return "\n".join(parts)


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
        return (
            f"Proposal {self.id}: {self.rendering}\n"
            f"To send exactly this, reply: confirm {self.id}"
        )


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

    def notes(self, session_id: str) -> list[str]:
        """The queued notes, left queued for ``show``."""
        with self._lock:
            return list(self._notes.get(session_id, []))

    def show(self, session_id: str, turn_id: str, now: float) -> list[str]:
        """The lines to append to this turn's reply: queued notes, then the ``line`` of every
        pending proposal, shown or not. A first showing marks it shown (confirmable) in
        ``turn_id``; showing every pending one again keeps the plugin's rendering the last one
        the coach sees for its id."""
        with self._lock:
            lines = self._notes.pop(session_id, [])
            for proposal in self._drop_expired(session_id, now).values():
                if not proposal.shown:
                    proposal.shown_turn = turn_id or "unknown"
                lines.append(proposal.line())
            return lines
