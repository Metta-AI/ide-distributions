"""Validate cold and retained startup telemetry from the real image's combined logs."""

import json
import sys
from pathlib import Path
from statistics import median

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

expected_boots = int(sys.argv[1]) if len(sys.argv) > 1 else 2
assert len(boots) == expected_boots, (
    f"Expected {expected_boots} distinct boot IDs, got {list(boots)}"
)
phases = [("startup_total", None, "started")]
for role in ("orchestrator", "researcher"):
    for phase in ("profile_install", "profile_prepare"):
        phases.extend((phase, role, outcome) for outcome in ("started", "passed"))
for phase in ("gateway_ready", "application_wake", "application_ready"):
    phases.extend((phase, None, outcome) for outcome in ("started", "passed"))
phases.append(("startup_total", None, "passed"))

samples: dict[tuple[str, str, str | None], list[float]] = {}
states = []
for boot_id, events in boots.items():
    state = events[0].workspace_state
    states.append(state)
    assert all(e.workspace_state == state for e in events), events
    startup = [e for e in events if e.phase not in {"inference_ready", "relay_connected"}]
    assert [(e.phase, e.role, e.outcome) for e in startup] == phases, events
    for phase in ("inference_ready", "relay_connected"):
        readiness = [e for e in events if e.phase == phase]
        assert [e.outcome for e in readiness] in (["started"], ["started", "passed"]), events
        assert all(e.role is None and e.timestamp >= startup[-1].timestamp for e in readiness), (
            events
        )
    assert [e.timestamp for e in events] == sorted(e.timestamp for e in events), events
    total = startup[-1].elapsed_seconds
    assert total >= sum(e.elapsed_seconds for e in startup[1:-1]), events
    print(f"PASS startup telemetry: boot={boot_id} events={len(events)} total={total}s")

    for event in events:
        if event.outcome == "passed":
            samples.setdefault((state, event.phase, event.role), []).append(event.elapsed_seconds)

assert states == ["fresh", *(["retained"] * (expected_boots - 1))], states
for (state, phase, role), values in samples.items():
    print(
        f"{state} {phase} {role or '-'}: n={len(values)} "
        f"median={median(values):.3f}s min={min(values):.3f}s max={max(values):.3f}s"
    )
