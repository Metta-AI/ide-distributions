"""Shared gate contract for local_check.py and build.py (library, no CLI).

A gate returns GateResult(outcome, details): ``passed``, ``failed`` (the policy was measured
and rejected: a PolicyFailure) or ``errored`` (the tool or environment broke; any other
exception). Gates never repair input.

Artifacts: a BASIC file, a ZIP package file, or a package directory (packed here into a
deterministic ZIP: sorted entries, fixed timestamps) so every gate and the upload see the
same bytes.
"""
from __future__ import annotations

import hashlib
import traceback
import zipfile
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Literal

import episodes

GATE_NAMES = ('G1', 'G2', 'G3', 'G4')
ADVISORY = {'G4'}
ZIP_DATE = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class GateResult:
    outcome: Literal['passed', 'failed', 'errored']
    details: dict

    @property
    def ok(self) -> bool:
        return self.outcome == 'passed'


class PolicyFailure(ValueError):
    """The measured policy behaviour or bytes are rejected."""


def exception_summary(error: BaseException) -> dict:
    return {'type': type(error).__name__, 'message': str(error),
            'traceback': ''.join(traceback.format_exception(error, limit=-8))}


def gate_run(run):
    """PolicyFailure -> failed; any other exception -> errored (kept, never repaired)."""
    @wraps(run)
    def checked(*args, **kwargs):
        try:
            return run(*args, **kwargs)
        except PolicyFailure as error:
            return GateResult('failed', {'error': str(error)})
        except Exception as error:  # noqa: BLE001 - persist arbitrary tool faults
            return GateResult('errored', {'error': exception_summary(error)})
    return checked


@dataclass
class GateContext:
    work_dir: Path
    baseline: Path | None = None
    # episodes.Runner for G2-G4; built for `commit` (default: the live engine) on first use.
    runner: object | None = None
    runner_kind: str = 'league'
    commit: str | None = None
    seeds: int = 10
    jobs: int = 2
    timeout: int = 900
    variant: str = 'league-3v3'
    # G3 and G4 share one set of candidate-vs-baseline episodes (seeds x sides).
    screen: list | None = None
    notes: dict = field(default_factory=dict)

    @property
    def config(self) -> dict:
        return episodes.variant_config(self.variant)


def is_zip(path: Path) -> bool:
    with path.open('rb') as stream:
        head = stream.read(4)
    return len(head) >= 4 and head[:2] == b'PK' and head[2:4] in (b'\x03\x04', b'\x05\x06', b'\x07\x08')


def pack_directory(directory: Path, target: Path) -> Path:
    """Deterministic ZIP of a package directory (regular files only, sorted, fixed dates)."""
    files = sorted(path for path in directory.rglob('*') if not path.is_dir())
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise PolicyFailure(f'package entries must be regular files: {path}')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(directory).as_posix(), date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return target


def artifact_file(artifact: Path, work_dir: Path) -> Path:
    """The single file a seat loads: the artifact itself, or the packed package directory."""
    if artifact.is_dir():
        work_dir.mkdir(parents=True, exist_ok=True)
        return pack_directory(artifact, work_dir / 'package.zip')
    if not artifact.is_file():
        raise FileNotFoundError(f'artifact does not exist: {artifact}')
    return artifact


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
