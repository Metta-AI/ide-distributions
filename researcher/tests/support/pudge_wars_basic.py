"""Shared helpers for the Pudge Wars Basic game folder tests (used by conftest and tests).

The tools live in a hyphenated folder and import their siblings by bare name
(`import evaluate`), so in-process tests load them through `load_tools`, which keeps them
from colliding with same-named modules of other games in one pytest session.
"""
import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GAME = ROOT / 'games/pudge-wars-basic'
TOOLS = GAME / 'tools'
FIXTURES = ROOT / 'tests/fixtures/pudge_wars_basic'
TOOL_MODULES = sorted(path.stem for path in TOOLS.glob('*.py'))


def load_tools() -> types.SimpleNamespace:
    saved = {name: sys.modules.pop(name) for name in TOOL_MODULES if name in sys.modules}
    sys.path.insert(0, str(TOOLS))
    try:
        loaded = {name: importlib.import_module(name) for name in TOOL_MODULES}
    finally:
        sys.path.remove(str(TOOLS))
        for name in TOOL_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
    return types.SimpleNamespace(**loaded)


def fixture(name: str) -> Path:
    """A recorded fixture, checked against the sha256 in PROVENANCE.md."""
    path = FIXTURES / name
    provenance = (FIXTURES / 'PROVENANCE.md').read_text()
    assert hashlib.sha256(path.read_bytes()).hexdigest() in provenance, f'{name} differs from PROVENANCE.md'
    return path


def results(name: str) -> dict:
    return json.loads(fixture(name).read_text())
