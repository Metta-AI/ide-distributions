"""``autoresearch queue ...``: read the board and request cards.

Same envelope as ``autoresearch records``. Exit codes: 0 ok, 1 failure (unknown card, Kanban
error), 2 bad arguments (argparse errors, not in a worker, malformed request), 3 Hermes is
not installed.

``queue list|show`` read the board through ``queue_from_env``. ``queue request`` only writes
``cards/requests/<Q-id>.json`` into the current worker worktree; it takes effect after that
branch merges and queue-keeper admits it.
"""

import argparse
import os
from dataclasses import asdict

import jsonschema

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.paths import Layout
from autoresearch.queue.card import CardSpec
from autoresearch.queue.kanban_queue import KanbanError
from autoresearch.queue.requests import write_request
from autoresearch.queue.select import queue_from_env
from autoresearch.records.cli import EXIT_FAILURE, BadArguments, _fail, _ok

EXIT_HERMES_MISSING = 3
_MODES = ["investigation", "scouting", "experimentation", "maintenance", "planning"]
_PRIORITIES = ["P0", "P1", "P2", "P3", "P4", "P5"]
_STATES = ["pending", "claimed", "completed", "blocked"]


def _context() -> runctx.RunContext:
    try:
        return runctx.from_env()
    except KeyError as error:
        raise BadArguments(f"{error.args[0]} is not set") from None


def _worker_layout(ctx: runctx.RunContext) -> Layout:
    if ctx.worker is None:
        raise BadArguments("not in a worker worktree: requests are written into the worker")
    return Layout(ctx.worker)


def _list(args, ctx: runctx.RunContext) -> int:
    cards = queue_from_env(os.environ, ctx).list(args.state)
    return _ok({"cards": [asdict(card) for card in cards]})


def _show(args, ctx: runctx.RunContext) -> int:
    return _ok(asdict(queue_from_env(os.environ, ctx).get(args.task_id)))


def _request_create(args, ctx: runctx.RunContext) -> int:
    layout = _worker_layout(ctx)
    if ctx.card_id is None:
        raise BadArguments("no current card id (HERMES_KANBAN_TASK is not set)")
    spec = CardSpec(
        request_id="",  # replaced by write_request
        title=args.title,
        mode=args.mode,
        why=args.why,
        refs=args.ref,
        created_by=ctx.card_id,
        priority=args.priority,
        order=args.order,
    )
    return _ok({"request_id": write_request(layout, "create", spec=spec)})


def _request_reprioritize(args, ctx: runctx.RunContext) -> int:
    request_id = write_request(
        _worker_layout(ctx),
        "reprioritize",
        target_request=args.target,
        priority=args.priority,
        reason=args.reason,
    )
    return _ok({"request_id": request_id})


def _request_drop(args, ctx: runctx.RunContext) -> int:
    request_id = write_request(
        _worker_layout(ctx), "drop", target_request=args.target, reason=args.reason
    )
    return _ok({"request_id": request_id})


def _run(handler):
    def run(args) -> int:
        try:
            return handler(args, _context())
        except BadArguments as error:
            return _fail(str(error), 2)
        except jsonschema.ValidationError as error:
            return _fail(f"invalid request: {error.message}", 2)
        except KanbanError as error:
            code = EXIT_HERMES_MISSING if "hermes not found" in error.stderr else EXIT_FAILURE
            return _fail(str(error), code)
        except (KeyError, ValueError, OSError) as error:
            return _fail(str(error.args[0] if isinstance(error, KeyError) else error), 1)

    return run


def setup(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    sub = commands.add_parser("list", help="cards on the board")
    sub.add_argument("--state", choices=_STATES)
    sub.set_defaults(func=_run(_list))

    sub = commands.add_parser("show", help="one card, with its runs")
    sub.add_argument("task_id")
    sub.set_defaults(func=_run(_show))

    request = commands.add_parser("request", help="write a card request into this worker")
    ops = request.add_subparsers(dest="op", metavar="OP", required=True)

    sub = ops.add_parser("create", help="request a new card; prints the request id")
    sub.add_argument("--title", required=True)
    sub.add_argument("--mode", required=True, choices=_MODES)
    sub.add_argument("--why", required=True)
    sub.add_argument("--ref", action="append", default=[], metavar="URI")
    sub.add_argument("--priority", default="P2", choices=_PRIORITIES)
    sub.add_argument("--order", type=int, default=0, help="0..9, runs first within a priority")
    sub.set_defaults(func=_run(_request_create))

    sub = ops.add_parser("reprioritize", help="change a requested card's priority")
    sub.add_argument("target", metavar="Q-ID")
    sub.add_argument("--priority", required=True, choices=_PRIORITIES)
    sub.add_argument("--reason", required=True)
    sub.set_defaults(func=_run(_request_reprioritize))

    sub = ops.add_parser("drop", help="drop a requested card that has not started")
    sub.add_argument("target", metavar="Q-ID")
    sub.add_argument("--reason", required=True)
    sub.set_defaults(func=_run(_request_drop))


register_group("queue", setup)
