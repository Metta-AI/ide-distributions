"""Deterministic Phase A storage relocation. No source or gameplay substitution."""

import hashlib
import re

from gota_host import BASELINE, BASELINE_SHA256

ARRAYS = {
    "rt__def": (64, 64),
    "rt__prio": (128, 64),
    "rt__rcap": (192, 64),
    "rt__ok": (256, 64),
    "rt__flag": (320, 125),
    "rt__fw": (445, 4),
    **{
        f"rt__a_{name}": (449 + 5 * i, 5)
        for i, name in enumerate(("on", "until", "prev", "rule", "op", "amt", "dur"))
    },
}
FIXED = {
    name: 484 + i
    for i, name in enumerate(
        [
            "rt__as_expired",
            "rt__best",
            "rt__bit",
            "rt__cap",
            "rt__changed",
            "rt__cond",
            "rt__ended",
            "rt__held",
            "rt__i",
            "rt__in0",
            "rt__in1",
            "rt__in2",
            "rt__interrupt",
            "rt__j",
            "rt__last",
            "rt__margin",
            "rt__min_hold",
            "rt__n_adapt",
            "rt__n_rules",
            "rt__n_sits",
            "rt__n_words",
            "rt__new",
            "rt__p_cap",
            "rt__p_done",
            "rt__p_held",
            "rt__p_in0",
            "rt__p_in1",
            "rt__p_in2",
            "rt__p_pver",
            "rt__p_rule",
            "rt__p_t",
            "rt__pb",
            "rt__pe",
            "rt__pver",
            "rt__rc_b",
            "rt__rc_old",
            "rt__rc_p",
            "rt__rc_r",
            "rt__rule",
            "rt__s",
            "rt__since",
            "rt__snap",
            "rt__started",
            "rt__status",
            "rt__switch",
            "rt__valid",
            "rt__w",
            "rt__word",
            "telemetryoff",
        ]
    )
}


