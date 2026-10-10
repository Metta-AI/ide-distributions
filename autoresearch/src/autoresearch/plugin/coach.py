"""The IDE coach chat inside this profile (coach-chat-role.md §1-§5, plan 3.7). STDLIB ONLY.

On Softmax hosting every coach line in the IDE chat becomes one Hermes run on the API server
(platform ``api_server``); Kanban research workers run as ``cli``. The chat is the coach's
interface, not a second research agent:

- **Tools.** The toolset ``autoresearch_coach``: fixed-argv read commands of the profile CLI
  (``READ_TOOLS``; output cut to ``OUTPUT_MAX`` characters) and ``propose_inbound``. Config
  gives ``api_server`` only this toolset; ``adapters.pre_tool_call`` also blocks every other
  tool in a chat session, because Hermes's config load fails open to the full default
  toolset. Every tool refuses unless its session was recorded as a chat session by
  ``chat_turn`` (``pre_llm_call`` with platform ``api_server``), and under
  ``HERMES_KANBAN_TASK``.
- **Proposals, confirmed by the coach's own line.** No model tool call ever runs ``comms
  inbound`` (the policy and the CLI refuse it). ``propose_inbound`` validates the payload
  with ``comms inbound --dry-run`` and stores it in memory (``proposals``); the
  ``transform_llm_output`` hook appends the plugin's rendering to the reply; ``chat_turn``
  sends it only when the coach's whole stripped line is ``confirm P-xxxx`` in the same
  session. ``cancel P-xxxx`` drops it. ``/pause-research`` and ``/resume-research`` (whole
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
import os
import re
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
SEND_TIMEOUT_S = 180  # comms inbound commits and pushes under the canonical lock
ENV_CHAT_DAILY_USD = "AUTORESEARCH_CHAT_DAILY_USD"
DEFAULT_CHAT_DAILY_USD = 3.0
PROPOSABLE_KINDS = ("task", "steer", "reply", "control")
OUR_PLUGIN = Path("plugins") / "autoresearch" / "plugin.yaml"
CHAT_SPEND_FILE = Path("local") / "chat-spend.json"
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
_sessions: dict[str, float] = {}  # chat session id -> last turn time
_sessions_lock = threading.Lock()
_spend_lock = threading.Lock()


def _now() -> float:
    """The clock proposals expire by (a seam for tests)."""
    return time.time()


class Refused(Exception):
    """A coach tool or send that must not run; the message says why, in plain words."""


# --- sessions ---


def record_chat_session(session_id: str | None) -> bool:
    """Remember ``session_id`` as a chat session; True when this process had not seen it."""
    if not session_id:
        return False
    with _sessions_lock:
        new = session_id not in _sessions
        _sessions[session_id] = time.time()
        return new


def is_chat_session(session_id: str | None) -> bool:
    with _sessions_lock:
        return bool(session_id) and session_id in _sessions


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


def _log(event: dict) -> None:
    """Best effort: a chat event in the profile's runtime dir; never raises."""
    try:
        home, env = profile_env()
        directory = _canonical(home, env) / ".supervisor" / "runtime"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / CHAT_EVENTS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), **event}, default=str) + "\n")
    except Exception:  # noqa: BLE001, S110 - a plugin must never break the agent
        pass


def log_error(hook: str, session_id: str | None, error: BaseException) -> None:
    _log({"event": "error", "hook": hook, "session_id": session_id, "error": repr(error)[:500]})


# --- running the profile CLI ---


