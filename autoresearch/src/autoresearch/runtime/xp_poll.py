"""xp-poll: XP evidence, review cards, and relaunch after credit denials. A Hermes cron
script job, every 5 minutes (``scripts/xp_poll.py`` -> ``python -m autoresearch.runtime.xp_poll``).

For each experiment in canonical (amendments A10, A12):

``evaluating`` or ``awaiting-credits``: evidence. The experiment's XP requests come from
``Softmax.xp_recover``; per (batch, seed, arm) only the latest attempt counts. Each request's
status comes from ``xp_status`` (an unrecognised platform status stays non-terminal: logged,
polled again). For every completed request without evidence yet, ``xp_artifacts`` downloads
into ``runtime/xp/<request id>/`` (``ArtifactsIncomplete``: retried on a later run), the
game's ``evaluate`` binding reads each ``results.json`` (``<evaluate> --results <file>
--json``, see ``games/_template/GAME.md``), and, under the canonical lock, one commit adds:

- ``evidence/xp/<E>/<request id>/results.json`` (the platform file) and ``rows.jsonl``: one
  ``EpisodeRow`` per episode (``episode_id`` ``<request id>:<episode>``; ``seed`` from the
  file's top-level ``seed``, else the episode's, else the request's; ``policy_version`` from the
  request's arm: the candidate binding's platform id or the design's baseline; ``seats`` and
  ``opponents`` from the frozen design; ``game_version`` from the file, else the league's
  current version; ``metrics`` from the binding);
- ``evidence/index/<EV-id>.json``: kind ``xp-result``, ``source_ids.xreq`` = the request id
  (so ``ar://xreq/<id>`` resolves), the file's sha256, paths, batch, attempt, seed and arm;
- ``evidence/xp/<E>/<request id>/meta.json``: ``{evidence, request_id}``, the "done" marker.

A request whose ``meta.json`` is committed is never fetched again; files a crash left
uncommitted are rewritten (an uncommitted index entry for the request is replaced, not
duplicated).

``evaluating``: review. When every latest request is terminal (completed or failed) and each
completed one has evidence, one runtime review request (marker ``review:<E>:n<count of xp_requests>``, P1, refs the
experiment, its hypothesis and the hypothesis's Topics, ``rebuild.review_refs``, so the
review may update them under D-23) is committed, unless an open request already covers the
experiment.

``awaiting-credits``: relaunch (A10). When ``lifecycle.next_launch_after`` has passed (the
softmax tool sets it on a credit denial: 30 minutes, doubling, capped at 8 hours), the credit
balance is not known to be 0 (only FakeSoftmax can read it; a Player credential cannot), and
no open request covers the experiment, one runtime launch request with marker
``launch:<E>:<attempt>:after:<next_launch_after>`` (``markers.relaunch_marker``: one relaunch
per denial). The card runs
``xp recover`` and then ``xp create`` with the same attempt, which creates only the requests
still missing, so a relaunch never duplicates an XP request.

Each experiment is handled independently: a failure is logged to
``.supervisor/runtime/xp_poll.log`` and the others go on. One run at a time
(``runtime/xp_poll.run.lock``). The run ends with a push of ``main``.
"""

import datetime
import hashlib
import json
import os
import shutil
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from autoresearch import runctx
from autoresearch.games.bindings import Evaluate, binding_evaluate
from autoresearch.games.loader import Game, load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_text
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.bootstrap import REPO_ROOT
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json
from autoresearch.runtime.markers import next_launch_attempt, relaunch_marker, review_marker
from autoresearch.runtime.planning import RUNTIME, commit_runtime_request
from autoresearch.runtime.rebuild import launch_step, open_experiment_refs, review_refs
from autoresearch.softmax_tool.protocol import Softmax, XpHandle, XpStatus
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.softmax_tool.xp import ArtifactsIncomplete
from autoresearch.stats.design import EvaluationDesign
from autoresearch.stats.ingest import EpisodeRow

LOG_FILE = "xp_poll.log"
RUN_LOCK = "xp_poll.run.lock"
EVIDENCE_XP_DIR = "evidence/xp"
TERMINAL = ("completed", "failed")
WATCHED = ("evaluating", "awaiting-credits")
CARD_PRIORITY = "P1"
REMOTE = "origin"
GAME_ENV = "AUTORESEARCH_GAME"
DEFAULT_GAME = "gota"



