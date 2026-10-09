"""Submit a promoted candidate to the live league: the guards in front of the single submit
path (Plan 03 Task 3.3, Overview C6/C8, amendment A9).

``submit`` refuses (``Refused`` with a machine-readable ``reason``) unless, in this order:

1. the card has a worker worktree (``no_worker``);
2. the experiment's ``decision.outcome`` is ``promote`` (``decision_not_promote``) and it has
   a candidate binding (``no_binding``);
3. the stored ``stats evaluate`` outcome ``stats/outcomes/<E>.json`` exists (``no_outcome``),
   says ``promote_eligible`` (``not_eligible``), and is not stale: it was computed for the bound
   candidate, on the current rows file (``rows_sha256``) and the frozen design
   (``design_sha256``) (``stale_outcome``);
4. the credential is the expected unexpired Player (``identity``; checked here so a bad
   credential never even acquires the promotion, and again by the client right before submit);
5. there is a champion (``promotion.champion_status``: the policy the league shows competing
   for our player, A9; ``champion_unknown`` when it shows none) and it is the champion the
   outcome was evaluated against (``champion_mismatch``).

Then ``promotion.submit_promotion`` does the C8 steps: acquire, champion == baseline, intent
(committed and pushed with the candidate branch), Softmax submit, submitted, and stages the
candidate STRATEGY at the champion path in the worker's worktree. Its refusals become
``promotion_refused``. On success the version gets a ``submitted`` event in the worktree.

``bootstrap_champion`` is the other guarded path (amendment A14): on a fresh install with no
champion, it uploads the game's starter policy build and promotes it as the first champion
through ``promotion.bootstrap_promotion`` (the same C8 steps, a ``bootstrap`` promotion with no
experiment, outcome or decision). Its guards, in order, before anything is uploaded:

1. the card has a worker worktree (``no_worker``) and the build passed (the shared build
   reader: ``gates_failed``, ``bad_build``, ``artifact_mismatch``);
2. the credential is the expected unexpired Player (``identity``);
3. no promotion is in flight (``promotion_in_flight``; ``acquire`` would refuse it anyway,
   this only saves the upload);
4. no champion exists: the league shows no policy competing for our player
   (``champion_exists``, A9);
5. the worktree's ``strategy/STRATEGY.md`` (committed or not) is byte-identical to canonical
   HEAD's, and every STRATEGY.md source hash the build records (``source_hashes`` entries for
   a ``STRATEGY.md``, or ``strategy_sha256`` when ``source_path`` is one) equals it
   (``not_shipped_strategy``). A build that names no STRATEGY.md source (a prebuilt
   artifact) cannot be tied to it here; the game's ``## Starter policy`` section and the build's
   provenance are responsible for that.

Then ``upload.upload_bootstrap`` (idempotent within the card's view of the records), a commit
of the version record and its ``uploaded`` event on the card branch (so ``record_intent``
pushes the champion's provenance with the branch), and ``bootstrap_promotion``. Its refusals
keep their code (``champion_exists`` when a champion appeared, ``not_shipped_strategy`` when
the branch's ``strategy/STRATEGY.md`` is not the shipped one) or become ``promotion_refused``
(another card took the lock first, or a push failed). On success the version gets a
``submitted`` event in the worktree.

The outcome, rows and experiment are read from the worker's worktree (a checkout of canonical
taken when the card started); ``submit_promotion`` reads the binding and baseline from canonical.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from autoresearch.io import gitops
from autoresearch.io.docs import read_json
from autoresearch.queue.base import Queue
from autoresearch.runctx import RunContext
from autoresearch.runtime import promotion
from autoresearch.softmax_tool.identity import identity_problems
from autoresearch.softmax_tool.protocol import Refused, Softmax
from autoresearch.softmax_tool.upload import checked_build, upload_bootstrap, worker_store
from autoresearch.stats.cli import _design_sha256  # the hash stats evaluate stores
from autoresearch.stats.design import EvaluationDesign


def candidate_path(experiment: str) -> str:
    """Where the promotion card commits the candidate STRATEGY on its branch."""
    return f"strategy/candidates/{experiment}/STRATEGY.md"


def _check_outcome(store, experiment: str, meta: dict, platform_id: str) -> dict:
    layout = store.layout
    outcome_path = layout.stats / "outcomes" / f"{experiment}.json"
    if not outcome_path.exists():
        raise Refused("no_outcome", f"no stored outcome {outcome_path}; run stats evaluate")
    outcome = read_json(outcome_path)
    if outcome.get("promote_eligible") is not True:
        failures = outcome.get("failures") or []
        raise Refused(
            "not_eligible",
            f"stats evaluate did not find {experiment} eligible: {', '.join(failures) or '?'}",
            {"failures": failures},
        )
    if outcome.get("candidate_platform_id") != platform_id:
        raise Refused(
            "stale_outcome",
            f"the outcome was evaluated for {outcome.get('candidate_platform_id')}, "
            f"but {experiment} is bound to {platform_id}",
        )
    rows_path = layout.stats / "rows" / f"{experiment}.jsonl"
    rows_hash = hashlib.sha256(rows_path.read_bytes()).hexdigest() if rows_path.exists() else None
    if rows_hash is None or outcome.get("rows_sha256") != rows_hash:
        raise Refused(
            "stale_outcome",
            f"the rows file {rows_path} changed since stats evaluate (or is missing); "
            "evaluate again",
        )
    design = EvaluationDesign.from_dict(meta["evaluation_design"])
    if outcome.get("design_sha256") != _design_sha256(design):
        raise Refused("stale_outcome", "the outcome was computed for another evaluation design")
    return outcome


def submit(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    experiment: str,
    league_id: str,
    player_id: str,
    candidate: str | None = None,
) -> dict:
    """Run every guard, then the single submit path. Returns the submission summary."""
    store = worker_store(ctx)
    meta, _ = store.load(experiment)
    decision = (meta.get("decision") or {}).get("outcome")
    if decision != "promote":
        raise Refused(
            "decision_not_promote", f"{experiment} decision is {decision!r}, not 'promote'"
        )
    binding = meta.get("candidate_binding")
    if not binding:
        raise Refused("no_binding", f"{experiment} has no candidate_binding")
    platform_id = binding["platform_policy_version_id"]
    outcome = _check_outcome(store, experiment, meta, platform_id)
    problems = identity_problems(sm.whoami(), player_id, datetime.now(UTC))
    if problems:
        raise Refused("identity", "; ".join(problems))
    champion, why_unknown = promotion.champion_status(ctx, sm, league_id)
    if champion is None:
        raise Refused("champion_unknown", why_unknown)
    if outcome.get("champion") != champion:
        raise Refused(
            "champion_mismatch",
            f"the outcome was evaluated against {outcome.get('champion')}, "
            f"but the current champion is {champion}; evaluate again",
        )
    version_id = binding["version_id"]
    result = promotion.submit_promotion(
        ctx,
        queue,
        sm,
        experiment,
        version_id,
        candidate or candidate_path(experiment),
        league_id,
    )
    if not result.ok:
        details = {"promotion_id": result.promotion_id, "request_id": result.request_id}
        raise Refused("promotion_refused", result.reason or "promotion refused", details)
    store.add_event(
        version_id,
        "submitted",
        {
            "submission_id": result.submission_id,
            "policy_version_id": platform_id,
            "league_id": league_id,
            "promotion_id": result.promotion_id,
        },
    )
    return {
        "experiment": experiment,
        "version_id": version_id,
        "platform_policy_version_id": platform_id,
        "promotion_id": result.promotion_id,
        "submission_id": result.submission_id,
        "champion_path": promotion.CHAMPION_PATH,
    }


def _refuse_if_champion(ctx: RunContext, sm: Softmax, league_id: str) -> None:
    current = promotion.load(ctx.canonical)
    if current is not None and current.stage in promotion.IN_FLIGHT:
        raise Refused(
            "promotion_in_flight",
            f"promotion {current.id} is {current.stage}; only one promotion runs at a time",
            {"promotion_id": current.id},
        )
    on_board = promotion.current_champion(ctx, sm, league_id)
    if on_board is not None:
        raise Refused(
            "champion_exists",
            f"the {league_id} league shows {on_board} competing for us: the champion (A9, A14)",
        )


def _strategy_source_hashes(build: dict) -> list[str]:
    """The STRATEGY.md hashes a build says it compiled: its ``source_hashes`` entries for a
    ``STRATEGY.md`` file, and ``strategy_sha256`` when ``source_path`` is a ``STRATEGY.md``.
    Empty when the build names no STRATEGY.md source (a prebuilt artifact)."""
    hashes = [
        value
        for path, value in (build.get("source_hashes") or {}).items()
        if Path(path).name == "STRATEGY.md"
    ]
    if Path(build.get("source_path") or "").name == "STRATEGY.md" and build.get("strategy_sha256"):
        hashes.append(build["strategy_sha256"])
    return hashes


def _refuse_unless_shipped(ctx: RunContext, build: dict) -> None:
    """``not_shipped_strategy`` unless the worktree's champion STRATEGY (committed or not) and
    every STRATEGY.md hash the build records equal canonical HEAD's champion STRATEGY."""
    shipped = gitops.show(ctx.canonical, "HEAD", promotion.CHAMPION_PATH)
    worktree = ctx.worker / promotion.CHAMPION_PATH
    if shipped is None or not worktree.is_file() or worktree.read_bytes() != shipped:
        raise Refused(
            "not_shipped_strategy",
            f"{promotion.CHAMPION_PATH} in this worktree differs from the shipped one at "
            "canonical HEAD; a bootstrap promotes only the unchanged shipped strategy",
        )
    shipped_hash = hashlib.sha256(shipped).hexdigest()
    built = [value for value in _strategy_source_hashes(build) if value != shipped_hash]
    if built:
        raise Refused(
            "not_shipped_strategy",
            f"the build compiled a STRATEGY.md with sha256 {built[0]}, not the shipped "
            f"{promotion.CHAMPION_PATH} ({shipped_hash})",
        )


