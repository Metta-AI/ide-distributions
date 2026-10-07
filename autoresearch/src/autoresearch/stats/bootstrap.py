"""Paired bootstrap confidence interval on a mean difference (no distributional assumption)."""

import numpy as np


def paired_bootstrap_ci(
    diffs: list[float], alpha: float = 0.05, iters: int = 10000, seed: int = 0
) -> tuple[float, float]:
    """Percentile CI of the mean of ``diffs``; the same seed always gives the same interval."""
    if len(diffs) == 0:
        raise ValueError("diffs is empty")
    values = np.asarray(diffs, dtype=float)
    rng = np.random.default_rng(seed)
    resampled = rng.choice(values, size=(iters, len(values)), replace=True).mean(axis=1)
    low, high = np.percentile(resampled, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(low), float(high)
