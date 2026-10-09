"""``report baseline``: performance and cost of the agent since a date, for the coach chat
(``baseline_report``) and the hosted baseline findings. Read only: it never writes.

``build`` returns ``{"data": {...}, "warnings": [...]}`` like ``status``; a source that cannot be
read becomes a warning and an empty section. No credential is read into the result.

Sources and rules:

- **Sessions.** The profile ``state.db`` (opened read-only, ``spend.state_rows``) and every
  compiler copy at ``<profile>/gota-compiler/*/state.db`` (written by the GotA build adapter,
  ``games/gota/tools/build.py``). A session's cost is ``spend.session_usd`` (actual, else
  Hermes' estimate, else the fallback prices). Days are UTC.
- **cards**: every card the plugin recorded a session for (``spend.card_sessions``) whose runs
  started on or after ``since``. Tokens and cost cover each run's session tree
  (``parent_session_id``); ``wall_s`` sums, per run, the last session end (now for one still
  open) minus the run's first start; ``model`` lists the runs' models in start order. ``mode``,
  ``runs`` and ``outcome`` (the last finished run's outcome, else the card state) come from the
  board, else from the card's latest handoff record.
- **days**: ``estimated_usd_by_source`` always has ``cli`` (card workers: Hermes sources
  ``kanban`` and ``cli``), ``api_server`` (the coach chat), ``compiler`` (the compiler copies)
  and ``cron``; any other source seen (``delegate``, ``oneshot``, ...) is an extra key with
  its own total. ``key_usage_delta_usd`` is the key's ``usage_daily`` for today and null for
  earlier days (no key history is kept). ``cards_finished`` counts cards with a ``done``
  handoff record first committed that day.
- **experiments**: experiment records created, or with XP evidence fetched, since ``since``.
  ``xp_requests`` is the record's list; ``episodes`` the rows of its ``xp-result`` evidence;
  ``credits`` is null (no per-request credit cost is stored); ``wall_s_to_evidence`` runs from
  the experiment's creation (its id's ULID time) to its latest evidence; the cost is that of
  every card whose refs name ``ar://experiment/<E>``, all time.
- **builds**: version records created since ``since`` (uploaded builds only). ``card`` is the
  card whose handoff created the version; ``wall_s`` is ``build.finished - build.started``;
  ``gates`` maps each gate (lower case) to its ``seconds`` (null when not recorded).
- **key**: ``GET /api/v1/key`` (``preflight.fetch_key_data``): limit, usage, remaining.
"""

import datetime
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from ulid import ULID

from autoresearch.ids import parse_id
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext
from autoresearch.runtime import spend
from autoresearch.runtime.ledger import ledger_of
from autoresearch.runtime.preflight import HttpGet, fetch_key_data
from autoresearch.runtime.status import finite_number

COMPILER_SESSIONS = "gota-compiler"  # games/gota/tools/build.py COMPILER_SESSIONS_DIR
NAMED_SOURCES = ("cli", "api_server", "compiler", "cron")
CARD_SOURCES = frozenset({"kanban", "cli"})  # reported as ``cli``
_SESSION_COLUMNS = (
    "s.id, s.source, s.model, s.started_at, s.ended_at, s.actual_cost_usd, "
    "s.estimated_cost_usd, s.cost_status, s.input_tokens, s.output_tokens, "
    "s.cache_read_tokens, s.cache_write_tokens"
)


def build(
    ctx: RunContext,
    env: Mapping[str, str],
    since: datetime.date,
    http_get: HttpGet,
    queue: Queue,
    now: datetime.datetime | None = None,
) -> dict:
    """The ``data`` and ``warnings`` of the report (module docstring)."""
    now = now or datetime.datetime.now(datetime.UTC)
    start = datetime.datetime.combine(since, datetime.time(), datetime.UTC)
    warnings: list[str] = []
    try:
        prices = spend.SpendConfig.from_env(env).prices
    except ValueError as error:
        warnings.append(f"spend config invalid, using default prices: {error}")
        prices = spend.SpendConfig().prices
    key_data, key_warnings = fetch_key_data(env, ctx.hermes_home, http_get)
    warnings.extend(key_warnings)
    key_data = key_data or {}
    board = _board(queue, warnings)
    handoffs = _handoffs(ctx.canonical, warnings)
    data = {
        "cards": _cards(ctx, start, now, prices, board, handoffs, warnings),
        "days": _days(ctx, start, now, prices, key_data, handoffs, warnings),
        "experiments": _experiments(ctx, start, prices, board, handoffs, warnings),
        "builds": _builds(ctx, start, handoffs, warnings),
        "key": {
            "limit_usd": finite_number(key_data.get("limit")),
            "usage_usd": finite_number(key_data.get("usage")),
            "limit_remaining_usd": finite_number(key_data.get("limit_remaining")),
        },
    }
    return {"data": data, "warnings": warnings}


# --- sources ---


def _board(queue: Queue, warnings: list[str]) -> dict:
    try:
        return {card.task_id: card for card in queue.list()}
    except Exception as error:  # noqa: BLE001 - the board may be unreachable
        warnings.append(f"board unavailable: {type(error).__name__}: {error}")
        return {}


