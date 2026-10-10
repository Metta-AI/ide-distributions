"""The IDE coach chat inside this profile (coach-chat-role.md §1-§5, plan 3.7). STDLIB ONLY.

On Softmax hosting every coach line in the IDE chat becomes one Hermes run on the API server
(platform ``api_server``); Kanban research workers run as ``cli``. The chat is the coach's
interface, not a second research agent:

- **Tools.** The toolset ``autoresearch_coach``: fixed-argv read commands of the profile CLI
  (``READ_TOOLS``; output cut to ``OUTPUT_MAX`` characters) and ``propose_inbound``. The names
  are the spec's (``status``, ``list_cards``, ...); because they are generic,
  ``foreign_tool_names`` checks that Hermes's registry maps each to our handler, and any name
  owned by another plugin turns the coach tools off. A chat turn may make at most
  ``MAX_TOOL_ROUNDS`` model calls with tools. Config
  gives ``api_server`` only this toolset; ``adapters.pre_tool_call`` also blocks every other
  tool in a chat session, because Hermes's config load fails open to the full default
  toolset. Every tool refuses unless its session was recorded as a chat session by
  ``chat_turn`` (``pre_llm_call`` with platform ``api_server``), and under
  ``HERMES_KANBAN_TASK``.
- **Proposals, confirmed by the coach's own line.** No model tool call ever runs ``comms
  inbound`` (the policy and the CLI refuse it). ``propose_inbound`` validates the payload
  with ``comms inbound --dry-run`` and stores it in memory (``proposals``); the
  ``transform_llm_output`` hook puts the plugin's rendering first in the reply; ``chat_turn``
  sends it only when the coach's whole stripped line is ``confirm P-xxxx`` in the same
  session (state is keyed by profile home and session id). The send runs on a plugin thread,
  so the hook returns inside Hermes's 30 s hook timeout; its outcome is a plugin-written line
  on the next reply. ``cancel P-xxxx`` drops it. ``/pause-research`` and ``/resume-research`` (whole
  line) send a ``control`` inbound with no proposal: the line is the coach's.
- **Environment (I5).** The tools run in the one multiplexed gateway process, whose
  environment is the runtime's (no ``AUTORESEARCH_CLI``, no profile ``HERMES_HOME``, no
  player credential). ``profile_env`` resolves the routed profile with Hermes's
  ``get_hermes_home()`` at call time, refuses unless it carries this plugin
  (``plugins/autoresearch/plugin.yaml``) and a ``.env``, and builds the CLI's environment:
  the gateway's minus every ``HERMES_*``/``AUTORESEARCH_*``/``SOFTMAX_*`` name (and the
  Python-path names of the gateway's venv), plus the profile ``.env`` as Hermes loads it,
  plus ``HERMES_HOME=<profile>``. Not isolation: everything is one uid.
- **Chat budget.** ``AUTORESEARCH_CHAT_DAILY_USD`` (default 3, 0 off) per UTC day, from
  snapshots of the chat sessions' running cost (``runtime.state_db.chat_day_usd``, file
  ``<profile>/local/chat-spend.json``). Over it, every tool but ``status`` refuses and the
  turn's context tells the model to say so. Research spend is counted apart
  (``runtime.spend.day_cost`` leaves chat sessions out).

Events go to ``<canonical>/.supervisor/runtime/chat_events.jsonl``: chat turns, read tools and
whether they returned ``ok``, proposals, shown lines and sends (ids and kinds, never payload
text).
"""

import datetime
import json
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from autoresearch import runctx
from autoresearch.io.dotenv import worker_env
from autoresearch.plugin import proposals
from autoresearch.runtime import state_db

