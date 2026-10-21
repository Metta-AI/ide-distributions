"""Tool schemas and synchronous handlers loaded in the Hermes process."""

import json
from urllib.error import HTTPError

from .board import Board
from .context import profile_context
from .ide import IDE
from .research import Research, ResearchError
from .service import Service

S = {"type": "string"}
I = {"type": "integer"}
O = {"type": "object"}
STRINGS = {"type": "array", "items": S}

# name: (owner, description, parameters, required)
TOOLS = {
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
            "Create or replay <kind>:<entity>:<attempt> on autoresearch. "
            "Refuse another open card on entity unless replaces names its blocked preceding attempt. "
            "Replacement preserves dependency gates before archiving the old card. "
            "Repair uses its own entity and failed card_sha, without parents. "
            "Describe the objective, inputs, constraints and completion criteria in body. "
            "Omit skills by default; the worker selects its own procedures. "
            "Explicit preloads must come from worker_skills. priority is native Hermes "
            "(higher goes first)."
        ),
        {
            "kind": {
                "type": "string",
                "enum": [
                    "investigation",
                    "scouting",
                    "experimentation",
                    "maintenance",
                    "planning",
                    "review",
                    "repair",
                ],
            },
            "entity": S,
            "attempt": {"type": "integer", "minimum": 1},
            "assignee": S,
            "title": S,
            "body": S,
            "refs": STRINGS,
            "priority": I,
            "skills": {
                **STRINGS,
                "description": "Optional explicit procedure preloads. Omit by default; the worker chooses skills.",
            },
            "max_runtime_seconds": I,
            "failure_limit": I,
            "parents": STRINGS,
            "card_sha": S,
            "replaces": S,
        },
        ["kind", "entity", "assignee", "title", "body"],
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
        "Save an answer, then unblock the card without changing its configuration. For invalid skills, validate corrected names with worker_skills, create the next numbered attempt with corrected skills and replaces set to the blocked card ID.",
        {"id": S, "text": S},
        ["id", "text"],
    ),
    "card_close": (
        Board,
        (
            "Complete with nonempty evidence (never force a live claim), or archive "
            "a cancelled card. Configuration recovery uses card_create with replaces, never archive first: archiving releases dependent tasks. Code failures still require an integrated repair."
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
    "promotion_submit": (
        Research,
        ("Submit a reviewed promote decision through the shared write-ahead promotion workflow."),
        {"experiment": S},
        ["experiment"],
    ),
    "bootstrap_champion": (
        Research,
        "Submit an integrated starter-policy version (V-...) only when no champion exists.",
        {"version": S},
        ["version"],
    ),
    "accept_champion": (
        Research,
        "Record the league's current champion as the loop's, only after the user confirms.",
        {},
        [],
    ),
    "workspace_integrate": (
        Research,
        ("Integrate a pushed card or repair branch via the shared validator and canonical lock."),
        {"branch": S},
        ["branch"],
    ),
    "xp_status": (
        Research,
        "Read XP requests for an experiment.",
        {"experiment": S},
        ["experiment"],
    ),
    "credits": (Research, "Read the player's available XP credits.", {}, []),
    "leaderboard": (Research, "Read the current league leaderboard.", {}, []),
    "latest_league_result": (
        Research,
        ("Read this player's latest live league match: round and episode identifiers, completion "
        "status, participating policy versions, scores and replay. available=false means no "
        "recorded replay yet. Check the policy version and episode status before claiming a "
        "submitted policy completed a match. This reads the platform, not local research records."),
        {},
        [],
    ),
}


def handler(name):
    owner = TOOLS[name][0]

    def call(args, session_id=None, **kwargs):
        try:
            arguments = dict(args)
            if name == "card_create":
                arguments["session_id"] = session_id
            data = getattr(owner(profile_context()), name)(**arguments)
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