@dataclass
class XpPollRun:
    skipped: bool = False  # another run held the run lock
    evidence: list[str] = field(default_factory=list)  # request ids with new evidence
    reviews: list[CardSpec] = field(default_factory=list)
    launches: list[CardSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # experiment -> error


def _log(ctx: RunContext, message: str) -> None:
    path = runtime_dir(ctx) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _parse_time(value) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    moment = datetime.datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def latest_handles(handles: list[XpHandle]) -> list[XpHandle]:
    """The latest attempt per (batch, seed, arm): a retried request replaces the earlier one."""
    latest: dict[tuple, XpHandle] = {}
    for handle in handles:
        key = (handle.batch, handle.seed, handle.arm)
        if key not in latest or handle.attempt > latest[key].attempt:
            latest[key] = handle
    return sorted(latest.values(), key=lambda h: (h.batch, h.seed or 0, h.arm or "", h.attempt))


def _request_dir(canonical: Path, experiment: str, request_id: str) -> Path:
    return Path(canonical) / EVIDENCE_XP_DIR / experiment / request_id


def has_evidence(canonical: Path, experiment: str, request_id: str) -> bool:
    return (
        committed_json(canonical, _request_dir(canonical, experiment, request_id) / "meta.json")
        is not None
    )


def _uncommitted_index_entries(canonical: Path, request_id: str) -> list[Path]:
    """Index files on disk that cite ``request_id`` but are not committed (crash debris)."""
    found = []
    for path in (Layout(canonical).evidence_index).glob("EV-*.json"):
        record = read_json(path)
        if (record.get("source_ids") or {}).get("xreq") != request_id:
            continue
        if committed_json(canonical, path) is None:
            found.append(path)
    return found


def _rows(
    results: dict,
    episodes: list[dict],
    handle: XpHandle,
    design: EvaluationDesign,
    policy: str,
    game_version: str,
) -> list[EpisodeRow]:
    rows = []
    for index, episode in enumerate(episodes):
        seed = results.get("seed", episode.get("seed", handle.seed))
        rows.append(
            EpisodeRow(
                episode_id=f"{handle.request_id}:{episode.get('episode', index)}",
                game_version=str(results.get("game_version") or game_version).strip(),
                policy_version=policy,
                seed=int(seed),
                seats=tuple(design.seats),
                opponents=tuple(design.opponents),
                metrics={name: float(value) for name, value in episode["metrics"].items()},
                status="failed" if episode.get("status") == "failed" else "completed",
            )
        )
    return rows


@dataclass
class _Fetched:
    handle: XpHandle
    files: list[tuple[str, bytes]]  # (name, content) of each results file
    rows: list[EpisodeRow]
    policy: str
    game_version: str


def _arm_policy(experiment: dict, design: EvaluationDesign, handle: XpHandle) -> str:
    """The policy version the request's arm ran. Raises rather than guess: rows labelled with
    the wrong policy would silently corrupt the paired comparison."""
    if handle.arm == "baseline":
        return design.baseline_version
    if handle.arm != "candidate":
        raise ValueError(f"XP {handle.request_id} has unknown arm {handle.arm!r}")
    policy = (experiment.get("candidate_binding") or {}).get("platform_policy_version_id")
    if not policy:
        raise ValueError(f"{experiment['id']} has no candidate_binding policy version")
    return policy


def _fetch(
    ctx: RunContext,
    sm: Softmax,
    handle: XpHandle,
    experiment: dict,
    evaluate: Evaluate,
    league_version: Callable[[], str],
) -> _Fetched:
    """Download and map one completed request (outside the lock)."""
    dest = runtime_dir(ctx) / "xp" / handle.request_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    paths = [Path(p) for p in sm.xp_artifacts(handle, dest)]
    results_paths = sorted(p for p in paths if p.name == "results.json")
    if not results_paths:
        raise ArtifactsIncomplete(f"XP {handle.request_id} has no results.json yet")
    design = EvaluationDesign.from_dict(experiment["evaluation_design"])
    policy = _arm_policy(experiment, design, handle)
    files, rows = [], []
    game_version = ""
    for path in results_paths:
        results = json.loads(path.read_text(encoding="utf-8"))
        game_version = str(results.get("game_version") or league_version()).strip()
        rows += _rows(results, evaluate(path), handle, design, policy, game_version)
        files.append(
            (
                path.name if len(results_paths) == 1 else f"{path.parent.name}-{path.name}",
                path.read_bytes(),
            )
        )
    return _Fetched(handle, files, rows, policy, game_version)


def _write_evidence(
    canonical: Path, experiment_id: str, fetched: _Fetched, now: datetime.datetime
) -> None:
    """Write and commit one request's evidence (module docstring). Under the canonical lock."""
    handle = fetched.handle
    directory = _request_dir(canonical, experiment_id, handle.request_id)
    if directory.exists():
        shutil.rmtree(directory)
    for name, content in fetched.files:
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(content)
    rows_text = "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in fetched.rows)
    write_text(directory / "rows.jsonl", rows_text)
    for debris in _uncommitted_index_entries(canonical, handle.request_id):
        debris.unlink()
    first_name, first_content = fetched.files[0]
    relative = directory.relative_to(canonical).as_posix()
    store = RecordStore(Layout(canonical))
    evidence_id = store.create(
        "evidence",
        {
            "sha256": hashlib.sha256(first_content).hexdigest(),
            "kind": "xp-result",
            "source_ids": {"xreq": handle.request_id, "experiment": experiment_id},
            "game_version": fetched.game_version,
            "policy_versions": [fetched.policy],
            "fetched_at": now.isoformat(timespec="seconds"),
            "path": f"{relative}/{first_name}",
            "rows_path": f"{relative}/rows.jsonl",
            "batch": handle.batch,
            "attempt": handle.attempt,
            "seed": handle.seed,
            "arm": handle.arm,
        },
    )
    write_text(
        directory / "meta.json",
        json.dumps({"evidence": evidence_id, "request_id": handle.request_id}, sort_keys=True)
        + "\n",
    )
    commit(
        canonical,
        [directory, store.path_of(evidence_id)],
        f"xp-poll: evidence {evidence_id} for {handle.request_id} ({experiment_id})",
    )


def _covered(canonical: Path, experiment_id: str) -> bool:
    return f"ar://experiment/{experiment_id}" in open_experiment_refs(canonical)


def _experiment_refs(experiment: dict) -> list[str]:
    refs = [f"ar://experiment/{experiment['id']}"]
    version = (experiment.get("candidate_binding") or {}).get("version_id")
    if version:
        refs.append(f"ar://version/{version}")
    return refs


def _request_review(
    canonical: Path, experiment: dict, completed: int, failed: int
) -> CardSpec | None:
    experiment_id = experiment["id"]
    why = (
        f"The XP requests of {experiment_id} finished: {completed} completed, {failed} failed. "
        f"Episode rows are in {EVIDENCE_XP_DIR}/{experiment_id}/*/rows.jsonl (evidence index "
        f"entries cite each request as ar://xreq/<id>). Concatenate the rows, run "
        f'"$AUTORESEARCH_CLI" stats ingest --experiment {experiment_id} --rows <file> and '
        f'"$AUTORESEARCH_CLI" stats evaluate {experiment_id}, then write the Result and the '
        f"decision."
    )
    spec = CardSpec(
        "",
        f"Review {experiment_id}",
        "experimentation",
        why,
        [f"ar://experiment/{experiment_id}", *review_refs(canonical, experiment)],
        RUNTIME,
        CARD_PRIORITY,
    )
    with canonical_lock(canonical):
        if _covered(canonical, experiment_id):
            return None
        return commit_runtime_request(canonical, review_marker(experiment), spec)


def _credits_known_zero(sm: Softmax) -> bool:
    try:
        return sm.credits() == 0
    except Exception:  # noqa: BLE001 - a Player credential cannot read credits (A10)
        return False


def _request_relaunch(
    canonical: Path, sm: Softmax, experiment: dict, now: datetime.datetime
) -> CardSpec | None:
    lifecycle = experiment.get("lifecycle") or {}
    after = _parse_time(lifecycle.get("next_launch_after"))
    if after is None:  # the softmax tool sets it with every denial
        raise ValueError(f"{experiment['id']} is awaiting-credits without next_launch_after")
    if now < after or _credits_known_zero(sm):
        return None
    denials = int(lifecycle.get("credit_denials", 0))
    attempt = next_launch_attempt(experiment)
    marker = relaunch_marker(experiment["id"], attempt, lifecycle["next_launch_after"])
    reason = f"relaunch after credit denial {denials}; the backoff has passed"
    marker, title, why, mode = launch_step(experiment, reason, marker)
    spec = CardSpec("", title, mode, why, _experiment_refs(experiment), RUNTIME, CARD_PRIORITY)
    with canonical_lock(canonical):
        if _covered(canonical, experiment["id"]):
            return None
        return commit_runtime_request(canonical, marker, spec)


def _statuses(sm: Softmax, handles: list[XpHandle], result: XpPollRun) -> dict[str, XpStatus]:
    statuses = {}
    for handle in handles:
        status = sm.xp_status(handle)
        if status.warning:
            result.warnings.append(f"{handle.request_id}: {status.warning}")
        statuses[handle.request_id] = status
    return statuses


def poll_experiment(
    ctx: RunContext,
    sm: Softmax,
    experiment: dict,
    now: datetime.datetime,
    evaluate: Evaluate,
    league_version: Callable[[], str],
    result: XpPollRun,
) -> None:
    """Evidence, then the review (``evaluating``) or relaunch (``awaiting-credits``)."""
    canonical, experiment_id = ctx.canonical, experiment["id"]
    handles = latest_handles(sm.xp_recover(experiment_id))
    statuses = _statuses(sm, handles, result)
    for handle in handles:
        if statuses[handle.request_id].state != "completed":
            continue
        if has_evidence(canonical, experiment_id, handle.request_id):
            continue
        try:
            fetched = _fetch(ctx, sm, handle, experiment, evaluate, league_version)
        except ArtifactsIncomplete as error:
            result.warnings.append(f"{handle.request_id}: {error}")
            continue
        with canonical_lock(canonical):
            _write_evidence(canonical, experiment_id, fetched, now)
        result.evidence.append(handle.request_id)
    if experiment.get("status") == "evaluating" and handles:
        states = [statuses[h.request_id].state for h in handles]
        evidenced = all(
            has_evidence(canonical, experiment_id, h.request_id)
            for h in handles
            if statuses[h.request_id].state == "completed"
        )
        if all(state in TERMINAL for state in states) and evidenced:
            review = _request_review(
                canonical, experiment, states.count("completed"), states.count("failed")
            )
            if review is not None:
                result.reviews.append(review)
    elif experiment.get("status") == "awaiting-credits":
        launch = _request_relaunch(canonical, sm, experiment, now)
        if launch is not None:
            result.launches.append(launch)


def run(
    ctx: RunContext,
    sm: Softmax,
    game: Game,
    now: datetime.datetime,
    *,
    env: Mapping[str, str],
    evaluate: Evaluate | None = None,
    repo_root: Path = REPO_ROOT,
) -> XpPollRun:
    """One xp-poll run (module docstring). ``evaluate`` defaults to the game's binding.
    ``env`` is accepted for symmetry with the other jobs (the Softmax client reads its own)."""
    del env
    result = XpPollRun()
    evaluate = evaluate or binding_evaluate(game, repo_root)
    league: dict[str, str] = {}

    def league_version() -> str:
        if "version" not in league:
            league["version"] = str(sm.game_version(game.league_id)).strip()
        return league["version"]

    try:
        with file_lock(runtime_dir(ctx) / RUN_LOCK, timeout_s=0):
            store = RecordStore(Layout(ctx.canonical))
            for experiment_id in store.find("experiment"):
                experiment, _ = store.load(experiment_id)
                if experiment.get("status") not in WATCHED:
                    continue
                try:
                    poll_experiment(ctx, sm, experiment, now, evaluate, league_version, result)
                except Exception as error:  # noqa: BLE001 - one experiment never stops the rest
                    result.failures[experiment_id] = f"{type(error).__name__}: {error}"
                    _log(ctx, f"{experiment_id} failed: {error}\n{traceback.format_exc()}")
            for warning in result.warnings:
                _log(ctx, f"warning: {warning}")
            if REMOTE in gitops.git(ctx.canonical, "remote").split():
                with canonical_lock(ctx.canonical):
                    if not gitops.push(ctx.canonical, REMOTE, ["HEAD:refs/heads/main"]):
                        _log(ctx, "push of main failed; queue-keeper retries it")
    except LockTimeout:
        result.skipped = True
    return result


def main(argv: list[str] | None = None) -> int:
    del argv
    env = os.environ
    try:
        ctx = runctx.from_env(env)
        game = load_game(REPO_ROOT, env.get(GAME_ENV) or DEFAULT_GAME)
        sm = softmax_from_env(env, ctx.canonical)
    except Exception as error:  # noqa: BLE001 - report any setup failure and fail the job
        print(f"xp-poll: cannot start: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if not (ctx.canonical / ".git").exists():
        print("xp-poll: no canonical workspace yet (queue-keeper bootstraps it)")
        return 0
    result = run(ctx, sm, game, datetime.datetime.now(datetime.UTC), env=env)
    summary = {
        "skipped": result.skipped,
        "evidence": len(result.evidence),
        "reviews": [spec.request_id for spec in result.reviews],
        "launches": [spec.request_id for spec in result.launches],
        "warnings": result.warnings,
        "failures": result.failures,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