TOOLSET = "autoresearch_coach"
CHAT_PLATFORM = "api_server"
OUTPUT_MAX = 12_000
TRUNCATED = '..." [truncated]'
READ_TIMEOUT_S = 60
EXIT_BAD_ARGS = 2  # the CLI's exit for bad arguments or a refused context (records.cli)
SEND_TIMEOUT_S = 300  # comms inbound waits for the canonical lock, then commits and pushes
MAX_TOOL_ROUNDS = 8  # model calls with tools per chat turn (coach-chat-role §4, the shim's cap)
ENV_CHAT_DAILY_USD = "AUTORESEARCH_CHAT_DAILY_USD"
DEFAULT_CHAT_DAILY_USD = 3.0
PROPOSABLE_KINDS = ("task", "steer", "reply", "control")
OUR_PLUGIN = Path("plugins") / "autoresearch" / "plugin.yaml"
CHAT_SPEND_FILE = state_db.CHAT_SPEND_FILE
CHAT_EVENTS_FILE = "chat_events.jsonl"
DISTRIBUTION_ROOT = Path(__file__).resolve().parents[3]
BRIEF_PATH = DISTRIBUTION_ROOT / "doctrine" / "coach-chat.md"
INBOUND_SCHEMAS = DISTRIBUTION_ROOT / "schemas" / "comms" / "inbound"
BRIEF_FALLBACK = (
    "You are the IDE coach chat of the autoresearch agent. Use only your coach tools. Never "
    "claim something was sent unless the plugin said so."
)
STRIPPED_PREFIXES = ("HERMES_", "AUTORESEARCH_", "SOFTMAX_")
STRIPPED_NAMES = frozenset({"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "PYTHONPATH"})

CONFIRM = re.compile(rf"confirm ({proposals.ID_PATTERN})")
CANCEL = re.compile(rf"cancel ({proposals.ID_PATTERN})")
CONTROL = re.compile(r"/(pause|resume)-research")
_ARG = re.compile(r"[^\x00-\x1f-][^\x00-\x1f]{0,499}")  # one CLI word, never an option
_WHERE_KEY = re.compile(r"[a-z_][a-z0-9_]{0,63}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

STORE = proposals.Store()
_sessions: dict[str, float] = {}  # chat key (profile home + session id) -> last turn time
_sessions_lock = threading.Lock()
_rounds: dict[str, tuple[str, set[str], int]] = {}  # chat key -> (turn id, api requests, calls)
_sends: list[threading.Event] = []  # one per queued send, set when it is done (tests wait)
# Profile home -> the FIFO of its sends. One daemon worker drains each, so sends to a profile
# go out in the order the coach typed them (``_send_in_background``).
_send_queues: dict[Path, queue.Queue] = {}
_spend_lock = threading.Lock()


def _now() -> float:
    """The clock proposals expire by (a seam for tests)."""
    return time.time()


class Refused(Exception):
    """A coach tool or send that must not run; the message says why, in plain words."""


class SendUnknown(Exception):
    """A send whose outcome is not known (timed out, or failed unexpectedly): it may or may
    not have been committed."""


# --- sessions ---


def chat_key(session_id: str | None) -> str:
    """What chat state is keyed by: the routed profile home and the session id, so a proposal
    made through one profile can never be confirmed through another. "" without a session."""
    return f"{_hermes_home() or ''}\n{session_id}" if session_id else ""


def record_chat_session(session_id: str | None) -> bool:
    """Remember ``session_id`` (in this profile) as a chat session; True when this process had
    not seen it."""
    key = chat_key(session_id)
    if not key:
        return False
    with _sessions_lock:
        new = key not in _sessions
        _sessions[key] = time.time()
        return new


def is_chat_session(session_id: str | None) -> bool:
    key = chat_key(session_id)
    with _sessions_lock:
        return bool(key) and key in _sessions


def over_round_cap(session_id: str | None, turn_id: str | None, api_request_id: str | None) -> bool:
    """Count this tool call against the turn's ``MAX_TOOL_ROUNDS`` model calls (one round per
    ``api_request_id``; every call counts as a round when Hermes gives none). True once over."""
    key = chat_key(session_id)
    with _sessions_lock:
        turn, requests, calls = _rounds.get(key, ("", set(), 0))
        if turn != (turn_id or ""):
            turn, requests, calls = turn_id or "", set(), 0
        if api_request_id:
            requests = requests | {api_request_id}
        calls += 1
        _rounds[key] = (turn, requests, calls)
        return (len(requests) if api_request_id else calls) > MAX_TOOL_ROUNDS


def session_platform() -> str:
    """The platform Hermes's gateway bound for this turn (``HERMES_SESSION_PLATFORM``, a
    context variable of the API server's turn), or "" outside the gateway and in a Kanban
    card (whose own process could inherit the name)."""
    if os.environ.get(runctx.ENV_CARD_ID):
        return ""
    try:
        from gateway.session_context import get_session_env

        return get_session_env("HERMES_SESSION_PLATFORM") or ""
    except Exception:  # noqa: BLE001 - not inside Hermes, or an older Hermes
        return ""


# --- the profile's environment (I5) ---


def _hermes_home() -> str | None:
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except ImportError:
        return os.environ.get(runctx.ENV_HERMES_HOME)


def profile_env() -> tuple[Path, dict[str, str]]:
    """(profile home, environment for its CLI) for the profile this turn is routed to (module
    docstring). Raises Refused when that is not one of our profiles or has no CLI yet."""
    raw = _hermes_home()
    if not raw:
        raise Refused("no profile home: Hermes did not route this turn to a profile")
    home = Path(raw)
    if not (home / OUR_PLUGIN).is_file():
        raise Refused(f"{home} is not an autoresearch profile (no {OUR_PLUGIN.as_posix()})")
    if not (home / ".env").is_file():
        raise Refused(f"{home} has no .env")
    base = {
        name: value for name, value in os.environ.items()
        if not name.startswith(STRIPPED_PREFIXES) and name not in STRIPPED_NAMES
    }
    try:
        env = worker_env(base, home)
    except (OSError, UnicodeDecodeError) as error:
        raise Refused(f"{home}/.env cannot be read: {type(error).__name__}") from None
    env[runctx.ENV_HERMES_HOME] = str(home)
    if not env.get("AUTORESEARCH_CLI"):
        raise Refused(
            "AUTORESEARCH_CLI is not in the profile .env yet: the first queue-keeper run "
            "(bootstrap) writes it"
        )
    return home, env


def _canonical(home: Path, env: dict[str, str]) -> Path:
    return Path(env.get(runctx.ENV_CANONICAL) or home / "workspace")


def _log(event: dict, where: tuple[Path, dict[str, str]] | None = None) -> None:
    """Best effort: a chat event in the profile's runtime dir (``where``: the profile_env of
    this turn, for a worker thread that cannot resolve it); never raises."""
    try:
        home, env = where or profile_env()
        directory = _canonical(home, env) / ".supervisor" / "runtime"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / CHAT_EVENTS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **event}, default=str) + "\n")
    except Exception:  # noqa: BLE001, S110 - a plugin must never break the agent
        pass


