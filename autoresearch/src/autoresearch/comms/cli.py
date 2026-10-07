"""``autoresearch comms ...``: stage, read, resolve and send messages (Runtime 9.3, layer L1).

Same JSON envelope and exit codes as ``autoresearch records``: ``{"ok": true, "data": ...}``
or ``{"ok": false, "error": {"message", "problems"?}}``; 0 ok, 1 failure (invalid message,
not found, unresolvable), 2 bad arguments or no workspace.

- ``stage KIND --title T (--body B | --body-file F) [--ref R]... [--iteration N]
  [--priority P0..P5] [--expects-response | --no-expects-response] [--thread T]
  [--in-reply-to ID] [--supersedes M] [--expires ISO] [--fields JSON]``: a card worker stages
  a message in its worktree (RunContext worker; ``--root`` overrides). ``--ref`` is an
  ``ar://`` URI (rev ``@result``) or a JSON object ``{uri, rev?, role?, note?}``.
  Defaults: priority P2, expects_response true only for a question.
- ``list [--after N] [--consumer C [--advance]] [--all-kinds]``: published messages after
  ``N`` or after C's cursor; ``--advance`` then moves C's cursor to ``last_seq``. The listing
  and ``last_seq`` come from one commit snapshot (``rev`` in the data), so a publication
  that lands meanwhile is left for the next read. Data: ``{messages, last_seq, cursor, rev}``.
- ``get <seq|M-id>``, ``status <M-id>``, ``resolve <uri> [--rev R] [--game G]``.
- ``inbound KIND (--json J | --file F) --author A``: an external agent submits a
  ``reply``, ``steer``, ``task`` or ``seen`` (``comms.inbound``).

Every command except ``stage`` works on the canonical workspace (``--root`` overrides it).
"""

import argparse
import base64
import json
from pathlib import Path

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.comms import reader
from autoresearch.comms.envelope import PRIORITIES, MessageProblems
from autoresearch.comms.inbound import INBOUND_KINDS, InboundProblems, submit_inbound
from autoresearch.comms.resolver import ResolveError, resolve
from autoresearch.comms.stage import stage
from autoresearch.games.loader import GameError, load_game
from autoresearch.paths import Layout
from autoresearch.records.cli import EXIT_BAD_ARGS, EXIT_FAILURE, BadArguments, _fail, _ok

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _context() -> runctx.RunContext:
    try:
        return runctx.from_env()
    except KeyError as error:
        raise BadArguments(f"no --root given and {error.args[0]} is not set") from None


def _canonical(args) -> Path:
    return Path(args.root) if args.root else _context().canonical


