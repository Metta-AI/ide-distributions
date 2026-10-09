"""``status``: one read-only snapshot of the agent for the coach chat and operators.

``build`` gathers what other modules already know (pause, spend, key, queue, comms, promotion,
the distribution) into one dict. A part that cannot be read becomes a neutral value plus an
entry in ``warnings``; ``status`` never fails on one bad source. No credential is ever read
into the result: the key lookup keeps only the three numbers of ``GET /api/v1/key``.

Files read, besides the ledger and comms:

- ``<profile>/distribution.yaml``: ``version``.
- ``<profile>/.softmax-distribution-commit``: the installed distribution commit, plain text.
  Written by the Softmax runtime (hosted installs); absent otherwise (``commit`` null).
- ``<profile>/.softmax-update-failed``: JSON ``{"commit": str, "error": str}``, written by the
  Softmax runtime when a distribution update failed. Absent means no failed update.
- ``<canonical>/.supervisor/ledger/coach-hold.json``: present while the coach holds the agent.
"""

import datetime
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml

from autoresearch import __version__
from autoresearch.comms import delivery, reader
from autoresearch.comms.inbound import parse_time
from autoresearch.io.docs import read_json
from autoresearch.queue.base import Queue
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime import autonomy, spend
from autoresearch.runtime.ledger import ledger_of
from autoresearch.runtime.pause import is_paused, pause_path
from autoresearch.runtime.preflight import HttpGet, credit_floor, fetch_key_data
from autoresearch.runtime.promotion import champion_conflict

COACH_HOLD_FILE = "coach-hold.json"
DISTRIBUTION_FILE = "distribution.yaml"
COMMIT_MARKER = ".softmax-distribution-commit"
UPDATE_FAILED_MARKER = ".softmax-update-failed"
KEEPER_LOG = "keeper.log"
QUESTION_KINDS = frozenset({"question", "help_request"})
LOG_TAIL_BYTES = 4096

# Returns the policy version the league shows as our champion (None: none); may raise.
ChampionLookup = Callable[[], str | None]


def build(
    ctx: RunContext,
    env: Mapping[str, str],
    http_get: HttpGet,
    queue: Queue,
    champion_lookup: ChampionLookup,
    now: datetime.datetime | None = None,
) -> dict:
    """The ``data`` and ``warnings`` of the status envelope: ``{"data": {...}, "warnings": [...]}``."""
    now = now or datetime.datetime.now(datetime.UTC)
    warnings: list[str] = []
    ledger = ledger_of(ctx.canonical)
    mode, mode_problem = autonomy.autonomy_mode(env)
    if mode_problem:
        warnings.append(f"{autonomy.ENV_AUTONOMY}: {mode_problem}")
    floor, floor_warnings = credit_floor(env)
    data = {
        "distribution": _distribution(ctx.hermes_home, warnings),
        "paused": is_paused(ledger),
        "pause_reasons": _pause_reasons(ledger),
        "coach_hold": (ledger / COACH_HOLD_FILE).exists(),
        "autonomy": mode,
        "started": autonomy.is_started(ctx.canonical),
        "spend": _spend(ctx, env, now, warnings),
        "key": _key(env, ctx.hermes_home, http_get, warnings),
        "openrouter_floor_usd": floor,
        "running_cards": _running_cards(queue, warnings),
        "open_questions": _open_questions(ctx.canonical, now, warnings),
        "champion": _champion(ctx.canonical, champion_lookup, now, warnings),
        "last_keeper_run": _last_keeper_run(ctx),
    }
    return {"data": data, "warnings": [*floor_warnings, *warnings]}


