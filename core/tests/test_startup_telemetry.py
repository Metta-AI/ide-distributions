"""The image smoke must fail for missing, malformed or credential-bearing telemetry."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[2] / "image/system_tests/check-startup.py"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing",
        "reused_boot",
        "secret",
        "negative",
        "json",
        "state",
        "duplicate_ready",
        "premature_ready",
    ],
)
@pytest.mark.parametrize("cycles", [2, 4])
@pytest.mark.parametrize("connected", [False, True])
def test_packaged_startup_log_validation(fault, cycles, connected):
    phases = [("startup_total", None, "started")]
    for role in ("orchestrator", "researcher"):
        for phase in ("profile_install", "profile_prepare"):
            phases.extend((phase, role, outcome) for outcome in ("started", "passed"))
    phases.append(("inference_ready", None, "started"))
    for phase in ("gateway_ready", "application_wake", "application_ready"):
        phases.extend((phase, None, outcome) for outcome in ("started", "passed"))
    phases.append(("startup_total", None, "passed"))
    phases.append(("relay_connected", None, "started"))
    if connected:
        phases.extend((phase, None, "passed") for phase in ("relay_connected", "inference_ready"))
    events = [
        {
            "event": "workspace_startup",
            "workspace_id": "00000000-0000-4000-8000-000000000149",
            "boot_id": str(boot),
            "workspace_state": "fresh" if boot == 0 else "retained",
            "phase": phase,
            "role": role,
            "outcome": outcome,
            "elapsed_seconds": 20
            if phase == "startup_total" and outcome == "passed"
            else int(outcome == "passed"),
            "timestamp": f"2026-11-01T00:00:{index:02d}Z",
        }
        for boot in range(cycles)
        for index, (phase, role, outcome) in enumerate(phases)
    ]
    if fault == "duplicate_ready":
        events.append(events[-1])
    if fault == "premature_ready":
        target = next(e for e in events if e["phase"] == "relay_connected")
        target["outcome"] = "passed"
    if fault == "missing":
        events.pop(1)
    if fault == "reused_boot":
        for event in events:
            event["boot_id"] = "same"
    if fault == "secret":
        events[0]["credential"] = "must-not-log"
    if fault == "state":
        events[-1]["workspace_state"] = "fresh"
    if fault == "negative":
        events[0]["elapsed_seconds"] = -1
    log = "Gateway log noise\n" + "\n".join(json.dumps(e, separators=(",", ":")) for e in events)
    if fault == "json":
        log += '\n{"event":"workspace_startup",broken}'
    result = subprocess.run(
        [sys.executable, str(CHECK), str(cycles)], input=log, text=True, capture_output=True
    )
    assert (result.returncode == 0) == (fault is None), result.stderr
