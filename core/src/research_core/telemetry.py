"""Payload-free OpenTelemetry for both workspace and native Hermes interpreters.

The SDK owns clocks, IDs, context and export. Console JSON is always available in process
logs; standard OTLP/HTTP settings add a backend without changing instrumentation.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_provider: TracerProvider | None = None
_lock = threading.Lock()
propagator = TraceContextTextMapPropagator()


def tracer() -> trace.Tracer:
    global _provider
    with _lock:
        if _provider is None:
            _provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": os.environ.get("OTEL_SERVICE_NAME", "ide-distributions"),
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
            )
            _provider.add_span_processor(
                SimpleSpanProcessor(
                    ConsoleSpanExporter(
                        out=sys.stderr, formatter=lambda span: span.to_json(indent=None) + "\n"
                    )
                )
            )
            if "OTEL_EXPORTER_OTLP_ENDPOINT" in os.environ or (
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in os.environ
            ):
                _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
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
