"""Validate cold and retained startup telemetry from the real image's combined logs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timing import StartupTiming  # noqa: E402

boots: dict[str, list[StartupTiming]] = {}
for line in sys.stdin:
    if not line.startswith('{"event":"workspace_startup"'):
        continue
    data = json.loads(line)
    event = StartupTiming.model_validate(data)
    assert event.workspace_id == "00000000-0000-4000-8000-000000000149", event
    assert event.timestamp.utcoffset().total_seconds() == 0, event
    assert event.elapsed_seconds >= 0, event
    boots.setdefault(event.boot_id, []).append(event)

assert len(boots) == 2, f"Expected distinct cold/restart boot IDs, got {list(boots)}"
phases = [("startup_total", None, "started")]
for role in ("orchestrator", "researcher"):
    for phase in ("profile_install", "profile_prepare"):
        phases.extend((phase, role, outcome) for outcome in ("started", "passed"))
for phase in ("gateway_ready", "application_wake", "application_ready"):
    phases.extend((phase, None, outcome) for outcome in ("started", "passed"))
phases.append(("startup_total", None, "passed"))

for boot_id, events in boots.items():
    assert [(e.phase, e.role, e.outcome) for e in events] == phases, events
    assert [e.timestamp for e in events] == sorted(e.timestamp for e in events), events
    total = events[-1].elapsed_seconds
    assert total >= sum(e.elapsed_seconds for e in events[1:-1]), events
    print(f"PASS startup telemetry: boot={boot_id} events={len(events)} total={total}s")
