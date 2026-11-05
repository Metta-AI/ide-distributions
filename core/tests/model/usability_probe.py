"""Running one situation with the session's real prompt and tools, and judging what the model did.

A run asks until the model does something that settles the situation, at most ``MAX_REQUESTS``
requests. Every call it makes before that is answered as a session would answer it. A session runs
every call in a reply, so a write later in the reply that settled a run makes it ``wrong_act``:

- ``skill_view`` and ``skills_list`` return the real skill text or list, and count as opening;
- a preparation (a read, a terminal command, an edit: :func:`is_preparation`) gets the tool's own
  refusal when the argument check refuses it, else what ``usability_reads`` holds for it;
- a call named in ``situation.acts`` is judged by ``write_actions.judge``. Valid ends the run as
  ``right``; invalid gets the refusal and the model may try again, which is how recovery shows;
- ``situation.before`` names writes that may sensibly come first; they are answered and the run
  goes on;
- any other call that passes the checks and changes something ends the run as ``wrong_act``:
  production would have acted on it. One the checks refuse changes nothing, so it gets the refusal
  and the run goes on.

Outcomes: ``right`` (also words alone when ``"text"`` is in ``situation.acts``), ``wrong_act``,
``text`` (answered in words where a call was wanted, with or without earlier calls, or with
nothing at all),
``limit`` (still preparing after ``MAX_REQUESTS``), ``rejected`` (the host refused the request),
``host_error`` (transport, 5xx, or account limit; not counted in any rate).

The baseline in ``usability_baseline.json`` predates ``usability_reads``: every read in it got one
placeholder line, and ``docs/reports/tool-usability-baseline-2026-11-04.md`` marks what that did.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field

from research_core.plugin.turn import render_context_block
from research_core.task_types import load as load_task_type
from research_core.tools import catalog
from research_core.tools.base import error_payload
from research_core.watcher import dispatch
from research_core.watcher.wakes import TEMPLATES_DIR
from tests.model import harness, usability_context, usability_reads, write_actions
from tests.model.usability_situations import Situation

MAX_REQUESTS = 8
HOST_RETRIES = 3
TASK_ID = usability_context.TASK_ID
# Hermes's tools that change nothing the session cannot redo; an edit is how work starts, and
# models use execute_code to look around (every case seen on 2026-11-04 was a read).
PREPARATION_BUILTINS = frozenset(
    {
        "execute_code",
        "patch",
        "process_manage",
        "read_file",
        "search_files",
        "session_search",
        "terminal",
        "todo_list",
        "vision_analyze",
        "web_extract",
        "web_search",
        "write_file",
    }
)
# Beyond write_actions.READ_ACTIONS. A notice or a view informs and leaves the next step open.
PREPARATION_ACTIONS = frozenset({"workers", "command", "show", "notify", "repositories"})


def label_of(name: str, args: dict | None) -> str:
    """``tool.action`` for an IDE action tool, else the tool's name."""
    action = (args or {}).get("action")
    if isinstance(action, str) and name not in usability_context.builtin_names():
        return f"{name}.{action}"
    return name


def is_preparation(name: str, args: dict | None) -> bool:
    args = args or {}
    if name in PREPARATION_BUILTINS or name == "platform":
        return True
    if name == "softmax_api":
        return args.get("method") in ("GET", "HEAD")
    action = args.get("action")
    return action in write_actions.READ_ACTIONS or action in PREPARATION_ACTIONS


@dataclass
class Run:
    situation: str
    repeat: int
    outcome: str = "limit"
    calls: list[str] = field(default_factory=list)  # every call, in order
    skills: list[str] = field(default_factory=list)  # skill_view names, in order
    act: str = ""  # the call that ended the run
    act_valid: bool | None = None  # whether the first call naming an expected act was valid
    act_attempts: int = 0  # calls naming an expected act
    args_right: bool | None = None  # whether the right call holds what ``situation.args`` asks
    act_args: dict | None = None  # the arguments of the call that ended the run
    problems: list[str] = field(default_factory=list)  # what the checks said, per refused call
    unknown_tools: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)  # Versions an allowed ``upload`` sent
    requests: int = 0
    first_prompt_tokens: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    provider: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def first_call(self) -> str:
        """The first call that is not opening a skill: the tool the model reached for."""
        return next((c for c in self.calls if not c.startswith("skill")), "")


# --- the conversation -------------------------------------------------------------------------


def _assistant_call(
    call_id: str, role: str, tool: str, args: dict, schemas: harness.SchemaSet
) -> dict:
    tool, args = schemas.outgoing(role, tool, args)
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)},
            }
        ],
    }


