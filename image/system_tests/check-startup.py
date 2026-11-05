"""Validate SDK console spans from cold and retained boots of the packaged runtime."""

import json
import sys
from datetime import datetime
from statistics import median

boots: dict[str, list[dict]] = {}
for line in sys.stdin:
    if not line.startswith('{"name": "workspace.'):
        continue
    span = json.loads(line)
    if span["name"] not in {
        "workspace.startup",
        "workspace.profile_install",
        "workspace.profile_prepare",
        "workspace.gateway_ready",
        "workspace.application_wake",
        "workspace.application_ready",
        "workspace.inference_ready",
        "workspace.relay_connected",
    }:
        continue
    resource = span["resource"]["attributes"]
    assert resource["workspace.id"] == "00000000-0000-4000-8000-000000000149", span
    assert set(span["attributes"]) <= {
        "workspace.state",
        "role",
        "startup.complete",
        "readiness.complete",
    }, span
    start, end = (datetime.fromisoformat(span[key]) for key in ("start_time", "end_time"))
    assert end >= start and start.utcoffset().total_seconds() == 0, span
    boots.setdefault(resource["service.instance.id"], []).append(span)

expected_boots = int(sys.argv[1]) if len(sys.argv) > 1 else 2
assert len(boots) == expected_boots, f"Expected {expected_boots} boot IDs, got {list(boots)}"
samples: dict[tuple[str, str, str], list[float]] = {}
states = []
for boot_id, spans in boots.items():
    roots = [s for s in spans if s["name"] == "workspace.startup"]
    assert len(roots) == 1, spans
    root = roots[0]
    assert root["attributes"]["startup.complete"] is True, root
    state = root["attributes"]["workspace.state"]
    states.append(state)
    expected = {
        ("workspace.startup", ""),
        *(
            (f"workspace.profile_{phase}", role)
            for role in ("orchestrator", "researcher")
            for phase in ("install", "prepare")
        ),
        *(
            (f"workspace.{phase}", "")
            for phase in ("gateway_ready", "application_wake", "application_ready")
        ),
    }
    startup = [
        s
        for s in spans
        if s["name"] not in {"workspace.inference_ready", "workspace.relay_connected"}
    ]
    assert len(startup) == len(expected), spans
    assert {(s["name"], s["attributes"].get("role", "")) for s in startup} == expected, spans
    for current in spans:
        assert current["context"]["trace_id"] == root["context"]["trace_id"], current
        if current is not root:
            assert current["parent_id"] == root["context"]["span_id"], current
        if current in startup:
            assert current["status"]["status_code"] != "ERROR", current
            assert (
                root["start_time"]
                <= current["start_time"]
                <= current["end_time"]
                <= root["end_time"]
            )
        seconds = (
            datetime.fromisoformat(current["end_time"])
            - datetime.fromisoformat(current["start_time"])
        ).total_seconds()
        samples.setdefault(
            (state, current["name"], current["attributes"].get("role", "")), []
        ).append(seconds)
    print(f"PASS startup spans: boot={boot_id} state={state} completed={len(spans)}")

assert states == ["fresh", *(["retained"] * (expected_boots - 1))], states
for (state, phase, role), values in samples.items():
    print(f"{state} {phase} {role or '-'}: n={len(values)} median={median(values):.3f}s")