def _distribution(profile: Path, warnings: list[str]) -> dict:
    version = __version__
    try:
        manifest = yaml.safe_load((profile / DISTRIBUTION_FILE).read_text(encoding="utf-8"))
        version = str(manifest["version"])
    except (OSError, yaml.YAMLError, TypeError, KeyError) as error:
        warnings.append(f"{DISTRIBUTION_FILE} version unreadable ({type(error).__name__})")
    return {
        "version": version,
        "commit": _read_text(profile / COMMIT_MARKER),
        "update_failed": _update_failed(profile / UPDATE_FAILED_MARKER, warnings),
    }


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _update_failed(path: Path, warnings: list[str]) -> dict | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        record = json.loads(text)
        return {"commit": str(record["commit"]), "error": str(record["error"])}
    except (ValueError, TypeError, KeyError):
        warnings.append(f"{UPDATE_FAILED_MARKER} is not JSON with commit and error")
        return {"commit": "unknown", "error": text}


def _pause_reasons(ledger: Path) -> list[str]:
    try:
        reason = read_json(pause_path(ledger)).get("reason")
    except (OSError, ValueError, AttributeError):
        return []
    return [str(reason)] if reason else []


def _spend(ctx: RunContext, env: Mapping[str, str], now: datetime.datetime, warnings: list[str]):
    try:
        config = spend.SpendConfig.from_env(env)
    except ValueError as error:
        warnings.append(f"spend config invalid, showing defaults: {error}")
        config = spend.SpendConfig()
    try:
        today = round(spend.day_cost(ctx.hermes_home, now, config.prices).usd, 4)
    except Exception as error:  # noqa: BLE001 - a missing or unreadable state.db is a warning
        warnings.append(f"today's spend unavailable: {type(error).__name__}: {error}")
        today = 0.0
    return {"today_usd": today, "daily_budget_usd": config.daily_usd}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _key(env, hermes_home: Path, http_get: HttpGet, warnings: list[str]) -> dict:
    data, key_warnings = fetch_key_data(env, hermes_home, http_get)
    warnings.extend(key_warnings)
    data = data or {}
    return {
        "limit_usd": _number(data.get("limit")),
        "usage_usd": _number(data.get("usage")),
        "limit_remaining_usd": _number(data.get("limit_remaining")),
    }


def _running_cards(queue: Queue, warnings: list[str]) -> list[dict]:
    try:
        cards = queue.list("claimed")
    except Exception as error:  # noqa: BLE001 - the board may be unreachable
        warnings.append(f"running cards unavailable: {type(error).__name__}: {error}")
        return []
    return [
        {"task": card.task_id, "mode": card.spec.mode, "title": card.spec.title} for card in cards
    ]


def _open_questions(canonical: Path, now: datetime.datetime, warnings: list[str]) -> int:
    """Published question and help_request messages with no reply, not superseded and not
    expired (the rules of ``comms.inbound.derived_status``, applied in one pass)."""
    try:
        published = reader.read_since(canonical, "status", after=0)
        answered = {
            message.get("in_reply_to")
            for message in delivery.inbox(canonical).values()
            if message.get("kind") == "reply"
        }
    except Exception as error:  # noqa: BLE001
        warnings.append(f"open questions unavailable: {type(error).__name__}: {error}")
        return 0
    superseded = {message.get("supersedes") for message in published}
    return sum(
        1
        for message in published
        if message.get("kind") in QUESTION_KINDS
        and message["id"] not in answered
        and message["id"] not in superseded
        and not (message.get("expires") and parse_time(message["expires"]) <= now)
    )


def _champion(canonical: Path, lookup: ChampionLookup, now, warnings: list[str]) -> dict:
    try:
        policy = lookup()
        conflict = champion_conflict(canonical, policy, now) is not None
    except Exception as error:  # noqa: BLE001 - Softmax unreachable, no game, no credential
        warnings.append(f"champion unavailable: {type(error).__name__}: {error}")
        return {"policy": None, "conflict": False}
    return {"policy": policy, "conflict": conflict}


def _last_keeper_run(ctx: RunContext) -> str | None:
    """The time stamp that starts the last line of ``keeper.log``."""
    path = runtime_dir(ctx) / KEEPER_LOG
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    last = next((line for line in reversed(lines) if line.strip()), "")
    return last.split(" ", 1)[0] or None
