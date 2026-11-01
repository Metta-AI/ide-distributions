"""The image smoke must fail for missing, malformed or credential-bearing telemetry."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[2] / "image/system_tests/check-startup.py"


@pytest.mark.parametrize("fault", [None, "missing", "reused_boot", "secret", "negative", "json"])
def test_packaged_startup_log_validation(fault):
    phases = [("startup_total", None, "started")]
    for role in ("orchestrator", "researcher"):
        for phase in ("profile_install", "profile_prepare"):
            phases.extend((phase, role, outcome) for outcome in ("started", "passed"))
    for phase in ("gateway_ready", "application_wake", "application_ready"):
        phases.extend((phase, None, outcome) for outcome in ("started", "passed"))
    phases.append(("startup_total", None, "passed"))
    events = [
        {
            "event": "workspace_startup",
            "workspace_id": "00000000-0000-4000-8000-000000000149",
            "boot_id": str(boot),
            "phase": phase,
            "role": role,
            "outcome": outcome,
            "elapsed_seconds": 20 if index == len(phases) - 1 else int(outcome == "passed"),
            "timestamp": f"2026-11-01T00:00:{index:02d}Z",
        }
        for boot in range(2)
        for index, (phase, role, outcome) in enumerate(phases)
    ]
    if fault == "missing":
        events.pop()
    if fault == "reused_boot":
        for event in events:
            event["boot_id"] = "same"
    if fault == "secret":
        events[0]["credential"] = "must-not-log"
    if fault == "negative":
        events[0]["elapsed_seconds"] = -1
    log = "Gateway log noise\n" + "\n".join(json.dumps(e, separators=(",", ":")) for e in events)
    if fault == "json":
        log += '\n{"event":"workspace_startup",broken}'
    result = subprocess.run([sys.executable, str(CHECK)], input=log, text=True, capture_output=True)
    assert (result.returncode == 0) == (fault is None), result.stderr
