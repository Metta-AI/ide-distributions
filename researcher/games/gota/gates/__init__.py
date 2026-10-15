"""Shared GotA gate contract. All subprocesses are local; no policy publication."""
import traceback
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class GateResult:
    outcome: Literal['passed', 'failed', 'errored']
    details: dict

    @property
    def ok(self) -> bool:
        return self.outcome == 'passed'

    @classmethod
    def errored(cls, error: Exception) -> 'GateResult':
        return cls('errored', {'error': exception_summary(error)})


class PolicyFailure(ValueError):
    """Explicit rejection of measured policy behavior or policy telemetry."""


def exception_summary(error: BaseException) -> dict:
    """Retain exception identity and the last eight traceback frames for diagnosis."""
    return {'type': type(error).__name__, 'message': str(error),
            'traceback': ''.join(traceback.format_exception(error, limit=-8))}


def retain_episode_locators(gates: dict, build_dir: Path) -> None:
    """Make retained episode navigation relative to the build; preserve executed commands."""
    for name in ('G2', 'G5'):
        summary = gates.get(name, {}).get('summary')
        if isinstance(summary, dict) and summary.get('episode_dir'):
            episode = Path(summary['episode_dir'])
            if episode.is_relative_to(build_dir):
                summary['episode_dir'] = str(episode.relative_to(build_dir))


def gate_run(run):
    """Keep deliberate policy rejection distinct from unexpected tooling faults."""
    @wraps(run)
    def checked(*args, **kwargs):
        try:
            return run(*args, **kwargs)
        except PolicyFailure as error:
            return GateResult('failed', {'error': str(error)})
        except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults; never repair them.
            return GateResult.errored(error)
    return checked


@dataclass
class GateContext:
    work_dir: Path
    baseline: Path | None = None
    manifest: Path | None = None
    episode_dir: Path | None = None
    native_binary: Path | None = None
    seeds: tuple[int, ...] = tuple(range(1, 11))
    max_ticks: int = 31200
    timeout: int = 600
    variant: str = 'competition'
    measurements: dict = field(default_factory=dict)
    engine_commit: str | None = None
    compiler_map: dict | None = None
