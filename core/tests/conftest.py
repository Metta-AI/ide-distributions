"""What every test shares: real SDK spans without networking or process-log noise, and a file for
this test's agent telemetry lines."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from research_core import telemetry
from research_core.agent_telemetry.stream import STREAM_VARIABLE


@pytest.fixture(autouse=True)
def spans(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_provider", provider)
    # ``research_core.cli.main`` moves the root logger's handlers behind a queue for good. Called in
    # this process, that would send later tests' ``caplog`` every record twice.
    monkeypatch.setattr(telemetry, "_logging_started", True)
    yield exporter
    provider.shutdown()


@pytest.fixture(autouse=True)
def stream_file(tmp_path, monkeypatch):
    """Where this test's telemetry lines go, from whichever process writes them."""
    path = tmp_path / "telemetry-stream"
    monkeypatch.setenv(STREAM_VARIABLE, str(path))
    return path
