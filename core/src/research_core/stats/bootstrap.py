"""Paired bootstrap confidence interval on a mean difference (no distributional assumption).

numpy is imported inside the function so that importing this module (and ``decision_rule``,
which uses it) stays light; it is only available in the workspace environment.
"""


def paired_bootstrap_ci(
    diffs: list[float], alpha: float = 0.05, iters: int = 10000, seed: int = 0
) -> tuple[float, float]:
    """Percentile CI of the mean of ``diffs``; the same seed always gives the same interval."""
    import numpy as np

    if len(diffs) == 0:
        raise ValueError("diffs is empty")
    values = np.asarray(diffs, dtype=float)
    rng = np.random.default_rng(seed)
    resampled = rng.choice(values, size=(iters, len(values)), replace=True).mean(axis=1)
    low, high = np.percentile(resampled, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(low), float(high)
