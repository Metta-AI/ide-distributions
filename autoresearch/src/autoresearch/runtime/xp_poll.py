"""xp-poll: XP evidence, review cards, and relaunch after credit denials and the coach's
refusals. A Hermes cron script job, every 5 minutes (``scripts/xp_poll.py`` -> ``python -m
autoresearch.runtime.xp_poll``).

For each experiment in canonical (amendments A10, A12):

``evaluating``, ``awaiting-credits`` or ``awaiting-permit``: evidence. The experiment's XP
requests come from ``Softmax.xp_recover``; per (batch, seed, arm) only the latest attempt
counts. Each request's status comes from ``xp_status`` (an unrecognised platform status
stays non-terminal: logged, polled again). For every completed request without evidence
yet, ``xp_artifacts`` downloads into ``runtime/xp/<request id>/`` (``ArtifactsIncomplete``:
retried on a later run), the game's ``evaluate`` binding reads each ``results.json``
(``<evaluate> --results <file> --json``, see ``games/_template/GAME.md``), and, under the
canonical lock, one commit adds:

- ``evidence/xp/<E>/<request id>/results.json`` (the platform file) and ``rows.jsonl``: one
  ``EpisodeRow`` per episode (``episode_id`` ``<request id>:<episode>``, or
  ``<request id>:<file dir>:<episode>`` when the request has several results files; ``seed``
  from the file's top-level ``seed``, else our lowest seat's entry, else the request's;
  ``policy_version`` from the request's arm: the candidate binding's platform id or the
  design's baseline; ``seats`` and ``opponents`` from the frozen design (``opponents``: the
  request seed's sampled line-up, A12, or the fixed set); ``game_version`` from
  the file, else the league's current version; ``metrics`` as below);
- ``evidence/index/<EV-id>.json``: kind ``xp-result``, ``source_ids.xreq`` = the request id
  (so ``ar://xreq/<id>`` resolves), the file's sha256, paths, batch, attempt, seed and arm;
- ``evidence/xp/<E>/<request id>/meta.json``: ``{evidence, request_id}``, the "done" marker.

Seat contract (controller ruling, C3). The binding returns one entry per (episode, seat),
each with ``episode`` and ``seat``; the results of a multi-seat game cover every seat,
opponents included. xp-poll keeps only the entries whose ``seat`` is in ``design.seats``
(our policy fills exactly those slots, ``softmax_tool.cli._roster``) and reduces each metric
by the arithmetic mean over our seats, so an episode is ONE observation of the arm, never
one per seat and never an opponent's. Every one of our seats must be present: a missing or
failed own seat (or own seats with different metric names) records the episode as
``status: failed`` with a ``reason``, which ``stats ingest`` counts and drops. An entry
without ``episode`` or ``seat`` is a binding bug and fails the experiment's poll (logged,
retried next run). League rows (``softmax_tool.league``) stay one row per seat.

A request whose ``meta.json`` is committed is never fetched again; files a crash left
uncommitted are rewritten (an uncommitted index entry for the request is replaced, not
duplicated).

``evaluating``: review. When every latest request is terminal (completed or failed) and each
completed one has evidence, one runtime review request (marker ``review:<E>:n<count of xp_requests>``, P1, refs the
experiment, its hypothesis and the hypothesis's Topics, ``rebuild.review_refs``, so the
review may update them under D-23) is committed, unless an open request already covers the
experiment.

``awaiting-credits``: relaunch (A10). The Player can read the credit balance
(``Softmax.credits``), read once per run. The balance refills continuously, so "above 0" is
not enough: relaunch at once only when it covers the experiment's remaining requests
(``relaunch_need``: the requests of the design not yet created, one episode each, times the
cost per episode of our recent XP requests, ``Softmax.episode_cost``; when that is unknown,
``AUTORESEARCH_RELAUNCH_MIN_CREDITS``, default 100) and is above the balance read at the denial
(``lifecycle.credits_at_denial``). Otherwise the backoff decides: relaunch once
``lifecycle.next_launch_after`` has passed (the softmax tool sets it on a credit denial: 30
minutes, doubling, capped at 8 hours), also when the balance cannot be read. A balance read
as 0 or less never relaunches. At most one experiment is relaunched per run (the oldest
first), so a refill does not start every waiting experiment at once. In every case no open
request may cover the experiment. One runtime launch request with marker
``launch:<E>:<attempt>:after:<next_launch_after>`` (``markers.relaunch_marker``: one relaunch
per denial). The card runs
``xp recover`` and then ``xp create`` with the same attempt, which creates only the requests
still missing, so a relaunch never duplicates an XP request.

``awaiting-permit``: the coach's IDE Controls refused the batch (Softmax hosting;
``lifecycle.permit_wait``, written by ``softmax xp create``). Every experiment with open permit
asks is here, also when credits ran out in the same batch (``permit_wait.credits_short``);
there is no mixed credit-and-permit state, and this is the one gate. First one runtime help
request per ask set (``runtime_messages.permit_needed``, keyed by ``permit_wait.announced``:
how many asks, how to approve them and how to stop the asking per request), or for a league
refusal one per league and cause (``league_not_chosen``). Then every run reads each open ask's
answer (``Softmax.permit_answer``: our cogent's ``asks/<key>`` record, read with the Player
credential, no model spend):

- any no (or a refusal the server already called declined): the experiment goes ``stopped``
  (terminal, committed here) with ``lifecycle.stopped``, and one note
  (``runtime_messages.coach_declined``). Nothing is asked or launched for it again.
- over today's cap (``permit_wait.not_before``, the next UTC day): nothing until then.
- every ask yes on this run's read (or a yes already used): the relaunch is due at once.
- an answer that cannot be read: the relaunch is due once ``lifecycle.next_launch_after`` (the
  credit backoff, counted per set of asks) has passed.
- otherwise the asks still wait: nothing, and no card. A known waiting ask also holds back
  the backoff relaunch (such a card would only be refused). When an ask still waits
  ``REMINDER_AFTER`` (24 hours) after the set was first announced, one reminder help request,
  never more.

A due relaunch with ``credits_short`` also waits until the credits cover it, decided as for
``awaiting-credits`` (``credits_cover`` over ``credits_short``), except that a readable balance
that does not cover the remaining requests always keeps it waiting: the timed backoff applies
only when the balance cannot be read (the asks are approved, so a card at a known shortfall
would only be denied again). Then one relaunch request with
the same marker and card as a credit relaunch: the card reruns ``xp create`` with exactly the
batch of the asks (``permit_wait.batch``, ``attempt``, ``batches``; ``rebuild.launch_step``),
so each refused request goes again with the same body and idempotency key and the server
grants it against its yes. ``xp create`` refuses any other batch while the asks are open.

Every open ask is read again on every run (``permit_answers``): the coach can change a yes or a
no until the act uses it, so only ``used`` is final and kept in ``.supervisor/runtime/permit_answers.json``
(a later no to a cached yes would otherwise go unseen). New asks on a relaunch (a yes the price outgrew) are a new
set, so they get their own help request; asks that only drop out as their requests are created
do not.

A league refusal relaunches on that backoff (a no to an ask it still holds stops it as above).
Relaunches of every kind share the one-per-run limit. A relaunch card that ends without
creating the requests and without a new refusal or denial leaves the same wait; the next try
is generation 2, then 3, of the same marker (``markers.relaunch_generation``), for the
identical batch, and after ``RELAUNCH_GENERATIONS`` (3) one help request
(``runtime_messages.relaunch_stuck``) and no more cards for that wait.

Each experiment is handled independently: a failure is logged to
``.supervisor/runtime/xp_poll.log`` and the others go on. Within one experiment, a failure
while gathering evidence (artifacts, the evaluate binding, a platform read) is reported the
same way, but only after the relaunch and coach-answer steps above have run. One run at a time
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
from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.games.bindings import Evaluate, binding_evaluate
from autoresearch.games.loader import Game, load_game
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json, write_text
from autoresearch.io.locks import LockTimeout, file_lock
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime import provenance
from autoresearch.runtime.bootstrap import REPO_ROOT
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json
from autoresearch.runtime.markers import (
    next_launch_attempt,
    relaunch_generation,
    relaunch_marker,
    review_marker,
)
from autoresearch.runtime.planning import RUNTIME, commit_runtime_request, marked_request
from autoresearch.runtime.rebuild import launch_step, open_experiment_refs, review_refs
from autoresearch.softmax_tool.protocol import (
    ANSWER_NO,
    ANSWER_USED,
    ANSWER_WAITING,
    ANSWER_YES,
    PERMIT_DECLINED,
    PERMIT_OVER_CAP,
    Softmax,
    XpHandle,
    XpStatus,
    read_credits,
)
from autoresearch.softmax_tool.protocol import from_env as softmax_from_env
from autoresearch.softmax_tool.xp import ArtifactsIncomplete
from autoresearch.stats.design import EvaluationDesign
from autoresearch.stats.ingest import EpisodeRow

LOG_FILE = "xp_poll.log"
RUN_LOCK = "xp_poll.run.lock"
EVIDENCE_XP_DIR = "evidence/xp"
TERMINAL = ("completed", "failed")
WATCHED = ("evaluating", "awaiting-credits", "awaiting-permit")
CARD_PRIORITY = "P1"
REMOTE = "origin"
GAME_ENV = "AUTORESEARCH_GAME"
DEFAULT_GAME = "gota"
ANSWERS_FILE = "permit_answers.json"
# An ask still unanswered this long after it was first refused gets one reminder, never more.
REMINDER_AFTER = datetime.timedelta(hours=24)
# Tries of one wait's relaunch whose cards end without a new refusal, then one help request.
RELAUNCH_GENERATIONS = 3
MIN_CREDITS_ENV = "AUTORESEARCH_RELAUNCH_MIN_CREDITS"
DEFAULT_MIN_CREDITS = 100.0


@dataclass
class XpPollRun:
    skipped: bool = False  # another run held the run lock
    evidence: list[str] = field(default_factory=list)  # request ids with new evidence
    reviews: list[CardSpec] = field(default_factory=list)
    launches: list[CardSpec] = field(default_factory=list)
    help_requests: list[str] = field(default_factory=list)  # experiments asked about
    stopped: list[str] = field(default_factory=list)  # experiments the coach declined
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


def _episode_row(
    episode_id: str,
    entries: list[dict],
    results: dict,
    handle: XpHandle,
    design: EvaluationDesign,
    policy: str,
    game_version: str,
) -> EpisodeRow:
    """One episode's row: our seats (``design.seats``) reduced by the arithmetic mean.

    ``entries`` are the binding's entries for this episode, one per seat. Opponent seats are
    dropped. A missing or failed own seat, or own seats that disagree on the metric names,
    make the row ``failed`` with a ``reason`` (``ingest`` then drops it and counts it)."""
    ours: dict[int, dict] = {}
    for entry in entries:
        seat = entry["seat"]
        if seat in design.seats:
            if seat in ours:
                raise ValueError(f"{episode_id}: the evaluate binding gave seat {seat} twice")
            ours[seat] = entry
    first = ours[min(ours)] if ours else {}
    seed = results.get("seed", first.get("seed", handle.seed))
    reason = None
    missing = [seat for seat in design.seats if seat not in ours]
    failed = [seat for seat in design.seats if ours.get(seat, {}).get("status") == "failed"]
    names = {frozenset(entry["metrics"]) for entry in ours.values()}
    if not design.seats:
        reason = "the evaluation design names no seats of ours"
    elif missing:
        reason = (
            f"evaluate gave no entry for our seat(s) {missing} (design seats {list(design.seats)})"
        )
    elif failed:
        reason = f"our seat(s) {failed} failed"
    elif len(names) != 1:
        reason = "our seats report different metric names"
    metrics = {}
    if reason is None:
        metrics = {
            name: sum(float(ours[seat]["metrics"][name]) for seat in design.seats)
            / len(design.seats)
            for name in sorted(next(iter(names)))
        }
    return EpisodeRow(
        episode_id=episode_id,
        game_version=game_version,
        policy_version=policy,
        seed=int(seed),
        seats=tuple(design.seats),
        opponents=design.opponents_for(handle.seed if handle.seed is not None else int(seed)),
        metrics=metrics,
        status="completed" if reason is None else "failed",
        reason=reason,
    )


def _rows(
    results: dict,
    entries: list[dict],
    handle: XpHandle,
    design: EvaluationDesign,
    policy: str,
    game_version: str,
    file_label: str | None = None,
) -> list[EpisodeRow]:
    """One row per episode of one results file (the XP seat contract, module docstring).

    ``entries`` is the evaluate binding's output: one entry per (episode, seat), each with
    ``episode`` and ``seat``. ``file_label`` keeps episode ids unique when a request has
    several results files."""
    by_episode: dict[object, list[dict]] = {}
    for index, entry in enumerate(entries):
        if "episode" not in entry or "seat" not in entry:
            raise ValueError(
                f"XP {handle.request_id}: evaluate entry {index} lacks 'episode' or 'seat' "
                f"(GAME.md: one entry per seat, each naming its seat)"
            )
        by_episode.setdefault(entry["episode"], []).append(entry)
    prefix = handle.request_id if file_label is None else f"{handle.request_id}:{file_label}"
    version = str(results.get("game_version") or game_version).strip()
    return [
        _episode_row(
            f"{prefix}:{episode}", episode_entries, results, handle, design, policy, version
        )
        for episode, episode_entries in by_episode.items()
    ]


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
        label = None if len(results_paths) == 1 else path.parent.name
        rows += _rows(results, evaluate(path), handle, design, policy, game_version, label)
        files.append((path.name if label is None else f"{label}-{path.name}", path.read_bytes()))
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


def relaunch_need(experiment: dict, episode_cost: float | None, min_credits: float) -> float:
    """Credits the experiment's remaining XP requests should cost (module docstring): the
    design's (seed, arm) pairs without a request yet, one episode each, times
    ``episode_cost``; ``min_credits`` when the cost per episode is unknown."""
    if episode_cost is None:
        return min_credits
    seeds = (experiment.get("evaluation_design") or {}).get("seeds") or []
    created = {(entry.get("seed"), entry.get("arm")) for entry in experiment.get("xp_requests") or []}
    remaining = max(2 * len(seeds) - len(created), 1)
    return remaining * episode_cost


def relaunch_due(
    lifecycle: dict, credits: float | None, now: datetime.datetime, need: float = 0.0
) -> bool:
    """Whether an ``awaiting-credits`` experiment should relaunch now (A10, module docstring):
    at once when the readable balance covers ``need`` and is above the balance at the denial;
    otherwise once the backoff has passed (also when the balance cannot be read); never at a
    balance of 0 or less."""
    after = _parse_time(lifecycle.get("next_launch_after"))
    if after is None:  # the softmax tool sets it with every denial
        raise ValueError("awaiting-credits without next_launch_after")
    if credits is not None and credits <= 0:
        return False
    at_denial = lifecycle.get("credits_at_denial")
    if credits is not None and credits >= need and (at_denial is None or credits > at_denial):
        return True
    return now >= after


@dataclass
class CreditView:
    """The credit facts of one xp-poll run, each read at most once (minor: one HTTP read)."""

    sm: Softmax
    min_credits: float
    _read: dict = field(default_factory=dict)

    def balance(self) -> float | None:
        if "balance" not in self._read:
            self._read["balance"] = read_credits(self.sm)
        return self._read["balance"]

    def episode_cost(self) -> float | None:
        if "cost" not in self._read:
            try:
                self._read["cost"] = self.sm.episode_cost()
            except Exception:  # noqa: BLE001 - unknown cost: the threshold applies
                self._read["cost"] = None
        return self._read["cost"]


def min_relaunch_credits(env: Mapping[str, str]) -> float:
    """``AUTORESEARCH_RELAUNCH_MIN_CREDITS`` (a non-negative number), else 100; a malformed
    value falls back to the default (xp-poll must not stop for it)."""
    try:
        value = float(env.get(MIN_CREDITS_ENV) or DEFAULT_MIN_CREDITS)
    except ValueError:
        return DEFAULT_MIN_CREDITS
    return value if value >= 0 else DEFAULT_MIN_CREDITS


def credits_cover(
    credits_view: CreditView,
    experiment: dict,
    denial: dict,
    now: datetime.datetime,
    known_short_waits: bool = False,
) -> str | None:
    """Why the credits now allow a relaunch after ``denial`` (``credit_denials``,
    ``credits_at_denial``, ``next_launch_after``: the lifecycle of an ``awaiting-credits``
    experiment, or ``permit_wait.credits_short``), or None while they do not
    (``relaunch_due``). ``known_short_waits``: a readable balance below the need never
    relaunches, also after the backoff (``credits_short``, module docstring)."""
    credits = credits_view.balance()
    need = relaunch_need(experiment, credits_view.episode_cost(), credits_view.min_credits)
    if known_short_waits and credits is not None and credits < need:
        return None
    try:
        due = relaunch_due(denial, credits, now, need)
    except ValueError as error:
        raise ValueError(f"{experiment['id']}: {error}") from None
    if not due:
        return None
    denials = int(denial.get("credit_denials", 0))
    if credits is not None and credits >= need:
        when = f"{credits:g} credits available, about {need:g} needed"
    else:
        when = "the backoff has passed"
    return f"relaunch after credit denial {denials}; {when}"


def _request_relaunch(
    canonical: Path, credits_view: CreditView, experiment: dict, now: datetime.datetime
) -> CardSpec | None:
    reason = credits_cover(credits_view, experiment, experiment.get("lifecycle") or {}, now)
    return None if reason is None else _commit_relaunch(canonical, experiment, reason)


def _commit_relaunch(canonical: Path, experiment: dict, reason: str) -> CardSpec | None:
    """The relaunch request of a waiting experiment (marker ``markers.relaunch_marker``), unless
    an open request covers the experiment.

    A relaunch card can end (partial or failed) without creating the requests and without a
    new refusal or denial, so the wait and its marker stay the same. Then the next try is the
    next generation of the same marker (``markers.relaunch_generation``), for the identical
    batch, attempt and batches (so the same requests and idempotency keys), up to
    ``RELAUNCH_GENERATIONS``. After that, one help request (``runtime_messages.relaunch_stuck``)
    and no more cards for this wait."""
    lifecycle = experiment.get("lifecycle") or {}
    wait = lifecycle.get("permit_wait") or {}
    # A wait with open asks names its batch's attempt (``launch_step`` uses the same).
    attempt = (
        int(wait["attempt"])
        if wait.get("asks") and wait.get("attempt") is not None
        else next_launch_attempt(experiment)
    )
    base = relaunch_marker(experiment["id"], attempt, lifecycle["next_launch_after"])
    with canonical_lock(canonical):
        if _covered(canonical, experiment["id"]):
            return None
        for generation in range(1, RELAUNCH_GENERATIONS + 1):
            marker = relaunch_generation(base, generation)
            earlier = marked_request(canonical, marker)
            if earlier is None or committed_json(canonical, earlier) is None:
                break  # this generation has not been requested yet
        else:
            message = runtime_messages.relaunch_stuck(
                experiment["id"], base, RELAUNCH_GENERATIONS, _refs_of(experiment)
            )
            if not already_published(canonical, message):
                publish_message(canonical, message, allow_no_remote=True)
            return None
        if generation > 1:
            reason = (
                f"{reason}; try {generation} of {RELAUNCH_GENERATIONS}: the last relaunch card "
                "ended without creating the requests"
            )
        marker, title, why, mode = launch_step(experiment, reason, marker)
        refs = _experiment_refs(experiment)
        spec = CardSpec("", title, mode, why, refs, RUNTIME, CARD_PRIORITY)
        return commit_runtime_request(canonical, marker, spec)


def _refs_of(experiment: dict) -> list[dict]:
    return [{"uri": f"ar://experiment/{experiment['id']}"}]


def coach_wait_message(
    experiment: dict, reminder: bool = False
) -> runtime_messages.RuntimeMessage:
    """The one help request about an ``awaiting-permit`` experiment's batch: its permit asks
    (``reminder``: the one reminder, ``REMINDER_AFTER`` after them), or the league choice (one
    per league and cause). Its key is the ``announced`` set, so asks that drop out as their
    requests are created do not make a new help request; the text lists the open asks."""
    wait = (experiment.get("lifecycle") or {}).get("permit_wait") or {}
    refs = [{"uri": f"ar://experiment/{experiment['id']}"}]
    league = wait.get("league_id") or ""
    if wait.get("kind") == "league":
        minutes = int(wait.get("retry_minutes") or 30)
        reason = wait.get("reason") or ""
        return runtime_messages.league_not_chosen("xp", league, reason, minutes, refs)
    asks = wait.get("asks") or []
    if not asks:
        raise ValueError(f"{experiment['id']}: awaiting-permit without lifecycle.permit_wait.asks")
    subject = f"{experiment['id']}:b{wait.get('batch')}:a{wait.get('attempt')}"
    keys = [entry.get("ask") or "?" for entry in asks]
    over_cap = any(entry.get("state") == PERMIT_OVER_CAP for entry in asks)
    return runtime_messages.permit_needed(
        "xp",
        subject,
        len(asks),
        keys,
        league,
        over_cap,
        refs,
        reminder=reminder,
        key_asks=wait.get("announced") or keys,
    )


def _answers_path(ctx: RunContext) -> Path:
    """Disposable runtime state: the asks whose yes was already used (the one final answer)."""
    return runtime_dir(ctx) / ANSWERS_FILE


def permit_answers(ctx: RunContext, sm: Softmax, wait: dict) -> list[str | None]:
    """The coach's answer to each ask of ``wait`` (``Softmax.permit_answer``; None when it
    cannot be read). A refusal the server already called declined counts as a no. The coach
    can change a yes or a no until the act uses it (the server's permit-ask guard), so every
    ask is read again each run; only ``used`` is final, kept in ``_answers_path``. The cache is
    never pruned: one small entry per used ask."""
    path = _answers_path(ctx)
    try:
        cached = read_json(path) if path.exists() else {}
    except ValueError:
        cached = {}
    # Only the final answer counts; anything else (a yes or no, a hand edit, a torn or foreign
    # file) is read again.
    final = (ANSWER_USED,)
    known = (
        {key: value for key, value in cached.items() if isinstance(key, str) and value in final}
        if isinstance(cached, dict)
        else {}
    )
    answers, learned = [], {}
    for entry in wait.get("asks") or []:
        key = entry.get("ask")
        if entry.get("state") == PERMIT_DECLINED:
            answers.append(ANSWER_NO)
            continue
        if key in known:
            answers.append(known[key])
            continue
        try:
            answer = sm.permit_answer(key) if key else None
        except Exception:  # noqa: BLE001 - an unreadable answer only means "unknown"
            answer = None
        if answer in final:
            learned[key] = answer
        answers.append(answer)
    if learned:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {**known, **learned})
    return answers


def _stop_declined(canonical: Path, experiment: dict, now: datetime.datetime) -> None:
    """The coach said no: ``stopped`` (terminal; nothing is asked or launched again) and one
    note. Under the lock, only if the experiment still waits on the same asks."""
    experiment_id = experiment["id"]
    wait = (experiment.get("lifecycle") or {}).get("permit_wait") or {}
    seeds = (experiment.get("evaluation_design") or {}).get("seeds") or []
    store = RecordStore(Layout(canonical))
    with canonical_lock(canonical):
        current, _ = store.load(experiment_id)
        lifecycle = current.get("lifecycle") or {}
        if current.get("status") != "awaiting-permit" or lifecycle.get("permit_wait") != wait:
            return
        stopped = {"by": "coach", "at": now.isoformat(timespec="seconds"), "wait": wait}
        store.update(
            experiment_id, {"status": "stopped", "lifecycle": {**lifecycle, "stopped": stopped}}
        )
        commit(
            canonical,
            [store.path_of(experiment_id)],
            f"xp-poll: {experiment_id} stopped: the coach declined its XP requests",
        )
        # The design's (seed, arm) pairs that have a request, of all of them (every batch and
        # attempt of a pair counts once).
        requested = {(r.get("seed"), r.get("arm")) for r in current.get("xp_requests") or []}
        message = runtime_messages.coach_declined(
            "xp",
            f"{experiment_id}:b{wait.get('batch')}:a{wait.get('attempt')}",
            len(requested),
            2 * len(seeds),
            [{"uri": f"ar://experiment/{experiment_id}"}],
        )
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote=True)


def _publish_once(
    canonical: Path, message: runtime_messages.RuntimeMessage, result: XpPollRun, eid: str
) -> None:
    with canonical_lock(canonical):
        if not already_published(canonical, message):
            publish_message(canonical, message, allow_no_remote=True)
            result.help_requests.append(eid)


def _wait_for_coach(
    ctx: RunContext,
    sm: Softmax,
    experiment: dict,
    now: datetime.datetime,
    result: XpPollRun,
    credits_view: CreditView,
) -> None:
    """``awaiting-permit`` (module docstring): the one gate. The help request (and the one
    reminder), then the answers decide, then a credit shortfall of the same batch."""
    canonical = ctx.canonical
    _publish_once(canonical, coach_wait_message(experiment), result, experiment["id"])
    lifecycle = experiment.get("lifecycle") or {}
    wait = lifecycle.get("permit_wait") or {}
    after = _parse_time(lifecycle.get("next_launch_after"))
    if after is None:  # the softmax tool sets it with every refusal
        raise ValueError(f"{experiment['id']}: awaiting-permit without next_launch_after")
    answers = permit_answers(ctx, sm, wait)  # none for a league wait without open asks
    if wait.get("kind") == "permit":
        since = _parse_time(wait.get("since"))
        if ANSWER_WAITING in answers and since is not None and now - since >= REMINDER_AFTER:
            reminder = coach_wait_message(experiment, reminder=True)
            _publish_once(canonical, reminder, result, experiment["id"])
    if ANSWER_NO in answers:
        _stop_declined(canonical, experiment, now)
        result.stopped.append(experiment["id"])
        return
    if wait.get("kind") == "league":
        due, reason = now >= after, "the league choice refused it; the backoff has passed"
    else:
        not_before = _parse_time(wait.get("not_before"))
        if not_before is not None and now < not_before:
            return  # over today's cap: no yes covers it before the UTC day turns
        if all(answer in (ANSWER_YES, ANSWER_USED) for answer in answers):
            due, reason = True, f"the coach approved all {len(answers)} permit ask(s)"
        elif None in answers and ANSWER_WAITING not in answers:
            # An answer could not be read, and none is known to wait: the backoff decides.
            due = now >= after
            reason = "the coach's answers could not be read; the backoff has passed"
        else:
            return  # the coach has not answered every ask yet
    if result.launches or not due:
        return
    short = wait.get("credits_short")
    if short:
        # Credits ran out in the same batch: no card until they cover it too.
        credit_reason = credits_cover(credits_view, experiment, short, now, known_short_waits=True)
        if credit_reason is None:
            return
        reason = f"{reason}; {credit_reason}"
    launch = _commit_relaunch(canonical, experiment, reason)
    if launch is not None:
        result.launches.append(launch)


def _statuses(sm: Softmax, handles: list[XpHandle], result: XpPollRun) -> dict[str, XpStatus]:
    statuses = {}
    for handle in handles:
        status = sm.xp_status(handle)
        if status.warning:
            result.warnings.append(f"{handle.request_id}: {status.warning}")
        statuses[handle.request_id] = status
    return statuses


def _evidence_and_review(
    ctx: RunContext,
    sm: Softmax,
    experiment: dict,
    now: datetime.datetime,
    evaluate: Evaluate,
    league_version: Callable[[], str],
    result: XpPollRun,
) -> None:
    """Evidence for every completed request, then the review of a finished ``evaluating``
    experiment (module docstring)."""
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


def poll_experiment(
    ctx: RunContext,
    sm: Softmax,
    experiment: dict,
    now: datetime.datetime,
    evaluate: Evaluate,
    league_version: Callable[[], str],
    result: XpPollRun,
    credits_view: CreditView | None = None,
) -> None:
    """Evidence, then the review (``evaluating``) or relaunch (``awaiting-credits``,
    ``awaiting-permit``). The relaunch decision and the coach's answers do not depend on the
    evidence: a failure there (artifacts, the evaluate binding, a platform read) is raised
    after them, so it never keeps a no from stopping the experiment or a yes from relaunching
    it."""
    credits_view = credits_view or CreditView(sm, DEFAULT_MIN_CREDITS)
    evidence_failure: Exception | None = None
    try:
        _evidence_and_review(ctx, sm, experiment, now, evaluate, league_version, result)
    except Exception as error:  # noqa: BLE001 - raised below, after the waits are handled
        evidence_failure = error
    status = experiment.get("status")
    if status == "awaiting-credits" and not result.launches:
        launch = _request_relaunch(ctx.canonical, credits_view, experiment, now)
        if launch is not None:
            result.launches.append(launch)
    elif status == "awaiting-permit":
        _wait_for_coach(ctx, sm, experiment, now, result, credits_view)
    if evidence_failure is not None:
        raise evidence_failure


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
    ``env`` gives ``AUTORESEARCH_RELAUNCH_MIN_CREDITS`` (the Softmax client reads its own)."""
    result = XpPollRun()
    credits_view = CreditView(sm, min_relaunch_credits(env))
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
                    poll_experiment(
                        ctx, sm, experiment, now, evaluate, league_version, result, credits_view
                    )
                except Exception as error:  # noqa: BLE001 - one experiment never stops the rest
                    result.failures[experiment_id] = f"{type(error).__name__}: {error}"
                    _log(ctx, f"{experiment_id} failed: {error}\n{traceback.format_exc()}")
            for warning in result.warnings:
                _log(ctx, f"warning: {warning}")
            if REMOTE in gitops.git(ctx.canonical, "remote").split():
                with canonical_lock(ctx.canonical):
                    if not provenance.push_main(ctx.canonical, REMOTE):
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
        "help_requests": result.help_requests,
        "stopped": result.stopped,
        "warnings": result.warnings,
        "failures": result.failures,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
