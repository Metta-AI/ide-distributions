"""Spend caps: runs and estimated model cost per card, and a daily budget (queue-keeper).

The box spend report of 2026-10-08 found no cap on a card's runs or cost (one stuck card and
its repair cost about $13 of $40). queue-keeper runs ``enforce`` every minute, before the
blocked-card step:

- **Per card** (pending or claimed): a card that would start run ``max_runs + 1`` (pending
  with ``max_runs`` counted runs) or has started it (claimed), or whose sessions cost an
  estimated ``max_card_usd`` or more, is stopped: ``queue.stop`` blocks it with a reason
  starting ``spend-cap:`` (Hermes ``kanban block``; the dispatcher ends a running worker
  within about two minutes), and one runtime ``help_request`` goes out. A ``spend-cap:``
  block is never requeued or repaired (``blocked.handle_blocked`` skips it). A card blocked
  for another reason once it has ``max_runs`` counted runs is held the same way
  (``hold_blocked``, called from ``handle_blocked``) instead of being requeued.
- **Allowance after a stop.** Each stop is recorded in the committed marker
  ``.supervisor/ledger/health/spend/<task-id>.json`` with the card's runs and cost at that
  point. Caps count from the latest stop, so a human who unblocks a stopped card gives it
  another full allowance.
- **Daily budget**: when the estimated spend of all sessions that started this UTC day
  reaches ``daily_usd``, queue-keeper pauses dispatch (``pause.pause``, reason starting
  ``daily budget:``) and sends one ``help_request`` per day. It does not resume while over
  budget (``may_resume``); on the next UTC day the spend is under budget again and the usual
  resume runs. Running cards are not stopped by the budget; their own caps hold.

Counted runs are the card's runs except those that did no work (``NO_WORK_OUTCOMES``).

Cost source. Hermes records per-session token usage and its own estimate in the profile's
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
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.pause import is_paused, pause, pause_path

ENV_MAX_RUNS = "AUTORESEARCH_MAX_CARD_RUNS"
ENV_MAX_CARD_USD = "AUTORESEARCH_MAX_CARD_USD"
ENV_DAILY_USD = "AUTORESEARCH_DAILY_BUDGET_USD"
ENV_FALLBACK_PRICES = "AUTORESEARCH_FALLBACK_PRICES"
DEFAULT_MAX_RUNS = 4
DEFAULT_MAX_CARD_USD = 4.0
DEFAULT_DAILY_USD = 25.0
# USD per million tokens, for sessions Hermes could not price. Above the rate the box showed
# for kimi-k3 (about $36.5 for 4.6M input, 0.39M output, 59.2M cache-read tokens).
DEFAULT_FALLBACK_PRICES = {"input": 3.0, "output": 12.0, "cache_read": 0.75, "cache_write": 3.0}
SPEND_CAP_PREFIX = "spend-cap:"
BUDGET_PAUSE_PREFIX = "daily budget:"
# Runs that never reached a model call: a spawn failure, a provider rate limit at start.
NO_WORK_OUTCOMES = frozenset({"spawn_failed", "rate_limited"})
EVENTS_FILE = "kanban_events.jsonl"  # written by the plugin (plugin.adapters)
UNPRICED_FILE = "spend_unpriced.json"
STATE_DB = "state.db"
HELP_PRIORITY = "P1"
_TOKEN_COLUMNS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_write": "cache_write_tokens",
}


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
            prices=_prices(env.get(ENV_FALLBACK_PRICES)),
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


def _prices(raw: str | None) -> dict[str, float]:
    """``input=3,output=12,cache_read=0.75,cache_write=3`` over the defaults."""
    prices = dict(DEFAULT_FALLBACK_PRICES)
    for entry in (raw or "").split(","):
        if not entry.strip():
            continue
        kind, _, value = (part.strip() for part in entry.partition("="))
        if kind not in prices:
            raise ValueError(f"{ENV_FALLBACK_PRICES}: unknown token kind {kind!r}")
        try:
            prices[kind] = float(value)
        except ValueError:
            raise ValueError(f"{ENV_FALLBACK_PRICES}: {kind}: not a number: {value!r}") from None
    return prices


# --- runs and cost ---


def counted_runs(card: Card) -> int:
    return sum(1 for run in card.runs if run.outcome not in NO_WORK_OUTCOMES)


@dataclass
class Cost:
    usd: float = 0.0
    unpriced: list[str] = field(default_factory=list)  # sessions priced from token counts


def card_sessions(ctx: RunContext) -> dict[str, set[str]]:
    """task id -> the root sessions the plugin recorded for its runs."""
    path = runtime_dir(ctx) / EVENTS_FILE
    found: dict[str, set[str]] = {}
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # a line cut by a crash
        if not isinstance(event, dict):
            continue
        task, session = event.get("task"), event.get("session_id")
        if event.get("event") in ("session_start", "session_end") and task and session:
            found.setdefault(str(task), set()).add(str(session))
    return found


def _session_usd(row: sqlite3.Row, prices: Mapping[str, float]) -> tuple[float, bool]:
    """(cost, priced from tokens?) of one session row (module docstring)."""
    actual = row["actual_cost_usd"] or 0.0
    if actual > 0:
        return actual, False
    estimated = row["estimated_cost_usd"] or 0.0
    if estimated > 0 or row["cost_status"] == "included":
        return estimated, False
    tokens = {kind: row[column] or 0 for kind, column in _TOKEN_COLUMNS.items()}
    if not any(tokens.values()):
        return 0.0, False
    return sum(prices[kind] * count for kind, count in tokens.items()) / 1_000_000, True


_COLUMNS = "s.id, s.actual_cost_usd, s.estimated_cost_usd, s.cost_status, " + ", ".join(
    f"s.{column}" for column in _TOKEN_COLUMNS.values()
)


def _rows(hermes_home: Path, query: str, params: tuple) -> list[sqlite3.Row]:
    """Rows of a read-only query on the profile ``state.db``; none before the first session."""
    path = Path(hermes_home) / STATE_DB
    if not path.exists():
        return []
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(query, params))
    finally:
        connection.close()


def _total(rows: list[sqlite3.Row], prices: Mapping[str, float]) -> Cost:
    cost = Cost()
    for row in rows:
        usd, unpriced = _session_usd(row, prices)
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
        f"SELECT {_COLUMNS} FROM sessions s JOIN tree USING (id)"
    )
    return _total(_rows(hermes_home, query, tuple(sorted(roots))), prices)


def day_cost(hermes_home: Path, now: datetime.datetime, prices: Mapping[str, float]) -> Cost:
    """Cost of every session that started on ``now``'s UTC day."""
    day = now.astimezone(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    query = f"SELECT {_COLUMNS} FROM sessions s WHERE s.started_at >= ?"
    return _total(_rows(hermes_home, query, (day.timestamp(),)), prices)


def card_cost(ctx: RunContext, task_id: str, prices: Mapping[str, float]) -> Cost:
    return sessions_cost(ctx.hermes_home, card_sessions(ctx).get(task_id, set()), prices)


# --- stops ---


def marker_path(canonical: Path, task_id: str) -> Path:
    return ledger_of(canonical) / "health" / "spend" / f"{task_id}.json"


def _record(canonical: Path, task_id: str) -> dict | None:
    return committed_json(canonical, marker_path(canonical, task_id))


def _base(record: dict | None) -> tuple[int, float]:
    """Runs and cost already used at the latest stop: the caps count from there."""
    if not record or not record.get("stops"):
        return 0, 0.0
    last = record["stops"][-1]
    return int(last["runs"]), float(last["cost_usd"])


def is_held(canonical: Path, card: Card) -> bool:
    """True for a blocked card the caps own: stopped (``spend-cap:`` reason) or held at the
    run cap for this block (``hold_blocked``)."""
    if (card.block_reason or "").startswith(SPEND_CAP_PREFIX):
        return True
    record = _record(canonical, card.task_id)
    last = record["stops"][-1] if record and record.get("stops") else None
    return bool(card.block_run_id and last and last.get("block_run_id") == card.block_run_id)


def run_cap_reached(canonical: Path, card: Card, config: SpendConfig) -> bool:
    runs, _ = _base(_record(canonical, card.task_id))
    return bool(config.max_runs) and counted_runs(card) - runs >= config.max_runs


def _report(
    ctx: RunContext,
    card: Card,
    why: str,
    runs: int,
    cost: float,
    config: SpendConfig,
    allow_no_remote: bool,
    block_run_id: str | None,
) -> None:
    """Record the stop in the marker and publish its help_request (call without the lock)."""
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
                "runs": runs,
                "cost_usd": round(cost, 4),
                "block_run_id": block_run_id,
            }
        )
        message = runtime_messages.card_over_cap(
            card.spec.title,
            why,
            config.allowance(),
            card.task_id,
            card.spec.request_id,
            len(record["stops"]),
            [{"uri": uri} for uri in card.spec.refs],
            running=block_run_id is None,
        )
        if not already_published(canonical, message):
            publish_message(
                canonical, message, allow_no_remote, priority=HELP_PRIORITY, expects_response=True
            )
        write_json(path, record)
        commit(canonical, [path], f"spend: card {card.task_id} stopped at its cap")