def log_error(hook: str, session_id: str | None, error: BaseException) -> None:
    _log({"event": "error", "hook": hook, "session_id": session_id, "error": repr(error)[:500]})


# --- running the profile CLI ---


def spawn(argv: list[str], env: dict[str, str], cwd: str, timeout: float):
    """Run ``argv`` (no shell) in a session of its own and return its CompletedProcess. On
    timeout the whole process group is killed (the CLI's ``git commit``/``git push`` children
    too, not only the shim) and TimeoutExpired is raised."""
    process = subprocess.Popen(
        argv, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:  # timeout, or anything else while waiting: never leave it running
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=10)
        except Exception:  # noqa: BLE001, S110 - reaping is best effort; the error below wins
            pass
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run_cli(env: dict[str, str], args: list[str], timeout: float) -> dict:
    """``"$AUTORESEARCH_CLI" <args>`` with no shell; its JSON envelope, or a failure
    envelope when it printed none (``error.timed_out`` when it was killed at ``timeout``)."""
    argv = [env["AUTORESEARCH_CLI"], *args]
    try:
        result = spawn(argv, env, env[runctx.ENV_HERMES_HOME], timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"message": f"{args[0]}: timed out after {timeout:.0f} s",
                                       "timed_out": True}}
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "error": {"message": f"{args[0]}: {type(error).__name__}: {error}"}}
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and "ok" in envelope:
        if not envelope.get("ok") and isinstance(envelope.get("error"), dict):
            envelope["error"].setdefault("exit_code", result.returncode)
        return envelope
    detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
    return {"ok": False, "error": {"message": f"exit {result.returncode}: {detail[0][:300]}",
                                   "exit_code": result.returncode}}


def _cut(text: str) -> str:
    return text if len(text) <= OUTPUT_MAX else text[: OUTPUT_MAX - len(TRUNCATED)] + TRUNCATED


def _refusal(reason: str) -> str:
    return json.dumps({"ok": False, "error": {"message": reason}})


def _author(env: dict[str, str]) -> str:
    player = (env.get("AUTORESEARCH_PLAYER_ID") or "").strip()
    if not player:
        raise Refused("AUTORESEARCH_PLAYER_ID is not in the profile .env")
    return f"coach:{player}"


