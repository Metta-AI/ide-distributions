"""``card finish``: the trusted path that ends a card run (Overview C2, C3).

1. Commit everything in the worker's worktree on its card branch (allow empty) -> result_sha.
2. base_sha = merge-base(result_sha, canonical HEAD); ``validate_at`` from git objects.
3. Invalid and outcome != ``failed``: return the problems; nothing is recorded, the worker
   fixes them and runs ``card finish`` again.
4. Otherwise, under ``canonical.lock``, ONE canonical commit that writes
   ``.supervisor/ledger/handoffs/<task>/<run>.json`` (state ``committed``) and moves the request
   receipt to ``handed_off{task_id, run_id}``. That commit is the durable handoff.
5. Set ``finished=true`` in ``runtime/cards/<task>.json`` so the plugin blocks model writes.

Idempotent per run: if the record for (task, run) is committed at canonical HEAD, it is
returned as is and step 5 is redone. A record written but not committed (crash before the
commit) is rewritten with its receipt and committed. A new run id of the same task writes a second record.
"""

from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.card import CardSpec
from autoresearch.queue.requests import load_schema
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.admission import receipt_path
from autoresearch.runtime.handoff import (
    HandoffReport,
    card_spec,
    handoff_path,
    validate_at,
    write_handoff,
)
from autoresearch.runtime.ledger import (
    canonical_lock,
    commit,
    committed_json,
    ledger_of,
    now_iso,
)

# System Design §5.8 (done|partial|dropped|failed) plus the runtime's ``blocked``.
OUTCOMES = ("done", "partial", "dropped", "failed", "blocked")
# Receipt states from which a handoff may be recorded: a first run, or a retry after an
# earlier run's handoff.
HANDOFF_FROM_STATES = ("admitted", "handed_off")


class FinishUsageError(ValueError):
    """``card finish`` was run outside a card worker, or with a bad outcome or card state."""


@dataclass
class FinishResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    record: dict | None = None
    report: HandoffReport | None = None
    existing: bool = False  # the record was already committed by an earlier call


def card_file(ctx: RunContext, task_id: str) -> Path:
    return runtime_dir(ctx) / "cards" / f"{task_id}.json"


def _mark_finished(path: Path) -> None:
    meta = read_json(path)
    if not meta.get("finished"):
        write_json(path, meta | {"finished": True})


def _checked_context(ctx: RunContext, outcome: str) -> tuple[Path, str, str]:
    if outcome not in OUTCOMES:
        raise FinishUsageError(f"outcome must be one of {', '.join(OUTCOMES)}; got {outcome!r}")
    if ctx.worker is None or not ctx.card_id or not ctx.run_id:
        raise FinishUsageError(
            "card finish needs a card worker context (worktree, task id and run id)"
        )
    return ctx.worker, ctx.card_id, ctx.run_id


def _load_card_meta(path: Path, task_id: str) -> tuple[dict, CardSpec]:
    if not path.exists():
        raise FinishUsageError(f"no card metadata for {task_id} at {path}")
    try:
        meta = read_json(path)
        spec = card_spec(meta)
        responsible = meta.get("responsible_inbound", [])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise FinishUsageError(f"card metadata {path} is malformed: {error!r}") from None
    if not isinstance(responsible, list):
        raise FinishUsageError(f"card metadata {path}: responsible_inbound must be a list")
    return meta, spec


def _committed_receipt(canonical: Path, request_id: str, task_id: str) -> tuple[Path, dict]:
    """The request receipt at canonical HEAD, checked to accept a handoff of ``task_id``."""
    path = receipt_path(ledger_of(canonical), request_id)
    receipt = committed_json(canonical, path)
    if receipt is None:
        raise FinishUsageError(f"request {request_id} has no committed receipt")
    if receipt["state"] not in HANDOFF_FROM_STATES:
        raise FinishUsageError(
            f"request {request_id} is {receipt['state']}; it cannot be handed off"
        )
    if task_id not in [task["task_id"] for task in receipt.get("tasks", [])]:
        raise FinishUsageError(f"task {task_id} is not a task of request {request_id}")
    return path, receipt


def _record_handoff(ctx: RunContext, record: dict) -> None:
    """Write the handoff record and move the receipt in one canonical commit. Built from the
    committed receipt, so a rerun after a crash before the commit rewrites the same pair."""
    task_id, run_id = record["card"], record["run_id"]
    record_path = handoff_path(ctx.canonical, task_id, run_id)
    receipt_file, receipt = _committed_receipt(ctx.canonical, record["request_id"], task_id)
    receipt["state"] = "handed_off"
    receipt["handed_off"] = {"task_id": task_id, "run_id": run_id}
    receipt["history"].append({"state": "handed_off", "at": record["history"][0]["at"]})
    jsonschema.validate(receipt, load_schema("receipt.json"))
    write_handoff(record_path, record)
    write_json(receipt_file, receipt)
    commit(ctx.canonical, [record_path, receipt_file], f"handoff {task_id} run {run_id}")


def finish(ctx: RunContext, outcome: str) -> FinishResult:
    """Commit, validate and (if valid, or if the outcome is ``failed``) record the handoff.

    Raises FinishUsageError outside a card worker, for an unknown outcome, malformed card
    metadata, or a receipt that cannot take this handoff. The caller must not hold the
    canonical lock."""
    worker, task_id, run_id = _checked_context(ctx, outcome)
    meta_path = card_file(ctx, task_id)
    meta, spec = _load_card_meta(meta_path, task_id)
    record_path = handoff_path(ctx.canonical, task_id, run_id)
    existing = committed_json(ctx.canonical, record_path)
    if existing is not None:
        _mark_finished(meta_path)
        return FinishResult(ok=True, record=existing, existing=True)
    _committed_receipt(ctx.canonical, spec.request_id, task_id)  # fail before committing

    result_sha = gitops.commit_all(
        worker, f"card {task_id} run {run_id}: {outcome}", allow_empty=True
    )
    base_sha = gitops.merge_base(ctx.canonical, result_sha, "HEAD")
    responsible = list(meta.get("responsible_inbound", []))
    report = validate_at(
        ctx.canonical, base_sha, result_sha, spec, responsible, task_id, outcome=outcome
    )
    if not report.ok and outcome != "failed":
        return FinishResult(ok=False, problems=report.problems, report=report)

    record = {
        "card": task_id,
        "run_id": run_id,
        "request_id": spec.request_id,
        "mode": spec.mode,
        "refs": spec.refs,
        "base_sha": base_sha,
        "result_sha": result_sha,
        "outcome": outcome,
        "journal": report.journal,
        "responsible_inbound": responsible,
        "report": report.to_dict(),
        "state": "committed",
        "history": [{"state": "committed", "at": now_iso()}],
    }
    with canonical_lock(ctx.canonical):
        committed = committed_json(ctx.canonical, record_path)
        if committed is not None:  # a concurrent call for the same run won the race
            record = committed
        else:  # also repairs a pair written but left uncommitted by a crash
            _record_handoff(ctx, record)
    _mark_finished(meta_path)
    return FinishResult(ok=True, problems=report.problems, record=record, report=report)
