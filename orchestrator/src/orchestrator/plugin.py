"""Tool schemas and synchronous handlers loaded in the Hermes process."""

import json
from contextlib import closing
from urllib.error import HTTPError

from .board import Board
from .context import profile_context
from .ide import IDE
from .research import Research, ResearchError
from .service import Service
from .storage import open_storage
from .workspace import Workspace

S = {"type": "string"}
I = {"type": "integer"}
O = {"type": "object"}
STRINGS = {"type": "array", "items": S}

# name: (owner, description, parameters, required)
TOOLS = {
    "ide_workspace": (
        Workspace,
        (
            "Edit the same saved workspace as the human in /ide-final, through its MCP server. "
            "Use a stable request_id; identity comes from your scoped player credential. "
            "operation.method: components.search (query), components.describe (name), layout.read, "
            "layout.validate (view), layout.save (view, revision), draft.read, "
            "draft.save (title, notes, revision), or league.overview. "
            "Read before editing; preserve human content. A conflict requires rereading. "
            "A view has version:1 and body:[components]; discover component props first. "
            "Draft notes are project direction, not executable source. This never releases a policy. "
            "Read the MCP result's isError and structured status; transport success is not effect success."
        ),
        {"request_id": S, "operation": O},
        ["request_id", "operation"],
    ),
    "ide_say": (
        IDE,
        (
            "Publish background news once with key say:<table>:<rowid>. Never use "
            "for a coach chat reply; the bridge publishes that reply. Retry with the same "
            "key and identical text and goal_id; a changed message needs its own event."
        ),
        {
            "text": {"type": "string", "minLength": 1, "maxLength": 20_000},
            "key": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "pattern": r"^(?!permit:)[^\x00]*$",
            },
            "goal_id": {"type": "string", "maxLength": 200},
        },
        ["text", "key"],
    ),
    "ide_task_update": (
        IDE,
        (
            "Update an IDE task. patch is the bridge shape: set (changed body "
            "fields), base_rev (observed revision), idempotency_key (stable for this patch)."
        ),
        {
            "task_id": S,
            "patch": {
                "type": "object",
                "properties": {"set": O, "base_rev": I, "idempotency_key": S},
                "required": ["set", "base_rev", "idempotency_key"],
            },
        },
        ["task_id", "patch"],
    ),
    "worker_skills": (
        Board,
        "Discover worker skills only when an explicit procedure preload is needed.",
        {"assignee": S},
        ["assignee"],
    ),
    "card_create": (
        Board,
        (
            "Create native researcher work. command_id identifies this execution command, not a "
            "topic or task family. Replay identical arguments after a lost response. Choose relevant "
            "existing tasks before creating new work. Describe objective, inputs, constraints and "
            "completion evidence in body. Omit skills unless explicit preloads are necessary."
        ),
        {
            "command_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "assignee": S,
            "title": S,
            "body": S,
            "priority": I,
            "skills": STRINGS,
            "max_runtime_seconds": I,
            "failure_limit": I,
        },
        ["command_id", "assignee", "title", "body"],
    ),
    "card_show": (
        Board,
        "Read a card, its exact last_failure_error, current status, comments and handoff runs. Read this after every failure notification before reporting or recovering.",
        {"id": S},
        ["id"],
    ),
    "card_list": (
        Board,
        (
            "List cards; filters accept native status, assignee, include_archived, "
            "session_id, tenant, limit and order_by."
        ),
        {"filters": O},
        [],
    ),
    "card_comment": (
        Board,
        "Comment durably; steers a running worker.",
        {"id": S, "body": S},
        ["id", "body"],
    ),
    "card_answer": (
        Board,
        "After resolving its blocker, save the answer and resume a blocked or triaged native task. Earlier integrated commits do not imply completion. Configuration changes require a new task after cancelling the old task.",
        {"id": S, "text": S},
        ["id", "text"],
    ),
    "card_close": (
        Board,
        (
            "Complete with nonempty evidence (never force a live claim), or archive "
            "a cancelled card. Inspect native termination before reusing its worker slot. Preserve unfinished artifacts before cancellation."
        ),
        {
            "id": S,
            "outcome": {"type": "string", "enum": ["done", "archived"]},
            "summary": S,
            "metadata": O,
        },
        ["id", "outcome"],
    ),
    "board_reconcile": (
        Service,
        (
            "Read unacknowledged board events, platform transitions and "
            "branches needing recovery, and the current card state including last_failure_error. "
            "Current card state overrides historical event labels. Observation never acknowledges anything."
        ),
        {},
        [],
    ),
    "event_ack": (
        Board,
        (
            "Acknowledge only after acting. Use table/id from events or pending. "
            "Promotion state is canonical, not an acknowledgeable event."
        ),
        {
            "ids": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "enum": ["task_events", "task_comments", "task_runs"],
                        },
                        "id": I,
                    },
                    "required": ["table", "id"],
                },
            }
        },
        ["ids"],
    ),
    "status": (
        Service,
        "Read cards, canonical research summary, pause and promotion state.",
        {},
        [],
    ),
    "records_find": (
        Research,
        "Find canonical records by entity and field=value filters.",
        {"entity": S, "filters": O},
        ["entity"],
    ),
    "records_show": (Research, "Read a canonical record.", {"id": S}, ["id"]),
    "plan_write": (Research, "Publish plan.md to canonical main.", {"text": S}, ["text"]),
    "control_write": (
        Research,
        "Publish pause/resume to canonical control/pause.json.",
        {"action": {"type": "string", "enum": ["pause", "resume"]}, "reason": S},
        ["action", "reason"],
    ),
    "upload_build": (
        Board,
        "Upload the latest completed task run’s verified build. build_dir is a committed task-relative directory containing policy.bas, version.json and gate evidence. Return upload receipts and the new staged commit for integration. Reuse name on retry. Optional experiment binds a research candidate.",
        {"id": S, "run_id": I, "build_dir": S, "name": S, "experiment": S},
        ["id", "run_id", "build_dir", "name"],
    ),
    "promotion_submit": (
        Board,
        ("Submit a reviewed promote decision through the shared write-ahead promotion workflow."),
        {"id": S, "run_id": I, "experiment": S},
        ["id", "run_id", "experiment"],
    ),
    "submit_requested_version": (
        Board,
        (
            "Submit an integrated uploaded version only when the user explicitly requested submission. "
            "Quote that request. This records a requested submission, not statistical improvement. "
            "For autonomous research promotion use promotion_submit instead. Strategy must match the build."
        ),
        {"id": S, "run_id": I, "version": S, "strategy": S, "request": S},
        ["id", "run_id", "version", "strategy", "request"],
    ),
    "bootstrap_champion": (
        Board,
        "Submit an integrated starter-policy version (V-...) only when no champion exists.",
        {"id": S, "run_id": I, "version": S},
        ["id", "run_id", "version"],
    ),
    "accept_champion": (
        Research,
        "Record the league's current champion as the loop's, only after the user confirms.",
        {},
        [],
    ),
    "workspace_integrate": (
        Board,
        "Accept the exact staged commit from the latest completed native run. Archived tasks cannot publish.",
        {"id": S, "run_id": {"type": "integer"}, "commit": S},
        ["id", "run_id", "commit"],
    ),
    "xp_status": (
        Research,
        "Read XP requests for an experiment.",
        {"experiment": S},
        ["experiment"],
    ),
    "budget_limit": (
        Research,
        (
            "Lower the shared workspace episode ceiling to the user's requested limit. Never increases it. "
            "All researchers and uncertain requests consume this same allowance."
        ),
        {"episodes": {"type": "integer", "minimum": 0}},
        ["episodes"],
    ),
    "credits": (Research, "Read the player's available XP credits.", {}, []),
    "leaderboard": (Research, "Read the current league leaderboard.", {}, []),
    "latest_league_result": (
        Research,
        (
            "Read this player's latest live league match: round and episode identifiers, completion "
            "status, participating policy versions, scores and replay. available=false means no "
            "recorded replay yet. Check the policy version and episode status before claiming a "
            "submitted policy completed a match. participants[].position is a zero-based roster "
            "seat index, never a finishing rank or placement. Scores are game-defined; do not infer "
            "a win, loss, draw or rank without an explicit outcome or scoring rule. "
            "This reads the platform, not local research records."
        ),
        {},
        [],
    ),
}