def bootstrap_champion(
    ctx: RunContext,
    queue: Queue,
    sm: Softmax,
    build_dir: Path,
    league_id: str,
    player_id: str,
) -> dict:
    """Run the bootstrap guards (module docstring), upload the shipped strategy build and
    promote it as the first champion. Returns the submission summary."""
    store = worker_store(ctx)
    build, _, _ = checked_build(Path(build_dir))
    problems = identity_problems(sm.whoami(), player_id, datetime.now(UTC))
    if problems:
        raise Refused("identity", "; ".join(problems))
    _refuse_if_champion(ctx, sm, league_id)
    _refuse_unless_shipped(ctx, build)
    version_id, platform_id = upload_bootstrap(ctx, sm, Path(build_dir), player_id)
    # Commit the version record (and its upload event) on the card branch before intent, so
    # the pushed branch carries the champion's provenance even if this card dies after submit
    # (A11: stage-champion restores it from the branch).
    version_dir = promotion.version_dir(version_id)
    gitops.commit_paths(ctx.worker, [version_dir], f"bootstrap champion version {version_id}")
    result = promotion.bootstrap_promotion(ctx, queue, sm, version_id, platform_id, league_id)
    if not result.ok:
        details = {"promotion_id": result.promotion_id}
        reason = result.code or "promotion_refused"
        raise Refused(reason, result.reason or "promotion refused", details)
    store.add_event(
        version_id,
        "submitted",
        {
            "submission_id": result.submission_id,
            "policy_version_id": platform_id,
            "league_id": league_id,
            "promotion_id": result.promotion_id,
        },
    )
    return {
        "version_id": version_id,
        "platform_policy_version_id": platform_id,
        "promotion_id": result.promotion_id,
        "submission_id": result.submission_id,
        "champion_path": promotion.CHAMPION_PATH,
    }
