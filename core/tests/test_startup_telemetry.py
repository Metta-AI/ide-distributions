"""The packaged check validates real SDK output, with no private timing event format."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import set_span_in_context

CHECK = Path(__file__).resolve().parents[2] / "image/system_tests/check-startup.py"


@pytest.mark.parametrize(
    "fault", [None, "missing", "reused_boot", "secret", "negative", "state", "parent"]
)
def test_packaged_startup_log_validation(fault):
    events = []
    for boot in range(2):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "workspace.id": "00000000-0000-4000-8000-000000000149",
                    "service.instance.id": str(boot),
                }
            )
        )
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("proof")
        root = tracer.start_span(
            "workspace.startup",
            attributes={
                "workspace.state": "fresh" if boot == 0 else "retained",
                "startup.complete": True,
            },
        )
        for role in ("orchestrator", "researcher"):
            for phase in ("profile_install", "profile_prepare"):
                tracer.start_span(
                    f"workspace.{phase}",
                    context=set_span_in_context(root),
                    attributes={"role": role},
                ).end()
        for phase in ("gateway_ready", "application_wake", "application_ready"):
            tracer.start_span(f"workspace.{phase}", context=set_span_in_context(root)).end()
        root.end()
        events.extend(json.loads(s.to_json()) for s in exporter.get_finished_spans())
        provider.shutdown()
    if fault == "missing":
        events.pop(0)
    if fault == "reused_boot":
        for event in events:
            event["resource"]["attributes"]["service.instance.id"] = "same"
    if fault == "secret":
        events[0]["attributes"]["credential"] = "must-not-log"
    if fault == "negative":
        events[0]["end_time"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    if fault == "state":
        events[-1]["attributes"]["workspace.state"] = "fresh"
    if fault == "parent":
        events[0]["parent_id"] = "0x0000000000000001"
    log = "Gateway noise\n" + "\n".join(json.dumps(e) for e in events)
    result = subprocess.run(
        [sys.executable, str(CHECK), "2"], input=log, text=True, capture_output=True
    )
    assert (result.returncode == 0) == (fault is None), result.stderr
