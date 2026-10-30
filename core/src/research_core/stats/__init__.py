"""Statistics for Experiments: the evaluation design, pairing, noise, power and the decision rule.

Everything here is pure functions over plain data. ``design``, ``ingest``, ``noise`` and
``power`` are stdlib only; ``bootstrap`` (and so ``decision_rule``, which calls it) uses numpy,
imported inside the function, so importing a module never loads numpy. These modules run in
the workspace environment, never inside Hermes.

- ``design``: ``EvaluationDesign`` and ``MetricSpec``, what an Experiment fixes before play.
- ``ingest``: ``EpisodeRow``, parsing the mixin ``evaluate`` output, cleaning and pairing rows.
- ``noise``: the metric noise record (``compute_noise``) and the sd of one paired difference.
- ``power``: allowed slip (``delta``) and pairs needed (``min_episodes``) from that sd.
- ``bootstrap``: the paired bootstrap confidence interval.
- ``decision_rule``: ``evaluate(design, pairs) -> Verdict``.
"""
