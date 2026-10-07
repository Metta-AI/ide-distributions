"""``autoresearch records ...``: a thin JSON CLI over RecordStore.

Every command prints one JSON envelope to stdout: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"message": ..., "problems": [...]}}``. Exit codes: 0 ok,
1 validation or business failure (rule violation, record not found), 2 bad arguments
(argparse errors, malformed JSON or id, no workspace root).

The workspace root is ``--root`` if given, else the RunContext worker worktree if set, else
the canonical workspace.
"""

import argparse
import json
from pathlib import Path

from autoresearch import runctx
from autoresearch.cli import register_group
from autoresearch.paths import Layout
from autoresearch.records.registry import REGISTRY, entity_of_id
from autoresearch.records.store import RecordNotFound, RecordProblems, RecordStore

EXIT_OK, EXIT_FAILURE, EXIT_BAD_ARGS = 0, 1, 2


class BadArguments(ValueError):
    pass


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _ok(data) -> int:
    _emit({"ok": True, "data": data})
    return EXIT_OK


def _fail(message: str, code: int, problems=None) -> int:
    error = {"message": message}
    if problems is not None:
        error["problems"] = problems
    _emit({"ok": False, "error": error})
    return code


def _store(args) -> RecordStore:
    if args.root:
        return RecordStore(Layout(Path(args.root)))
    try:
        ctx = runctx.from_env()
    except KeyError as error:
        raise BadArguments(f"no --root given and {error.args[0]} is not set") from None
    return RecordStore(Layout(ctx.worker or ctx.canonical))


def _json_arg(text: str | None, file: str | None, what: str) -> dict:
    if file is not None:
        text = Path(file).read_text(encoding="utf-8")
    if text is None:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise BadArguments(f"{what} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise BadArguments(f"{what} must be a JSON object")
    return value


def _body_arg(text: str | None, file: str | None) -> str | None:
    if file is not None:
        return Path(file).read_text(encoding="utf-8")
    return text


def _check_id(id_: str) -> str:
    try:
        entity_of_id(id_)
    except ValueError as error:
        raise BadArguments(str(error)) from None
    return id_


def _loose_value(text: str):
    """A JSON value if ``text`` parses as one (object, number, ...), else the plain string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# --- handlers ---


def _new(args, store: RecordStore) -> int:
    meta = _json_arg(args.meta, args.meta_file, "--meta")
    body = _body_arg(args.body, args.body_file) or ""
    id_ = store.create(args.entity, meta, body, slug=args.slug)
    return _ok({"id": id_, "path": str(store.path_of(id_))})


def _show(args, store: RecordStore) -> int:
    id_ = _check_id(args.id)
    meta, body = store.load(id_)
    data = {"id": id_, "entity": entity_of_id(id_).name, "path": str(store.path_of(id_))}
    return _ok({**data, "meta": meta, "body": body})


def _update(args, store: RecordStore) -> int:
    id_ = _check_id(args.id)
    patch = _json_arg(args.patch, args.patch_file, "--patch")
    store.update(id_, patch, body=_body_arg(args.body, args.body_file))
    return _ok({"id": id_})


def _status(args, store: RecordStore) -> int:
    id_ = _check_id(args.id)
    store.set_status(id_, args.state, reason=args.reason, ref=args.ref)
    return _ok({"id": id_, "state": args.state})


def _link(args, store: RecordStore) -> int:
    id_ = _check_id(args.id)
    store.link(id_, args.field, _loose_value(args.target))
    return _ok({"id": id_, "field": args.field})


def _find(args, store: RecordStore) -> int:
    eq = {}
    for clause in args.where:
        key, separator, value = clause.partition("=")
        if not separator or not key:
            raise BadArguments(f"--where expects field=value, got {clause!r}")
        eq[key] = _loose_value(value)
    return _ok({"ids": store.find(args.entity, **eq)})


def _validate(args, store: RecordStore) -> int:
    if args.all == (args.id is not None):
        raise BadArguments("give exactly one of an id or --all")
    if args.all:
        checked, problems = store.validate_all()
    else:
        id_ = _check_id(args.id)
        found = store.validate(id_)
        checked, problems = 1, ({id_: found} if found else {})
    if problems:
        return _fail(f"{len(problems)} record(s) have problems", EXIT_FAILURE, problems)
    return _ok({"checked": checked})


def _run(handler):
    def run(args) -> int:
        try:
            return handler(args, _store(args))
        except BadArguments as error:
            return _fail(str(error), EXIT_BAD_ARGS)
        except RecordNotFound as error:
            return _fail(str(error), EXIT_FAILURE)
        except RecordProblems as error:
            return _fail(str(error), EXIT_FAILURE, error.problems)
        except (ValueError, OSError) as error:
            return _fail(str(error), EXIT_FAILURE)

    return run


# --- parser ---


def setup(parser: argparse.ArgumentParser) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="workspace root (default: worker worktree or canonical)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    entities = sorted(REGISTRY)

    def command(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(func=_run(handler))
        return sub

    def meta_args(sub, flag: str) -> None:
        group = sub.add_mutually_exclusive_group()
        group.add_argument(f"--{flag}", help="JSON object")
        group.add_argument(f"--{flag}-file", help="path to a JSON object")

    def body_args(sub) -> None:
        group = sub.add_mutually_exclusive_group()
        group.add_argument("--body", help="Markdown body")
        group.add_argument("--body-file", help="path to a Markdown body")

    sub = command("new", _new, "create a record; prints its id")
    sub.add_argument("entity", choices=entities)
    meta_args(sub, "meta")
    body_args(sub)
    sub.add_argument("--slug")

    sub = command("show", _show, "print a record")
    sub.add_argument("id")

    sub = command("update", _update, "merge a patch into a record's front matter / body")
    sub.add_argument("id")
    meta_args(sub, "patch")
    body_args(sub)

    sub = command("status", _status, "change a record's status with a reason and ref")
    sub.add_argument("id")
    sub.add_argument("state")
    sub.add_argument("--reason")
    sub.add_argument("--ref")

    sub = command("link", _link, "append a target (string or JSON) to a list field")
    sub.add_argument("id")
    sub.add_argument("field")
    sub.add_argument("target")

    sub = command("find", _find, "list ids of records matching field=value filters")
    sub.add_argument("entity", choices=entities)
    sub.add_argument("--where", action="append", default=[], metavar="FIELD=VALUE")

    sub = command("validate", _validate, "validate one record or --all records")
    sub.add_argument("id", nargs="?")
    sub.add_argument("--all", action="store_true")


register_group("records", setup)
