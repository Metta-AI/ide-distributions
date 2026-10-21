"""Tool schemas and synchronous handlers loaded in the Hermes process.

Each handler runs ``python -m play <tool>`` in the profile venv (where coworld and softmax-cli
are installed) and returns its envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false,
"error": {"reason", "message", "details"?}}``. A coach refusal is data with ``outcome:
"refused"``, not an error (``play.program``).
"""

import json
import subprocess

from .context import Context, profile_context
from .launch import ensure_venv

S = {"type": "string"}
POLICY_VERSION = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*:v[0-9]+$",
    "description": "The <name>:v<N> that program_upload returned.",
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
            "Download the coworld's starter player file into the program workspace "
            "(starter/<player>.bas) and check its sha256 against the published manifest. "
            "Safe to repeat."
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
    "program_upload": (
        (
            "Upload a program file from the workspace with coworld upload-policy; returns its "
            "policy_version <name>:v<N>. Each call makes a new version, so upload once per "
            "change and reuse the result."
        ),
        {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "The .bas file, absolute or relative to the program workspace.",
            },
            "name": {
                "type": "string",
                "pattern": r"^[a-z0-9]+(-[a-z0-9]+)*$",
                "maxLength": 40,
                "description": "Program name, lowercase words and hyphens; the player id is appended.",
            },
        },
        ["path", "name"],
    ),
    "practice_request": (
        (
            "Request private practice episodes for an uploaded program in the game's league; "
            "your program plays the chosen seats and random league players fill the rest. "
            "Repeating the same arguments returns the same request; raise attempt only to run the same practice again. " + REFUSAL
        ),
        {
            "policy_version": POLICY_VERSION,
            "episodes": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
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
        ["policy_version"],
    ),
    "practice_status": (
        (
            "Read a practice request's state and episode counts. Once completed, its results "
            "files are downloaded into practice/<request_id>/ and listed."
        ),
        {"request_id": {"type": "string", "pattern": r"^xreq_[A-Za-z0-9_-]+$"}},
        ["request_id"],
    ),
    "program_release": (
        (
            "Submit an uploaded program to the game's league. Call only after the user has "
            "said to release this version. Idempotent: a version already submitted is reported "
            "as released. " + REFUSAL
        ),
        {"policy_version": POLICY_VERSION},
        ["policy_version"],
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
