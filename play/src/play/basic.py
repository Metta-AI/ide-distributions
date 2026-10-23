"""A cheap static check of a GotA BASIC program, before it is written or uploaded.

It follows the compiler's lexer and statement grammar (polyworld ``src/polyworld/basic.nim``
``lex`` and ``parseStatement``, as the policy surface in researcher/games/gota/docs describes):
case-insensitive words, ``'`` and ``REM`` comments, ``:`` as a line break, multiline ``IF … THEN`` / ``ELSE`` / ``END IF``,
``WHILE`` / ``WEND``, top-level ``SUB`` / ``END SUB`` and top-level ``DIM`` arrays with literal
bounds. It checks the limits the builder's GotA reference states: 64 KiB of source, 32 arrays and
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
TOKEN = re.compile(r'\s*(?:(?P<word>[A-Za-z_]\w*)|(?P<int>\d+)|(?P<string>"(?:[^"]|"")*")|(?P<op><=|>=|<>|[()+\-*/\\^.=<>,;]))')
# Statement words the compiler starts a statement with; the rest are assignments or calls.
STATEMENTS = {"dim", "if", "else", "end", "while", "wend", "sub", "exit", "return", "print", "stop", "call", "let"}
OPERATORS = {"and", "or", "xor", "not", "mod", "true", "false", "then"}
RESERVED = {"goto", "gosub"}  # reserved words with no statement: never valid
# Words that cannot start a statement unless they are a variable or a SUB call (``next = 1``).
NOT_GOTA = {
    "for": "GotA BASIC has no FOR/NEXT; use a counter with WHILE … WEND",
    "next": "GotA BASIC has no FOR/NEXT; use a counter with WHILE … WEND",
    "goto": "GotA BASIC has no GOTO; use IF blocks, WHILE loops and SUBs",
    "gosub": "GotA BASIC has no GOSUB; declare a SUB and CALL it",
    "function": "GotA BASIC has no functions that return a value; use a SUB that sets a global",
    "elseif": "GotA BASIC has no ELSEIF; nest another IF block inside ELSE",
    "do": "GotA BASIC has no DO/LOOP; use WHILE … WEND",
    "loop": "GotA BASIC has no DO/LOOP; use WHILE … WEND",
    "select": "GotA BASIC has no SELECT CASE; use IF blocks",
    "case": "GotA BASIC has no SELECT CASE; use IF blocks",
    "const": "GotA BASIC has no CONST; assign a global once",
    "input": "GotA BASIC has no INPUT or file access",
    "open": "GotA BASIC has no file access",
}


@dataclass(frozen=True)
class Problem:
    line: int
    problem: str


@dataclass
class _Block:
    kind: str  # "if", "while", "sub" or "type"
    line: int
    has_else: bool = False


def _statements(text: str):
    """(line number, tokens) of each statement; a bad character is a token ``("bad", char)``."""
    for number, raw in enumerate(text.splitlines(), start=1):
        tokens: list[tuple[str, str]] = []
        rest = raw
        while rest.strip():
            stripped = rest.lstrip()
            if stripped.startswith("'"):
                break
            if stripped.startswith(":"):
                yield number, tokens
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
        yield number, tokens


def check(text: str, starter: str | None = None) -> tuple[list[Problem], list[Problem]]:
    """(problems, warnings). A program with problems would not compile; warnings are names the
    starter never uses (a likely misspelled host call), checked only when a starter is given."""
    problems: list[Problem] = []
    structured = STRUCTURES in text
    limit = MAX_STRUCTURED_SOURCE_BYTES if structured else MAX_SOURCE_BYTES
    size = len(text.encode("utf-8"))
    if size > limit:
        problems.append(Problem(0, f"the program is {size} bytes; the limit is {limit}"))
    blocks: list[_Block] = []
    arrays: dict[str, int] = {}
    defined: set[str] = set()
    called: dict[str, int] = {}
    for line, tokens in _statements(text):
        if not tokens:
            continue
        bad = next((value for kind, value in tokens if kind == "bad"), None)
        reserved = next((value for kind, value in tokens if kind == "word" and value in RESERVED), None)
        first, second = tokens[0][1], tokens[1] if len(tokens) > 1 else None
        if bad is not None:
            problems.append(Problem(line, f"'{bad}' is not allowed outside a PRINT string; "
                                          "GotA BASIC has no string variables and no such symbol"))
        elif reserved is not None:
            problems.append(Problem(line, NOT_GOTA[reserved]))
        elif first in NOT_GOTA and second not in (("op", "="), ("op", "(")):
            problems.append(Problem(line, NOT_GOTA[first]))
        else:
            for position, (kind, value) in enumerate(tokens[:-1]):
                if kind == "word" and tokens[position + 1] == ("op", "("):
                    called.setdefault(value, line)
            problems += _statement(line, tokens, blocks, arrays, defined)
    for block in reversed(blocks):
        closer = {"if": "END IF", "while": "WEND", "sub": "END SUB", "type": "END TYPE"}[block.kind]
        problems.append(Problem(block.line, f"this {block.kind.upper()} is never closed with {closer}"))
    if not structured and len(arrays) > MAX_ARRAYS:
        problems.append(Problem(0, f"{len(arrays)} arrays; the limit is {MAX_ARRAYS}"))
    cells = sum(arrays.values())
    if not structured and cells > MAX_ARRAY_CELLS:
        problems.append(Problem(0, f"the arrays hold {cells} elements; the limit is {MAX_ARRAY_CELLS}"))
    warnings = []
    if starter is not None:
        known = {value for _, tokens in _statements(starter) for kind, value in tokens if kind == "word"}
        known |= defined | set(arrays) | OPERATORS
        warnings = [
            Problem(line, f"{name} is not a name the starter uses or this program defines; "
                          "check its spelling against the starter")
            for name, line in called.items()
            if name not in known
        ]
    return sorted(problems, key=lambda p: p.line), warnings


def _statement(line, tokens, blocks, arrays, defined) -> list[Problem]:
    kind, first = tokens[0]
    words = [value if k == "word" else None for k, value in tokens]
    if kind != "word":
        if kind == "int":
            return [Problem(line, "GotA BASIC has no line numbers; start the line with a statement")]
        return [Problem(line, f"a statement cannot start with {first}")]
    if first == "if":
        if words[-1] != "then":
            return [Problem(line, "IF must end its line with THEN; put the statement on the next "
                                  "line and close the block with END IF")]
        blocks.append(_Block("if", line))
    elif first == "else":
        if len(tokens) != 1:
            return [Problem(line, "ELSE must stand on its own line; nest another IF inside it")]
        if not blocks or blocks[-1].kind != "if" or blocks[-1].has_else:
            return [Problem(line, "this ELSE has no open IF block")]
        blocks[-1].has_else = True
    elif first == "while":
        blocks.append(_Block("while", line))
    elif first == "wend":
        return _close(line, "while", "WEND", blocks)
    elif first == "end" and len(words) > 1:
        if words[1] not in ("if", "sub", "type"):
            return [Problem(line, f"END {tokens[1][1].upper()} closes nothing GotA BASIC has")]
        return _close(line, words[1], f"END {words[1].upper()}", blocks)
    elif first == "type" and len(tokens) == 2 and words[1] is not None:
        blocks.append(_Block("type", line))
    elif first == "sub":
        if blocks:
            return [Problem(line, "a SUB can only be declared at the top level, not inside a block")]
        if len(words) < 2 or words[1] is None:
            return [Problem(line, "SUB needs a name")]
        defined.add(words[1])
        blocks.append(_Block("sub", line))
    elif first in ("exit", "return"):
        if not any(block.kind == "sub" for block in blocks):
            return [Problem(line, f"{first.upper()} can only be used inside a SUB")]
        if first == "exit" and words[1:2] != ["sub"]:
            return [Problem(line, "the only EXIT is EXIT SUB")]
    elif first == "dim":
        if blocks:
            return [Problem(line, "DIM arrays are declared at the top level, not inside a block")]
        found = re.findall(r"(\w+) \( (\d+) \)", " ".join(value for _, value in tokens[1:]))
        if not found and "as" not in words:
            return [Problem(line, "DIM needs a name and a whole-number bound, like DIM seen(9)")]
        for name, bound in found:
            arrays[name] = int(bound) + 1
    elif first in STATEMENTS or first == "rem":
        pass
    elif first in OPERATORS:
        return [Problem(line, f"a statement cannot start with {first.upper()}")]
    elif len(tokens) == 1:
        return [Problem(line, f"{first} on its own is not a statement; assign it or call it with ()")]
    return []


def _close(line, kind, word, blocks) -> list[Problem]:
    if not blocks or blocks[-1].kind != kind:
        if not blocks:
            return [Problem(line, f"this {word} has nothing to close")]
        open_block = blocks[-1]
        return [Problem(line, f"this {word} comes before the {open_block.kind.upper()} on line "
                              f"{open_block.line} is closed")]
    blocks.pop()
    return []


def as_dicts(items: list[Problem]) -> list[dict]:
    return [asdict(item) for item in items]
