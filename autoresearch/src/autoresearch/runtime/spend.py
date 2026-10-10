"""Spend caps: runs and estimated model cost per card, and a daily budget (queue-keeper).

The box spend report of 2026-10-08 found no cap on a card's runs or cost (one stuck card and
its repair cost about $13 of $40). queue-keeper runs ``enforce`` every minute, before the
blocked-card step:

- **Per card** (pending or claimed, foreign cards included: ``runtime.foreign``): a card that would start run ``max_runs + 1`` (pending
  with ``max_runs`` counted runs) or has started it (claimed), or whose sessions cost an
  estimated ``max_card_usd`` or more, is stopped: ``queue.stop`` blocks it with a reason
  starting ``spend-cap:`` (Hermes ``kanban block`` with a typed kind, so the unblock-loop
  breaker keeps it in ``blocked``; the dispatcher ends a running worker within about two
  minutes), and one runtime ``help_request`` goes out, worded for where the card landed.
  Only cards Hermes can block (``running``, ``ready``) are tried. A stop that fails is
  retried every minute (logged); the first failure of a streak is recorded as
  ``not_stopped``, which grants no new allowance, with one help_request asking a human to
  block it. A ``spend-cap:`` block is never requeued or repaired
  (``blocked.handle_blocked`` skips it). A card blocked for another reason once it has
  reached either cap is held the same way (``hold_blocked``, called from
  ``handle_blocked``) instead of being requeued. queue-keeper also runs the caps while
  preflight fails: running cards keep running during a pause.
- **Allowance after a stop.** Each stop is recorded in the committed marker
  ``.supervisor/ledger/health/spend/<task-id>.json`` with the card's runs and cost at that
  point. Caps count from the latest stop, so a human who unblocks a stopped card gives it
  another full allowance.
- **Daily budget**: when the estimated spend of all research sessions (every session
  but the IDE coach chat's, ``source`` ``api_server``, which has its own day cap,
  ``AUTORESEARCH_CHAT_DAILY_USD``) that started this UTC day
  reaches ``daily_usd``, queue-keeper pauses dispatch (``pause.pause``, reason starting
  ``daily budget:``) and sends one ``help_request`` per day. It does not resume while over
  budget (``may_resume``; when the spend cannot be read it keeps a budget pause, or any pause
  on a day the budget was reached, ``runtime/budget_hold.json``); on the next UTC day the
  spend is under budget again and the usual resume runs. Running cards are not stopped by
  the budget; their own caps hold.

Counted runs are the card's runs except those that did no work (``NO_WORK_OUTCOMES``).

Cost source (the ``state.db`` reads and pricing live in ``runtime.state_db``, which the plugin
also imports). Hermes records per-session token usage and its own estimate in the profile's
``state.db`` (``sessions.estimated_cost_usd``, ``actual_cost_usd``; updated after each API
call, so a running session's cost is current). A card's sessions are the ones the plugin
recorded at each run's first turn (``session_start`` events in
``runtime/kanban_events.jsonl``, plus ``session_end`` events), with every descendant through
``parent_session_id`` (context-compression continuations, delegate subagents). A session with
an ``actual_cost_usd`` uses it; else Hermes' estimate. When Hermes has no estimate
(``cost_status`` ``unknown``, cost 0, tokens above 0: OpenRouter pricing was unavailable), the
session is priced from its token counts with ``AUTORESEARCH_FALLBACK_PRICES`` (USD per million
tokens), set above kimi-k3's observed rate on purpose so a cap trips early rather than late,
and queue-keeper logs the session once. Not counted: compiler child runs of a GotA build
(their own ``HERMES_HOME``), and any spend outside this profile.
"""

import datetime
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io.docs import read_json, write_json
from autoresearch.queue.base import Queue
from autoresearch.queue.card import Card
from autoresearch.queue.kanban_queue import STOPPABLE_STATUSES
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.pause import is_paused, pause, pause_path
from autoresearch.runtime.state_db import (  # noqa: F401 - public here too (baseline, tests)
    CHAT_SOURCE,
    COLUMNS,
    DEFAULT_FALLBACK_PRICES,
    ENV_FALLBACK_PRICES,
    STATE_DB,
    parse_prices,
    session_usd,
    state_rows,
    utc_day_start,
)

