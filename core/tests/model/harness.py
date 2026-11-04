"""What the model tool-call tests share: the OpenRouter request, host pins, tool lists, results.

The tests call OpenRouter directly, never through Hermes, so they measure what a model and an
OpenRouter host make of the tool schemas, not Hermes's own parsing of the reply. The request is
shaped as Hermes v0.21.6 shapes it on its OpenRouter provider profile: no ``temperature``
(``agent/transports/chat_completions.py`` ``_base_kwargs`` sends one only for a profile's fixed
temperature, and OpenRouter's has none), and ``reasoning: {enabled: true, effort: medium}`` for a
model whose OpenRouter catalog entry lists ``reasoning`` among its supported parameters when the
profile config sets no reasoning effort (``plugins/model-providers/openrouter/__init__.py``
``build_api_kwargs_extras``; the profiles set none).

Hosts. A host label is either ``shipped`` (the pin ``prepare.PROVIDER_ROUTING_MODELS`` gives the
model, or OpenRouter's default routing when it has none) or an OpenRouter provider slug or
endpoint tag (``wafer``, ``moonshotai/highspeed``), sent as ``provider: {only: [label]}``. Which
hosts serve a model: ``GET https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints``.

Tool lists. :class:`SchemaSet` supplies a role's IDE tool schemas and translates calls between
its tools and the catalog's, so a candidate schema (split write actions, per-action schemas) can
be measured by naming another set in ``IDE_TOOL_CALL_SCHEMAS=module:attribute`` without changing
the cases. :func:`full_toolset` builds what a session sends: the IDE tools plus Hermes's
built-in tools, sorted by name as Hermes's registry does, through Hermes's own schema sanitizer
(``tools/schema_sanitizer.py``) and, for Kimi models, its Moonshot sanitizer
(``agent/moonshot_schema.py``). Both are loaded from the Hermes source tree that
``IDE_HERMES_SRC`` names; without it the full-toolset tests fail rather than send schemas Hermes
would rewrite.

``hermes_builtin_tools.json`` is Hermes v0.21.6's ``hermes-api-server`` toolset as one
credential-free local install resolves it (memory off, tool search off): 21 tools. It is not
read from the hosted image, whose credentials and installed browser can add or remove tools
(``web_search`` backends, the browser tools). It was generated on 2026-11-02 with Python 3.14
and Hermes's pinned dependencies, and is regenerated the same way::

    uv pip compile --python-version 3.14 <hermes>/pyproject.toml -o /tmp/hermes-reqs.txt
    HERMES_HOME=$(mktemp -d) PYTHONPATH=<hermes> uv run --no-project --python 3.14 \\
      --with-requirements /tmp/hermes-reqs.txt python -c "import json, model_tools; \\
      json.dump(model_tools.get_tool_definitions(enabled_toolsets=['hermes-api-server'], \\
      quiet_mode=True), open('hermes_builtin_tools.json', 'w'), indent=1, ensure_ascii=False)"

with a ``config.yaml`` in that ``HERMES_HOME`` that sets ``memory.memory_enabled: false`` and
``tools.tool_search.enabled: "off"``, as the profiles do.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from research_core import prepare
from research_core.tools import catalog
from research_core.tools.base import ToolContext, run_tool
from research_core.tools.catalog import Role

KEY = os.environ.get("OPENROUTER_API_KEY")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
SHIPPED = "shipped"
HERMES_BUILTIN_TOOLS = Path(__file__).with_name("hermes_builtin_tools.json")
# What Hermes's OpenRouter profile sends to a reasoning-capable model when the config sets no
# reasoning effort.
HERMES_DEFAULT_REASONING = {"enabled": True, "effort": "medium"}


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def provider_for(model: str, host: str) -> dict | None:
    """The request's ``provider`` object for a host label (None: OpenRouter's default routing)."""
    if host == SHIPPED:
        return prepare.PROVIDER_ROUTING_MODELS.get(model)
    return {"only": [host]}


# --- the request ------------------------------------------------------------------------------


class HostError(Exception):
    """No reply to judge. ``rejected``: the host refused the request itself (a 4xx such as a
    schema it cannot take), which counts against the model and host; otherwise a transport
    error, a 5xx, or an account limit (401, 402, 403, 408, 429), which does not."""

    def __init__(self, message: str, *, rejected: bool = False):
        super().__init__(message)
        self.rejected = rejected


# 4xx statuses that are about the account or the moment, not the request's content.
NOT_A_REJECTION = {401, 402, 403, 408, 429}


def _is_rejection(status: Any) -> bool:
    return isinstance(status, int) and 400 <= status < 500 and status not in NOT_A_REJECTION


@dataclass
class Call:
    id: str
    name: str
    arguments: dict | None  # None: the arguments were not a JSON object
    raw: str


@dataclass
class Reply:
    message: dict  # the assistant message as returned, to send back in a longer history
    calls: list[Call]
    text: str
    provider: str  # the host that served the request
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    cost: float
    seconds: float


@functools.cache
def reasoning_models() -> frozenset[str]:
    """Models whose OpenRouter catalog entry lists ``reasoning`` (Hermes's live-catalog test)."""
    with urllib.request.urlopen(MODELS_URL, timeout=60) as response:
        models = json.load(response)["data"]
    return frozenset(
        m["id"] for m in models if "reasoning" in (m.get("supported_parameters") or [])
    )


def request_body(
    model: str, messages: list[dict], tools: list[dict], *, tool_choice: str, provider: dict | None
) -> dict:
    return {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        **({"reasoning": HERMES_DEFAULT_REASONING} if model in reasoning_models() else {}),
        **({"provider": provider} if provider else {}),
        "usage": {"include": True},
    }


def complete(
    model: str,
    messages: list[dict],
    tools: list[dict],
    *,
    tool_choice: str,
    provider: dict | None,
) -> Reply:
    body = request_body(model, messages, tools, tool_choice=tool_choice, provider=provider)
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise HostError(
            f"HTTP {error.code}: {detail}", rejected=_is_rejection(error.code)
        ) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise HostError(f"{type(error).__name__}: {error}") from None
    if "error" in payload or not payload.get("choices"):
        error = payload.get("error") or {}
        code = error.get("code") if isinstance(error, dict) else None
        raise HostError(json.dumps(error or payload)[:300], rejected=_is_rejection(code))
    message = payload["choices"][0]["message"]
    usage = payload.get("usage") or {}
    return Reply(
        message=message,
        calls=[_call(c) for c in message.get("tool_calls") or []],
        text=message.get("content") or "",
        provider=payload.get("provider", "?"),
        prompt_tokens=usage.get("prompt_tokens", 0),
        cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
        completion_tokens=usage.get("completion_tokens", 0),
        cost=float(usage.get("cost") or 0.0),
        seconds=time.monotonic() - started,
    )


def _call(raw: dict) -> Call:
    text = raw["function"].get("arguments") or "{}"
    try:
        arguments = json.loads(text)
    except json.JSONDecodeError:
        arguments = None
    arguments = arguments if isinstance(arguments, dict) else None
    return Call(raw.get("id", ""), raw["function"]["name"], arguments, text)


# --- tool lists -------------------------------------------------------------------------------


def catalog_refusal(role: Role, tool: str, args: dict) -> str:
    """``run_tool``'s text for a call it refuses before touching the store or the workspace
    (the argument check), so the context needs only the role."""
    ctx = ToolContext(role=role, actor=None, workspace=None, env={})  # type: ignore[arg-type]
    return run_tool(tool, args, ctx)


class SchemaSet(Protocol):
    label: str

    def tools(self, role: Role) -> list[dict]:
        """The role's IDE tools as OpenAI function bodies (name, description, parameters)."""

    def canonical(self, role: Role, name: str, args: dict) -> tuple[str, dict]:
        """The catalog tool and arguments (``action`` included) that a returned call stands for."""

    def outgoing(self, role: Role, tool: str, args: dict) -> tuple[str, dict]:
        """The reverse of ``canonical``: how this set names a catalog call (for a history)."""

    def refusal(self, role: Role, tool: str, args: dict) -> str:
        """The tool result this set's tools give a catalog call that the argument check refuses
        (a recovery case's history, and the answer to a refused call before the judged one)."""


class ShippedSchemas:
    """The schemas the profile plugin registers today (``ToolSpec.schema``)."""

    label = "shipped"

    def tools(self, role: Role) -> list[dict]:
        return [spec.schema(role) for spec in catalog.tools_for(role)]

    def canonical(self, role: Role, name: str, args: dict) -> tuple[str, dict]:
        return name, args

    def outgoing(self, role: Role, tool: str, args: dict) -> tuple[str, dict]:
        return tool, args

    def refusal(self, role: Role, tool: str, args: dict) -> str:
        return catalog_refusal(role, tool, args)


def schema_set() -> SchemaSet:
    reference = os.environ.get("IDE_TOOL_CALL_SCHEMAS")
    if not reference:
        return ShippedSchemas()
    module, _, attribute = reference.partition(":")
    return getattr(importlib.import_module(module), attribute)


def _load(source: Path, relative: str, name: str) -> ModuleType:
    path = source / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def hermes_sanitizers() -> tuple[ModuleType, ModuleType]:
    """Hermes's ``tools/schema_sanitizer.py`` and ``agent/moonshot_schema.py`` (stdlib only),
    from the source tree ``IDE_HERMES_SRC`` names."""
    source = os.environ.get("IDE_HERMES_SRC")
    if not source:
        raise RuntimeError(
            "IDE_HERMES_SRC is not set: full-toolset mode sends schemas through Hermes's own "
            "sanitizers; point it at a Hermes v0.21.6 source tree"
        )
    root = Path(source)
    return (
        _load(root, "tools/schema_sanitizer.py", "hermes_schema_sanitizer"),
        _load(root, "agent/moonshot_schema.py", "hermes_moonshot_schema"),
    )


def full_toolset(role: Role, schemas: SchemaSet, model: str) -> list[dict]:
    """Every tool a session of the role offers ``model``, as Hermes sends them: sorted by name,
    sanitized, and for a Kimi model rewritten for Moonshot's schema flavour."""
    sanitizer, moonshot = hermes_sanitizers()
    # The profiles disable Hermes's browser toolset (prepare.PROTECTED_KEYS); the snapshot has it.
    builtins = [
        tool
        for tool in json.loads(HERMES_BUILTIN_TOOLS.read_text())
        if not tool["function"]["name"].startswith("browser_")
    ]
    ide = [{"type": "function", "function": s} for s in schemas.tools(role)]
    tools = sanitizer.sanitize_tool_schemas(
        sorted([*builtins, *ide], key=lambda tool: tool["function"]["name"])
    )
    return moonshot.sanitize_moonshot_tools(tools) if moonshot.is_moonshot_model(model) else tools


# --- results ----------------------------------------------------------------------------------


@dataclass
class Result:
    model: str
    host: str
    case: str  # "<role> <tool>.<action>", with " (recovery)" for a recovery case
    outcome: str  # one of OUTCOME_CELL
    detail: str = ""
    replies: list[Reply] = field(default_factory=list)  # one per request, in order
    detours: list[str] = field(default_factory=list)  # other calls made before the judged one


@dataclass
class Results:
    schemas: str = ""
    items: list[Result] = field(default_factory=list)


RESULTS = Results()
OUTCOME_CELL = {
    "valid": "ok",  # the judged call's arguments pass the tools' checks
    "invalid": "BAD",  # they do not, or it named another write action of the tool
    "wrong_tool": "TOOL",  # never called the expected tool within the request limit
    "no_call": "TEXT",  # answered in text at once
    "stopped": "STOP",  # answered in text after other calls
    "rejected": "REJ",  # the host refused the request (4xx)
    "host_error": "ERR",  # transport error, 5xx or account limit: not counted
}


def _short(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def _cell(results: list[Result]) -> str:
    if not results:
        return ""
    if len(results) == 1:
        result = results[0]
        detours = f"+{len(result.detours)}" if result.detours else ""
        return OUTCOME_CELL[result.outcome] + detours
    judged = [r for r in results if r.outcome != "host_error"]
    valid = sum(r.outcome == "valid" for r in judged)
    errors = len(results) - len(judged)
    return f"{valid}/{len(judged)}" + (f" +{errors}E" if errors else "")


def _rate(results: list[Result]) -> str:
    judged = [r for r in results if r.outcome != "host_error"]
    valid = sum(r.outcome == "valid" for r in judged)
    return f"{valid}/{len(judged)} {100 * valid / len(judged):.0f}%" if judged else "-"


def _hosts(results: list[Result]) -> str:
    served = sorted({x.provider for r in results for x in r.replies})
    return ",".join(served[:2]) + (f" +{len(served) - 2}" if len(served) > 2 else "")


def _first_prompt_tokens(results: list[Result]) -> str:
    """Prompt tokens of each case's first request: the tool list plus the case's history."""
    first = [r.replies[0].prompt_tokens for r in results if r.replies]
    return f"{sum(first) / len(first):,.0f}" if first else "-"


def _cached(results: list[Result]) -> str:
    replies = [x for r in results for x in r.replies]
    total = sum(x.prompt_tokens for x in replies)
    return f"{100 * sum(x.cached_tokens for x in replies) / total:.0f}%" if total else "-"


def _cost(results: list[Result]) -> float:
    return sum(x.cost for r in results for x in r.replies)


def summary() -> list[str]:
    """The results as a table: one row per case, one column per model and host."""
    items = RESULTS.items
    if not items:
        return []
    columns = list(dict.fromkeys((r.model, r.host) for r in items))
    cases = list(dict.fromkeys(r.case for r in items))
    by_cell: dict[tuple, list[Result]] = {}
    for r in items:
        by_cell.setdefault((r.case, r.model, r.host), []).append(r)
    rows = [[case, *(_cell(by_cell.get((case, m, h), [])) for m, h in columns)] for case in cases]

    def footer(label: str, value) -> list[str]:
        return [label, *(value([r for r in items if (r.model, r.host) == c]) for c in columns)]

    def recovery(results: list[Result]) -> list[Result]:
        return [r for r in results if r.case.endswith("(recovery)")]

    def first_try(results: list[Result]) -> list[Result]:
        return [r for r in results if not r.case.endswith("(recovery)")]

    rows += [
        ["-" * 8, *("" for _ in columns)],
        footer("served by", _hosts),
        footer("first-try valid", lambda rs: _rate(first_try(rs))),
        footer("recovery valid", lambda rs: _rate(recovery(rs))),
        footer("valid, all cases", _rate),
        footer("valid, no detour", lambda rs: _rate([r for r in rs if not r.detours])),
        footer("prompt tokens", _first_prompt_tokens),
        footer("cached share", _cached),
        footer("requests", lambda rs: str(sum(len(r.replies) for r in rs))),
        footer("cost $", lambda rs: f"{_cost(rs):.3f}"),
    ]
    table = [["case", *(f"{_short(m)}@{h}" for m, h in columns)], *rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
    rendered = ["  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)) for row in table]
    lines = [
        f"Tool schemas: {RESULTS.schemas}. Each cell judges the first call to the expected tool "
        "(any call to the tool without an action, or with a write action). ok = it passes the "
        "tools' argument and record checks; BAD = it does not; TOOL = never within the request "
        "limit; TEXT = a text answer at once; STOP = a text answer after other calls; REJ = the "
        "host refused the request (4xx); ERR = transport error or 5xx (not counted). +N = N "
        "other calls first, answered as the tools would (refusal or plausible result). "
        "Prompt tokens: mean of each case's first request.",
        "",
        *rendered,
        "",
        f"Total cost ${_cost(items):.3f} over {sum(len(r.replies) for r in items)} requests.",
    ]
    failures = [r for r in items if r.outcome != "valid"]
    if failures:
        lines += ["", "Not valid:"]
        lines += [
            f"  {_short(r.model)}@{r.host} {r.case}: {r.outcome}: {r.detail}"[:400]
            for r in failures
        ]
    return lines
