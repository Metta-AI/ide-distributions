"""A cheap static check of a GotA BASIC program, before it is written or uploaded.

It follows the lexer and statement grammar of the compiler GotA runs (treeform/bassy
``src/bassy.nim``: ``lex``, ``parseStatement`` and the ``parse*`` procs it calls; polyworld
requires it): case-insensitive words, ``'`` and ``REM`` comments, ``:`` as a line break, ``_`` line
continuation, labels and ``GOTO`` / ``GOSUB`` / ``ON``, single-line and block ``IF`` with ``ELSEIF``
and ``ELSE``, ``WHILE`` / ``WEND``, ``FOR`` / ``TO`` / ``STEP`` / ``NEXT``, ``DO`` / ``LOOP``,
``SELECT CASE``, ``EXIT SUB`` / ``FOR`` / ``DO``, top-level ``SUB`` / ``END SUB`` and top-level ``DIM``
arrays with literal bounds. It also counts what it can cheaply: the 64 routines and the 256 globals (512 in structures mode)
(only variables assigned to, so a floor, never an overcount). It checks the limits the builder's GotA reference states: 64 KiB of source, 32 arrays and
4,096 array elements in total. A program that opts into GotA's structures (a ``' @gota-structures``
comment; researcher/games/gota/gates/limits.py) gets the host's storage, so only its size and
blocks are checked. ``TYPE`` blocks and ``.`` member access are accepted in both modes.

It flags only what the compiler certainly refuses, and it does not compile: a misspelled host
call or a budget overrun still shows only in a game, so names the starter never uses come back as
warnings.
"""

import re
from dataclasses import asdict, dataclass

MAX_SOURCE_BYTES = 65536
MAX_STRUCTURED_SOURCE_BYTES = 65535
STRUCTURES = "@gota-structures"
MAX_ARRAYS = 32
MAX_ARRAY_CELLS = 4096
MAX_ROUTINES = 64  # including the top-level routine
MAX_GLOBALS = 256
MAX_STRUCTURED_GLOBALS = 512  # structures.nim limits: 512 globals, 128 arrays, 262144 cells
MAX_STRUCTURED_ARRAYS = 128
MAX_STRUCTURED_ARRAY_CELLS = 262144
MAX_PARAMETERS = 16
TOKEN = re.compile(
    r'\s*(?:(?P<word>[A-Za-z_]\w*\$?)|(?P<int>\d+)|(?P<string>"(?:[^"]|"")*")|(?P<op><=|>=|<>|[()+\-*/\\^.=<>,;]))'
)
# Statement words the compiler starts a statement with; the rest are assignments or calls.
STATEMENTS = {
    "dim",
    "if",
    "elseif",
    "else",
    "end",
    "while",
    "wend",
    "sub",
    "exit",
    "return",
    "print",
    "stop",
    "call",
    "let",
    "for",
    "next",
    "do",
    "loop",
    "select",
    "case",
    "goto",
    "gosub",
    "on",
    "data",
    "type",
}
# Words that cannot start a statement.
OPERATORS = {
    "and",
    "or",
    "xor",
    "not",
    "mod",
    "true",
    "false",
    "then",
    "to",
    "step",
    "until",
    "is",
    "eqv",
    "imp",
    "as",
}
# Words that cannot start a statement unless they are a variable or a SUB call (``input = 1``).
NOT_GOTA = {
    "function": "GotA BASIC has no functions that return a value; use a SUB that sets a global",
    "const": "GotA BASIC has no CONST; assign a global once",
    "input": "GotA BASIC has no INPUT or file access",
    "open": "GotA BASIC has no file access",
}
CLOSERS = {
    "if": "END IF",
    "while": "WEND",
    "sub": "END SUB",
    "type": "END TYPE",
    "for": "NEXT",
    "do": "LOOP",
    "select": "END SELECT",
}


@dataclass(frozen=True)
class Problem:
    line: int
    problem: str


@dataclass
class _Block:
    kind: str  # "if", "while", "sub", "type", "for", "do" or "select"
    line: int
    has_else: bool = False
    counter: str | None = None


def _statements(text: str):
    """(line number, tokens, colon) of each statement; ``colon`` is true when ``:`` ended it. A bad
    character is a token ``("bad", char)``. A trailing ``_`` joins the next line."""
    carry: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        tokens: list[tuple[str, str]] = carry
        carry = []
        rest = raw
        while rest.strip():
            stripped = rest.lstrip()
            if stripped.startswith("'"):
                break
            if stripped.startswith(":"):
                yield number, tokens, True
                tokens, rest = [], stripped[1:]
                continue
            match = TOKEN.match(rest)
            if match is None:
                tokens.append(("bad", stripped[0]))
                rest = stripped[1:]
                continue
            kind = match.lastgroup
            value = match.group(kind)
            rest = rest[match.end() :]
            if kind == "word":
                value = value.lower()
                if value == "rem":
                    tokens.append((kind, value))
                    break
            tokens.append((kind, value))
        if tokens and tokens[-1] == ("word", "_"):
            carry = tokens[:-1]
            continue
        yield number, tokens, False
    if carry:
        yield number, carry, False