# --- the chat budget ---


def _chat_cap(env: dict[str, str]) -> float:
    raw = (env.get(ENV_CHAT_DAILY_USD) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CHAT_DAILY_USD
    except ValueError:
        return DEFAULT_CHAT_DAILY_USD
    return value if value >= 0 else DEFAULT_CHAT_DAILY_USD


def budget_problem(home: Path, env: dict[str, str], now: datetime.datetime) -> str | None:
    """Why the chat budget stops the coach tools today, or None. Updates the day snapshot.
    A spend that cannot be read stops them too (fail closed)."""
    cap = _chat_cap(env)
    try:
        prices = state_db.parse_prices(env.get(state_db.ENV_FALLBACK_PRICES))
    except ValueError:
        prices = dict(state_db.DEFAULT_FALLBACK_PRICES)
    try:
        with _spend_lock:
            spent = state_db.chat_day_usd(home, home / CHAT_SPEND_FILE, now, prices)
    except Exception as error:  # noqa: BLE001 - unreadable spend: refuse, say why
        return f"the chat's spend cannot be read ({type(error).__name__}), so coach tools are off"
    if cap and spent >= cap:
        return (
            f"the chat budget for today is spent: an estimated ${spent:.2f} of ${cap:.2f} "
            f"({ENV_CHAT_DAILY_USD}, per UTC day). Only status works until 00:00 UTC. Research "
            "is not affected."
        )
    return None


# --- tools ---


def _gate(tool: str, session_id: str | None) -> tuple[Path, dict[str, str]]:
    if os.environ.get(runctx.ENV_CARD_ID):
        raise Refused("coach tools do not run in a Kanban card")
    if not is_chat_session(session_id):
        raise Refused("coach tools run only in the IDE coach chat (an api_server session)")
    home, env = profile_env()
    if tool != "status":
        problem = budget_problem(home, env, datetime.datetime.now(datetime.UTC))
        if problem:
            raise Refused(problem)
    return home, env


def _word(args: dict, key: str) -> str:
    value = args.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not _ARG.fullmatch(value):
        raise Refused(f"{key}: give one value (not empty, not starting with '-')")
    return value


def _optional_word(args: dict, key: str) -> str | None:
    return _word(args, key) if args.get(key) not in (None, "") else None


def _list_messages(args: dict, env: dict[str, str]) -> list[str]:
    after = args.get("after") or 0
    if isinstance(after, str) and after.isdigit():
        after = int(after)
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise Refused("after: a seq number, 0 or more")
    return ["comms", "list", "--after", str(after)]


def _resolve_ref(args: dict, env: dict[str, str]) -> list[str]:
    argv = ["comms", "resolve", _word(args, "uri")]
    rev = _optional_word(args, "rev")
    if rev and rev != "@result":
        argv += ["--rev", rev]
    if env.get("AUTORESEARCH_GAME"):
        argv += ["--game", env["AUTORESEARCH_GAME"]]
    return argv


def _find_records(args: dict, env: dict[str, str]) -> list[str]:
    argv = ["records", "find", _word(args, "entity")]
    where = args.get("where") or {}
    if not isinstance(where, dict):
        raise Refused("where: an object of field=value filters")
    for name, value in sorted(where.items()):
        if not _WHERE_KEY.fullmatch(str(name)):
            raise Refused(f"where: {name!r} is not a field name")
        argv += ["--where", f"{name}={_word({f'where.{name}': value}, f'where.{name}')}"]
    return argv


def _baseline(args: dict, env: dict[str, str]) -> list[str]:
    since = _word(args, "since")
    if not _DAY.fullmatch(since):
        raise Refused("since: a UTC day, YYYY-MM-DD")
    return ["report", "baseline", "--since", since, "--json"]


_STR = {"type": "string"}
# name -> (description, JSON Schema properties, required, argv builder). The argv is fixed per
# tool; arguments become single CLI words (never options).
READ_TOOLS = {
    "list_messages": (
        "Published research messages (outbox) with seq above `after`, oldest first.",
        {"after": {"type": "integer", "description": "seq to start after (0 = all)"}}, [],
        _list_messages,
    ),
    "get_message": (
        "One published message by seq or M-id, or an inbound message by its id.",
        {"key": _STR}, ["key"], lambda a, e: ["comms", "get", _word(a, "key")],
    ),
    "message_status": (
        "Status of a published message (open, answered, withdrawn, expired) and who saw it.",
        {"message_id": _STR}, ["message_id"],
        lambda a, e: ["comms", "status", _word(a, "message_id")],
    ),
    "resolve_ref": (
        "Content of an ar:// reference, at a workspace commit (default: the latest).",
        {"uri": _STR, "rev": _STR}, ["uri"], _resolve_ref,
    ),
    "list_cards": (
        "Cards on the research agent's Kanban board.", {}, [], lambda a, e: ["queue", "list"],
    ),
    "show_card": (
        "One card with its runs.", {"task_id": _STR}, ["task_id"],
        lambda a, e: ["queue", "show", _word(a, "task_id")],
    ),
    "find_records": (
        "Ids of the records of one type, optionally filtered by field=value.",
        {
            "entity": {
                "type": "string",
                "enum": [
                    "topic", "hypothesis", "experiment", "result", "claim", "hub", "lesson",
                    "journal", "version", "evidence",
                ],
            },
            "where": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        ["entity"], _find_records,
    ),
    "show_record": (
        "One record (front matter and body) by id.", {"record_id": _STR}, ["record_id"],
        lambda a, e: ["records", "show", _word(a, "record_id")],
    ),
    "promotion_status": (
        "The champion pipeline: the current promotion record.", {}, [],
        lambda a, e: ["promotion", "status"],
    ),
    "status": (
        (
            "The agent now: paused or running and why, coach hold, today's research spend "
            "against its budget, the key's credit, running cards, open questions, permit asks "
            "waiting for the coach, the champion. "
            "Works even when the chat budget is spent."
        ),
        {}, [], lambda a, e: ["status", "--json"],
    ),
    "baseline_report": (
        "Cost and outcome report of research since a UTC day: spend, runs, cards.",
        {"since": {"type": "string", "description": "first UTC day, YYYY-MM-DD"}}, ["since"],
        _baseline,
    ),
}


def _read_handler(name: str):
    builder = READ_TOOLS[name][3]

    def handler(args, session_id=None, **kwargs):
        try:
            _, env = _gate(name, session_id)
            argv = builder(args if isinstance(args, dict) else {}, env)
        except Refused as refused:
            return _refusal(str(refused))
        envelope = run_cli(env, argv, READ_TIMEOUT_S)
        _log({"event": "tool", "session_id": session_id, "tool": name,
              "ok": envelope.get("ok") is True})
        return _cut(json.dumps(envelope, default=str))

    return handler


def proposable_fields(kind: str) -> frozenset[str]:
    """The fields a proposal of ``kind`` may carry: its schema's properties, but ``kind``."""
    schema = json.loads((INBOUND_SCHEMAS / f"{kind}.json").read_text(encoding="utf-8"))
    return frozenset(schema.get("properties", {})) - {"kind"}


def propose_inbound(args, session_id=None, **kwargs):
    """Validate and store a proposal; nothing is sent (module docstring)."""
    args = args if isinstance(args, dict) else {}
    try:
        _, env = _gate("propose_inbound", session_id)
        kind, payload = args.get("kind"), args.get("payload")
        summary = args.get("summary") if isinstance(args.get("summary"), str) else ""
        if kind not in PROPOSABLE_KINDS:
            raise Refused(f"kind must be one of {', '.join(PROPOSABLE_KINDS)}")
        if not isinstance(payload, dict):
            raise Refused("payload must be an object with the fields of that kind")
        unknown = sorted(set(payload) - proposable_fields(kind))
        if unknown:
            raise Refused(
                f"payload fields not in the {kind} schema: {', '.join(unknown)}. Send only "
                "the kind's fields: the coach must see everything that is sent"
            )
        author = _author(env)
    except Refused as refused:
        return _refusal(str(refused))
    checked = run_cli(
        env,
        ["comms", "inbound", kind, "--json", json.dumps(payload), "--author", author, "--dry-run"],
        READ_TIMEOUT_S,
    )
    if not checked.get("ok"):
        return _cut(json.dumps(checked, default=str))
    answers = None
    if kind == "reply":
        answered = run_cli(env, ["comms", "get", payload["in_reply_to"]], READ_TIMEOUT_S)
        if not answered.get("ok"):
            return _cut(json.dumps(answered, default=str))
        title = (answered.get("data") or {}).get("title")
        answers = title if isinstance(title, str) else None
    proposal = STORE.propose(chat_key(session_id), kind, payload, summary, _now(), answers=answers)
    _log({"event": "proposed", "session_id": session_id, "proposal": proposal.id, "kind": kind})
    return json.dumps({
        "ok": True,
        "proposal": proposal.id,
        "rendering": proposal.line(),
        "next": (
            "Nothing is sent yet. Reply to the coach in text this turn: the plugin shows "
            "this rendering at the top of your reply. The coach sends it by typing exactly "
            f"`confirm {proposal.id}` (or drops it with `cancel {proposal.id}`) within 15 "
            "minutes."
        ),
    })


def tool_specs() -> list[tuple[str, dict, object]]:
    """(name, Hermes schema, handler) of every tool in ``TOOLSET``."""
    specs = []
    for name, (description, properties, required, _) in READ_TOOLS.items():
        parameters = {"type": "object", "properties": properties, "required": required}
        specs.append((name, {"name": name, "description": description, "parameters": parameters},
                      _read_handler(name)))
    propose = {
        "name": "propose_inbound",
        "description": (
            "Propose an inbound message to the research agent for the coach to confirm. It "
            "sends nothing: the coach confirms by typing the confirm line the plugin shows. "
            "Kinds: reply {in_reply_to (M-id), choice and/or text}; steer {text, urgency (now|"
            "next|whenever), optional proposed_topic {title, objective, metrics, priority}}; "
            "task {title, mode (investigation|scouting|experimentation|maintenance|planning), "
            "why, refs (ar:// list), priority (P0-P5)}; control {action (pause|resume)}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(PROPOSABLE_KINDS)},
                "payload": {"type": "object", "description": "the fields of this kind"},
                "summary": {"type": "string", "description": "one line, for the log"},
            },
            "required": ["kind", "payload", "summary"],
        },
    }
    specs.append(("propose_inbound", propose, propose_inbound))
    return specs


SPECS = tool_specs()  # registered once; the handlers' identity is how we tell ours apart
TOOL_NAMES = frozenset(name for name, _, _ in SPECS)
_HANDLERS = {name: handler for name, _, handler in SPECS}


def foreign_tool_names() -> list[str]:
    """Coach tool names that Hermes's tool registry does not map to our handlers here (another
    plugin or a built-in registered the name first: Hermes then quietly rejects ours, v0.21.6
    ``hermes_cli/plugins.py`` ``register_tool``). Any such name turns the coach tools off
    (``adapters.pre_tool_call``), because the chat allowlist matches by name. Empty outside
    Hermes."""
    try:
        from tools.registry import registry
    except ImportError:
        return []
    foreign = []
    for name, handler in _HANDLERS.items():
        try:
            entry = registry.get_entry(name)
        except Exception:  # noqa: BLE001 - an unreadable registry is not ours: fail closed
            entry = None
        if getattr(entry, "handler", None) is not handler:
            foreign.append(name)
    return foreign


# --- the chat turn: brief, exact-line commands, notices ---


def brief() -> str:
    try:
        return BRIEF_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return BRIEF_FALLBACK


def send_inbound(where: tuple[Path, dict[str, str]], kind: str, payload: dict) -> str:
    """Send one inbound message as the coach through the profile ``where`` (its
    ``profile_env``); its inbound id. Raises Refused when the CLI refused it (nothing was
    written), SendUnknown when the outcome is unknown."""
    _, env = where
    author = _author(env)
    sent = run_cli(
        env, ["comms", "inbound", kind, "--json", json.dumps(payload), "--author", author],
        SEND_TIMEOUT_S,
    )
    if not sent.get("ok"):
        error = sent.get("error") or {}
        message = str(error.get("message") or "the send failed")
        problems = "; ".join(str(p) for p in error.get("problems") or [])
        # Only a validation answer (problems) or a bad-arguments exit is a refusal before
        # anything was written. Any other failure (a traceback, a git or OS error, a kill, a
        # timeout) may have come after the commit.
        if problems or error.get("exit_code") == EXIT_BAD_ARGS:
            raise Refused(problems or message)
        raise SendUnknown(message)
    return str((sent.get("data") or {}).get("id"))


def _note(key: str, text: str) -> None:
    """Queue a note for the plugin block. Its text can carry CLI words (an error reason), so
    ``&``, ``<`` and ``[`` are escaped: no HTML, link or link reference can form in the block."""
    STORE.note(key, text.replace("&", "&amp;").replace("<", "&lt;").replace("[", "\\["))


def _drain(sends: queue.Queue) -> None:
    """The send worker of one profile: run its sends one at a time, in the order queued. A
    send that breaks does not stop the worker (``work`` reports every outcome)."""
    while True:
        work, done = sends.get()
        try:
            work()
        except Exception:  # keep draining: a later send must still go out
            logging.getLogger(__name__).exception("coach send worker: a send broke")
        finally:
            done.set()


def _send_in_background(key: str, where, label: str, kind: str, payload: dict, event: dict):
    """Queue the send for its profile's worker thread, so ``pre_llm_call`` returns inside
    Hermes's hook timeout (v0.21.6 ``hermes_cli/plugins_dispatch.py``: 30 s by default, and a
    callback that times out is skipped for 60 s). The send is queued here, in the order the
    coach typed the lines, and one FIFO worker per profile home runs them in that order: the
    inbound id and time are made inside the CLI, so two concurrent sends could land reversed
    (a pause, then a resume, applied as resume, pause). The outcome goes out as a note: shown
    in the plugin block of the next chat reply and named in the next turn's context. The
    caller already took the proposal, so a second confirm can never send it again."""

    def work():
        try:
            inbound_id = send_inbound(where, kind, payload)
        except Refused as refused:  # the CLI refused it: nothing was committed
            _note(key, f"Sending {label} failed: {refused}. Nothing was sent.")
            _log({**event, "event": "send_failed"}, where)
            return
        except Exception as error:  # noqa: BLE001 - timed out or broke: outcome unknown
            reason = str(error) if isinstance(error, SendUnknown) else type(error).__name__
            _note(key, (
                f"Sending {label} did not finish ({reason}): it may or may not have been sent. "
                "Check message_status (or list the inbox with get_message) before proposing it "
                "again."
            ))
            _log({**event, "event": "send_unknown"}, where)
            return
        _note(key, f"Sent {label} as {inbound_id}.")
        _log({**event, "event": "sent", "inbound": inbound_id}, where)

    home, _ = where
    done = threading.Event()
    with _sessions_lock:
        _sends[:] = [pending for pending in _sends if not pending.is_set()]
        _sends.append(done)
        sends = _send_queues.get(home)
        if sends is None:
            sends = _send_queues[home] = queue.Queue()
            threading.Thread(
                target=_drain, args=(sends,), name=f"coach-send-{Path(home).name}", daemon=True
            ).start()
        sends.put((work, done))


def wait_for_sends(timeout: float = 30.0) -> None:
    """Wait for the queued sends to finish (tests; a clean shutdown)."""
    with _sessions_lock:
        pending = list(_sends)
    for done in pending:
        done.wait(timeout)


def _confirm(key: str, proposal_id: str, now: float) -> str:
    try:
        where = profile_env()
    except Refused as refused:
        return f"The coach typed `confirm {proposal_id}`, but nothing can be sent: {refused}."
    proposal = STORE.take(key, proposal_id, now)
    if proposal is None:
        if STORE.was_taken(key, proposal_id):
            return f"{proposal_id} was already confirmed in this chat; nothing more is sent."
        return (
            f"The coach typed `confirm {proposal_id}`, but this chat has no confirmable "
            f"proposal {proposal_id}: nothing was sent. It may have expired (15 minutes), "
            "been cancelled, never been shown, or been made in another session. Tell the coach "
            "and propose again if they still want it."
        )
    _send_in_background(key, where, proposal_id, proposal.kind, proposal.payload, {
        "session_id": key.split("\n")[-1], "proposal": proposal_id, "kind": proposal.kind,
    })
    return (
        f"The coach confirmed {proposal_id}. The plugin is sending it now (Sending "
        f"{proposal_id}…); it shows the result with a reply. Tell the coach it is being sent; "
        "do not say it was sent or failed."
    )


def _control(key: str, action: str) -> str:
    command = f"/{action}-research"
    try:
        where = profile_env()
    except Refused as refused:
        return f"The coach typed {command}, but nothing can be sent: {refused}. Tell the coach."
    _send_in_background(key, where, command, "control", {"action": action}, {
        "session_id": key.split("\n")[-1], "kind": "control", "action": action,
    })
    effect = (
        "research pauses: no new cards start; running cards finish under their caps"
        if action == "pause" else "the coach hold is lifted and research continues"
    )
    return (
        f"The coach typed {command}. The plugin is sending it now (Sending {command}…); it "
        f"shows the result with a reply. The runtime applies it on its next full run; if the "
        f"agent is paused for credit or commit checks, when that clears (`status` shows why). "
        f"Then {effect}. Tell the coach it is being sent."
    )


def _command(key: str, line: str, now: float) -> str | None:
    """The result of an exact-line coach command, or None when the line is not one."""
    if match := CONFIRM.fullmatch(line):
        return _confirm(key, match.group(1), now)
    if match := CANCEL.fullmatch(line):
        dropped = STORE.cancel(key, match.group(1))
        return (
            f"The coach cancelled {match.group(1)}; it will not be sent." if dropped
            else f"There is no pending proposal {match.group(1)} in this chat to cancel."
        )
    if match := CONTROL.fullmatch(line):
        return _control(key, match.group(1))
    return None


def chat_turn(session_id: str | None, user_message, is_first_turn: bool) -> str:
    """``pre_llm_call`` for a chat turn: the context to inject. Records the session first."""
    new = record_chat_session(session_id)
    key = chat_key(session_id)
    _log({"event": "chat_turn", "session_id": session_id, "platform": CHAT_PLATFORM,
          "first": bool(is_first_turn or new)})
    # Hermes keeps injected context in the session history (the api_content sidecar), so the
    # brief goes in once per session and process: its first turn here, or after a compression
    # (which starts a new session id).
    parts = [brief()] if (new or is_first_turn) else []
    foreign = foreign_tool_names()
    if foreign:
        parts.append(
            "[autoresearch plugin] Coach tools are off: another plugin owns the tool names "
            f"{', '.join(foreign)}. Tell the coach the chat cannot read the research now."
        )
    now = _now()
    for line in STORE.notes(key):
        parts.append(f"[autoresearch plugin] Since your last reply: {line} Tell the coach.")
    line = user_message.strip() if isinstance(user_message, str) else ""
    if key and not os.environ.get(runctx.ENV_CARD_ID):
        result = _command(key, line, now)
        if result:
            parts.append(f"[autoresearch plugin] {result}")
    for proposal in STORE.unshown(key, now):
        parts.append(
            f"[autoresearch plugin] Proposal {proposal.id} was never shown to the coach (the "
            "turn that made it ended without a reply), so it cannot be confirmed yet. The "
            "plugin shows it after your reply this turn."
        )
    try:
        home, env = profile_env()
        problem = budget_problem(home, env, datetime.datetime.now(datetime.UTC))
    except Refused as refused:
        problem = f"coach tools are unavailable: {refused}"
    if problem:
        parts.append(f"[autoresearch plugin] {problem[0].upper()}{problem[1:]}. Tell the coach.")
    return "\n\n".join(parts)


def transform(session_id: str | None, response_text: str, turn_id: str | None) -> str | None:
    """``transform_llm_output`` for a chat turn, or None when the plugin has nothing to show.

    The reply becomes the plugin's block first (queued notes, then every pending proposal's
    rendering, which marks it shown), then a horizontal rule, then the model's text,
    neutralized. Model text placed after the block cannot hide it, whatever Markdown or HTML it
    opens, and every pending proposal is shown again on each reply, so the first rendering of
    an id the coach sees is always the plugin's."""
    lines = STORE.show(chat_key(session_id), turn_id or "", _now())
    if not lines:
        return None
    _log({"event": "shown", "session_id": session_id, "turn": turn_id, "lines": len(lines)})
    block = "\n\n".join(lines)
    model = neutralize(response_text).strip()
    return f"{block}\n\n---\n\n{model}" if model else block


def neutralize(text: str) -> str:
    """All model text in a reply that carries a plugin block: line endings normalized to
    ``\\n`` (CommonMark also ends a line at a lone CR), and ``&`` and ``<`` HTML-escaped on
    every line, inside code too (code then shows ``&lt;`` literally), so no HTML can open."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("&", "&amp;").replace("<", "&lt;")