ENV_MAX_RUNS = "AUTORESEARCH_MAX_CARD_RUNS"
ENV_MAX_CARD_USD = "AUTORESEARCH_MAX_CARD_USD"
ENV_DAILY_USD = "AUTORESEARCH_DAILY_BUDGET_USD"
DEFAULT_MAX_RUNS = 4
DEFAULT_MAX_CARD_USD = 4.0
DEFAULT_DAILY_USD = 100.0
SPEND_CAP_PREFIX = "spend-cap:"
BUDGET_PAUSE_PREFIX = "daily budget:"
# Runs that never reached a model call: a spawn failure, a provider rate limit at start.
NO_WORK_OUTCOMES = frozenset({"spawn_failed", "rate_limited"})
EVENTS_FILE = "kanban_events.jsonl"  # written by the plugin (plugin.adapters)
UNPRICED_FILE = "spend_unpriced.json"
BUDGET_HOLD_FILE = "budget_hold.json"  # the UTC day the daily budget was last reached
DB_FAILURE = "spend_db"  # SpendRun.failures key when state.db cannot be read
HELP_PRIORITY = "P1"


@dataclass(frozen=True)
class SpendConfig:
    max_runs: int = DEFAULT_MAX_RUNS  # 0: no run cap
    max_card_usd: float = DEFAULT_MAX_CARD_USD  # 0: no cost cap
    daily_usd: float = DEFAULT_DAILY_USD  # 0: no daily budget
    prices: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_FALLBACK_PRICES))

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SpendConfig":
        """The caps from the profile ``.env``; unset or empty keeps the default. ValueError
        for a negative or unreadable value."""
        return cls(
            max_runs=int(_number(env, ENV_MAX_RUNS, DEFAULT_MAX_RUNS)),
            max_card_usd=_number(env, ENV_MAX_CARD_USD, DEFAULT_MAX_CARD_USD),
            daily_usd=_number(env, ENV_DAILY_USD, DEFAULT_DAILY_USD),
            prices=parse_prices(env.get(ENV_FALLBACK_PRICES)),
        )

    def allowance(self) -> str:
        parts = []
        if self.max_runs:
            parts.append(f"{self.max_runs} more runs")
        if self.max_card_usd:
            parts.append(f"an estimated ${self.max_card_usd:.2f} of model spend")
        return " and ".join(parts) or "no limit"


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}: not a number: {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name}: must not be negative: {raw!r}")
    if name == ENV_MAX_RUNS and value != int(value):
        raise ValueError(f"{name}: must be a whole number: {raw!r}")
    return value


# --- runs and cost ---


def counted_runs(card: Card) -> int:
    return sum(1 for run in card.runs if run.outcome not in NO_WORK_OUTCOMES)


@dataclass
class Cost:
    usd: float = 0.0
    unpriced: list[str] = field(default_factory=list)  # sessions priced from token counts


def session_runs(ctx: RunContext) -> dict[str, tuple[str, str | None]]:
    """session id -> (task id, run id) for every session the plugin recorded for a card run."""
    path = runtime_dir(ctx) / EVENTS_FILE
    found: dict[str, tuple[str, str | None]] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a line cut by a crash
        if not isinstance(event, dict):
            continue
        task, session, run = event.get("task"), event.get("session_id"), event.get("run")
        if event.get("event") in ("session_start", "session_end") and task and session:
            found.setdefault(str(session), (str(task), str(run) if run else None))
    return found


def card_sessions(ctx: RunContext) -> dict[str, set[str]]:
    """task id -> the root sessions the plugin recorded for its runs."""
    found: dict[str, set[str]] = {}
    for session, (task, _) in session_runs(ctx).items():
        found.setdefault(task, set()).add(session)
    return found




def _total(rows: list[sqlite3.Row], prices: Mapping[str, float]) -> Cost:
    cost = Cost()
    for row in rows:
        usd, unpriced = session_usd(row, prices)
        cost.usd += usd
        if unpriced:
            cost.unpriced.append(row["id"])
    return cost