def _structured(text: str) -> bool:
    """The compiler honours the marker only as a comment on the very first line."""
    first = text.split("\n", 1)[0]
    return re.match(r"'\s*" + re.escape(STRUCTURES), first) is not None


def check(text: str, starter: str | None = None) -> tuple[list[Problem], list[Problem]]:
    """(problems, warnings). A program with problems would not compile; warnings are names the
    starter never uses (a likely misspelled host call), checked only when a starter is given."""
    problems: list[Problem] = []
    structured = _structured(text)
    limit = MAX_STRUCTURED_SOURCE_BYTES if structured else MAX_SOURCE_BYTES
    size = len(text.encode("utf-8"))
    if size > limit:
        problems.append(Problem(0, f"the program is {size} bytes; the limit is {limit}"))
    blocks: list[_Block] = []
    arrays: dict[str, int] = {}
    defined: set[str] = set()
    called: dict[str, int] = {}
    assigned: set[str] = set()
    params: set[str] = set()
    inline_line = 0  # the last line that held a single-line IF, where ELSE may follow mid-line
    for line, tokens, colon in _statements(text):
        if tokens and tokens[0][0] == "int":  # a line number is a label
            tokens = tokens[1:]
        if not tokens:
            continue
        bad = next((value for kind, value in tokens if kind == "bad"), None)
        first, second = tokens[0][1], tokens[1] if len(tokens) > 1 else None
        if bad is not None:
            problems.append(
                Problem(
                    line,
                    f"'{bad}' is not a character GotA BASIC understands "
                    "(or a string is missing its closing quote)",
                )
            )
        elif (
            len(tokens) == 1
            and tokens[0][0] == "word"
            and colon
            and first not in STATEMENTS | OPERATORS
        ):
            continue  # ``name:`` is a label
        elif first in NOT_GOTA and second not in (("op", "="), ("op", "(")):
            problems.append(Problem(line, NOT_GOTA[first]))
        else:
            for position, (kind, value) in enumerate(tokens[:-1]):
                if kind == "word" and tokens[position + 1] == ("op", "("):
                    called.setdefault(value, line)
            if (
                first == "if"
                and "then" in [v for _, v in tokens]
                and (colon or _body(tokens)[-1][1] != "then")
            ):
                inline_line = line  # ``THEN:`` is a single-line IF too (parseIf)
            problems += _statement(
                line, tokens, blocks, arrays, defined, inline_line == line, colon
            )
            if first == "sub":
                params = {v for k, v in tokens[2:] if k == "word"}
                if len(params) > MAX_PARAMETERS:
                    problems.append(
                        Problem(line, f"{len(params)} parameters; the limit is {MAX_PARAMETERS}")
                    )
            elif first == "end" and second == ("word", "sub"):
                params = set()
            target = _assigned(tokens)
            if target is not None and target not in params:
                assigned.add(target)
    for block in reversed(blocks):
        problems.append(
            Problem(
                block.line, f"this {block.kind.upper()} is never closed with {CLOSERS[block.kind]}"
            )
        )
    if len(defined) + 1 > MAX_ROUTINES:
        problems.append(
            Problem(
                0,
                f"{len(defined)} SUBs; the limit is {MAX_ROUTINES - 1} "
                "(the top level counts as a routine)",
            )
        )
    scalars = len(assigned - set(arrays) - defined)
    max_globals = MAX_STRUCTURED_GLOBALS if structured else MAX_GLOBALS
    max_arrays = MAX_STRUCTURED_ARRAYS if structured else MAX_ARRAYS
    max_cells = MAX_STRUCTURED_ARRAY_CELLS if structured else MAX_ARRAY_CELLS
    if scalars > max_globals:
        problems.append(
            Problem(
                0, f"at least {scalars} global variables are assigned; the limit is {max_globals}"
            )
        )
    if len(arrays) > max_arrays:
        problems.append(Problem(0, f"{len(arrays)} arrays; the limit is {max_arrays}"))
    cells = sum(arrays.values())
    if cells > max_cells:
        problems.append(Problem(0, f"the arrays hold {cells} elements; the limit is {max_cells}"))
    warnings = []
    if starter is not None:
        known = {
            value
            for _, tokens, _ in _statements(starter)
            for kind, value in tokens
            if kind == "word"
        }
        known |= defined | set(arrays) | OPERATORS
        warnings = [
            Problem(
                line,
                f"{name} is not a name the starter uses or this program defines; "
                "check its spelling against the starter",
            )
            for name, line in called.items()
            if name not in known
        ]
    return sorted(problems, key=lambda p: p.line), warnings


def _body(tokens):
    """The tokens without a trailing ``REM`` comment."""
    return tokens[:-1] if tokens and tokens[-1] == ("word", "rem") else tokens