def hold_blocked(
    ctx: RunContext, card: Card, config: SpendConfig, allow_no_remote: bool
) -> None:
    """A blocked card at the run cap stays blocked: no requeue, no repair (module docstring)."""
    runs = counted_runs(card)
    cost = card_cost(ctx, card.task_id, config.prices).usd
    why = f"it was blocked after {runs} runs, and the cap is {config.max_runs} runs per card"
    _report(ctx, card, why, runs, cost, config, allow_no_remote, card.block_run_id)


def _over_cap(card: Card, used_runs: int, used_usd: float, config: SpendConfig) -> str | None:
    if config.max_runs:
        if card.state == "pending" and used_runs >= config.max_runs:
            return f"it already had {used_runs} runs, and the cap is {config.max_runs} runs per card"
        if card.state == "claimed" and used_runs > config.max_runs:
            return f"it started run {used_runs}, and the cap is {config.max_runs} runs per card"
    if config.max_card_usd and used_usd >= config.max_card_usd:
        return (
            f"its runs used an estimated ${used_usd:.2f} of model spend, and the cap is "
            f"${config.max_card_usd:.2f} per card"
        )
    return None


@dataclass
class SpendRun:
    stopped: list[tuple[str, str]] = field(default_factory=list)  # (task id, why)
    day_usd: float = 0.0
    over_budget: bool = False
    paused: bool = False  # this run paused for the budget
    unpriced: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # task id -> error


