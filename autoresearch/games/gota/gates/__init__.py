"""Shared GotA gate contract. All subprocesses are local; no policy publication."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GateResult:
    ok: bool
    details: dict


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
    compiler_map: dict | None = None