def _assigned(tokens) -> str | None:
    """The scalar a statement assigns, if it is ``name = …``, ``LET name = …`` or ``FOR name = …``."""
    words = tokens[1:] if tokens[0] == ("word", "let") else tokens
    if tokens[0] == ("word", "for"):
        words = tokens[1:]
    if len(words) > 1 and words[0][0] == "word" and words[1] == ("op", "="):
        name = words[0][1]
        if tokens[0][1] in STATEMENTS - {"let", "for"} or name in OPERATORS or name.endswith("$"):
            return None
        return name
    return None


def _statement(line, tokens, blocks, arrays, defined, inline, colon=False) -> list[Problem]:
    kind, first = tokens[0]
    tokens = _body(tokens)
    words = [value if k == "word" else None for k, value in tokens]
    if kind != "word":
        return [Problem(line, f"a statement cannot start with {first}")]
    if first == "if":
        if "then" not in words:
            return [Problem(line, "IF needs THEN")]
        if words[-1] == "then" and not colon:
            blocks.append(_Block("if", line))
    elif first == "elseif":
        if not blocks or blocks[-1].kind != "if" or blocks[-1].has_else:
            return [Problem(line, "this ELSEIF has no open IF block")]
        if words[-1] != "then":
            return [Problem(line, "ELSEIF must end its line with THEN")]
    elif first == "else":
        if inline and not blocks:
            return []
        if len(tokens) != 1:
            return [Problem(line, "ELSE must stand on its own line; nest another IF inside it")]
        if not blocks or blocks[-1].kind != "if" or blocks[-1].has_else:
            return [Problem(line, "this ELSE has no open IF block")]
        blocks[-1].has_else = True
    elif first == "while":
        blocks.append(_Block("while", line))
    elif first == "wend":
        return _close(line, "while", "WEND", blocks)
    elif first == "for":
        blocks.append(_Block("for", line, counter=words[1] if len(words) > 1 else None))
        if "to" not in words:
            return [Problem(line, "FOR needs TO, like FOR i = 0 TO 9")]
    elif first == "next":
        # ``NEXT j, i`` is split into two NEXTs (prepareTokens): the inner counter first
        names = [w for w in words[1:] if w]
        problems = []
        for name in names or [None]:
            problems += _close(line, "for", "NEXT", blocks, [name] if name else [])
        return problems
    elif first == "do":
        blocks.append(_Block("do", line))
    elif first == "loop":
        return _close(line, "do", "LOOP", blocks)
    elif first == "select":
        if words[1:2] != ["case"]:
            return [Problem(line, "SELECT must be followed by CASE")]
        blocks.append(_Block("select", line))
    elif first == "case":
        if not any(block.kind == "select" for block in blocks):
            return [Problem(line, "CASE can only be used inside SELECT CASE")]
    elif first == "end" and len(words) > 1:
        if words[1] not in ("if", "sub", "type", "select"):
            return [Problem(line, f"END {tokens[1][1].upper()} closes nothing GotA BASIC has")]
        return _close(line, words[1], f"END {words[1].upper()}", blocks)
    elif first == "type" and len(tokens) == 2 and words[1] is not None:
        blocks.append(_Block("type", line))
    elif first == "sub":
        if blocks:
            return [
                Problem(line, "a SUB can only be declared at the top level, not inside a block")
            ]
        if len(words) < 2 or words[1] is None:
            return [Problem(line, "SUB needs a name")]
        defined.add(words[1])
        blocks.append(_Block("sub", line))
    elif first == "exit":
        what = words[1] if len(words) > 1 else None
        if what not in ("sub", "for", "do"):
            return [
                Problem(line, "EXIT must be EXIT SUB, EXIT FOR or EXIT DO (there is no EXIT WHILE)")
            ]
        if not any(block.kind == what for block in blocks):
            return [Problem(line, f"EXIT {what.upper()} has no enclosing {what.upper()}")]
    elif first == "dim":
        if blocks:
            return [Problem(line, "DIM arrays are declared at the top level, not inside a block")]
        found = re.findall(r"([\w$]+) \( (\d+) \)", " ".join(value for _, value in tokens[1:]))
        if not found and "as" not in words:
            return [Problem(line, "DIM needs a name and a whole-number bound, like DIM seen(9)")]
        for name, bound in found:
            arrays[name] = int(bound) + 1
    elif first in STATEMENTS:
        pass
    elif first in OPERATORS:
        return [Problem(line, f"a statement cannot start with {first.upper()}")]
    elif len(tokens) == 1:
        return [
            Problem(line, f"{first} on its own is not a statement; assign it or call it with ()")
        ]
    return []


def _close(line, kind, word, blocks, counters=()) -> list[Problem]:
    if not blocks or blocks[-1].kind != kind:
        if not blocks:
            return [Problem(line, f"this {word} has nothing to close")]
        open_block = blocks[-1]
        return [
            Problem(
                line,
                f"this {word} comes before the {open_block.kind.upper()} on line "
                f"{open_block.line} is closed",
            )
        ]
    counter = blocks.pop().counter
    if len(counters) == 1 and counter and counters[0] != counter:
        return [Problem(line, f"NEXT {counters[0]} does not match FOR {counter}")]
    return []


def as_dicts(items: list[Problem]) -> list[dict]:
    return [asdict(item) for item in items]