def _stop(
    ctx: RunContext, queue: Queue, card: Card, why: str, cost: float, config: SpendConfig,
    allow_no_remote: bool,
) -> None:
    """Block first, then record: a crash between them loses the help_request but never lets
    the card run on (a recorded stop with the card still running would reset its allowance)."""
    queue.stop(card.task_id, f"{SPEND_CAP_PREFIX} {why}")
    # Stopping a pending card adds a zero-length blocked run (Hermes, FileQueue): count it in
    # the base so an unblocked card starts from a full allowance.
    runs = counted_runs(card) + (1 if card.state == "pending" else 0)
    _report(ctx, card, why, runs, cost, config, allow_no_remote, None)


def enforce(
    ctx: RunContext,
    queue: Queue,
    config: SpendConfig,
    now: datetime.datetime,
    allow_no_remote: bool = False,
) -> SpendRun:
    """Stop pending and claimed cards over a cap; pause when today's spend reaches the daily
    budget (module docstring). One card that cannot be stopped does not stop the others."""
    result = SpendRun()
    canonical = ctx.canonical
    sessions = card_sessions(ctx)
    for card in queue.list():
        if card.state not in ("pending", "claimed"):
            continue
        cost = sessions_cost(ctx.hermes_home, sessions.get(card.task_id, set()), config.prices)
        result.unpriced += cost.unpriced
        runs, usd = _base(_record(canonical, card.task_id))
        why = _over_cap(card, counted_runs(card) - runs, cost.usd - usd, config)
        if why is None:
            continue
        try:
            _stop(ctx, queue, card, why, cost.usd, config, allow_no_remote)
        except Exception as error:  # noqa: BLE001 - reported; the other cards still go
            result.failures[card.task_id] = f"{type(error).__name__}: {error}"
            continue
        result.stopped.append((card.task_id, why))
    today = day_cost(ctx.hermes_home, now, config.prices)
    result.day_usd = today.usd
    result.unpriced += today.unpriced
    result.over_budget = bool(config.daily_usd) and today.usd >= config.daily_usd
    if result.over_budget:
        ledger = ledger_of(canonical)
        result.paused = not is_paused(ledger)
        day = f"{now.astimezone(datetime.UTC):%Y-%m-%d}"
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
    result.unpriced = sorted(set(result.unpriced))
    return result


def may_resume(canonical: Path, spend: SpendRun | None) -> bool:
    """Whether queue-keeper may lift the pause this run: never while over the daily budget,
    and not a budget pause when this run could not read the spend."""
    if spend is not None:
        return not spend.over_budget
    path = pause_path(ledger_of(canonical))
    reason = read_json(path).get("reason", "") if path.exists() else ""
    return not reason.startswith(BUDGET_PAUSE_PREFIX)


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
