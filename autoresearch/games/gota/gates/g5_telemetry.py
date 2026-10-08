"""G5: parse every real policy log; malformed snapshots never disappear silently."""

from pathlib import Path

from ..tools.gota_episodes import _parse_telemetry
from . import GateContext, GateResult, PolicyFailure, gate_run
from .g2_episode import inspect_episode


def parse_log(path: Path) -> dict:
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts[:1] != ["LH"]:
            continue
        if len(parts) < 12:
            raise PolicyFailure(f"{path}: malformed LH telemetry")
        try:
            [int(value) for value in parts[1:]]
        except ValueError as error:
            raise PolicyFailure(f"{path}: malformed LH telemetry") from error
    failed, telemetry = _parse_telemetry(path)
    if failed or telemetry is None:
        raise PolicyFailure(f"{path}: BASIC failure or missing LH telemetry")
    return vars(telemetry)


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    if context.episode_dir is None:
        raise ValueError("G2 episode logs required")
    inspect_episode(artifact, context.episode_dir)
    if context.compiler_map is not None:
        return compiler_logs(context)
    seats = [
        parse_log(context.episode_dir / "logs" / f"policy_agent_{slot}.log")
        for slot in range(10)
    ]
    return GateResult(
        'passed',
        {
            "seats": seats,
            "episode_dir": str(context.episode_dir),
            "telemetry_kind": "legacy_LH",
            "compiler_trace_coverage": None,
        },
    )


@gate_run
def compiler_logs(context: GateContext) -> GateResult:
    """Parse the compiler wire using its strict shared reader, for all ten seats.

    Parsing provides attribution syntax evidence only; semantic truth and complete
    per-tick trace coverage remain the separate audit's responsibility.
    """
    import sys

    tools = Path(__file__).resolve().parents[1] / "compiler/tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from pw_intent import IntentError, parse_telemetry_line

    seats = []
    try:
        for seat in range(10):
            path = context.episode_dir / "logs" / f"policy_agent_{seat}.log"
            counts = {}
            previous = -1
            for line in path.read_text().splitlines():
                if "BASIC VM disabled" in line or "BASIC error:" in line:
                    raise PolicyFailure(f"seat {seat}: BASIC failure")
                if line.split(" ", 1)[0] not in ("PWD", "PWP", "PWE", "PWB", "PWC"):
                    continue
                for event in parse_telemetry_line(line, context.compiler_map):
                    if event["t"] < previous:
                        raise PolicyFailure(f"seat {seat}: telemetry ticks went backwards")
                    previous = event["t"]
                    counts[event["kind"]] = counts.get(event["kind"], 0) + 1
            if not counts.get("PWD"):
                raise PolicyFailure(f"seat {seat}: missing compiler decisions")
            seats.append({"seat": seat, "lines": counts})
        return GateResult(
            'passed',
            {
                "seats": seats,
                "telemetry_kind": "compiler",
                "compiler_trace_coverage": None,
                "claim": "wire parsed; full trace coverage not measured",
            },
        )
    except IntentError as error:
        raise PolicyFailure(str(error)) from error