def _wake_prompt(situation: Situation) -> str:
    template = (TEMPLATES_DIR / f"{situation.wake}.md").read_text(encoding="utf-8")
    payload = json.dumps(situation.payload, indent=2, sort_keys=True)
    for placeholder, value in (
        ("{event_kind}", situation.event),
        ("{subject}", situation.subject),
        ("{payload_json}", payload),
        ("{coworld_id}", usability_context.COWORLD),
    ):
        template = template.replace(placeholder, value)
    return template


def _task_prompt(situation: Situation) -> str:
    return dispatch.render(
        "task_start",
        coworld=usability_context.COWORLD,
        task_id=TASK_ID,
        title=situation.title,
        type=situation.task_type,
        priority="normal",
        goal=usability_reads.goal_line(situation.goal),
        end_condition="deliverable",
        experiment=None,
        depends_on=None,
        description=situation.text,
        reasoning="",
        worktree="worktrees/task-21",
        branch="task/21",
    )


def _claim_exchange(situation: Situation, schemas: harness.SchemaSet) -> list[dict]:
    """The ``task.claim`` already made, with the type's real instructions in its result."""
    result = usability_reads.claim_result(situation, load_task_type(situation.task_type).body)
    args = {"action": "claim", "task": TASK_ID, "operation_id": "claim-t-21"}
    return [
        _assistant_call("call_claim", situation.role, "task", args, schemas),
        {"role": "tool", "tool_call_id": "call_claim", "content": json.dumps(result)},
    ]


def first_user_message(situation: Situation) -> str:
    if situation.wake:
        return _wake_prompt(situation)
    if situation.role == "researcher":
        return _task_prompt(situation)
    if situation.turn_context:
        # The turn hook adds the client's context block to the user's message.
        return f"{situation.text}\n\n{render_context_block(situation.turn_context)}"
    return situation.text


def messages_for(situation: Situation, schemas: harness.SchemaSet) -> list[dict]:
    your_task = ""
    if situation.role == "researcher":
        your_task = (
            f'Your Task: {TASK_ID} "{situation.title}" — {situation.task_type} for '
            f"{situation.goal}, in-progress; read it with task.read before working\n"
        )
    system = usability_context.system_prompt(
        situation.role,
        your_task=your_task,
        declared=situation.declared,
        capabilities=situation.capabilities,
        has_coworld=situation.has_coworld,
    )
    history = [
        {"role": "system", "content": system},
        {"role": "user", "content": first_user_message(situation)},
    ]
    if situation.role == "researcher" and situation.claimed:
        history += _claim_exchange(situation, schemas)
    for number, prior in enumerate(situation.prior):
        call_id = f"call_prior_{number}"
        history += [
            _assistant_call(call_id, situation.role, prior.tool, prior.args, schemas),
            {"role": "tool", "tool_call_id": call_id, "content": prior.result},
        ]
    return history


# --- one call ---------------------------------------------------------------------------------


def _problems(situation: Situation, call: harness.Call) -> str:
    """Why production's argument and record checks refuse this call; empty when they pass."""
    args = call.arguments or {}
    if call.name == "delegate_task":
        # Hermes's own tool: only a spawn with tasks asks anyone for anything.
        spawns = args.get("tasks") and args.get("action") in (None, "spawn")
        return "" if spawns else f"delegate_task did not spawn a task; sent {sorted(args)}"
    case = write_actions.Case(
        situation.role, call.name, str(args.get("action") or ""), situation.text
    )
    outcome, detail = write_actions.judge(case, call, args)
    return "" if outcome == "valid" else detail


def _answer(
    situation: Situation,
    run: Run,
    call: harness.Call,
    schemas: harness.SchemaSet,
    problems: str = "",
) -> str:
    if call.name == "skill_view":
        return usability_context.skill_view_result(
            situation.role, str((call.arguments or {}).get("name"))
        )
    if call.name == "skills_list":
        return usability_context.skills_list_result(situation.role)
    args = call.arguments
    case = write_actions.Case(situation.role, call.name, "", situation.text)
    answer = write_actions.answer(case, schemas, call.name, args)
    if answer != write_actions.RESULT:
        return answer  # the tool's argument check refused it
    if problems:  # only a store check refused it, which production reports the same way
        return json.dumps(error_payload([problems], {"tool": call.name}))
    return usability_reads.result(situation, label_of(call.name, args), args, run.uploaded)


