"""Payload-free OpenTelemetry for both workspace and native Hermes interpreters.

The SDK owns clocks, IDs, context and export. Bounded standard batch processors keep both
console and remote delivery off functional threads; process exit does not flush telemetry.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from logging.handlers import QueueHandler, QueueListener

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import OTELResourceDetector, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.trace import Span, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_provider: TracerProvider | trace.NoOpTracerProvider | None = None
_lock = threading.Lock()
_logging_started = False
propagator = TraceContextTextMapPropagator()


def configure_logging() -> None:
    """Standard bounded logging queue; slow handlers never run on functional threads."""
    global _logging_started
    if _logging_started:
        return
    # Python logging otherwise flushes/locks every handler at interpreter exit.
    # Telemetry is best effort; exit must not wait for a blocked destination.
    atexit.unregister(logging.shutdown)
    logging.raiseExceptions = False
    root = logging.getLogger()
    destinations = root.handlers or [logging.StreamHandler(sys.stderr)]
    handler = QueueHandler(queue.Queue(maxsize=2048))
    root.handlers = [handler]
    QueueListener(handler.queue, *destinations, respect_handler_level=True).start()
    _logging_started = True


def tracer() -> trace.Tracer:
    global _provider
    with _lock:
        if _provider is None:
            if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
                # The long-lived native caller records the complete subprocess tool call.
                # A one-shot command must not start exporters or flush them at exit.
                _provider = trace.NoOpTracerProvider()
            else:
                try:
                    configure_logging()
                    _provider = TracerProvider(
                        shutdown_on_exit=False,
                        resource=OTELResourceDetector()
                        .detect()
                        .merge(
                            Resource(
                                {
                                    "service.name": os.environ.get(
                                        "OTEL_SERVICE_NAME", "ide-distributions"
                                    ),
                                    **{
                                        key: os.environ[env]
                                        for key, env in (
                                            ("workspace.id", "IDE_WORKSPACE_ID"),
                                            ("service.instance.id", "IDE_RUNTIME_ID"),
                                        )
                                        if env in os.environ
                                    },
                                }
                            )
                        ),
                    )
                    _provider.add_span_processor(
                        BatchSpanProcessor(
                            ConsoleSpanExporter(
                                out=sys.stderr,
                                formatter=lambda span: span.to_json(indent=None) + "\n",
                            )
                        )
                    )
                except Exception:
                    # Configuration/SDK failure disables tracing, never application work.
                    # Do not report through a synchronous log handler on this path.
                    _provider = trace.NoOpTracerProvider()
                if "OTEL_EXPORTER_OTLP_ENDPOINT" in os.environ or (
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in os.environ
                ):
                    try:
                        if isinstance(_provider, TracerProvider):
                            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                    except Exception:
                        # Keep console diagnostics when only the remote exporter is invalid.
                        with _provider.get_tracer("research_core").start_as_current_span(
                            "telemetry.exporter.disabled",
                            record_exception=False,
                        ) as current:
                            current.set_status(StatusCode.ERROR)
                trace.set_tracer_provider(_provider)
        return _provider.get_tracer("research_core")


@contextmanager
def span(name: str, *, context: Context | None = None, **attributes) -> Iterator[Span]:
    """Record safe, explicitly selected attributes; never capture exception text or payloads."""
    with tracer().start_as_current_span(
        name,
        context=context,
        attributes={key: value for key, value in attributes.items() if value is not None},
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        completed = False
        try:
            yield current
            completed = True
        finally:
            if not completed:
                current.set_status(StatusCode.ERROR)


def process_env(env: Mapping[str, str]) -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagator.inject(carrier)
    return {**env, **{key.upper(): value for key, value in carrier.items()}}


def process_context(env: Mapping[str, str]) -> Context:
    return propagator.extract(
        {key.lower(): value for key, value in env.items() if key in {"TRACEPARENT", "TRACESTATE"}}
    )
