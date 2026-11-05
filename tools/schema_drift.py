"""Compare the tool schemas frozen in a Session with what the deployed validator accepts.

Hermes freezes a Session's tool list in its ``state.db`` (``sessions.tool_names`` points at a row
of ``system_prompts`` holding ``{"version", "tools"}``) and keeps those bytes while Hermes's own
code stays the same, so a Session opened before one of our deploys can be told to send arguments
the deployed tools refuse. That cannot be fixed by a clearer refusal: a model on a closed schema
does not send a field the schema leaves out. This reads the frozen list, offline, and names every
disagreement that makes a correct call impossible or a shown option useless:

- a deployed tool or action missing from the frozen list (the model cannot choose it);
- a frozen tool or action the role's validator refuses;
- a field or nested key the validator requires that the frozen schema does not declare (every
  call is refused);
- a frozen field the validator does not know;
- a field whose top-level type changed, or a frozen enum value the validator refuses.

A new optional field is not reported: every call still passes without it. The frozen list also
holds Hermes's own tools, so a retired tool of ours cannot be told apart from one of those and
is not reported either.

Usage, on a copy of a profile's ``state.db``:

    uv run python tools/schema_drift.py <state.db> --role orchestrator [--session <id>]

Exit 0 when no frozen list disagrees; 1 with one line per disagreement otherwise.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "src"))

from research_core.tools import catalog  # noqa: E402
from research_core.tools.base import ToolSpec  # noqa: E402

MISSING = "the validator requires it and the frozen schema does not declare it"


@dataclass(frozen=True)
class Drift:
    tool: str
    action: str | None
    problem: str

    def __str__(self) -> str:
        label = f"{self.tool}.{self.action}" if self.action else self.tool
        return f"{label}: {self.problem}"


@dataclass(frozen=True)
class FrozenList:
    version: str  # the Hermes code identity that froze it
    tools: list[dict]
    session_ids: list[str]


def read_frozen_lists(state_db: Path, session_id: str | None = None) -> list[FrozenList]:
    """Each distinct frozen list in the database, with the Sessions that hold it. Sessions with
    no frozen list, or with a legacy list of bare names (no schemas to compare), are left out."""
    connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT s.id, s.tool_names, p.prompt FROM sessions s "
            "JOIN system_prompts p ON p.hash = s.tool_names "
            "WHERE ? IS NULL OR s.id = ? ORDER BY s.started_at",
            (session_id, session_id),
        ).fetchall()
    finally:
        connection.close()
    sessions_by_list: dict[str, list[str]] = {}
    pins: dict[str, dict] = {}
    for sid, list_hash, text in rows:
        pin = json.loads(text)
        if isinstance(pin, dict):
            pins[list_hash] = pin
            sessions_by_list.setdefault(list_hash, []).append(sid)
    return [
        FrozenList(str(pin.get("version")), pin["tools"], sessions_by_list[list_hash])
        for list_hash, pin in pins.items()
    ]


def compare(role: catalog.Role, frozen_tools: list[dict]) -> list[Drift]:
    """Every disagreement between a frozen tool list and the role's deployed tools."""
    frozen = {tool["function"]["name"]: tool["function"] for tool in frozen_tools}
    deployed = {spec.name: spec for spec in catalog.tools_for(role)}
    found = [
        Drift(name, None, "deployed, but not in the frozen list: the model cannot call it")
        for name in deployed
        if name not in frozen
    ]
    for name, function in frozen.items():
        if name in deployed:
            found.extend(_compare_tool(role, deployed[name], function.get("parameters") or {}))
        elif name in catalog.TOOLS:
            found.append(Drift(name, None, f"frozen, but the {role} does not have it now"))
    return found


def _missing_keys(frozen: dict, deployed: dict) -> list[str]:
    """Dotted keys the deployed object or list-of-objects schema requires and the frozen lacks."""
    frozen = frozen.get("items") or frozen
    deployed = deployed.get("items") or deployed
    known = frozen.get("properties")
    if known is None and frozen.get("additionalProperties") is not False:
        return []  # an open object takes any key
    known = known or {}
    missing = [key for key in deployed.get("required", ()) if key not in known]
    for key, schema in deployed.get("properties", {}).items():
        if key in known:
            missing += [f"{key}.{inner}" for inner in _missing_keys(known[key], schema)]
    return missing


def _compare_tool(role: catalog.Role, spec: ToolSpec, parameters: dict) -> list[Drift]:
    frozen = parameters.get("properties") or {}
    found: list[Drift] = []
    found.extend(
        Drift(spec.name, None, f"{f}: {MISSING}") for f in spec.required if f not in frozen
    )
    if spec.actions is not None:
        frozen_actions = (frozen.get("action") or {}).get("enum") or []
        for action in spec.actions_for(role):
            if action.name not in frozen_actions:
                found.append(
                    Drift(spec.name, action.name, "deployed action not in the frozen enum")
                )
                continue
            found.extend(
                Drift(spec.name, action.name, f"{f}: {MISSING}")
                for f in action.required
                if f not in frozen
            )
        allowed = {action.name for action in spec.actions_for(role)}
        found.extend(
            Drift(spec.name, name, "frozen action the validator refuses")
            for name in frozen_actions
            if name not in allowed
        )
    deployed = spec.properties(role)
    known = set(deployed) | {name for action in spec.actions or () for name in action.params}
    for name, schema in frozen.items():
        if name not in known:
            found.append(
                Drift(spec.name, None, f"{name}: frozen field the validator does not know")
            )
            continue
        found.extend(
            Drift(spec.name, None, f"{name}.{key}: {MISSING}")
            for key in _missing_keys(schema, deployed.get(name, {}))
        )
        old_type, new_type = schema.get("type"), deployed.get(name, {}).get("type")
        if old_type and new_type and old_type != new_type:
            found.append(Drift(spec.name, None, f"{name}: frozen as {old_type}, now {new_type}"))
        if name != "action" and "enum" in deployed.get(name, {}):
            refused = [v for v in schema.get("enum", []) if v not in deployed[name]["enum"]]
            if refused:
                found.append(
                    Drift(spec.name, None, f"{name}: frozen values refused now: {refused}")
                )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("state_db", type=Path, help="A copy of a profile's state.db")
    parser.add_argument("--role", required=True, choices=catalog.ROLES)
    parser.add_argument("--session", help="Check one Session instead of every Session")
    args = parser.parse_args(argv)
    frozen_lists = read_frozen_lists(args.state_db, args.session)
    if not frozen_lists:
        print("no frozen tool list found")
        return 0
    disagreements = 0
    for frozen_list in frozen_lists:
        drift = compare(args.role, frozen_list.tools)
        disagreements += len(drift)
        sessions = frozen_list.session_ids
        print(
            f"frozen by {frozen_list.version}, {len(sessions)} Session(s), "
            f"first {sessions[0]}: {len(drift)} disagreement(s)"
        )
        for item in drift:
            print(f"  {item}")
    return 1 if disagreements else 0


if __name__ == "__main__":
    sys.exit(main())
