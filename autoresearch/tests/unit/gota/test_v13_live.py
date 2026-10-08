"""The live compatibility artifact must change only authorized identifier and operator tokens."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('make_v13_live', ROOT / 'games/gota/tools/make_v13_live.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FIXTURES = ROOT / 'tests/fixtures/gota'


def test_original_identity_and_only_authorized_token_changes():
    original = (FIXTURES / 'submitted-v13.bas').read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        'd0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1')
    live = (FIXTURES / 'v13-live.bas').read_bytes()
    assert live.decode() == MODULE.make_live(original.decode())
    before = [m.group() for m in MODULE.TOKENS.finditer(original.decode())]
    after = [m.group() for m in MODULE.TOKENS.finditer(live.decode())]
    assert len(before) == len(after)
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(changed) == 71
    assert sum(a.lower() == 'is' and b == 'v13_item_slot' for a, b in changed) == 7
    assert sum(a == '/' and b == '\\' for a, b in changed) == 64


def test_strings_comments_and_other_identifiers_unchanged():
    assert MODULE.make_live('is = 1\nPRINT "is"\n\x27 is\nREM is\nthis = is\n') == (
        'v13_item_slot = 1\nPRINT "is"\n\x27 is\nREM is\nthis = v13_item_slot\n')


def test_identifier_collision_rejected():
    with pytest.raises(ValueError, match='collides'):
        MODULE.make_live('is = 1\nV13_ITEM_SLOT = 2\n')


def test_division_changes_only_executable_operators():
    assert MODULE.make_live('a = 5 / 2\nPRINT "/"\n\x27 /\nREM /\n') == (
        'a = 5 \\ 2\nPRINT "/"\n\x27 /\nREM /\n')