def sessions_cost(hermes_home: Path, roots: set[str], prices: Mapping[str, float]) -> Cost:
    """Cost of ``roots`` and every session descended from them (``parent_session_id``)."""
    if not roots:
        return Cost()
    marks = ",".join("?" * len(roots))
    query = (
        f"WITH RECURSIVE tree(id) AS (SELECT id FROM sessions WHERE id IN ({marks}) "
        "UNION SELECT s.id FROM sessions s JOIN tree t ON s.parent_session_id = t.id) "
        f"SELECT {COLUMNS} FROM sessions s JOIN tree USING (id)"
    )
    return _total(state_rows(hermes_home, query, tuple(sorted(roots))), prices)


def day_cost(hermes_home: Path, now: datetime.datetime, prices: Mapping[str, float]) -> Cost:
    """Cost of every research session that started on ``now``'s UTC day: IDE coach chat
    sessions (``source`` ``api_server``) are left out; the chat has its own day cap."""
    day = utc_day_start(now)
    query = f"SELECT {COLUMNS} FROM sessions s WHERE s.started_at >= ? AND s.source != ?"
    return _total(state_rows(hermes_home, query, (day.timestamp(), CHAT_SOURCE)), prices)


def card_cost(ctx: RunContext, task_id: str, prices: Mapping[str, float]) -> Cost:
    return sessions_cost(ctx.hermes_home, card_sessions(ctx).get(task_id, set()), prices)


# --- stops ---


def marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / "health" / "spend" / f"{task_id}.json"


def _record(canonical: Path, task_id: str) -> dict | None:
    return committed_json(canonical, marker_path(canonical, task_id))


def _counted_stops(record: dict | None) -> list[dict]:
    """The recorded stops that worked (``stopped``, ``triaged``, ``held``). A failed stop
    (``not_stopped``) is recorded for its report only: it grants no new allowance."""
    stops = record.get("stops", []) if record else []
    return [stop for stop in stops if stop.get("outcome") != runtime_messages.NOT_STOPPED]


def _base(record: dict | None) -> tuple[int, float]:
    """Runs and cost already used at the latest stop that worked: the caps count from there."""
    stops = _counted_stops(record)
    if not stops:
        return 0, 0.0
    return int(stops[-1]["runs"]), float(stops[-1]["cost_usd"])


def is_held(canonical: Path, card: Card) -> bool:
    """True for a blocked card the caps own: stopped (``spend-cap:`` reason) or held at a
    cap for this block (``hold_blocked``)."""
    if (card.block_reason or "").startswith(SPEND_CAP_PREFIX):
        return True
    stops = _counted_stops(_record(canonical, card.task_id))
    last = stops[-1] if stops else None
    return bool(card.block_run_id and last and last.get("block_run_id") == card.block_run_id)


def _over_cap(card: Card, used_runs: int, used_usd: float, config: SpendConfig) -> str | None:
    """Why ``card`` is over a cap, or None. A pending or blocked card may not start another
    run once it has ``max_runs``; a claimed card is over once it started run
    ``max_runs + 1``."""
    if config.max_runs:
        if card.state == "claimed" and used_runs > config.max_runs:
            return f"it started run {used_runs}, and the cap is {config.max_runs} runs per card"
        if card.state != "claimed" and used_runs >= config.max_runs:
            return f"it already had {used_runs} runs, and the cap is {config.max_runs} runs per card"
    if config.max_card_usd and used_usd >= config.max_card_usd:
        return (
            f"its runs used an estimated ${used_usd:.2f} of model spend, and the cap is "
            f"${config.max_card_usd:.2f} per card"
        )
    return None


def cap_reached(ctx: RunContext, card: Card, config: SpendConfig) -> str | None:
    """Why a (blocked) card is at its run or cost cap, counted from its latest stop. Raises
    sqlite3.Error when ``state.db`` cannot be read (the caller skips the card this run)."""
    runs, usd = _base(_record(ctx.canonical, card.task_id))
    cost = card_cost(ctx, card.task_id, config.prices).usd
    return _over_cap(card, counted_runs(card) - runs, cost - usd, config)