def pack(source: str, bindings: dict | None = None) -> tuple[str, dict]:
    """Map runtime storage into pvId(64..660), retaining original indices 0..63.

    Bindings explicitly identify renamed baseline storage; all other new scalars
    use the 128-cell reserve. Original input units remain in the immutable build.
    """
    from strategy_basic import TOKEN_RE, _tokens, scan_basic

    baseline = BASELINE.read_bytes()
    if hashlib.sha256(baseline).hexdigest() != BASELINE_SHA256:
        raise ValueError("v13-live baseline hash mismatch")
    original = scan_basic(baseline.decode(), backend="gota")
    scan = scan_basic(source, backend="gota")
    if bindings is not None and not isinstance(bindings, dict):
        raise TypeError("baseline bindings must be an object")
    aliases = {}
    for name, target in (bindings or {}).items():
        if not isinstance(name, str) or not isinstance(target, str):
            raise TypeError("baseline bindings must map names to names")
        name, target = name.lower(), target.lower()
        if name in aliases or target in aliases.values():
            raise ValueError("duplicate baseline binding")
        if name in FIXED or name in ARRAYS or name.startswith(("rt__", "st__")):
            raise ValueError("binding overlaps runtime storage")
        if name in scan.arrays:
            if target not in original.arrays or scan.arrays[name] != original.arrays[target]:
                raise ValueError(f"array binding kind/bound mismatch: {name} -> {target}")
        elif name in scan.globals:
            if target not in original.globals:
                raise ValueError(f"scalar binding kind mismatch: {name} -> {target}")
        else:
            raise ValueError(f"unknown bound symbol: {name}")
        if name != target and (target in scan.globals or target in scan.arrays):
            raise ValueError(f"baseline binding collides with existing storage: {target}")
        aliases[name] = target
    for name, cells in scan.arrays.items():
        if name in ARRAYS:
            # The vendored runtime reserves 32 adaptation cells; only codes 1..4
            # are admitted by GotA lint, so its generated allocation is five.
            expected = 32 if name.startswith("rt__a_") else ARRAYS[name][1]
            if cells != expected:
                raise ValueError(f"runtime array bound mismatch: {name}")
        elif aliases.get(name, name) not in original.arrays:
            raise ValueError(f"unsupported extra array: {name}; baseline binding required")
        elif cells != original.arrays[aliases.get(name, name)]:
            raise ValueError(f"baseline array bound mismatch: {name}")
    extras = sorted(scan.globals - set(FIXED) - original.globals - aliases.keys())
    if len(extras) > 128:
        raise ValueError(f"metadata/scratch reserve exceeded: {len(extras)} > 128")
    reserve = {name: 533 + i for i, name in enumerate(extras)}
    scalars = FIXED | reserve
    output = []
    parameters = set()
    has_pvid = False
    for line in source.splitlines():
        tokens = _tokens(line)
        if not tokens:
            # Strip comments and indentation to fit the source budget, as the
            # inspected partial port does; string literals remain exact.
            continue
        if ("op", ":") in tokens and any(
            word in {"dim", "sub"} for kind, word in tokens if kind == "id"
        ):
            raise ValueError("DIM/SUB compound statements are unsupported by packing")
        if tokens[0] == ("id", "dim") and tokens.count(("op", "(")) != 1:
            raise ValueError("multiple DIM declarations must use separate lines")
        if tokens[0] == ("id", "sub"):
            parameters = {text for kind, text in tokens[2:] if kind == "id"}
            collisions = parameters & ({"pvid"} | set(aliases.values()))
            if collisions:
                raise ValueError(
                    f"SUB {tokens[1][1]} parameter conflicts with physical storage "
                    f"{', '.join(sorted(collisions))}; rename the parameter before packing"
                )
        if tokens[:2] == [("id", "end"), ("id", "sub")]:
            parameters = set()
        if tokens[0] == ("id", "dim"):
            name = tokens[1][1]
            if name in ARRAYS:
                continue
            if aliases.get(name, name) == "pvid":
                output.append("DIM pvId(660)")
                has_pvid = True
                continue
        edits = []
        matches = list(TOKEN_RE.finditer(line))
        for i, match in enumerate(matches):
            kind = match.lastgroup
            token = match.group(kind)
            if kind == "bad" and token == "'" or kind == "id" and token.lower() == "rem":
                break
            if kind != "id":
                continue
            name = token.lower()
            if name in parameters:
                continue
            nxt = matches[i + 1].group(matches[i + 1].lastgroup) if i + 1 < len(matches) else ""
            replacement = None
            end = match.end(kind)
            if (name in ARRAYS or aliases.get(name, name) == "pvid") and nxt == "(":
                # Insert literal offset inside existing parentheses; nesting and
                # expression precedence are preserved by parenthesizing the index.
                opening = matches[i + 1]
                depth = 1
                closing = None
                for following in matches[i + 2 :]:
                    value = following.group(following.lastgroup)
                    depth += value == "("
                    depth -= value == ")"
                    if depth == 0:
                        closing = following
                        break
                if closing is None:
                    raise ValueError(f"unclosed array access: {name}")
                index = line[opening.end() : closing.start(closing.lastgroup)].strip()
                capacity = ARRAYS[name][1] if name in ARRAYS else 64
                if (
                    re.fullmatch(r"[+-]?\s*\d+", index)
                    and not 0 <= int(index.replace(" ", "")) < capacity
                ):
                    raise ValueError(f"array access exceeds capacity: {name}({index})")
                if name in ARRAYS:
                    replacement = f"pvId({ARRAYS[name][0]}+("
                    end = opening.end()
                    edits.append(
                        (closing.start(closing.lastgroup), closing.start(closing.lastgroup), ")")
                    )
                elif name in aliases:
                    replacement = "pvId"
            elif name in aliases and (nxt != "(" or name in scan.arrays):
                replacement = "pvId" if aliases[name] == "pvid" else aliases[name]
            elif name in scalars and nxt != "(":
                replacement = f"pvId({scalars[name]})"
            if replacement is not None:
                edits.append((match.start(kind), end, replacement))
        for start, end, value in sorted(edits, reverse=True):
            line = line[:start] + value + line[end:]
        output.append(line.strip())
    if not has_pvid:
        output.insert(0, "DIM pvId(660)")
    return "\n".join(output) + "\n", {
        "array": "pvId",
        "cells": 661,
        "baseline_region": [0, 63],
        "arrays": {name: list(value) for name, value in ARRAYS.items()},
        "fixed": FIXED,
        "reserve": reserve,
        "reserve_region": [533, 660],
        "bindings": aliases,
        "baseline_sha256": BASELINE_SHA256,
    }
