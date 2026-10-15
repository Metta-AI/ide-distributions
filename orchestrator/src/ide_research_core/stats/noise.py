"""The metric noise record (``stats/metric-noise.yaml``): measured per-metric spread.

Shape::

    {game_version, computed_at, evidence: [ref, ...],
     metrics: {name: {mean, marginal_sd, paired_diff_sd | None, n, n_pairs}}}

The record's revision is the git blob sha of the file (``write_noise`` returns it). Experiments
store that revision in their frozen design, so recalibrating never changes an existing design.
"""

import itertools
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml

from ide_research_core.io.docs import _atomic_write_text
from ide_research_core.io.gitops import git
from ide_research_core.paths import Layout
from ide_research_core.stats.ingest import EpisodeRow

MIN_PAIRS_FOR_PAIRED_SD = 10
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "metric-noise.json"


class NoiseError(ValueError):
    """The noise record is missing, for another game version, or has no usable spread."""


def _paired_diffs(rows: list[EpisodeRow], metric: str) -> list[float]:
    """Differences between two policy versions on the same (seed, seats, opponents) episode."""
    by_key: dict[tuple, dict[str, float]] = {}
    for row in sorted(rows, key=lambda r: r.episode_id):
        key = (row.seed, tuple(row.seats), tuple(row.opponents))
        by_key.setdefault(key, {}).setdefault(row.policy_version, row.metrics[metric])
    diffs = []
    for values in by_key.values():
        for first, second in itertools.combinations(sorted(values), 2):
            diffs.append(values[second] - values[first])
    return diffs


def _metric_entry(rows: list[EpisodeRow], metric: str) -> dict:
    lacking = [row.episode_id for row in rows if metric not in row.metrics]
    if lacking:
        raise NoiseError(f"metric {metric!r} is missing from rows: {lacking[:3]}")
    values = [row.metrics[metric] for row in rows]
    if len(values) < 2:
        raise NoiseError(f"metric {metric!r}: need at least 2 rows, have {len(values)}")
    diffs = _paired_diffs(rows, metric)
    return {
        "mean": statistics.fmean(values),
        "marginal_sd": statistics.stdev(values),
        "paired_diff_sd": statistics.stdev(diffs) if len(diffs) >= 2 else None,
        "n": len(values),
        "n_pairs": len(diffs),
    }


def compute_noise(
    rows: list[EpisodeRow], metrics: list[str], game_version: str, evidence_refs: list[str]
) -> dict:
    """Noise record from cleaned episode rows (see ``ingest_rows``).

    ``marginal_sd`` is the sample sd over all rows. ``paired_diff_sd`` is the sample sd of
    differences between policy versions on matching (seed, seats, opponents), or None if
    fewer than 2 such pairs exist.
    """
    return {
        "game_version": str(game_version),
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "evidence": list(evidence_refs),
        "metrics": {metric: _metric_entry(rows, metric) for metric in metrics},
    }


def _noise_path(layout: Layout) -> Path:
    return layout.stats / "metric-noise.yaml"


def write_noise(layout: Layout, record: dict) -> str:
    """Write the record, archiving the previous file to ``stats/history/<UTC ts>.yaml``.

    Returns the record revision: the git blob sha of the written file, which equals the blob
    sha after it is committed.
    """
    path = _noise_path(layout)
    if path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        _atomic_write_text(layout.stats / "history" / f"{stamp}.yaml", path.read_text("utf-8"))
    _atomic_write_text(path, yaml.safe_dump(record, sort_keys=True))
    return noise_revision(layout)


def noise_revision(layout: Layout) -> str:
    """The record revision: the git blob sha of ``stats/metric-noise.yaml`` as it is on disk."""
    path = _noise_path(layout)
    return git(layout.stats, "hash-object", str(path))


def load_noise(layout: Layout, game_version: str) -> dict:
    path = _noise_path(layout)
    if not path.exists():
        raise NoiseError(f"no noise record at {path}; calibrate noise first")
    try:
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and isinstance(record.get("game_version"), int | float | str):
            # A hand-written `game_version: 65` is a YAML number; versions are strings,
            # compared without surrounding whitespace.
            record["game_version"] = str(record["game_version"]).strip()
        jsonschema.validate(record, json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))
    except (yaml.YAMLError, jsonschema.ValidationError) as error:
        raise NoiseError(f"malformed noise record {path}: {error}") from error
    if record.get("game_version") != str(game_version).strip():
        raise NoiseError(
            f"noise record is for game version {record.get('game_version')!r}, "
            f"not {game_version!r}; recalibrate"
        )
    return record


def sd_for(record: dict, metric: str) -> float:
    """sd of one paired difference: measured if >= 10 pairs, else marginal sd * sqrt(2)."""
    entry = record["metrics"].get(metric)
    if entry is None:
        raise NoiseError(f"metric {metric!r} is not in the noise record")
    paired = entry.get("paired_diff_sd")
    if paired is not None and entry["n_pairs"] >= MIN_PAIRS_FOR_PAIRED_SD:
        sd = paired
    else:
        sd = entry["marginal_sd"] * 2**0.5
    if not math.isfinite(sd) or sd <= 0:
        raise NoiseError(f"metric {metric!r} has zero or non-finite sd; recalibrate")
    return sd
