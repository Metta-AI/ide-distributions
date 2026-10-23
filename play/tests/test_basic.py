import pytest
from conftest import STARTER

from play import basic


def problems(text: str) -> list[basic.Problem]:
    return basic.check(text)[0]


def test_the_real_gota_starter_has_no_problems_and_no_warnings():
    text = STARTER.decode()
    assert (
        "for candidate = 0 to 9" in text and "elseif" in text and "exit for" in text
    )  # what it uses
    assert basic.check(text, text) == ([], [])


@pytest.mark.parametrize(
    "text",
    [
        "FOR i = 0 TO 9 STEP 2\n  x = i\nNEXT i\n",
        "FOR i = 9 TO 0 STEP -1\n  FOR j = 0 TO 1\n    x = j\n  NEXT\nNEXT\n",
        "IF a = 1 THEN\n  x = 1\nELSEIF a = 2 THEN\n  x = 2\nELSE\n  x = 3\nEND IF\n",
        "FOR i = 0 TO 9\n  IF i = 3 THEN\n    EXIT FOR\n  END IF\nNEXT i\n",
        "DO\n  x = x + 1\n  IF x > 3 THEN EXIT DO\nLOOP\n",
        "DO WHILE x < 3\n  x = x + 1\nLOOP\n",
        "SELECT CASE x\n  CASE 1, 2\n    y = 1\n  CASE ELSE\n    y = 2\nEND SELECT\n",
        "IF x = 1 THEN y = 2 ELSE y = 3\n",
        "start:\nx = x + 1\nIF x < 3 THEN GOTO start\n",
        "10 x = 1\n20 GOTO 10\n",
        'DIM names$(3)\nnames$(0) = "a"\nSUB hi(n)\n  RETURN\nEND SUB\n',
        "x = 1 + _\n  2\n",
        "x = 1.5 + 2e1\n",
        "FOR i = 0 TO 1\n  FOR j = 0 TO 1\n    x = 1\n  NEXT j, i\n",
        "IF x THEN: y = 1\nz = 2\n",
    ],
)
def test_the_whole_dialect_the_compiler_accepts_is_accepted(text):
    assert problems(text) == []


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("FOR i = 0 TO 1\n  FOR j = 0 TO 1\n  NEXT i, j\n", "does not match FOR j"),
        ("EXIT WHILE\n", "no EXIT WHILE"),
        ("WHILE 1\n  EXIT FOR\nWEND\n", "no enclosing FOR"),
        ("FOR i = 0 TO 3\n  x = 1\n", "NEXT"),
        ("FOR i = 0 TO 3\nNEXT j\n", "does not match FOR i"),
        ("DO\n  x = 1\n", "LOOP"),
        ("SELECT CASE x\n  CASE 1\n", "END SELECT"),
        ("CASE 1\n", "inside SELECT"),
        ("ELSEIF x THEN\n", "no open IF"),
        ("IF x = 1\n  y = 2\nEND IF\n", "IF needs THEN"),
        ("y = 2 @ 3\n", "'@'"),
        ('x = "open\n', "'\"'"),
        ("CONST k = 1\n", "no CONST"),
        ("SUB a()\n  SUB b()\n  END SUB\nEND SUB\n", "top level"),
    ],
)
def test_what_the_compiler_certainly_refuses_is_refused(text, needle):
    found = problems(text)
    assert found and needle in " ".join(p.problem for p in found), found


def test_the_limits_it_can_count_cheaply():
    subs = "".join(f"SUB s{i}()\nEND SUB\n" for i in range(64))
    assert "64 routines" not in "".join(p.problem for p in problems(subs))
    assert any("63" in p.problem for p in problems(subs))  # 64 SUBs + the top level is 65 routines
    assert problems("".join(f"SUB s{i}()\nEND SUB\n" for i in range(63))) == []
    assert any(
        "global variables" in p.problem
        for p in problems("".join(f"v{i} = 1\n" for i in range(257)))
    )
    assert problems("".join(f"v{i} = 1\n" for i in range(256))) == []
    local = "SUB f(a)\n  a = 1\nEND SUB\n" + "".join(f"v{i} = 1\n" for i in range(256))
    assert problems(local) == []  # a parameter is local, not a global
    wide = "SUB f(" + ", ".join(f"p{i}" for i in range(17)) + ")\nEND SUB\n"
    assert any("17 parameters" in p.problem for p in problems(wide))


def test_the_structures_marker_counts_only_on_the_first_line():
    many = "".join(f"v{i} = 1\n" for i in range(300))
    assert problems("' @gota-structures\n" + many) == []
    assert any(
        "global variables" in p.problem for p in problems("x = 1\n' @gota-structures\n" + many)
    )
    assert any("global variables" in p.problem for p in problems(many))
    assert any(
        "limit is 512" in p.problem
        for p in problems("' @gota-structures\n" + "".join(f"v{i} = 1\n" for i in range(513)))
    )
    assert problems("' @gota-structures\n" + "".join(f"v{i} = 1\n" for i in range(512))) == []
    arrays = "".join(f"DIM a{i}(9)\n" for i in range(33))
    assert any("arrays" in p.problem for p in problems("x = 1\n' @gota-structures\n" + arrays))
    assert problems("' @gota-structures\n" + arrays) == []
