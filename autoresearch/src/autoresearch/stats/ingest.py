"""Clean hosted XP episode rows and pair baseline against candidate episodes."""

from dataclasses import dataclass, field
from typing import Literal

from autoresearch.stats.design import EvaluationDesign


@dataclass
class EpisodeRow:
    episode_id: str
    game_version: str
    policy_version: str
    seed: int
    seats: tuple[int, ...]
    opponents: tuple[str, ...]
    metrics: dict[str, float]
    status: Literal["completed", "failed"]


@dataclass
class IngestReport:
    kept: int = 0
    duplicates: int = 0
    failed: int = 0
    wrong_version: int = 0
    wrong_seats: int = 0
    missing_metrics: list[str] = field(default_factory=list)


def ingest_rows(
    rows: list[EpisodeRow], game_version: str, metrics: list[str]
) -> tuple[list[EpisodeRow], IngestReport]:
    """Drop duplicate (same episode_id), failed, wrong-game-version and metric-incomplete rows."""
    report = IngestReport()
    seen: set[str] = set()
    missing: set[str] = set()
    kept: list[EpisodeRow] = []
    for row in rows:
        if row.episode_id in seen:
            report.duplicates += 1
            continue
        seen.add(row.episode_id)
        if row.status == "failed":
            report.failed += 1
        elif row.game_version != game_version:
            report.wrong_version += 1
        elif lacking := [name for name in metrics if name not in row.metrics]:
            missing.update(lacking)
        else:
            kept.append(row)
    report.kept = len(kept)
    report.missing_metrics = sorted(missing)
    return kept, report


def ingest(
    rows: list[EpisodeRow], design: EvaluationDesign
) -> tuple[list[EpisodeRow], IngestReport]:
    """``ingest_rows`` plus the design's seat and opponent checks (both count as wrong_seats)."""
    kept, report = ingest_rows(rows, design.game_version, [m.name for m in design.metrics])
    in_design = [
        row for row in kept if row.seats == design.seats and row.opponents == design.opponents
    ]
    report.wrong_seats = len(kept) - len(in_design)
    report.kept = len(in_design)
    return in_design, report


def pair(
    rows: list[EpisodeRow], design: EvaluationDesign, candidate_platform_id: str
) -> list[tuple[EpisodeRow, EpisodeRow]]:
    """Match baseline and candidate rows on (game_version, seed, seats, opponents).

    Only the first row per side and key, in episode_id order, is used; extras are ignored.
    """
    sides = {design.baseline_version: {}, candidate_platform_id: {}}
    for row in sorted(rows, key=lambda r: r.episode_id):
        side = sides.get(row.policy_version)
        if side is not None:
            side.setdefault((row.game_version, row.seed, row.seats, row.opponents), row)
    baseline, candidate = sides[design.baseline_version], sides[candidate_platform_id]
    return [(baseline[key], candidate[key]) for key in sorted(baseline.keys() & candidate.keys())]
