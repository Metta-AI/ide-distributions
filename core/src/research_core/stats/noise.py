"""The metric noise record: measured per-metric spread, the input to ``power``.

Shape::

    {game_version, computed_at, evidence: [ref, ...],
     metrics: {name: {mean, marginal_sd, paired_diff_sd | None, n, n_pairs}}}

The record is plain data; callers keep it with the Experiment's methods (``methods.metrics``)
so a later recalibration never changes a started Experiment. There is no file and no revision.
"""

import itertools
import math
import statistics
from datetime import UTC, datetime

from research_core.stats.ingest import EpisodeRow

MIN_PAIRS_FOR_PAIRED_SD = 10


class NoiseError(ValueError):
    """The noise record lacks a metric or has no usable spread for it."""


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
    """Noise record from cleaned episode rows (see ``ingest.ingest_rows``).

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
