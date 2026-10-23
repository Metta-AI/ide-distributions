"""Tool schemas and synchronous handlers loaded in the Hermes process.

Each handler runs ``python -m play <tool>`` in the profile venv (where coworld and softmax-cli
are installed) and returns its envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false,
"error": {"reason", "message", "details"?}}``. A coach refusal is data with ``outcome:
"refused"``, not an error (``play.program``).
"""

import json
import subprocess

from .basic import MAX_SOURCE_BYTES
from .context import Context, profile_context
from .fresh import clear_notes
from .launch import ensure_venv
from .limits import DEFAULT_EPISODES, MAX_EPISODES, MAX_WAIT_SECONDS, NAME_MAX, STEP_STATUS, STEPS

PROGRAM = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]*((:[vV]| [vV]| version )[0-9]+)?$",
    "maxLength": 120,
    "description": (
        "The program as the user names it: \"towerless v2\" (or \"towerless:v2\") for one "
        "version, \"towerless\" for its latest. Never add a player id; the tool finds it."
    ),
}
NAME = {
    "type": "string",
    "pattern": r"^[a-z0-9]+(-[a-z0-9]+)*$",
    "maxLength": NAME_MAX,
    "description": "The program's name as the user knows it. Omit for the current program.",
}
REFUSAL = (
    "A coach refusal returns outcome refused with refusal permit_required (state waiting, "
    "declined or over_cap, and the ask), credit_denied or league_not_chosen. Tell the user; "
    "never retry around it."
)
TIMEOUT_S = 840  # an upload may take 600 s; Hermes allows tool calls up to timeouts.tools

