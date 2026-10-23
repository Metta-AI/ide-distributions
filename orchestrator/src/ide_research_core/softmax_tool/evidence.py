"""XP evidence: turn an experiment's finished XP requests into episode rows a review can ingest.

A review card runs ``research softmax xp evidence <E>`` in its worktree. The experiment's
requests come from ``Softmax.xp_recover``; per (batch, seed, arm) only the latest attempt
counts. For every completed request without evidence yet, ``xp_artifacts`` downloads its
results, the game's ``evaluate`` binding reads each ``results.json`` (``<evaluate> --results
<file> --json``, see ``games/_template/CONTRACT.md``), and the worktree gets:

- ``evidence/xp/<E>/<request id>/results.json`` (the platform file) and ``rows.jsonl``: one
  ``EpisodeRow`` per episode (``episode_id`` ``<request id>:<episode>``, or
  ``<request id>:<file dir>:<episode>`` when the request has several results files; ``seed``
  from the file's top-level ``seed``, else our lowest seat's entry, else the request's;
  ``policy_version`` from the request's arm: the candidate binding's platform id or the
  design's baseline; ``seats`` and ``opponents`` from the frozen design; ``game_version`` from
  the file, else the league's current version);
- ``evidence/index/<EV-id>.json``: kind ``xp-result``, ``source_ids.xreq`` = the request id
  (so ``ar://xreq/<id>`` resolves), the file's sha256, paths, batch, attempt, seed and arm;
- ``evidence/xp/<E>/<request id>/meta.json``: ``{evidence, request_id}``, the "done" marker.

``workspace integrate`` commits them with the rest of the card. A rerun skips requests whose
``meta.json`` exists and redoes any request a crash left half written.

Seat contract (C3). The binding returns one entry per (episode, seat), each with ``episode``
and ``seat``; the results of a multi-seat game cover every seat, opponents included. Only the
entries whose ``seat`` is in ``design.seats`` count, and each metric is reduced by the
arithmetic mean over our seats, so an episode is ONE observation of the arm, never one per
seat and never an opponent's. A missing or failed own seat (or own seats with different
metric names) records the episode as ``status: failed`` with a ``reason``, which ``stats
ingest`` counts and drops. An entry without ``episode`` or ``seat`` is a binding bug and
fails the command.
"""

import datetime
import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ide_research_core.games.bindings import Evaluate
from ide_research_core.io.docs import read_json, write_text
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.softmax_tool.protocol import Softmax, XpHandle
from ide_research_core.softmax_tool.xp import ArtifactsIncomplete
from ide_research_core.stats.design import EvaluationDesign
from ide_research_core.stats.ingest import EpisodeRow

EVIDENCE_XP_DIR = "evidence/xp"
TERMINAL = ("completed", "failed")


def latest_handles(handles: list[XpHandle]) -> list[XpHandle]:
    """The latest attempt per (batch, seed, arm): a retried request replaces the earlier one."""
    latest: dict[tuple, XpHandle] = {}
    for handle in handles:
        key = (handle.batch, handle.seed, handle.arm)
        if key not in latest or handle.attempt > latest[key].attempt:
            latest[key] = handle
    return sorted(latest.values(), key=lambda h: (h.batch, h.seed or 0, h.arm or "", h.attempt))


def request_dir(root: Path, experiment: str, request_id: str) -> Path:
    return Path(root) / EVIDENCE_XP_DIR / experiment / request_id


def has_evidence(root: Path, experiment: str, request_id: str) -> bool:
    return (request_dir(root, experiment, request_id) / "meta.json").is_file()


def _episode_row(
    episode_id: str,
    entries: list[dict],
    results: dict,
    handle: XpHandle,
    design: EvaluationDesign,
    policy: str,
    game_version: str,
) -> EpisodeRow:
    """One episode's row: our seats (``design.seats``) reduced by the arithmetic mean."""
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