def _handoffs(canonical: Path, warnings: list[str]) -> list[dict]:
    """Every handoff record, oldest first (``history[0].at``)."""
    records = []
    for path in sorted((ledger_of(canonical) / "handoffs").glob("*/*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            warnings.append(f"handoff {path.name} unreadable: {type(error).__name__}")
    return sorted(records, key=_handoff_time)


def _handoff_time(record: dict) -> str:
    history = record.get("history") or [{}]
    return str(history[0].get("at") or "")


def _session_rows(home: Path, query: str, params: tuple, warnings: list[str]) -> list:
    try:
        return spend.state_rows(home, query, params)
    except sqlite3.Error as error:
        warnings.append(f"{home / spend.STATE_DB} unreadable: {type(error).__name__}: {error}")
        return []


def _usd(row, prices) -> float:
    return spend.session_usd(row, prices)[0]


def _id_time(record_id: str) -> datetime.datetime:
    return ULID.from_str(parse_id(record_id)[1]).datetime


def _seconds(started: str | None, finished: str | None) -> float | None:
    try:
        begin = datetime.datetime.fromisoformat(str(started))
        end = datetime.datetime.fromisoformat(str(finished))
        return round((end - begin).total_seconds(), 3)
    except (ValueError, TypeError):  # missing, malformed, or one of them without a zone
        return None


# --- cards ---


def _card_trees(ctx: RunContext, roots: set[str], warnings: list[str]) -> list:
    """Rows of every session under ``roots``, each with the ``root`` it descends from."""
    if not roots:
        return []
    marks = ",".join("?" * len(roots))
    query = (
        f"WITH RECURSIVE tree(id, root) AS (SELECT id, id FROM sessions WHERE id IN ({marks}) "
        "UNION SELECT s.id, t.root FROM sessions s JOIN tree t ON s.parent_session_id = t.id) "
        f"SELECT tree.root, {_SESSION_COLUMNS} FROM sessions s JOIN tree USING (id)"
    )
    return _session_rows(ctx.hermes_home, query, tuple(sorted(roots)), warnings)


def _cards(ctx, start, now, prices, board, handoffs, warnings) -> list[dict]:
    sessions = spend.card_sessions(ctx)
    owner = {root: task for task, roots in sessions.items() for root in roots}
    rows = _card_trees(ctx, set(owner), warnings)
    # A recorded session can be another's descendant (a ``session_end`` after compression):
    # count it once, under the run it continues.
    descendants = {row["id"] for row in rows if row["id"] != row["root"]}
    by_task: dict[str, list] = defaultdict(list)
    for row in rows:
        if row["root"] not in descendants:
            by_task[owner[row["root"]]].append(row)
    latest_handoff = {record.get("card"): record for record in handoffs}
    cards = []
    for task, task_rows in sorted(by_task.items()):
        root_rows = sorted((row for row in task_rows if row["id"] == row["root"]),
                           key=lambda row: row["started_at"])
        if not root_rows or root_rows[-1]["started_at"] < start.timestamp():
            continue
        cards.append(
            {
                "task": task,
                **_card_meta(board.get(task), latest_handoff.get(task), len(root_rows)),
                "model": ", ".join(dict.fromkeys(r["model"] for r in root_rows if r["model"]))
                or None,
                "wall_s": _wall_s(task_rows, root_rows, now),
                "input_tokens": sum(row["input_tokens"] or 0 for row in task_rows),
                "output_tokens": sum(row["output_tokens"] or 0 for row in task_rows),
                "cache_read_tokens": sum(row["cache_read_tokens"] or 0 for row in task_rows),
                "estimated_usd": round(sum(_usd(row, prices) for row in task_rows), 4),
            }
        )
    return cards


def _card_meta(card, handoff: dict | None, sessions_seen: int) -> dict:
    if card is not None:
        finished = [run.outcome for run in card.runs if run.terminal and run.outcome]
        return {
            "mode": card.spec.mode,
            "runs": len(card.runs),
            "outcome": finished[-1] if finished else card.state,
        }
    return {
        "mode": handoff.get("mode") if handoff else None,
        "runs": sessions_seen,
        "outcome": handoff.get("outcome") if handoff else None,
    }


def _wall_s(rows, root_rows, now: datetime.datetime) -> float:
    last_end: dict[str, float] = {}
    for row in rows:
        end = row["ended_at"] or now.timestamp()
        last_end[row["root"]] = max(last_end.get(row["root"], 0.0), end)
    return round(sum(last_end[row["id"]] - row["started_at"] for row in root_rows), 3)


# --- days ---


def _days(ctx, start, now, prices, key_data, handoffs, warnings) -> list[dict]:
    query = f"SELECT {_SESSION_COLUMNS} FROM sessions s WHERE s.started_at >= ?"
    params = (start.timestamp(),)
    spend_by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in _session_rows(ctx.hermes_home, query, params, warnings):
        source = "cli" if row["source"] in CARD_SOURCES else str(row["source"])
        spend_by_day[_utc_date(row["started_at"])][source] += _usd(row, prices)
    for home in sorted((ctx.hermes_home / COMPILER_SESSIONS).glob(f"*/{spend.STATE_DB}")):
        for row in _session_rows(home.parent, query, params, warnings):
            spend_by_day[_utc_date(row["started_at"])]["compiler"] += _usd(row, prices)
    finished = _cards_finished(handoffs)
    today = now.astimezone(datetime.UTC).date()
    days = []
    day = start.date()
    while day <= today:
        date = day.isoformat()
        by_source = {source: 0.0 for source in NAMED_SOURCES} | dict(spend_by_day.get(date, {}))
        days.append(
            {
                "date": date,
                "estimated_usd_by_source": {k: round(v, 4) for k, v in by_source.items()},
                "key_usage_delta_usd": (
                    finite_number(key_data.get("usage_daily")) if day == today else None
                ),
                "cards_finished": finished.get(date, 0),
            }
        )
        day += datetime.timedelta(days=1)
    return days


def _utc_date(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp, datetime.UTC).date().isoformat()


def _cards_finished(handoffs: list[dict]) -> dict[str, int]:
    """Date -> cards whose first ``done`` handoff was committed that day."""
    first_done: dict[str, str] = {}
    for record in handoffs:
        if record.get("outcome") == "done" and _handoff_time(record):
            first_done.setdefault(record.get("card"), _handoff_time(record)[:10])
    counts: dict[str, int] = defaultdict(int)
    for date in first_done.values():
        counts[date] += 1
    return counts


# --- experiments ---


def _experiments(ctx, start, prices, board, handoffs, warnings) -> list[dict]:
    try:
        return _read_experiments(ctx, start, prices, board, handoffs, warnings)
    except Exception as error:  # noqa: BLE001 - an unreadable record: the rest still report
        warnings.append(f"experiments unavailable: {type(error).__name__}: {error}")
        return []


def _read_experiments(ctx, start, prices, board, handoffs, warnings) -> list[dict]:
    store = RecordStore(Layout(ctx.canonical))
    evidence = [store.load(i)[0] for i in store.find("evidence", kind="xp-result")]
    experiment_ids = store.find("experiment")
    by_experiment: dict[str, list[dict]] = defaultdict(list)
    for record in evidence:
        by_experiment[(record.get("source_ids") or {}).get("experiment")].append(record)
    refs = _card_refs(board, handoffs)
    sessions = spend.card_sessions(ctx)
    found = []
    for experiment in experiment_ids:
        created = _id_time(experiment)
        fetched = sorted(str(record.get("fetched_at")) for record in by_experiment[experiment])
        if created < start and not (fetched and fetched[-1] >= start.isoformat()):
            continue
        meta, _ = store.load(experiment)
        ref = f"ar://experiment/{experiment}"
        roots = set().union(*(sessions.get(task, set()) for task, r in refs.items() if ref in r))
        try:
            usd = spend.sessions_cost(ctx.hermes_home, roots, prices).usd
        except sqlite3.Error as error:
            warnings.append(f"{experiment} card cost unavailable: {type(error).__name__}")
            usd = None
        found.append(
            {
                "id": experiment,
                "xp_requests": len(meta.get("xp_requests") or []),
                "episodes": sum(
                    _count_rows(ctx.canonical, record) for record in by_experiment[experiment]
                ),
                "credits": None,
                "wall_s_to_evidence": _seconds(created.isoformat(), fetched[-1]) if fetched
                else None,
                "estimated_usd_of_its_cards": None if usd is None else round(usd, 4),
            }
        )
    return found


def _card_refs(board: dict, handoffs: list[dict]) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = defaultdict(set)
    for task, card in board.items():
        refs[task].update(card.spec.refs)
    for record in handoffs:
        refs[record.get("card")].update(record.get("refs") or [])
    return refs


def _count_rows(canonical: Path, evidence: dict) -> int:
    """Episode rows of one ``xp-result`` evidence record (``rows_path``, under canonical)."""
    path = canonical / str(evidence.get("rows_path") or "")
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


# --- builds ---


def _builds(ctx, start, handoffs, warnings) -> list[dict]:
    try:
        return _read_builds(ctx, start, handoffs)
    except Exception as error:  # noqa: BLE001 - an unreadable record: the rest still report
        warnings.append(f"builds unavailable: {type(error).__name__}: {error}")
        return []


def _read_builds(ctx, start, handoffs) -> list[dict]:
    store = RecordStore(Layout(ctx.canonical))
    creator = {
        created: record.get("card")
        for record in handoffs
        for created in (record.get("report") or {}).get("created", [])
    }
    builds = []
    for version in store.find("version"):
        if _id_time(version) < start:
            continue
        meta, _ = store.load(version)
        build = meta.get("build") or {}
        gates = meta.get("gates") or {}
        builds.append(
            {
                "card": creator.get(version),
                "version": version,
                "wall_s": _seconds(build.get("started"), build.get("finished")),
                "gates": {
                    name.lower(): finite_number((result or {}).get("seconds"))
                    for name, result in gates.items()
                    if isinstance(result, dict)
                },
            }
        )
    return builds