def args_hold(wanted: dict[str, str | None], args: dict) -> bool:
    """``wanted`` maps an argument to a regex searched in its JSON, or None for must be absent."""
    for name, pattern in wanted.items():
        value = args.get(name)
        if pattern is None:
            if value not in (None, "", []):
                return False
        elif value is None or not re.search(pattern, json.dumps(value), re.DOTALL):
            return False
    return True


def _with_checklist(args: dict) -> dict:
    """``task.complete`` takes its deliverables flat or under ``checklist``, which wins there."""
    return {**args, **(args.get("checklist") or {})}


def _is_ide_tool(situation: Situation, name: str) -> bool:
    return any(spec.name == name for spec in catalog.tools_for(situation.role))


def _handle_call(
    situation: Situation, run: Run, call: harness.Call, schemas: harness.SchemaSet
) -> str | None:
    """Record one call. Returns what to answer it with, or None when it ends the run."""
    if call.arguments is not None:
        name, canonical = schemas.canonical(situation.role, call.name, call.arguments)
        call = harness.Call(call.id, name, canonical, call.raw)
    args = call.arguments
    label = label_of(call.name, args)
    run.calls.append(label)
    if call.name == "skill_view":
        run.skills.append(usability_context.bare_skill_name(str((args or {}).get("name"))))
        return _answer(situation, run, call, schemas)
    if situation.stop and call.name != "skills_list":
        run.outcome, run.act, run.act_args = "wrong_act", label, args
        run.detail = call.raw[:300]
        return None
    is_ide = _is_ide_tool(situation, call.name)
    if not is_ide and call.name not in usability_context.builtin_names():
        run.unknown_tools.append(call.name)
        return json.dumps({"error": f"Unknown tool: {call.name}"})
    problems = _problems(situation, call) if is_ide or call.name == "delegate_task" else ""
    if problems:
        run.problems.append(f"{label}: {problems}"[:400])
    if label in situation.acts:
        run.act_attempts += 1
        if run.act_valid is None:
            run.act_valid = not problems
        if not problems:
            if run.outcome == "right":  # a second valid act in the reply: the first one is judged
                return None
            run.outcome, run.act, run.act_args = "right", label, args
            if situation.args is not None:
                run.args_right = args_hold(situation.args, _with_checklist(args or {}))
            return None
        return _answer(situation, run, call, schemas, problems)
    if problems or is_preparation(call.name, args) or call.name == "skills_list":
        return _answer(situation, run, call, schemas, problems)
    if label in situation.before:
        if label == "upload":
            run.uploaded.append(usability_reads.version_ref(args))
        return _answer(situation, run, call, schemas)
    run.outcome, run.act, run.act_args = "wrong_act", label, args
    run.detail = call.raw[:300]
    return None


# --- one run ----------------------------------------------------------------------------------


def _complete_with_retries(
    model: str, history: list[dict], tools: list[dict], provider: dict | None
) -> harness.Reply:
    """A transient host error costs the run nothing but time; a refusal is not retried."""
    for attempt in range(HOST_RETRIES):
        try:
            return harness.complete(model, history, tools, tool_choice="auto", provider=provider)
        except harness.HostError as error:
            if error.rejected or attempt == HOST_RETRIES - 1:
                raise
            time.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")


def _count(run: Run, reply: harness.Reply) -> None:
    run.requests += 1
    run.first_prompt_tokens = run.first_prompt_tokens or reply.prompt_tokens
    run.prompt_tokens += reply.prompt_tokens
    run.cached_tokens += reply.cached_tokens
    run.completion_tokens += reply.completion_tokens
    run.cost += reply.cost
    run.provider = reply.provider


def run_situation(
    situation: Situation,
    repeat: int,
    model: str,
    host: str,
    schemas: harness.SchemaSet,
) -> Run:
    run = Run(situation.id, repeat)
    history = messages_for(situation, schemas)
    tools = usability_context.session_tools(situation.role, schemas, model)
    provider = harness.provider_for(model, host)
    for _ in range(MAX_REQUESTS):
        try:
            reply = _complete_with_retries(model, history, tools, provider)
        except harness.HostError as error:
            run.outcome = "rejected" if error.rejected else "host_error"
            run.detail = str(error)[:300]
            return run
        _count(run, reply)
        if not reply.calls:
            run.outcome = "right" if "text" in situation.acts and reply.text.strip() else "text"
            run.detail = reply.text[:300]
            return run
        answers = []
        for call in reply.calls:
            content = _handle_call(situation, run, call, schemas)
            if run.outcome == "wrong_act":
                return run
            if content is not None:
                answers.append({"role": "tool", "tool_call_id": call.id, "content": content})
        if run.outcome == "right":
            return run
        history = [*history, reply.message, *answers]
    return run
