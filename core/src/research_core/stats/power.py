"""Slippage tolerance (delta) and minimum pair count (N) from measured noise.

Both take ``sd``, the standard deviation of one paired difference (``noise.sd_for``). Bad
inputs raise ``ValueError``.
"""

import math
from statistics import NormalDist


def _require_positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value}")


def delta(sd: float, n_planned: int, k: float = 0.5, k_cap: float = 1.0) -> float:
    """Allowed slippage: min(k, k_cap) standard errors of a difference over n_planned pairs."""
    _require_positive(sd, "sd")
    if n_planned < 1:
        raise ValueError(f"n_planned must be at least 1, got {n_planned}")
    return min(k, k_cap) * sd / math.sqrt(n_planned)


def min_episodes(
    sd: float, min_effect: float, power: float = 0.8, alpha: float = 0.05, floor: int = 40
) -> int:
    """Pairs needed to detect ``min_effect`` at the given power and two-sided alpha (normal
    approximation for a paired mean difference), never fewer than ``floor``."""
    _require_positive(sd, "sd")
    _require_positive(min_effect, "min_effect")
    z = NormalDist().inv_cdf
    needed = math.ceil(((z(1 - alpha / 2) + z(power)) * sd / min_effect) ** 2)
    return max(floor, needed)
