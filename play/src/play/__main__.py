"""``python -m play <operation>`` with the JSON arguments on stdin; prints one envelope.

The Hermes plugin runs this in the profile venv, where coworld and softmax-cli are installed.
"""

import json
import os
import sys

from ide_research_core.softmax_tool.platform_cli import IdentityError, SoftmaxCliError

from .program import PlayError, Program

OPERATIONS = (
    "starter_fetch",
    "program_upload",
    "practice_request",
    "practice_status",
    "program_release",
)


def run(operation: str, arguments: dict, program: Program | None = None) -> dict:
    if operation not in OPERATIONS:
        return PlayError("bad_arguments", f"unknown operation {operation!r}").envelope
    try:
        program = program or Program(os.environ)
        return {"ok": True, "data": getattr(program, operation)(**arguments)}
    except PlayError as error:
        return error.envelope
    except TypeError as error:
        return PlayError("bad_arguments", str(error)).envelope
    except IdentityError as error:
        return PlayError("identity", str(error)).envelope
    except SoftmaxCliError as error:
        return PlayError("softmax", str(error)).envelope


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m play <operation> < arguments.json", file=sys.stderr)
        return 2
    print(json.dumps(run(argv[0], json.loads(sys.stdin.read() or "{}"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
