#!/usr/bin/env python3
"""Count GotA BASIC storage without executing the policy.

This is a lexical inventory, not a syntax checker. It supports the structured
GotA dialect: SUB parameters are local; other bare variable uses are globals.
DIM arrays consume a separate storage budget and their bounds are inclusive.
Scalar DIM declarations are accepted for the Task 0.1 counting fixture; this
does not establish that the game compiler accepts scalar DIM syntax.
"""

# Token/comment handling adapted from personal_labs_paintbot_pw@6b0bffa5,
# paintbot_pw_lab/tools/strategy_basic.py:91-130. The storage inventory is new.
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TOKEN_RE = re.compile(
    r'\s*(?:(?P<string>"[^"\n]*")|(?P<number>\d+)|'
    r'(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)|(?P<symbol>\S))'
)
KEYWORDS = {
    "and", "call", "dim", "else", "end", "exit", "false", "if", "let", "mod",
    "not", "or", "print", "rem", "return", "stop", "sub", "then", "true",
    "wend", "while", "xor"
}
# Polyworld@7365e4e9390e97bbc8b7722b41a2ae88ae008c2b,
# examples/gods_of_the_arena/bots.nim:45-64,195-201. These are host
# inputs; reading them does not create script globals.
HOST_DATA = {name.lower() for name in (
    "selfId", "selfTeam", "selfClass", "selfX", "selfY", "selfHp",
    "selfMaxHp", "selfMana", "selfMaxMana", "selfGold", "selfLevel", "worldTick",
    "selfLayer", "selfMoveSpeed", "selfAttackRange", "selfAttackDamage",
    "selfTarget", "selfAttackCooldown", "selfAttacksLanded", "mapWidth",
    "mapHeight", "mapLayers", "worldScale", "tickRate"
)}


def _statements(source: str) -> list[list[tuple[str, str]]]:
    statements = []
    for line in source.splitlines():
        statement = []
        for match in TOKEN_RE.finditer(line):
            kind, value = match.lastgroup, match.group(match.lastgroup)
            if value == "'" or (kind == "identifier" and value.lower() == "rem"):
                break
            if value == ":" and kind == "symbol":
                if statement:
                    statements.append(statement)
                statement = []
            else:
                statement.append((kind, value.lower()))
        if statement:
            statements.append(statement)
    return statements


def inventory(source: str) -> dict:
    """Return distinct scalar names, array sizes, and inclusive cell count."""
    statements = _statements(source)
    arrays = {}
    scalars = set()
    routines = {
        statement[1][1] for statement in statements
        if statement[0][1] == "sub" and len(statement) > 1
    }
    # Collect DIM first so later array references cannot become scalars.
    for statement in statements:
        if statement[0][1] != "dim":
            continue
        position = 1
        while position < len(statement):
            kind, name = statement[position]
            if kind != "identifier":
                raise ValueError("Expected a name in DIM")
            position += 1
            if position < len(statement) and statement[position][1] == "(":
                if (position + 2 >= len(statement)
                        or statement[position + 1][0] != "number"
                        or statement[position + 2][1] != ")"):
                    raise ValueError("DIM needs a literal one-dimensional bound")
                arrays[name] = int(statement[position + 1][1]) + 1
                position += 3
            else:
                scalars.add(name)
            if position < len(statement):
                if statement[position][1] != ",":
                    raise ValueError("Expected a comma in DIM")
                position += 1

    parameters = set()
    for statement in statements:
        values = [value for _, value in statement]
        if values[0] == "dim":
            continue
        if values[0] == "sub":
            parameters = {value for kind, value in statement[2:] if kind == "identifier"}
            continue
        if values[:2] == ["end", "sub"]:
            parameters = set()
            continue
        for index, (kind, name) in enumerate(statement):
            if kind != "identifier" or name in KEYWORDS | HOST_DATA | parameters | routines:
                continue
            if index + 1 < len(statement) and statement[index + 1][1] == "(":
                continue  # Array reference or host function call.
            scalars.add(name)
    return {
        "scalar_names": sorted(scalars),
        "arrays": dict(sorted(arrays.items())),
        "array_cells": sum(arrays.values()),
    }


def count_globals(source: str) -> dict[str, int]:
    """Return independent counts of scalar globals and array allocations."""
    storage = inventory(source)
    return {"globals": len(storage["scalar_names"]), "arrays": len(storage["arrays"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--inventory", action="store_true", help="Include names and array cells")
    args = parser.parse_args()
    source = args.source.read_text()
    result = inventory(source) if args.inventory else count_globals(source)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