def episode_rows(
    results: dict,
    entries: list[dict],
    handle: XpHandle,
    design: EvaluationDesign,
    policy: str,
    game_version: str,
    file_label: str | None = None,
) -> list[EpisodeRow]:
    """One row per episode of one results file (the seat contract, module docstring)."""
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


def arm_policy(experiment: dict, design: EvaluationDesign, handle: XpHandle) -> str:
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


@dataclass
class Fetched:
    handle: XpHandle
    files: list[tuple[str, bytes]]  # (name, content) of each results file
    rows: list[EpisodeRow]
    policy: str
    game_version: str


def fetch(
    sm: Softmax,
    handle: XpHandle,
    experiment: dict,
    evaluate: Evaluate,
    league_version: Callable[[], str],
    scratch: Path,
) -> Fetched:
    """Download and map one completed request."""
    dest = scratch / handle.request_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    paths = [Path(p) for p in sm.xp_artifacts(handle, dest)]
    results_paths = sorted(p for p in paths if p.name == "results.json")
    if not results_paths:
        raise ArtifactsIncomplete(f"XP {handle.request_id} has no results.json yet")
    design = EvaluationDesign.from_dict(experiment["evaluation_design"])
    policy = arm_policy(experiment, design, handle)
    files, rows = [], []
    game_version = ""
    for path in results_paths:
        results = json.loads(path.read_text(encoding="utf-8"))
        game_version = str(results.get("game_version") or league_version()).strip()
        label = None if len(results_paths) == 1 else path.parent.name
        rows += episode_rows(results, evaluate(path), handle, design, policy, game_version, label)
        files.append((path.name if label is None else f"{label}-{path.name}", path.read_bytes()))
    return Fetched(handle, files, rows, policy, game_version)


def write_evidence(root: Path, experiment_id: str, fetched: Fetched, now: datetime.datetime) -> str:
    """Write one request's evidence into the worktree ``root``; return the evidence id."""
    handle = fetched.handle
    directory = request_dir(root, experiment_id, handle.request_id)
    if directory.exists():  # a crash left it half written
        shutil.rmtree(directory)
    layout = Layout(root)
    for path in layout.evidence_index.glob("EV-*.json"):
        if (read_json(path).get("source_ids") or {}).get("xreq") == handle.request_id:
            path.unlink()  # its index record from that half-written run
    for name, content in fetched.files:
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(content)
    rows_text = "".join(json.dumps(asdict(row), sort_keys=True) + "\n" for row in fetched.rows)
    write_text(directory / "rows.jsonl", rows_text)
    first_name, first_content = fetched.files[0]
    relative = directory.relative_to(root).as_posix()
    evidence_id = RecordStore(layout).create(
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
    return evidence_id


def collect(
    root: Path,
    sm: Softmax,
    experiment: dict,
    evaluate: Evaluate,
    league_version: Callable[[], str],
    scratch: Path,
    now: datetime.datetime,
) -> dict:
    """Evidence for every completed request of the experiment that has none yet.

    ``complete`` is true when every latest request is terminal and each completed one has
    evidence: the review can ingest the rows."""
    experiment_id = experiment["id"]
    collected, already, pending, failed = [], [], [], []
    # Rows come from the latest attempts only: a superseded attempt's evidence stays on disk
    # as history but must not enter the paired comparison.
    for handle in latest_handles(sm.xp_recover(experiment_id)):
        state = sm.xp_status(handle).state
        if state == "failed":
            failed.append(handle.request_id)
        elif state != "completed":
            pending.append(handle.request_id)
        elif has_evidence(root, experiment_id, handle.request_id):
            already.append(handle.request_id)
        else:
            fetched = fetch(sm, handle, experiment, evaluate, league_version, scratch)
            write_evidence(root, experiment_id, fetched, now)
            collected.append(handle.request_id)
    rows = sorted(
        (request_dir(root, experiment_id, request_id) / "rows.jsonl").relative_to(root).as_posix()
        for request_id in collected + already
    )
    return {
        "experiment": experiment_id,
        "collected": collected,
        "already": already,
        "pending": pending,
        "failed": failed,
        "complete": not pending,
        "rows": rows,
    }
