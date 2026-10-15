"""Storage counting for the GotA BASIC dialect."""

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "count_globals", ROOT / "games/gota/tools/count_globals.py"
)
counter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(counter)


def test_acceptance_fixture():
    source = (ROOT / "tests/fixtures/gota/globals.bas").read_text()
    assert counter.count_globals(source) == {"globals": 5, "arrays": 1}


def test_sub_scratch_is_global_but_parameters_are_local():
    source = '''
DIM history(7)
SUB update(local, other)
  local = other
  scratch = local + read_only
  history(local) = scratch
END SUB
shared = 1
update(shared, 2)
'''
    assert counter.count_globals(source) == {"globals": 3, "arrays": 1}


def test_case_comments_strings_and_colon_statements():
    source = '''
DIM History(7): DIM HISTORY(7)
Value = 1: VALUE = 2 ' fake = 3
REM ignored = 4
PRINT "DIM ghost(9): other = 2", Value
SUB report(value)
  LET scratch = value
END SUB
'''
    assert counter.count_globals(source) == {"globals": 2, "arrays": 1}


def test_host_data_does_not_allocate_globals():
    assert counter.count_globals("scratch = worldTick + selfX") == {
        "globals": 1, "arrays": 0
    }


def test_array_cells_use_inclusive_bound():
    result = counter.inventory("DIM first(7), second(0)\nscratch = 1")
    assert result["array_cells"] == 9
    assert result["scalar_names"] == ["scratch"]
    assert result["arrays"] == {"first": 8, "second": 1}


def test_exact_submitted_v13_artifact_inventory():
    artifact = (ROOT / "tests/fixtures/gota/submitted-v13.bas").read_bytes()
    assert hashlib.sha256(artifact).hexdigest() == (
        "d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1"
    )
    assert len(artifact) == 54718
    assert counter.count_globals(artifact.decode()) == {"globals": 251, "arrays": 32}
    assert counter.inventory(artifact.decode())["array_cells"] == 3464
