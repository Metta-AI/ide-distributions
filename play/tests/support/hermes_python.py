"""Find the Python interpreter Hermes runs cron scripts with."""

import os
import shlex
import shutil
from pathlib import Path


def hermes_python() -> str:
    """``HERMES_PYTHON`` (set by the EC2 image) or the interpreter named by the ``hermes`` launcher."""
    from_env = os.environ.get("HERMES_PYTHON")
    if from_env:
        return from_env
    return launcher_python(Path(shutil.which("hermes")).resolve().read_text(errors="replace"))


def launcher_python(script: str) -> str:
    """The interpreter a launcher script uses: a ``#!`` python shebang, or the program that a
    bash wrapper (the Docker image's ``hermes``) ``exec``s."""
    lines = script.splitlines()
    if lines and lines[0].startswith("#!") and "python" in lines[0]:
        return lines[0].removeprefix("#!").strip()
    for line in lines:
        words = shlex.split(line, comments=True)
        if len(words) > 1 and words[0] == "exec" and "python" in Path(words[1]).name:
            return words[1]
    raise RuntimeError("cannot find a python interpreter in the hermes launcher; set HERMES_PYTHON")
