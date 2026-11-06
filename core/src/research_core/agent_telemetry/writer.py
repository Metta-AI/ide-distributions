"""The capture API: what a callback or a command calls to record something.

A capture pickles what it was given, which makes it the writer's own copy, puts that on one bounded
in-memory queue and returns. So no caller ever waits for the stream, or for logging, which can block
on the same full pipe. One background thread unpickles, redacts, hashes and writes lines to the
container's stream, and so empties the queue by itself. A process about to exit calls ``close``,
which waits for the thread within a bound.

A capture never raises into its caller. One that cannot be pickled or does not fit in the queue is
counted, and the thread logs the count each time it changes. Captures take plain data: strings,
numbers, lists and mappings. A caller holding a provider object turns it into that where it observes
it. Anything else is dropped, at the latest when the thread cannot encode it.
"""

from __future__ import annotations

import logging
import pickle
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from research_core.agent_telemetry.encoder import ContextPart, PromptSection, RecordEncoder
from research_core.agent_telemetry.records import Producer
from research_core.agent_telemetry.redaction import Redactor

logger = logging.getLogger(__name__)

MAX_QUEUED_OBSERVATIONS = 4096
MAX_QUEUED_BYTES = 64 * 1024 * 1024


class TelemetryWriter:
    """One bounded queue and one background writer thread.

    With a ``redactor`` this is the gateway's writer and it takes text too. Without one, text
    captures are dropped and any other string goes through the Softmax token patterns alone.
    """

    def __init__(
        self,
        stream_path: str,
        producer: Producer,
        *,
        redactor: Redactor | None = None,
    ) -> None:
        """Opens nothing here: Hermes gives a plugin's registration only a short time."""
        self._encoder = RecordEncoder(stream_path, producer, redactor)
        self._ready = threading.Condition()
        # An ``Event`` sits in the queue so that the thread reaches it after all captured before it.
        self._queue: deque[tuple[str, float, bytes] | threading.Event] = deque()
        self._queued_bytes = 0
        self._dropped = 0
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telemetry-writer", daemon=True)
        self._thread.start()

    def _capture(self, kind: str, occurred_at: float | None, **payload: Any) -> None:
        occurred = time.time() if occurred_at is None else occurred_at
        try:
            blob = pickle.dumps(payload, 5)
        except Exception:  # A capture must never break the agent's work.
            blob = None
        with self._ready:
            if self._closing.is_set():
                return
            if (
                blob is None
                or len(self._queue) >= MAX_QUEUED_OBSERVATIONS
                or (self._queued_bytes and self._queued_bytes + len(blob) > MAX_QUEUED_BYTES)
            ):
                self._dropped += 1
            else:
                self._queue.append((kind, occurred, blob))
                self._queued_bytes += len(blob)
            self._ready.notify()

    def capture_operation(
        self,
        action: str,
        stage: str,
        *,
        occurred_at: float | None = None,
        content: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        """Record what happened. ``fields`` are the record's fields in the contract, and each event
        lists its own. ``details`` holds numbers and booleans only. ``content`` maps a reference
        name to text or a JSON value: each is stored as an object and named in ``content_refs``.
        """
        self._capture(
            "operation",
            occurred_at,
            action=action,
            stage=stage,
            content=content or {},
            fields=fields,
        )

    def capture_context_parts(
        self,
        target: str,
        parts: Sequence[ContextPart],
        *,
        occurred_at: float | None = None,
        **fields: Any,
    ) -> None:
        """Record where each part sits in a message's text, in bytes of the redacted text.

        A part that cannot be found is reported as ``unattributed``, with no place.
        """
        self._capture("context_parts", occurred_at, target=target, parts=parts, fields=fields)

    def capture_request(
        self,
        request: Mapping[str, Any],
        *,
        session_id: str,
        model_attempt_id: str,
        provider: str,
        prompt_sections: Sequence[PromptSection] = (),
        history_line: str = "main",
        occurred_at: float | None = None,
        **fields: Any,
    ) -> None:
        """Record one model request by reference: its keyword arguments, as the provider gets them.

        This also writes the ``model_attempt`` / ``request`` operation that names the manifest, and
        ``fields`` are that operation's. ``request["messages"]`` must be a list. A message's
        ``_row_id``, if present, becomes its ``hermes_message_ids`` entry. ``history_line``
        separates calls that do not share one history, such as auxiliary calls in a Session.
        """
        self._capture(
            "request",
            occurred_at,
            request=request,
            session_id=session_id,
            model_attempt_id=model_attempt_id,
            provider=provider,
            prompt_sections=prompt_sections,
            history_line=history_line,
            fields=fields,
        )

    def capture_response(
        self,
        message: Mapping[str, Any],
        *,
        session_id: str,
        model_attempt_id: str,
        partial: bool = False,
        occurred_at: float | None = None,
        **fields: Any,
    ) -> None:
        """Record one assistant message. ``partial``: a stream failed part way through.

        This also writes the ``model_attempt`` / ``response`` operation that names the message, and
        ``fields`` are that operation's, for example ``outcome``, ``usage`` and ``finish_reason``.
        """
        self.capture_operation(
            "model_attempt",
            "response",
            occurred_at=occurred_at,
            content={"message": message},
            session_id=session_id,
            model_attempt_id=model_attempt_id,
            partial=partial,
            **fields,
        )

    def flush(self, timeout_s: float) -> bool:
        """Wait at most ``timeout_s`` for the thread to process what is captured so far.

        True proves that the queue was processed up to here, not that anything arrived: a stream
        that could not be opened, a failed write and a dropped record all still count as processed.
        This makes no claim about later captures. Only a process's own exit needs this.
        """
        reached = threading.Event()
        with self._ready:
            if self._closing.is_set():
                return False
            self._queue.append(reached)
            self._ready.notify()
        return reached.wait(timeout_s)

    def close(self, timeout_s: float) -> bool:
        """Flush, then stop the thread. Later captures are ignored."""
        deadline = time.monotonic() + timeout_s
        reached = self.flush(timeout_s)
        with self._ready:
            self._closing.set()
            self._ready.notify()
        self._thread.join(max(0.0, deadline - time.monotonic()))
        return reached

    def _run(self) -> None:
        reported = 0
        while True:
            with self._ready:
                while not (self._queue or self._closing.is_set() or self._dropped != reported):
                    self._ready.wait()
                dropped = self._dropped
                item = self._queue.popleft() if self._queue else None
            if dropped != reported:
                reported = dropped
                logger.warning("telemetry: %d records dropped so far", dropped)
            if isinstance(item, threading.Event):
                item.set()
            elif item is not None:
                self._write(*item)
                with self._ready:
                    self._queued_bytes -= len(item[2])
            elif self._closing.is_set():
                break
        self._encoder.close()

    def _write(self, kind: str, occurred_at: float, blob: bytes) -> None:
        try:
            self._encoder.write(kind, occurred_at, pickle.loads(blob))
        except Exception as error:
            logger.warning("telemetry: dropped a %s record (%s)", kind, type(error).__name__)