# name: (description, parameters, required)
TOOLS = {
    "starter_fetch": (
        (
            "Download the coworld's starter program into the program workspace and check it "
            "against the published file. Safe to repeat."
        ),
        {
            "player": {
                "type": "string",
                "pattern": r"^[A-Za-z0-9_-]+$",
                "description": "Manifest player id. Omit for the coworld's starter (baseline).",
            }
        },
        [],
    ),
    "program_show": (
        (
            "Show the program as it is now (or the starter, before the first change): its full "
            "text, its name, its last uploaded version and whether it changed since. With "
            "program, show the text of that uploaded version, or of the starter with "
            "\"starter\", instead."
        ),
        {"program": PROGRAM},
        [],
    ),
    "program_change": (
        (
            "Replace the program with a full new text (use program_edit for a small change). The "
            "text is checked first (size, closed blocks, only GotA BASIC statements, the compile "
            "limits it can count); on a problem nothing is written and the error lists each "
            "problem by line. Returns the diff and warnings about names the starter never uses."
        ),
        {
            "text": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_SOURCE_BYTES,
                "description": "The whole new program, not a fragment.",
            },
            "change": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "What this change does, in the user's words.",
            },
        },
        ["text", "change"],
    ),
    "program_edit": (
        (
            "Change the program with small find-and-replace edits instead of rewriting it. Each "
            "find must match exactly once (edit_not_found / edit_ambiguous otherwise, with "
            "nothing written); edits apply in order to the program now, or the starter. The "
            "result gets the same check as program_change. Returns the diff and warnings."
        ),
        {
            "edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "find": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Exact text to replace; it must occur once. Include a neighbouring line if needed.",
                        },
                        "replace": {"type": "string", "description": "The new text; empty deletes the find text."},
                    },
                    "required": ["find", "replace"],
                    "additionalProperties": False,
                },
            },
            "change": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": "What this change does, in the user's words.",
            },
        },
        ["edits", "change"],
    ),
    "program_versions": (
        (
            "List all your programs and their versions on the platform, with what each version "
            "of the program changed, its practice results and whether it was released, and how "
            "the program now differs from its last version, or from the version `program` names."
        ),
        {"program": PROGRAM},
        [],
    ),
    "program_upload": (
        (
            "Upload the changed program as its next version and return it as the user names it "
            "(\"towerless v2\"). Uploading an unchanged program returns the same version."
        ),
        {
            "name": {
                **NAME,
                "description": (
                    "Needed for the first upload: the short name the user chose. Later uploads "
                    "keep the name; a different name starts a new program."
                ),
            }
        },
        [],
    ),
    "practice_request": (
        (
            "Request private practice games for an uploaded version; the program plays the "
            "chosen seats and players the platform draws from the league fill the rest. "
            "Repeating the same arguments returns the same request; raise attempt only to play "
            "the same practice again. " + REFUSAL
        ),
        {
            "program": {**PROGRAM, "description": PROGRAM["description"] + " Omit for the latest version."},
            "episodes": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_EPISODES,
                "default": DEFAULT_EPISODES,
                "description": "How many games.",
            },
            "seats": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 9},
                "minItems": 1,
                "uniqueItems": True,
                "default": [0],
                "description": "GotA seats the program plays: 0-4 red, 5-9 blue.",
            },
            "attempt": {"type": "integer", "minimum": 1, "default": 1},
        },
        [],
    ),
    "practice_status": (
        (
            "How far a practice is. Once it is over: each game's result in plain words (won, "
            "lost, ran out of time, failed, and why), the hero's glory and XP, a summary "
            "against the previous version's practice, what the results can and cannot measure, "
            "and a link to watch each game."
        ),
        {
            "request_id": {
                "type": "string",
                "pattern": r"^xreq_[A-Za-z0-9_-]+$",
                "description": "Omit for the latest practice.",
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_WAIT_SECONDS,
                "default": 0,
                "description": "Wait up to this long for the practice to end before answering.",
            },
        },
        [],
    ),
    "program_release": (
        (
            "Release a version of the program to the league. Call only after the user said yes "
            "to releasing exactly this version. Idempotent: a version already released is "
            "reported as released. " + REFUSAL
        ),
        {
            "program": {
                **PROGRAM,
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]*(:[vV]| [vV]| version )[0-9]+$",
                "description": "The exact version the user said yes to, as they name it: \"towerless v2\".",
            },
            "user_said_yes": {
                "const": True,
                "description": "True only because the user said to release this version.",
            },
        },
        ["program", "user_said_yes"],
    ),
    "task_update": (
        (
            "Keep the task card for the user's current request up to date; the user sees it in "
            "the Mind on the play page. Pass title to start the card for a new request, then "
            "update each step as it runs and ends; pass outcome to finish the card. Write in "
            "plain words."
        ),
        {
            "title": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "description": "Starts a new card: the request in a few plain words.",
            },
            "step": {"enum": list(STEPS)},
            "status": {"enum": list(STEP_STATUS)},
            "detail": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "What is happening or happened in this step, in plain words.",
            },
            "version": {"type": "integer", "minimum": 1, "description": "The version the card is about."},
            "outcome": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": "Finishes the card: what came of the request, in plain words.",
            },
        },
        ["step", "status", "detail"],
    ),
    "fresh_start": (
        (
            "Forget this user so that the next conversation is a first meeting: empties your "
            "own notes and your notes about them. It does not touch the program workspace, "
            "uploaded programs, credentials or anything else. Call only when the user has "
            "explicitly asked in this conversation to start over, and never otherwise."
        ),
        {
            "confirm": {
                "const": True,
                "description": "True only because the user explicitly asked to start over.",
            }
        },
        ["confirm"],
    ),
}


def call(context: Context, name: str, arguments: dict) -> dict:
    ensure_venv(context.home)
    result = subprocess.run(
        [str(context.home / "local/venv/bin/python"), "-m", "play", name],
        input=json.dumps(arguments),
        env=context.env,
        cwd=context.home,
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        return {"ok": False, "error": {"reason": "play", "message": detail or "no output"}}
    return json.loads(lines[-1])


def handler(name):
    def run(args, **kwargs):
        try:
            if name == "fresh_start":  # in this process: it only empties two note files
                if dict(args).get("confirm") is not True:
                    raise ValueError("fresh_start needs confirm: true")
                return json.dumps({"ok": True, "data": clear_notes(profile_context().home)})
            return json.dumps(call(profile_context(), name, dict(args)))
        except Exception as error:  # noqa: BLE001 - tool boundary reports native failures
            return json.dumps({"ok": False, "error": {"reason": "play", "message": str(error)}})

    return run


def schema(name):
    description, properties, required = TOOLS[name]
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def register(ctx):
    for name, (description, _, _) in TOOLS.items():
        ctx.register_tool(
            name=name,
            toolset="play",
            schema=schema(name),
            handler=handler(name),
            description=description,
        )
