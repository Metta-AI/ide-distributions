import pytest
from support.hermes_python import hermes_python, launcher_python

BASH_WRAPPER = """#!/usr/bin/env bash
set -e
export PYTHONPATH="/opt/hermes:/opt/hermes/.venv/lib/python3.14/site-packages"
exec /opt/hermes/.venv/bin/python -P -c 'import hermes_cli.main' "$@"
"""


def test_python_shebang_is_the_interpreter():
    assert launcher_python("#!/home/u/.venv/bin/python3\nimport sys\n") == "/home/u/.venv/bin/python3"


def test_bash_wrapper_returns_the_exec_target():
    assert launcher_python(BASH_WRAPPER) == "/opt/hermes/.venv/bin/python"


def test_wrapper_without_python_exec_is_an_error():
    with pytest.raises(RuntimeError, match="HERMES_PYTHON"):
        launcher_python("#!/bin/bash\nexec /usr/bin/true\n")


def test_env_var_wins(monkeypatch):
    monkeypatch.setenv("HERMES_PYTHON", "/opt/hermes/.venv/bin/python")
    assert hermes_python() == "/opt/hermes/.venv/bin/python"