def _report(
    ctx: RunContext,
    card: Card,
    why: str,
    runs: int,
    cost: float,
    config: SpendConfig,
    allow_no_remote: bool,
    outcome: str,
    block_run_id: str | None = None,
) -> None:
    """Commit the stop to the marker, then publish its help_request (call without the
    lock). The marker comes first so a crash never reuses a published stop number."""
    canonical = ctx.canonical
    path = marker_path(canonical, card.task_id)
    with canonical_lock(canonical):
        record = _record(canonical, card.task_id) or {
            "task_id": card.task_id,
            "request_id": card.spec.request_id,
            "stops": [],
        }
        record["stops"].append(
            {
                "at": now_iso(),
                "why": why,
                "outcome": outcome,
                "runs": runs,
                "cost_usd": round(cost, 4),
                "block_run_id": block_run_id,
            }
        )
        write_json(path, record)
        commit(canonical, [path], f"spend: card {card.task_id} at its cap ({outcome})")
        message = runtime_messages.card_over_cap(
            card.spec.title,
            why,
            config.allowance(),
            card.task_id,
            card.spec.request_id,
            len(_counted_stops(record)) if outcome == runtime_messages.NOT_STOPPED
            else len(record["stops"]),
            [{"uri": uri} for uri in card.spec.refs],
            outcome,
        )
        if not already_published(canonical, message):
            publish_message(
                canonical, message, allow_no_remote, priority=HELP_PRIORITY, expects_response=True
            )


def hold_blocked(
    ctx: RunContext, card: Card, why: str, config: SpendConfig, allow_no_remote: bool
) -> None:
    """A blocked card at a cap stays blocked: no requeue, no repair (module docstring)."""
    cost = card_cost(ctx, card.task_id, config.prices).usd
    _report(
        ctx, card, why, counted_runs(card), cost, config, allow_no_remote, runtime_messages.HELD,
        card.block_run_id,
    )


@dataclass
class SpendRun:
    stopped: list[tuple[str, str]] = field(default_factory=list)  # (task id, why)
    day_usd: float = 0.0
    over_budget: bool = False
    paused: bool = False  # this run paused for the budget
    unpriced: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # "spend_db" -> error
    stop_failures: dict[str, str] = field(default_factory=dict)  # task id -> error (retried)


def _stop(
    ctx: RunContext, queue: Queue, card: Card, why: str, cost: float, config: SpendConfig,
    allow_no_remote: bool,
) -> str | None:
    """Block first, then record: a crash between them loses the help_request but never lets
    the card run on (a recorded stop with the card still running would reset its allowance).
    A stop that fails returns the error; the caller retries it every minute. The first
    failure of a streak is recorded (``not_stopped``, no new allowance) with one
    help_request; later failures of the same streak are only logged."""
    # Stopping a pending card adds a zero-length blocked run (Hermes, FileQueue): count it in
    # the base so an unblocked card starts from a full allowance.
    runs = counted_runs(card) + (1 if card.state == "pending" else 0)
    try:
        landed = queue.stop(card.task_id, f"{SPEND_CAP_PREFIX} {why}")
    except Exception as error:  # noqa: BLE001 - retried next run; reported once a streak
        problem = f"{type(error).__name__}: {error}"
        record = _record(ctx.canonical, card.task_id)
        latest = record["stops"][-1] if record and record.get("stops") else None
        if not latest or latest.get("outcome") != runtime_messages.NOT_STOPPED:
            _report(
                ctx, card, f"{why} (the stop failed: {problem})", counted_runs(card), cost,
                config, allow_no_remote, runtime_messages.NOT_STOPPED,
            )
        return problem
    outcome = runtime_messages.STOPPED if landed == "blocked" else runtime_messages.TRIAGED
    _report(ctx, card, why, runs, cost, config, allow_no_remote, outcome)
    return None


def enforce_cards(
    ctx: RunContext, queue: Queue, config: SpendConfig, allow_no_remote: bool,
    result: SpendRun,
) -> None:
    """Stop the pending and claimed cards over a cap, foreign cards included
    (``Queue.foreign_cards``: no admission, but they still spend). Only cards Hermes can block
    are tried (``running``/``ready``); one card that fails does not stop the others, and a
    board that cannot list its foreign cards (``result.failures["foreign"]``) still has its
    admitted cards capped."""
    canonical = ctx.canonical
    sessions = card_sessions(ctx)
    cards = queue.list()
    try:
        cards = [*cards, *queue.foreign_cards()]
    except Exception as error:  # noqa: BLE001 - the admitted cards are capped anyway
        result.failures["foreign"] = f"{type(error).__name__}: {error}"
    for card in cards:
        if card.state not in ("pending", "claimed"):
            continue
        if card.board_status not in (None, *STOPPABLE_STATUSES):
            continue  # triage, todo, scheduled, review: not dispatched, cannot be blocked
        try:
            cost = sessions_cost(ctx.hermes_home, sessions.get(card.task_id, set()), config.prices)
        except sqlite3.Error as error:
            result.failures[DB_FAILURE] = f"{type(error).__name__}: {error}"
            continue
        result.unpriced += cost.unpriced
        runs, usd = _base(_record(canonical, card.task_id))
        why = _over_cap(card, counted_runs(card) - runs, cost.usd - usd, config)
        if why is None:
            continue
        problem = _stop(ctx, queue, card, why, cost.usd, config, allow_no_remote)
        if problem is None:
            result.stopped.append((card.task_id, why))
        else:
            result.stop_failures[card.task_id] = problem


