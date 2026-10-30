"""Tests that need a real Hermes install.

Every test in this folder carries the ``hermes`` marker (deselected by default through
``addopts``) and skips unless ``HERMES_BIN`` names the Hermes executable to test against.
Every Hermes invocation must set ``HERMES_HOME`` to a temporary directory so the developer's
own ``~/.hermes`` is never touched.
"""

import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        if HERE in item.path.resolve().parents:
            item.add_marker(pytest.mark.hermes)


@pytest.fixture(scope="session")
def hermes_bin() -> Path:
    value = os.environ.get("HERMES_BIN")
    if not value:
        pytest.skip("HERMES_BIN is not set; these tests need a real Hermes install")
    path = Path(value).expanduser()
    if not path.is_file():
        pytest.skip(f"HERMES_BIN does not name a file: {path}")
    return path