def handler(name):
    owner = TOOLS[name][0]

    def call(args, session_id=None, **kwargs):
        try:
            context = profile_context()
            if session_id:
                from tools.kanban_tools import _persisted_session_id

                origin = _persisted_session_id(session_id)
                if origin:
                    with closing(open_storage(context.storage_path)) as conn, conn:
                        conn.execute(
                            "INSERT INTO coordinator(key,value) VALUES ('session', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (origin,),
                        )
            arguments = dict(args)
            if name == "card_create":
                arguments["session_id"] = session_id
            data = getattr(owner(context), name)(**arguments)
            return json.dumps({"ok": True, "data": data})
        except ResearchError as error:
            return json.dumps(error.envelope)
        except HTTPError as error:
            details = {"status": error.code}
            if error.code == 422 and error.headers.get_content_type() == "application/json":
                detail = json.loads(error.read())["detail"]
                if isinstance(detail, list):
                    # FastAPI includes submitted inputs and validator context; never echo those.
                    fields = {"text", "idempotency_key", "goal_id"}
                    kinds = {
                        "string_too_long",
                        "string_too_short",
                        "string_pattern_mismatch",
                        "string_type",
                        "missing",
                        "extra_forbidden",
                        "value_error",
                    }
                    details["validation"] = [
                        {
                            "field": issue["loc"][1] if issue["loc"][1] in fields else "other",
                            "type": issue["type"] if issue["type"] in kinds else "validation_error",
                        }
                        for issue in detail
                        if len(issue["loc"]) == 2 and issue["loc"][0] == "body"
                    ]
                elif (
                    isinstance(detail, str)
                    and detail.startswith("Idempotency key ")
                    and detail.endswith(" was already used for a different write")
                ):
                    details["kind"] = "idempotency_key_reused"
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "reason": "http",
                        "message": f"cogents API returned HTTP {error.code}",
                        "details": details,
                    },
                }
            )
        except Exception as error:  # noqa: BLE001 - tool boundary reports native failures
            return json.dumps(
                {"ok": False, "error": {"reason": "orchestrator", "message": str(error)}}
            )

    return call


def register(ctx):
    for name, (_, description, properties, required) in TOOLS.items():
        schema = {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        }
        ctx.register_tool(
            name=name,
            toolset="orchestrator",
            schema=schema,
            handler=handler(name),
            description=description,
        )