def enforce_budget(
    ctx: RunContext, queue: Queue, config: SpendConfig, now: datetime.datetime,
    allow_no_remote: bool, result: SpendRun,
) -> None:
    """Pause when today's spend reaches the daily budget, with one help_request a day."""
    canonical = ctx.canonical
    try:
        today = day_cost(ctx.hermes_home, now, config.prices)
    except sqlite3.Error as error:
        result.failures[DB_FAILURE] = f"{type(error).__name__}: {error}"
        return
    result.day_usd = today.usd
    result.unpriced += today.unpriced
    result.over_budget = bool(config.daily_usd) and today.usd >= config.daily_usd
    if not result.over_budget:
        return
    day = f"{now.astimezone(datetime.UTC):%Y-%m-%d}"
    write_json(runtime_dir(ctx) / BUDGET_HOLD_FILE, {"day": day})
    ledger = ledger_of(canonical)
    result.paused = not is_paused(ledger)
    pause(
        queue,
        ledger,
        f"{BUDGET_PAUSE_PREFIX} estimated spend ${today.usd:.2f} of ${config.daily_usd:.2f} "
        f"on {day}",
    )
    message = runtime_messages.daily_budget_reached(today.usd, config.daily_usd, day)
    with canonical_lock(canonical):
        if not already_published(canonical, message):
            publish_message(
                canonical, message, allow_no_remote, priority="P0", expects_response=True
            )


def enforce(
    ctx: RunContext,
    queue: Queue,
    config: SpendConfig,
    now: datetime.datetime,
    allow_no_remote: bool = False,
) -> SpendRun:
    """Stop cards over a cap, then check the daily budget (module docstring)."""
    result = SpendRun()
    enforce_cards(ctx, queue, config, allow_no_remote, result)
    enforce_budget(ctx, queue, config, now, allow_no_remote, result)
    result.unpriced = sorted(set(result.unpriced))
    return result


def may_resume(ctx: RunContext, spend: SpendRun | None, now: datetime.datetime) -> bool:
    """Whether queue-keeper may lift the pause this run: never while over the daily budget.
    When this run could not read the spend (the step failed, or ``state.db`` could not be
    read), not if the pause is a budget pause or the budget was reached today
    (``runtime/budget_hold.json``; ``pause.json`` keeps only its first reason)."""
    if spend is not None and DB_FAILURE not in spend.failures:
        return not spend.over_budget
    path = pause_path(ledger_of(ctx.canonical))
    reason = read_json(path).get("reason", "") if path.exists() else ""
    if reason.startswith(BUDGET_PAUSE_PREFIX):
        return False
    hold = runtime_dir(ctx) / BUDGET_HOLD_FILE
    try:
        held_day = read_json(hold).get("day") if hold.exists() else None
    except ValueError:
        held_day = None  # disposable
    return held_day != f"{now.astimezone(datetime.UTC):%Y-%m-%d}"


def new_unpriced(ctx: RunContext, sessions: list[str]) -> list[str]:
    """The sessions in ``sessions`` not logged before (``runtime/spend_unpriced.json``), so
    queue-keeper logs each fallback-priced session once."""
    path = runtime_dir(ctx) / UNPRICED_FILE
    try:
        seen = set(read_json(path)) if path.exists() else set()
    except ValueError:
        seen = set()  # disposable
    new = [session for session in sessions if session not in seen]
    if new:
        write_json(path, sorted(seen | set(new)))
    return new