def _json_object(text: str, what: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise BadArguments(f"{what} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise BadArguments(f"{what} must be a JSON object")
    return value


def _ref(text: str):
    return _json_object(text, "--ref") if text.lstrip().startswith("{") else {"uri": text}


def _read_file(file: str) -> str:
    try:
        return Path(file).read_text(encoding="utf-8")
    except OSError as error:
        raise BadArguments(f"cannot read {file}: {error.strerror or error}") from None


# --- handlers ---


def _stage(args) -> int:
    if args.root:
        worktree = Path(args.root)
    else:
        worktree = _context().worker
        if worktree is None:
            raise BadArguments("comms stage runs in a card worker (no worktree in RunContext)")
    body = _read_file(args.body_file) if args.body_file is not None else (args.body or "")
    fields = _json_object(args.fields, "--fields") if args.fields else {}
    for name in (
        "iteration",
        "priority",
        "expects_response",
        "thread",
        "in_reply_to",
        "supersedes",
        "expires",
    ):
        if getattr(args, name) is not None:
            fields[name] = getattr(args, name)
    refs = [_ref(text) for text in args.ref]
    message_id = stage(Layout(worktree), args.kind, args.title, body, refs, **fields)
    path = Layout(worktree).comms_staged / f"{message_id}.json"
    return _ok({"id": message_id, "path": str(path)})


def _list(args) -> int:
    canonical = _canonical(args)
    if args.advance and args.consumer is None:
        raise BadArguments("--advance needs --consumer")
    consumer = args.consumer or "_"
    if args.after is None and args.consumer is None:
        args.after = 0
    rev = reader.snapshot(canonical)  # one view for the listing and the advance target
    try:
        position = reader.cursor(canonical, consumer) if args.after is None else args.after
        messages = reader.read_since(canonical, consumer, position, args.all_kinds, rev=rev)
    except ValueError as error:
        raise BadArguments(str(error)) from None
    last = reader.last_seq(canonical, rev=rev)
    if args.advance:
        reader.advance(canonical, consumer, last)
    return _ok({"messages": messages, "last_seq": last, "cursor": position, "rev": rev})


def _get(args) -> int:
    key = int(args.key) if args.key.isdigit() else args.key
    return _ok(reader.get(_canonical(args), key))


def _status(args) -> int:
    return _ok(reader.status(_canonical(args), args.message_id))


def _resolve(args) -> int:
    game = None
    if args.game:
        try:
            game = load_game(_REPO_ROOT, args.game)
        except GameError as error:
            raise BadArguments(str(error)) from None
    resolved = resolve(_canonical(args), args.uri, args.rev, game=game)
    data = {"uri": args.uri, "path": resolved.path, "rev": resolved.rev}
    try:
        data["content"] = resolved.content.decode("utf-8")
    except UnicodeDecodeError:
        data["content_base64"] = base64.b64encode(resolved.content).decode("ascii")
    return _ok(data)


def _inbound(args) -> int:
    text = _read_file(args.file) if args.file is not None else args.json
    payload = _json_object(text, "--json")
    inbound_id = submit_inbound(_canonical(args), args.kind, payload, author=args.author)
    return _ok({"id": inbound_id, "kind": args.kind})


def _run(handler):
    def run(args) -> int:
        try:
            return handler(args)
        except BadArguments as error:
            return _fail(str(error), EXIT_BAD_ARGS)
        except (MessageProblems, InboundProblems) as error:
            return _fail(str(error), EXIT_FAILURE, error.problems)
        except (ResolveError, LookupError, ValueError, OSError) as error:
            return _fail(str(error), EXIT_FAILURE)

    return run


# --- parser ---


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="workspace root (stage: the worktree; others: canonical)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    sub = command("stage", _stage, "stage an outbound message in this card's worktree")
    sub.add_argument("kind")
    sub.add_argument("--title", required=True)
    body = sub.add_mutually_exclusive_group()
    body.add_argument("--body", help="Markdown body, written for the Preston-role agent")
    body.add_argument("--body-file")
    sub.add_argument("--ref", action="append", default=[], metavar="URI|JSON")
    sub.add_argument("--iteration", type=int, help="this card run's journal iteration")
    sub.add_argument("--priority", choices=PRIORITIES)
    sub.add_argument(
        "--expects-response", dest="expects_response", action=argparse.BooleanOptionalAction
    )
    sub.add_argument("--thread")
    sub.add_argument("--in-reply-to", dest="in_reply_to")
    sub.add_argument("--supersedes", metavar="M-ID")
    sub.add_argument("--expires", metavar="ISO-TIME")
    sub.add_argument("--fields", help="JSON object of extra envelope fields")

    sub = command("list", _list, "published messages after a seq or a consumer's cursor")
    sub.add_argument("--after", type=int)
    sub.add_argument("--consumer")
    sub.add_argument("--advance", action="store_true", help="move the cursor to last_seq")
    sub.add_argument("--all-kinds", action="store_true", help="include unknown kinds")

    sub = command("get", _get, "one published message by seq or M-id")
    sub.add_argument("key", metavar="SEQ|M-ID")

    sub = command("status", _status, "publication and seen status of a message")
    sub.add_argument("message_id", metavar="M-ID")

    sub = command("resolve", _resolve, "print the content an ar:// reference names")
    sub.add_argument("uri")
    sub.add_argument("--rev", help="workspace commit (default: HEAD)")
    sub.add_argument("--game", help="game under games/ whose reference types to add")

    sub = command("inbound", _inbound, "submit an inbound reply, steer, task or seen")
    sub.add_argument("kind", choices=INBOUND_KINDS)
    payload = sub.add_mutually_exclusive_group(required=True)
    payload.add_argument("--json", help="the message fields as a JSON object")
    payload.add_argument("--file", help="path to the message fields as a JSON object")
    sub.add_argument("--author", required=True, help="consumer name of the sender")


register_group("comms", setup)