def run_cli(env: dict[str, str], args: list[str], timeout: float) -> dict:
    """``"$AUTORESEARCH_CLI" <args>`` with no shell; its JSON envelope, or a failure
    envelope when it printed none."""
    argv = [env["AUTORESEARCH_CLI"], *args]
    try:
        result = subprocess.run(
            argv, env=env, cwd=env[runctx.ENV_HERMES_HOME], capture_output=True, text=True,
            timeout=timeout, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "error": {"message": f"{args[0]}: {type(error).__name__}: {error}"}}
    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        envelope = None
    if isinstance(envelope, dict) and "ok" in envelope:
        return envelope
    detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
    return {"ok": False, "error": {"message": f"exit {result.returncode}: {detail[0][:300]}"}}


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
            "against its budget, the key's credit, running cards, open questions, the champion. "
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
    proposal = STORE.propose(session_id, kind, payload, summary, _now(), answers=answers)
    _log({"event": "proposed", "session_id": session_id, "proposal": proposal.id, "kind": kind})
    return json.dumps({
        "ok": True,
        "proposal": proposal.id,
        "rendering": proposal.line(),
        "next": (
            "Nothing is sent yet. Reply to the coach in text this turn: the plugin appends "
            "this rendering to your reply. The coach sends it by typing exactly "
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


TOOL_NAMES = frozenset(name for name, _, _ in tool_specs())


# --- the chat turn: brief, exact-line commands, notices ---


def brief() -> str:
    try:
        return BRIEF_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return BRIEF_FALLBACK


def send_inbound(kind: str, payload: dict) -> str:
    """Send one inbound message as the coach; its inbound id. Raises Refused."""
    _, env = profile_env()
    author = _author(env)
    sent = run_cli(
        env, ["comms", "inbound", kind, "--json", json.dumps(payload), "--author", author],
        SEND_TIMEOUT_S,
    )
    if not sent.get("ok"):
        error = sent.get("error") or {}
        problems = "; ".join(str(p) for p in error.get("problems") or [])
        raise Refused(problems or str(error.get("message") or "the send failed"))
    return str((sent.get("data") or {}).get("id"))


def _confirm(session_id: str, proposal_id: str, now: float) -> str:
    proposal = STORE.take(session_id, proposal_id, now)
    if proposal is None:
        if STORE.was_taken(session_id, proposal_id):
            return f"{proposal_id} was already confirmed in this chat; nothing more was sent."
        return (
            f"The coach typed `confirm {proposal_id}`, but this chat has no confirmable "
            f"proposal {proposal_id}: nothing was sent. It may have expired (15 minutes), "
            "been cancelled, never been shown, or been made in another session. Tell the coach "
            "and propose again if they still want it."
        )
    try:
        inbound_id = send_inbound(proposal.kind, proposal.payload)
    except Refused as refused:
        STORE.note(session_id, f"Sending {proposal_id} failed: {refused}. Nothing was sent.")
        _log({"event": "send_failed", "session_id": session_id, "proposal": proposal_id})
        return (
            f"The coach confirmed {proposal_id}, but sending it failed: {refused}. Nothing was "
            "sent. Tell the coach."
        )
    STORE.note(session_id, f"Sent {proposal_id} as {inbound_id}.")
    _log({"event": "sent", "session_id": session_id, "proposal": proposal_id,
          "kind": proposal.kind, "inbound": inbound_id})
    return f"The coach confirmed {proposal_id}; the plugin sent it as {inbound_id}. Tell the coach."


def _control(session_id: str, action: str) -> str:
    command = f"/{action}-research"
    try:
        inbound_id = send_inbound("control", {"action": action})
    except Refused as refused:
        STORE.note(session_id, f"Sending {command} failed: {refused}. Nothing was sent.")
        return f"The coach typed {command}, but sending it failed: {refused}. Tell the coach."
    STORE.note(session_id, f"Sent {command} as {inbound_id}.")
    _log({"event": "sent", "session_id": session_id, "kind": "control", "action": action,
          "inbound": inbound_id})
    effect = (
        "research pauses: no new cards start; running cards finish under their caps"
        if action == "pause" else "the coach hold is lifted and research continues"
    )
    return (
        f"The coach typed {command}; the plugin sent it as {inbound_id}. When the runtime "
        f"applies it (within about a minute) {effect}. Tell the coach."
    )


def _command(session_id: str, line: str, now: float) -> str | None:
    """The result of an exact-line coach command, or None when the line is not one."""
    if match := CONFIRM.fullmatch(line):
        return _confirm(session_id, match.group(1), now)
    if match := CANCEL.fullmatch(line):
        dropped = STORE.cancel(session_id, match.group(1))
        return (
            f"The coach cancelled {match.group(1)}; it will not be sent." if dropped
            else f"There is no pending proposal {match.group(1)} in this chat to cancel."
        )
    if match := CONTROL.fullmatch(line):
        return _control(session_id, match.group(1))
    return None


def chat_turn(session_id: str | None, user_message, is_first_turn: bool) -> str:
    """``pre_llm_call`` for a chat turn: the context to inject. Records the session first."""
    new = record_chat_session(session_id)
    _log({"event": "chat_turn", "session_id": session_id, "platform": CHAT_PLATFORM,
          "first": bool(is_first_turn or new)})
    # Hermes keeps injected context in the session history (the api_content sidecar), so the
    # brief goes in once per session and process: its first turn here, or after a compression
    # (which starts a new session id).
    parts = [brief()] if (new or is_first_turn) else []
    now = _now()
    line = user_message.strip() if isinstance(user_message, str) else ""
    if session_id and not os.environ.get(runctx.ENV_CARD_ID):
        result = _command(session_id, line, now)
        if result:
            parts.append(f"[autoresearch plugin] {result}")
    for proposal in STORE.unshown(session_id or "", now):
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
    """``transform_llm_output`` for a chat turn: the reply with the plugin's lines appended
    (sent lines, then unshown proposals, now marked shown), or None when there are none."""
    lines = STORE.show(session_id or "", turn_id or "", _now())
    if not lines:
        return None
    _log({"event": "shown", "session_id": session_id, "turn": turn_id, "lines": len(lines)})
    return f"{response_text.rstrip()}\n\n" + "\n\n".join(lines)
