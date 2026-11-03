"""What the model tool-call tests share: the OpenRouter request, host pins, tool lists, results.

The tests call OpenRouter directly, never through Hermes, so they measure what a model and an
OpenRouter host make of the tool schemas, not Hermes's own parsing of the reply.

Hosts. A host label is either ``shipped`` (the pin ``prepare.PROVIDER_ROUTING_MODELS`` gives the
model, or OpenRouter's default routing when it has none) or an OpenRouter provider slug or
endpoint tag (``wafer``, ``moonshotai/highspeed``), sent as ``provider: {only: [label]}``. Which
hosts serve a model: ``GET https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints``.

Tool lists. :class:`SchemaSet` supplies a role's IDE tool schemas and maps a returned call back
to the catalog tool it stands for, so a candidate schema (split write actions, per-action
schemas) can be measured by naming another set in ``IDE_TOOL_CALL_SCHEMAS=module:attribute``
without changing the cases. :func:`full_toolset` adds Hermes's own built-in tools, as a session
offers them: ``hermes_builtin_tools.json`` is Hermes v0.21.6's ``hermes-api-server`` toolset as
resolved with no extra credentials (memory off, tool search off). Regenerate it from a Hermes
checkout with Python 3.14 and Hermes's dependencies::

    HERMES_HOME=$(mktemp -d) PYTHONPATH=<hermes checkout> python -c "import json, model_tools; \
json.dump(model_tools.get_tool_definitions(enabled_toolsets=['hermes-api-server'], \
quiet_mode=True), open('hermes_builtin_tools.json', 'w'), indent=1, ensure_ascii=False)"
"""

from __future__ import annotations

import importlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from research_core import prepare
from research_core.tools import catalog
from research_core.tools.catalog import Role

KEY = os.environ.get("OPENROUTER_API_KEY")
URL = "https://openrouter.ai/api/v1/chat/completions"
SHIPPED = "shipped"
HERMES_BUILTIN_TOOLS = Path(__file__).with_name("hermes_builtin_tools.json")


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def provider_for(model: str, host: str) -> dict | None:
    """The request's ``provider`` object for a host label (None: OpenRouter's default routing)."""
    if host == SHIPPED:
        return prepare.PROVIDER_ROUTING_MODELS.get(model)
    return {"only": [host]}


# --- the request ------------------------------------------------------------------------------


class HostError(Exception):
    """OpenRouter or the host failed the request: no reply to judge."""


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


def complete(
    model: str,
    messages: list[dict],
    tools: list[dict],
    *,
    tool_choice: str,
    provider: dict | None,
) -> Reply:
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        # Hermes sends no temperature to Kimi (Moonshot accepts only its own); 0 elsewhere.
        **({} if model.rsplit("/", 1)[-1].startswith("kimi") else {"temperature": 0}),
        **({"provider": provider} if provider else {}),
        "usage": {"include": True},
    }
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
        raise HostError(f"HTTP {error.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise HostError(f"{type(error).__name__}: {error}") from None
    if "error" in payload or not payload.get("choices"):
        raise HostError(json.dumps(payload.get("error") or payload)[:300])
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


class SchemaSet(Protocol):
    label: str

    def tools(self, role: Role) -> list[dict]:
        """The role's IDE tools as OpenAI function bodies (name, description, parameters)."""

    def canonical(self, role: Role, name: str, args: dict) -> tuple[str, dict]:
        """The catalog tool and arguments (``action`` included) that a returned call stands for."""


class ShippedSchemas:
    """The schemas the profile plugin registers today (``ToolSpec.schema``)."""

    label = "shipped"

    def tools(self, role: Role) -> list[dict]:
        return [spec.schema(role) for spec in catalog.tools_for(role)]

    def canonical(self, role: Role, name: str, args: dict) -> tuple[str, dict]:
        return name, args


def schema_set() -> SchemaSet:
    reference = os.environ.get("IDE_TOOL_CALL_SCHEMAS")
    if not reference:
        return ShippedSchemas()
    module, _, attribute = reference.partition(":")
    return getattr(importlib.import_module(module), attribute)


def _as_hermes_sends(schema: Any) -> Any:
    """Hermes's schema sanitizer gives a bare ``{"type": "object"}`` empty properties and
    required lists before the request; the plugin's schemas have a few such fields."""
    if isinstance(schema, list):
        return [_as_hermes_sends(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: _as_hermes_sends(value) for key, value in schema.items()}
    if out.get("type") == "object" and "properties" not in out:
        out = {**out, "properties": {}, "required": out.get("required", [])}
    return out


def full_toolset(role: Role, schemas: SchemaSet) -> list[dict]:
    """Every tool a session of the role offers, sorted by name as Hermes's registry sends them."""
    builtins = json.loads(HERMES_BUILTIN_TOOLS.read_text())
    ide = [{"type": "function", "function": _as_hermes_sends(s)} for s in schemas.tools(role)]
    return sorted([*builtins, *ide], key=lambda tool: tool["function"]["name"])


# --- results ----------------------------------------------------------------------------------


@dataclass
class Result:
    model: str
    host: str
    case: str  # "<role> <tool>.<action>", with " (recovery)" for a recovery case
    outcome: str  # valid, invalid, wrong_tool, no_call, host_error
    detail: str = ""
    replies: list[Reply] = field(default_factory=list)  # one per request, in order
    detours: list[str] = field(default_factory=list)  # other calls made before the judged one


@dataclass
class Results:
    schemas: str = ""
    items: list[Result] = field(default_factory=list)


RESULTS = Results()
OUTCOME_CELL = {
    "valid": "ok",
    "invalid": "BAD",
    "wrong_tool": "TOOL",
    "no_call": "TEXT",
    "host_error": "ERR",
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
        "and action. ok = its arguments validate; BAD = they do not; TOOL = the model never "
        "called it within the request limit; TEXT = it answered without a tool call; "
        "ERR = host error (not counted). +N = N other calls first, each answered with a stub. "
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
