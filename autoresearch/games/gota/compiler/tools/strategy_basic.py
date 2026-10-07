#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_basic.py
"""Deterministic half of the strategy compiler: check LLM-written units, generate the tables, and
assemble policy.bas.

The compilation design (docs/designs/2026-09-30-strategy-compilation.md) splits the build into one
LLM step (unit files for K, S, C, A and COM components) and Python steps. This module is the
Python part the driver calls between them:

    check_unit(strategy, "K.position", text)   -> diagnostics (the repair-loop feedback)
    unit_contract(strategy, "K.position")      -> what the work order tells the agent
    assemble(strategy, units, runtime_dir, build_id, source_commit)
                                              -> {"policy", "units", "map", "budget"}
    telemetry_worst_case(strategy)            -> static per-tick print use (lint gate)
    reference_select(...), reference_priority(...)
                                              -> pure-Python mirrors of the runtime's rule
                                                 selection and adaptation arithmetic

The unit ABI, the name spaces and the telemetry v2 line formats are documented in the
compilation design §4.2, the source-format design §6, and
docs/strategy-compiler-maintainers.md. Stdlib only.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import strategy_comms as sc
import strategy_format as sf

# ---------------------------------------------------------------- engine facts (coworld-v0.3.123, Bassy)

KEYWORDS = {"and", "call", "dim", "else", "end", "exit", "false", "gosub", "goto", "if", "let", "mod",
            "not", "or", "print", "rem", "return", "stop", "sub", "then", "true", "wend", "while", "xor"}
# seat_view.nim:46-48 DataNames at 118e1619.
HOST_DATA = {name.lower() for name in (
    "selfId", "selfTeam", "selfX", "selfY", "selfHp", "carrying", "homeX", "homeY", "heartX", "heartY",
    "worldTick", "ownHeartX", "ownHeartY", "ownHeartStolen", "hasGrenade", "hasSpray", "armorHp",
    "livesLeft", "grenadeCharge", "trenchId", "worldSeats")}
LIMITS = {"globals": 512, "array_cells": 4096, "arrays": 256, "source_bytes": 128 * 1024}
PRINT_LIMITS = {"bytes": 512, "events": 64}  # half of the engine's 1,024 bytes / 128 events
INT_BYTES = 11  # "-2147483648"
RUNTIME_EXPORTS = {"rt__rule", "rt__cap", "rt__since", "rt__pver"}
KILL_SWITCH = "telemetryoff"
RUNTIME_FILES = {"runtime.lib": "lib.bas", "runtime.main": "main.bas"}
COMMS_RUNTIME = ("runtime.comms", "comms.bas")  # added only to builds whose COM components use comms-v1
# A COM component opts into the comms-v1 codec with an Encoding that starts `comms-v1 N`, N = the wire
# type 0..8 (strategy/comms.md §4). Wire types are 0-based; telemetry message codes stay 1-based.
CODEC_ENCODING = re.compile(r"comms-v1\s+(\d+)(?![\w.])")
CODEC_WIRE_TYPES = range(9)
# Comms batch line (one per tick, printed by the comms runtime): `PWC v=3 t=<tick> b=<records>`, each
# record two fixed-width 10-digit values (block A: 1e9 + pA for a receive, 2e9 + pA for the send;
# block B: 1e9 + pB), no separators, at most BATCH_RECEIVES receives then at most one send.
BATCH_HEADER, BATCH_SEPARATOR = "PWC v=3 t=", " b="
BATCH_RECEIVES, BATCH_SENDS, BATCH_VALUE_BYTES = sc.MAX_DECODE, 1, 10
# runtime.comms interface: unit_contract below and strategy/compiler/AGENT.md. Codec COM units may read the
# decoded packet fields and the per-seat heard/suspect memories, and call cm__send; they never write
# runtime names. Each codec COM declares `packet[2]` and logs it; generated code fills it.
CODEC_READS = {"cm__type", "cm__speaker", "cm__fa", "cm__fb", "cm__cell", "cm__pa", "cm__pb", "cm__rx_index",
               "cm__rx_slot", "cm__rx_x", "cm__rx_y", "cm__sent"}
CODEC_ARRAYS = {"cm__heard_t", "cm__heard_x", "cm__heard_y", "cm__suspect_t", "cm__suspect_x", "cm__suspect_y",
                "cm__rx_valid"}
KNOWLEDGE_CODEC_ARRAYS = {"cm__heard_t", "cm__heard_x", "cm__heard_y"}
SKILL_CODEC_ARRAYS = {"cm__rx_valid"}  # the motor's non-team speech cue
CODEC_CALLS = {"cm__send"}
CODEC_PACKET = "packet"
CODEC_DEFAULTS = {"cm__max_decode": sc.MAX_DECODE, "cm__min_gap": 6}  # comms.md §9 defaults
ABI = {  # kind -> required zero-argument SUBs (local names)
    "K": ("update",), "S": ("eval",), "C": ("start", "tick"), "A": ("eval",), "COM": ("recv", "send"),
    "SK": (),
}
MUST_ASSIGN = {"S": ("on",), "C": ("status",), "A": ("fire",)}
COM_MUST_ASSIGN = {"recv": ("got", "from"), "send": ("sent",)}
PHASE_SUBS = {"init", "update", "eval", "start", "tick", "recv", "send"}
KIND_ORDER = ("SK", "K", "S", "C", "A", "COM")
LOG_OFFSET_LCM_CAP = 5760


class BuildError(ValueError):
    """Assembly cannot proceed. `diagnostics` name the component to repair (the unit's ID)."""

    def __init__(self, diagnostics: list[sf.Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(f"{d.component or '-'}: {d.message}" for d in diagnostics[:5])
                         + (f" (+{len(diagnostics) - 5} more)" if len(diagnostics) > 5 else ""))


# ---------------------------------------------------------------- BASIC scanner

TOKEN_RE = re.compile(r'\s*(?:(?P<str>"[^"\n]*")|(?P<num>\d+)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)'
                      r"|(?P<op><>|<=|>=|[-+*/\\=<>(),;:])|(?P<bad>\S))")


@dataclass
class Scan:
    """What one BASIC text defines and references. Names are lower case."""
    host_data: set[str] = field(default_factory=lambda: HOST_DATA)
    keywords: set[str] = field(default_factory=lambda: KEYWORDS)
    subs: dict[str, int] = field(default_factory=dict)             # name -> arity
    sub_lines: dict[str, int] = field(default_factory=dict)
    arrays: dict[str, int] = field(default_factory=dict)           # name -> cells
    scalar_writes: list[tuple[str, int, str | None]] = field(default_factory=list)   # name, line, sub
    scalar_reads: list[tuple[str, int, str | None]] = field(default_factory=list)
    array_writes: list[tuple[str, int]] = field(default_factory=list)
    paren_refs: list[tuple[str, int]] = field(default_factory=list)  # name( in an expression
    calls: list[tuple[str, int]] = field(default_factory=list)       # statement-level calls
    globals: set[str] = field(default_factory=set)
    problems: list[tuple[int, str]] = field(default_factory=list)
    prints: list[int] = field(default_factory=list)


def _tokens(line: str) -> list[tuple[str, str]]:
    """Tokens of one source line without its comment. Kinds: str num id op."""
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(line):
        if line[pos:].lstrip().startswith("'"):
            break
        m = TOKEN_RE.match(line, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "id" and text.lower() == "rem":
            break
        out.append((kind, text if kind == "str" else text.lower()))
    return out


def scan_basic(text: str, *, backend: str = "paintbot") -> Scan:
    """Classify every name use in a BASIC text (dialect: docs/policy-surface.md §2)."""
    if backend == "gota":
        from gota_host import HOST_DATA as host_data
        from gota_host import KEYWORDS as keywords
        scan = Scan(host_data=host_data, keywords=keywords)
    else:
        scan = Scan()
    current_sub: str | None = None
    params: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        tokens = _tokens(raw)
        if ("op", "/") in tokens:
            scan.problems.append((number, "use integer division \\; fixed-point / violates the strategy integer contract"))
        statements: list[list[tuple[str, str]]] = [[]]
        depth = 0
        for tok in tokens:
            if tok == ("op", ":") and depth == 0:
                statements.append([])
                continue
            depth += tok == ("op", "(")
            depth -= tok == ("op", ")")
            statements[-1].append(tok)
        for stmt in statements:
            if not stmt:
                continue
            current_sub, params = _statement(scan, stmt, number, current_sub, params)
    if current_sub is not None:
        scan.problems.append((len(text.splitlines()), f"SUB {current_sub} has no END SUB"))
    return scan


def _expression(scan: Scan, toks: list[tuple[str, str]], number: int, sub: str | None, params: set[str]) -> None:
    for index, (kind, text) in enumerate(toks):
        if kind != "id" or text in scan.keywords:
            continue
        nxt = toks[index + 1] if index + 1 < len(toks) else None
        if nxt == ("op", "("):
            scan.paren_refs.append((text, number))
        elif text not in params:
            scan.scalar_reads.append((text, number, sub))
            if text not in scan.host_data:
                scan.globals.add(text)


def _statement(scan: Scan, stmt: list[tuple[str, str]], number: int, sub: str | None,
               params: set[str]) -> tuple[str | None, set[str]]:
    head = stmt[0][1] if stmt[0][0] == "id" else ""
    words = [t for _, t in stmt]
    if head == "sub":
        if sub is not None:
            scan.problems.append((number, "SUB inside a SUB (SUBs are top level only)"))
        if len(stmt) < 2 or stmt[1][0] != "id":
            scan.problems.append((number, "SUB without a name"))
            return sub, params
        name = stmt[1][1]
        names = [t for k, t in stmt[2:] if k == "id"]
        scan.subs[name] = len(names)
        scan.sub_lines[name] = number
        return name, set(names)
    if words[:2] == ["end", "sub"]:
        if sub is None:
            scan.problems.append((number, "END SUB without SUB"))
        return None, set()
    if head == "dim":
        if sub is not None:
            scan.problems.append((number, "DIM inside a SUB (arrays are top level only)"))
        for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*\(\s*(\d+)\s*\)", " ".join(words[1:])):
            scan.arrays[m.group(1)] = int(m.group(2)) + 1
        return sub, params
    if sub is None:
        scan.problems.append((number, f"top-level statement `{' '.join(words)[:40]}`: units hold only DIM and SUB"))
    if head in ("if", "while"):
        _expression(scan, stmt[1:], number, sub, params)
        return sub, params
    if head in ("else", "wend", "end", "exit", "return", "stop"):
        return sub, params
    if head == "print":
        scan.prints.append(number)
        _expression(scan, stmt[1:], number, sub, params)
        return sub, params
    if head == "call":
        stmt = stmt[1:]
        head = stmt[0][1] if stmt and stmt[0][0] == "id" else ""
        if head:
            scan.calls.append((head, number))
            _expression(scan, stmt[1:], number, sub, params)
        return sub, params
    if head == "let":
        stmt = stmt[1:]
        head = stmt[0][1] if stmt and stmt[0][0] == "id" else ""
    if not head:
        scan.problems.append((number, f"cannot parse `{' '.join(words)[:40]}`"))
        return sub, params
    if len(stmt) >= 2 and stmt[1] == ("op", "="):
        if head not in params:
            scan.scalar_writes.append((head, number, sub))
            scan.globals.add(head)
        _expression(scan, stmt[2:], number, sub, params)
        return sub, params
    if len(stmt) >= 2 and stmt[1] == ("op", "("):
        depth, close = 0, None
        for index, tok in enumerate(stmt[1:], start=1):
            depth += tok == ("op", "(")
            depth -= tok == ("op", ")")
            if depth == 0:
                close = index
                break
        if close is not None and close + 1 < len(stmt) and stmt[close + 1] == ("op", "="):
            scan.array_writes.append((head, number))
            _expression(scan, stmt[2:close] + stmt[close + 2:], number, sub, params)
        else:
            scan.calls.append((head, number))
            _expression(scan, stmt[2:], number, sub, params)
        return sub, params
    if len(stmt) == 1:
        scan.calls.append((head, number))
        return sub, params
    scan.problems.append((number, f"cannot parse `{' '.join(words)[:40]}`"))
    return sub, params


# ---------------------------------------------------------------- unit contract and checks

def unit_header(comp: sf.Component) -> str:
    return f"' unit {comp.id} {comp.text_hash} generated, do not edit"


def _generated_names(comp: sf.Component) -> set[str]:
    """Names generated code assigns in this component's namespace (units must not write them)."""
    p = comp.prefix
    names = {f"{p}__{prm.name}" for prm in comp.params}
    names |= {f"{p}__in_{i}" for i in comp.inputs}
    names |= {f"{p}__k_{c.name}" for c in comp.conditions}
    return names


def _dependency_names(strategy: sf.Strategy, comp: sf.Component) -> tuple[set[str], set[str], dict[str, int]]:
    """(readable scalars, readable arrays, callable subs) that `comp` gets from its Uses."""
    scalars: set[str] = set()
    arrays: set[str] = set()
    subs: dict[str, int] = {}
    for dep_id in comp.uses:
        dep = strategy.components.get(dep_id)
        if dep is None or dep.kind in ("P", "ST", "R"):
            continue
        for out in dep.outputs:
            (scalars if out.cells == 1 else arrays).add(f"{dep.prefix}__{out.name}")
        scalars |= {f"{dep.prefix}__{prm.name}" for prm in dep.params}
        if dep.kind == "SK":
            subs.update(dep.interface.get("subs", {}))
    return scalars, arrays, subs


def unit_contract(strategy: sf.Strategy, component_id: str) -> dict:
    """Everything the compiler agent needs about one unit's ABI (compilation design §4.2)."""
    comp = strategy.components[component_id]
    p = comp.prefix
    scalars, arrays, subs = _dependency_names(strategy, comp)
    constants = {f"{p}__{prm.name}": f"param {prm.name} = {prm.value} {prm.unit}".rstrip() for prm in comp.params}
    constants |= {f"{p}__in_{i}": f"input {i}, bound by generated code before start/tick" for i in comp.inputs}
    constants |= {f"{p}__k_{c.name}": f"{c.kind} condition code {c.code}" for c in comp.conditions}
    must = {"S": f"{p}__eval must assign {p}__on (0/1) and every declared output on every call",
            "C": f"{p}__tick must set {p}__status (0 running, 1 done, 2 abort) and, when not 0, {p}__cond "
                 f"to one of the {p}__k_* constants of that kind",
            "A": f"{p}__eval must assign {p}__fire (0/1) on every call",
            "COM": f"{p}__recv sets {p}__got = 1 and {p}__from = speaker seat when it decoded a message this "
                   f"tick; {p}__send sets {p}__sent = 1 when it shouted (both start at 0 each tick). Define only "
                   f"the SUBs of the declared Directions",
            "K": f"{p}__update refreshes the declared outputs every tick"}
    plan, _ = comms_plan(strategy)
    if plan is not None and comp.id in plan.values():
        wire = next(w for w, c in plan.items() if c == comp.id)
        must["COM"] = (f"comms-v1 wire type {wire}. {p}__recv runs once per decoded message of this type, after "
                       f"generated code sets {p}__packet(0..1), {p}__got (messages this tick) and {p}__from (true "
                       f"sender); read cm__type, cm__speaker, cm__fa, cm__fb, cm__cell, cm__rx_slot, cm__rx_x, "
                       f"cm__rx_y and the cm__heard_*/cm__suspect_* arrays. {p}__send proposes at most one message "
                       f"with cm__send(type, fieldsA, fieldsB, cell, quiet); generated code calls the send SUBs in "
                       f"priority order only while nothing was sent this tick and sets {p}__sent from cm__sent. "
                       f"Never write cm__* names or {p}__packet. Define only the SUBs of the declared Directions")
        scalars, arrays, subs = scalars | CODEC_READS, arrays | CODEC_ARRAYS, {**subs, "cm__send": 5}
    if plan is not None and comp.kind == "K":
        arrays |= KNOWLEDGE_CODEC_ARRAYS
    if plan is not None and comp.kind == "SK":
        arrays |= SKILL_CODEC_ARRAYS
    return {
        "id": comp.id, "kind": comp.kind, "prefix": p, "header": unit_header(comp),
        "required_subs": [f"{p}__{name}()" for name in required_subs(comp)],
        "optional_subs": [f"{p}__init()"],
        "outputs": {f"{p}__{o.name}": o.cells for o in comp.outputs},
        "output_arrays_dimmed_by_generator": [f"{p}__{o.name}" for o in comp.outputs if o.cells > 1],
        "constants": constants,
        "may_write": f"{p}__* except the constants above",
        "may_read": sorted(scalars | arrays | RUNTIME_EXPORTS) + ["host data (selfId, worldTick, ...)"],
        "may_call": sorted(subs) + ["host functions"],
        "must": must.get(comp.kind, ""),
        "forbidden": ["PRINT", "top-level statements", "DIM or SUB inside a SUB", "names without the prefix",
                      "writing another unit's names", "calling another unit's ABI SUBs"],
    }


def check_unit(strategy: sf.Strategy, component_id: str, text: str) -> list[sf.Diagnostic]:
    """Structure, name space and ABI checks for one unit (LLM-written or skill.bas)."""
    comp = strategy.components[component_id]
    diags: list[sf.Diagnostic] = []

    def err(code: str, message: str, line: int | None = None) -> None:
        diags.append(sf.Diagnostic("error", code, message + (f" (unit line {line})" if line else ""), comp.id, None))

    own = comp.prefix + "__"
    scan = scan_basic(text, backend=strategy.backend)
    for line, problem in scan.problems:
        err("unit-structure", problem, line)
    for line in scan.prints:
        err("unit-print", "units must not PRINT (telemetry is generated)", line)
    dep_scalars, dep_arrays, dep_subs = _dependency_names(strategy, comp)
    plan, _ = comms_plan(strategy)
    codec = plan is not None and comp.id in plan.values()
    if codec:
        dep_scalars, dep_arrays, dep_subs = dep_scalars | CODEC_READS, dep_arrays | CODEC_ARRAYS, dep_subs
    elif plan is not None and comp.kind == "SK":
        dep_arrays = dep_arrays | SKILL_CODEC_ARRAYS
    elif plan is not None and comp.kind == "K":
        dep_arrays = dep_arrays | KNOWLEDGE_CODEC_ARRAYS
    generated = _generated_names(comp)
    if codec:
        generated |= {own + name for name in ("got", "from", "sent")}
    output_arrays = {f"{own}{o.name}" for o in comp.outputs if o.cells > 1}
    own_arrays = set(scan.arrays) | output_arrays
    for name in scan.subs:
        if not name.startswith(own):
            err("unit-namespace", f"SUB {name} must be named {own}<name>", scan.sub_lines[name])
    for name in scan.arrays:
        if not name.startswith(own):
            err("unit-namespace", f"array {name} must be named {own}<name>")
        if name in output_arrays:
            err("unit-namespace", f"array {name} is a declared output; generated code DIMs it")
    for name, line, _ in scan.scalar_writes:
        if not name.startswith(own):
            err("unit-namespace", f"writes {name}; a unit writes only {own}* names", line)
        elif name in generated:
            err("unit-namespace", f"writes {name}, a constant set by generated code", line)
    for name, line in scan.array_writes:
        if name not in own_arrays:
            err("unit-namespace", f"writes array {name}; a unit writes only its own arrays", line)
        elif codec and name == own + CODEC_PACKET:
            err("unit-namespace", f"writes {name}; generated code fills the packet", line)
    for name, line, _ in scan.scalar_reads:
        if name.startswith(own) or name in scan.host_data or name in dep_scalars or name in RUNTIME_EXPORTS:
            continue
        if "__" not in name:
            err("unit-bare-name", f"reads {name}, which is neither host data nor a prefixed name "
                "(it would silently be a new global)", line)
        else:
            err("unit-namespace", f"reads {name}, which is not its own, a declared output/param of a component "
                "in Uses, or a runtime export", line)
    for name, line in scan.paren_refs:
        if "__" not in name:
            continue  # host function (unknown ones fail the engine compile, gate G2)
        if name in own_arrays or name in dep_arrays:
            continue
        if name in scan.subs or name in dep_subs:
            err("unit-call", f"{name}(...) used in an expression; SUBs return no value", line)
        else:
            err("unit-namespace", f"{name}(...) is not its own array or a declared output array in Uses", line)
    for name, line in scan.calls:
        if "__" not in name:
            continue
        local = name[len(own):] if name.startswith(own) else None
        if name in scan.subs:
            continue
        if name in dep_subs and name.rsplit("__", 1)[1] not in PHASE_SUBS:
            continue
        if codec and name in CODEC_CALLS:
            continue
        if local is not None:
            err("unit-call", f"calls {name}, which this unit does not define", line)
        else:
            err("unit-call", f"calls {name}; a unit calls only its own SUBs, SUBs of skills in Uses, and host "
                "functions", line)
    for local in required_subs(comp):
        name = own + local
        if name not in scan.subs:
            err("unit-abi", f"missing required SUB {name}()")
        elif scan.subs[name] != 0:
            err("unit-abi", f"SUB {name} must take no arguments")
    if own + "init" in scan.subs and scan.subs[own + "init"] != 0:
        err("unit-abi", f"SUB {own}init must take no arguments")
    written = {name for name, _, _ in scan.scalar_writes}
    must = list(MUST_ASSIGN.get(comp.kind, ()))
    for direction in comp.directions if not codec else ():  # codec: generated code sets got and sent
        must += COM_MUST_ASSIGN[direction]
    for local in must:
        if own + local not in written:
            err("unit-abi", f"never assigns {own}{local}")
    if comp.kind == "COM":
        for direction in ("recv", "send"):
            if direction not in comp.directions and own + direction in scan.subs:
                err("unit-abi", f"defines {own}{direction} but Directions does not include it")
    return diags


def required_subs(comp: sf.Component) -> tuple[str, ...]:
    return comp.directions if comp.kind == "COM" else ABI[comp.kind]


def normalize_unit(comp: sf.Component, text: str) -> str:
    """Canonical header first; an agent-written `' unit` first line is replaced."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].startswith("' unit "):
        lines = lines[1:]
    body = "\n".join(lines).strip("\n")
    return unit_header(comp) + "\n" + body + "\n"


# ---------------------------------------------------------------- comms-v1 opt-in

def comms_plan(strategy: sf.Strategy) -> tuple[dict | None, list[sf.Diagnostic]]:
    """({wire type: COM id} or None for a legacy build, diagnostics). A build is either all
    legacy COM components or all comms-v1 ones; wire types are unique and in 0..8."""
    coms = strategy.of_kind("COM")
    wires, legacy, diags = {}, [], []
    for comp in coms:
        match = CODEC_ENCODING.match(comp.fields.get("Encoding", "").strip())
        if not match:
            legacy.append(comp.id)
            continue
        wire = int(match.group(1))
        if wire not in CODEC_WIRE_TYPES:
            diags.append(sf.Diagnostic("error", "comms-wire-type", f"comms-v1 wire type {wire} is not in 0..8",
                                       comp.id, comp.line))
        elif wire in wires:
            diags.append(sf.Diagnostic("error", "comms-duplicate",
                                       f"comms-v1 wire type {wire} is also used by {wires[wire]}", comp.id, comp.line))
        else:
            wires[wire] = comp.id
        packet = comp.output(CODEC_PACKET)
        if packet is None or packet.cells != 2 or comp.log is None or tuple(comp.log.fields) != (CODEC_PACKET,):
            diags.append(sf.Diagnostic("error", "comms-packet", f"a comms-v1 component declares the output "
                                       f"`{CODEC_PACKET}[2]` and logs exactly `{CODEC_PACKET}`", comp.id, comp.line))
    if wires and legacy:
        diags += [sf.Diagnostic("error", "comms-mixed", "a build cannot mix comms-v1 and legacy COM components; "
                                "give this component a `comms-v1 N` Encoding or remove it", c, strategy.components[c].line)
                  for c in legacy]
    if not wires and not any(d.code != "comms-mixed" for d in diags):
        return None, diags
    return {w: wires[w] for w in sorted(wires)}, diags


def comms_map(plan: dict) -> dict:
    return {"version": 1, "keys": list(sc.DEFAULT_KEYS),
            "messages": {str(wire): comp_id for wire, comp_id in plan.items()},
            "batch": {"kind": "PWC", "version": 3, "header": BATCH_HEADER, "separator": BATCH_SEPARATOR,
                      "receives": BATCH_RECEIVES, "sends": BATCH_SENDS, "value_digits": BATCH_VALUE_BYTES,
                      "send_marker": 2, "receive_marker": 1}}


# ---------------------------------------------------------------- telemetry budget
#
# A print line is a list of items: ("lit", text) costs one event and len(text) bytes; ("val", expr,
# bytes) costs one event and at most `bytes` bytes; the newline costs one event and one byte.
# Value bounds are tight only where the runtime or generated code controls the value (rule,
# capability, held, event and condition codes, flag words, priorities clamped to 0..1000).
# Unit-written values and the tick keep INT_BYTES.

def _digits(high: int, low: int = 0) -> int:
    return max(len(str(high)), len(str(low)))


def line_cost(items: list[tuple]) -> tuple[int, int]:
    """(events, bytes) of one PRINT line including its newline."""
    nbytes = sum(len(item[1]) if item[0] == "lit" else item[2] for item in items)
    return len(items) + 1, nbytes + 1


def _merge(items: list[tuple]) -> list[tuple]:
    """Join adjacent literals: one PRINT item, the same printed text."""
    out = []
    for item in items:
        if item[0] == "lit" and out and out[-1][0] == "lit":
            out[-1] = ("lit", out[-1][1] + item[1])
        else:
            out.append(item)
    return out


def _print_statement(items: list[tuple]) -> str:
    return "PRINT " + "; ".join(f'"{item[1]}"' if item[0] == "lit" else item[1] for item in items)


def pwd_items(strategy: sf.Strategy, goal_refs: tuple[str, ...] = ()) -> list[tuple]:
    """The PWD print items. Inputs fold to the literal 0,0,0 when no rule capability has inputs
    (st__bind then zeroes rt__in0..2 every tick) and the priority version folds to 0 when there
    are no Adaptations (only rt__recompute changes it). Goal-aware builds merge literal IDs
    into the prefix, adding bytes but no PRINT items or stored state."""
    caps = len(strategy.of_kind("C"))
    folded_inputs = not any(strategy.components[r.capability].inputs for r in strategy.rules)
    prefix = "PWD v=2 t="
    if strategy.decision_version() == 3:
        prefix = f"PWD v=3 g={','.join(goal_refs) or '-'} t="
    items = [("lit", prefix), ("val", "worldTick", INT_BYTES),
             ("lit", " r="), ("val", "rt__rule", _digits(len(strategy.rules))),
             ("lit", " c="), ("val", "rt__cap", _digits(caps))]
    if folded_inputs:
        items.append(("lit", " i=0,0,0"))
    else:
        items += [("lit", " i="), ("val", "rt__in0", INT_BYTES), ("lit", ","), ("val", "rt__in1", INT_BYTES),
                  ("lit", ","), ("val", "rt__in2", INT_BYTES)]
    items += [("lit", " h="), ("val", "rt__held", 1)]
    if strategy.of_kind("A"):
        items += [("lit", " p="), ("val", "rt__pver", INT_BYTES)]
    else:
        items.append(("lit", " p=0"))
    items.append(("lit", " f="))
    sits = len(strategy.of_kind("S"))
    for word in range(flag_words(strategy)):
        bits = min(31, max(0, sits - 31 * word))
        if word:
            items.append(("lit", ","))
        items.append(("val", f"rt__fw({word})", _digits(2 ** bits - 1)))
    return _merge(items)


def pwe_costs(strategy: sf.Strategy) -> tuple[tuple[int, int], tuple[int, int]]:
    """(event line without a condition code, done/abort line) as runtime.lib prints them; k is a
    condition code or -1 for an invalid status."""
    cap = ("val", "c", _digits(len(strategy.of_kind("C"))))
    conditions = max((len(c.conditions) for c in strategy.of_kind("C")), default=0)
    plain = line_cost([("lit", "PWE v=2 t="), ("val", "t", INT_BYTES), ("lit", " c="), cap, ("lit", " e=1 k=0")])
    end = line_cost([("lit", "PWE v=2 t="), ("val", "t", INT_BYTES), ("lit", " c="), cap, ("lit", " e=2 k="),
                     ("val", "k", _digits(conditions, -1))])
    return plain, end


def pwp_cost(strategy: sf.Strategy) -> tuple[int, int]:
    """One Adaptation PWP line (rt__recompute): old and new priority are clamped to 0..1000."""
    return line_cost([("lit", "PWP v=2 t="), ("val", "t", INT_BYTES),
                      ("lit", " a="), ("val", "a", _digits(len(strategy.of_kind("A")))),
                      ("lit", " r="), ("val", "r", _digits(len(strategy.rules))),
                      ("lit", " o="), ("val", "o", 4), ("lit", " n="), ("val", "n", 4),
                      ("lit", " p="), ("val", "p", INT_BYTES)])


def batch_cost() -> tuple[int, int]:
    """The comms batch line at capacity: BATCH_RECEIVES receives plus one send, two values each."""
    values = 2 * (BATCH_RECEIVES + BATCH_SENDS)
    return line_cost([("lit", BATCH_HEADER), ("val", "t", INT_BYTES), ("lit", BATCH_SEPARATOR)]
                     + [("val", "block", BATCH_VALUE_BYTES)] * values)


def _pwb_cost(values: int, code: int) -> tuple[int, int]:
    literal = len(f" k={code} d=")
    return 3 + 2 * values, 10 + INT_BYTES + literal + INT_BYTES * values + (values - 1) + 1


def _pwc_cost(values: int, code: int, direction: int) -> tuple[int, int]:
    head = 10 + INT_BYTES + len(f" m={code} s={direction} w=") + INT_BYTES
    if values == 0:
        return 6, head + len(" d=0") + 1
    return 5 + 2 * values, head + len(" d=") + INT_BYTES * values + (values - 1) + 1


def log_values(strategy: sf.Strategy, comp: sf.Component) -> int:
    if comp.log is None:
        return 0
    return sum(comp.output(name).cells for name in comp.log.fields if comp.output(name))


def log_offsets(strategy: sf.Strategy) -> tuple[dict[str, int], tuple[int, int]]:
    """Choose each K's PWB phase to minimize the worst coincident cost; return offsets and that
    worst (events, bytes). Exact over the LCM of the periods when it is small, else the sum."""
    logged = [(c, c.log.every, _pwb_cost(log_values(strategy, c), c.code)) for c in strategy.of_kind("K") if c.log]
    if not logged:
        return {}, (0, 0)
    period = 1
    for _, every, _ in logged:
        period = math.lcm(period, every)
    if period > LOG_OFFSET_LCM_CAP:
        return ({c.id: 0 for c, _, _ in logged},
                (sum(cost[0] for *_, cost in logged), sum(cost[1] for *_, cost in logged)))
    events = [0] * period
    nbytes = [0] * period
    offsets = {}
    for comp, every, (ev, by) in logged:
        best = None
        for offset in range(every):
            worst = max((events[t] + ev, nbytes[t] + by) if t % every == offset else (events[t], nbytes[t])
                        for t in range(period))
            if best is None or worst < best[0]:
                best = (worst, offset)
        offsets[comp.id] = best[1]
        for t in range(best[1], period, every):
            events[t] += ev
            nbytes[t] += by
    worst_t = max(range(period), key=lambda t: (events[t], nbytes[t]))
    return offsets, (events[worst_t], nbytes[worst_t])


def telemetry_worst_case(strategy: sf.Strategy) -> dict:
    """Static worst-case telemetry per tick (events, bytes), against half the engine's limits.
    The initial PWP snapshot is excluded: the runtime prints it only into leftover room."""
    costs = [line_cost(pwd_items(strategy, refs)) for refs in [(), *(r.goal_refs for r in strategy.rules)]]
    pwd = (max(cost[0] for cost in costs), max(cost[1] for cost in costs))
    plain, end = pwe_costs(strategy)  # worst tick: died or preempted, start, then done/abort
    adapt = len(strategy.of_kind("A"))
    pwp = pwp_cost(strategy)
    lines = {
        "PWD": {"count": 1, "events": pwd[0], "bytes": pwd[1]},
        "PWE": {"count": 3, "events": 2 * plain[0] + end[0], "bytes": 2 * plain[1] + end[1]},
        "PWP": {"count": adapt, "events": pwp[0] * adapt, "bytes": pwp[1] * adapt},
    }
    plan, _ = comms_plan(strategy)
    if plan is not None:
        events, nbytes = batch_cost()
        lines["PWC"] = {"count": 1, "events": events, "bytes": nbytes, "batch": True}
    else:
        com_events = com_bytes = 0
        for comp in strategy.of_kind("COM"):
            values = log_values(strategy, comp)
            for direction in [2 if d == "recv" else 1 for d in comp.directions]:
                ev, by = _pwc_cost(values, comp.code, direction)
                com_events += ev
                com_bytes += by
        lines["PWC"] = {"count": sum(len(c.directions) for c in strategy.of_kind("COM")), "events": com_events,
                        "bytes": com_bytes}
    offsets, (pwb_events, pwb_bytes) = log_offsets(strategy)
    lines["PWB"] = {"count": len(offsets), "events": pwb_events, "bytes": pwb_bytes}
    return {"events": sum(v["events"] for v in lines.values()), "bytes": sum(v["bytes"] for v in lines.values()),
            "limit_events": PRINT_LIMITS["events"], "limit_bytes": PRINT_LIMITS["bytes"],
            "lines": lines, "log_offsets": offsets}


def flag_words(strategy: sf.Strategy) -> int:
    return max(1, math.ceil(len(strategy.of_kind("S")) / 31))


# ---------------------------------------------------------------- reference model

@dataclass(frozen=True)
class SelectState:
    rule: int = 0
    since: int = 0
    ended: bool = False
    held: int = 0
    new: bool = False
    preempted: int = 0  # rule code whose running activation this tick preempted, else 0


def reference_select(state: SelectState, ok: list[bool], prio: list[int], tick: int, commitment: dict) -> SelectState:
    """Pure-Python rt__select. `ok` and `prio` are indexed by rule code (index 0 unused).
    `state.ended` = the current capability reported done/abort on an earlier tick."""
    best = 0
    for r in range(1, len(ok)):
        if ok[r] and (best == 0 or prio[r] > prio[best]):
            best = r
    cur = state.rule
    switch = cur == 0 or state.ended or not ok[cur]
    if not switch and best > 0 and best != cur:
        if prio[best] >= commitment["interrupt_at"]:
            switch = True
        if tick - state.since >= commitment["min_hold"] and prio[best] >= prio[cur] + commitment["preempt_margin"]:
            switch = True
    if not switch:
        return replace(state, held=1, new=False, preempted=0)
    preempted = cur if cur > 0 and not state.ended else 0
    return SelectState(rule=best, since=tick, ended=False, held=0, new=best > 0, preempted=preempted)


def reference_priority(default: int, effects: list[tuple[str, int]]) -> int:
    """rt__recompute: apply the active effects on one rule in adaptation-code order (op "add" or
    "set"), then clamp to 0..1000."""
    value = default
    for op, amount in effects:
        value = value + amount if op == "add" else amount
    return max(0, min(1000, value))


# ---------------------------------------------------------------- generation

def _cond_expr(cond: sf.Cond, codes: dict[str, int]) -> str:
    kind = cond[0]
    if kind == "always":
        return "1"
    if kind == "sit":
        return f"rt__flag({codes[cond[1]]})"
    if kind == "not":
        return f"(1 - {_cond_expr(cond[1], codes)})"  # flags are 0/1; independent of NOT's semantics
    return f"({_cond_expr(cond[1], codes)} {kind.upper()} {_cond_expr(cond[2], codes)})"


def _arg_expr(strategy: sf.Strategy, arg: sf.Arg) -> str:
    if arg.kind == "int":
        return str(arg.value)
    return f"{strategy.components[arg.ref].prefix}__{arg.name}"


def _sub(name: str, body: list[str]) -> list[str]:
    return [f"SUB {name}()"] + [f"  {line}" if line else "" for line in body] + ["END SUB", ""]


def _if(cond: str, body: list[str]) -> list[str]:
    return [f"IF {cond} THEN"] + [f"  {line}" for line in body] + ["END IF"]


def _print_values(names: list[str]) -> str:
    return '; ","; '.join(names)


def generate_tables(strategy: sf.Strategy, unit_subs: dict[str, dict[str, int]]) -> str:
    """generated.tables: DIMs for declared output arrays and the st__* phase SUBs runtime.main calls."""
    comps = strategy.components
    codes = strategy.codes()
    sit_codes = codes["situation"]
    ordered = [c for kind in KIND_ORDER for c in strategy.of_kind(kind)]
    out = ["' generated.tables: rule, adaptation, commitment, constant and telemetry tables and the phase",
           "' dispatch. Generated by strategy_basic.py from the source; do not edit.", ""]
    for comp in ordered:
        for o in comp.outputs:
            if o.cells > 1:
                out.append(f"DIM {comp.prefix}__{o.name}({o.cells - 1})")
    out.append("")

    init = []
    for comp in comps.values():
        for prm in comp.params:
            tune = f" ' @tune {prm.low} {prm.high} {prm.step}" if prm.tunable else ""
            init.append(f"{comp.prefix}__{prm.name} = {prm.value}{tune}")
        for cond in comp.conditions:
            init.append(f"{comp.prefix}__k_{cond.name} = {cond.code}")
    for set_name, members in strategy.roles.items():
        init.append(f"st__role_{set_name} = 0")
        for code, (role, seats) in enumerate(members.items(), start=1):
            init += _if(" OR ".join(f"selfId = {s + (100 if strategy.backend == 'gota' else 0)}" for s in seats), [f"st__role_{set_name} = {code}"])
    init.append(f"rt__n_rules = {len(strategy.rules)}")
    for rule in strategy.rules:
        init += [f"rt__def({rule.code}) = {rule.priority}", f"rt__prio({rule.code}) = {rule.priority}",
                 f"rt__rcap({rule.code}) = {comps[rule.capability].code}"]
    adaptations = strategy.of_kind("A")
    init.append(f"rt__n_adapt = {len(adaptations)}")
    rule_codes = codes["rule"]
    for comp in adaptations:
        eff = comp.effect
        amount = str(eff.amount) if isinstance(eff.amount, int) else f"{comp.prefix}__{eff.amount}"
        if eff.op == "-=":
            amount = f"0 - {amount}" if not isinstance(eff.amount, int) else str(-eff.amount)
        duration = "-1" if eff.duration is None else (
            str(eff.duration) if isinstance(eff.duration, int) else f"{comp.prefix}__{eff.duration}")
        init += [f"rt__a_rule({comp.code}) = {rule_codes[eff.rule]}",
                 f"rt__a_op({comp.code}) = {2 if eff.op == '=' else 1}",
                 f"rt__a_amt({comp.code}) = {amount}", f"rt__a_dur({comp.code}) = {duration}"]
    commit_prefix = comps["ST.commitment"].prefix
    init += [f"rt__min_hold = {commit_prefix}__min_hold", f"rt__margin = {commit_prefix}__preempt_margin",
             f"rt__interrupt = {commit_prefix}__interrupt_at",
             f"rt__n_sits = {len(strategy.of_kind('S'))}", f"rt__n_words = {flag_words(strategy)}"]
    plan, _ = comms_plan(strategy)
    if plan is not None:
        init += [f"cm__keys({i}) = {key}" for i, key in enumerate(sc.DEFAULT_KEYS)]
        init += [f"{name} = {value}" for name, value in CODEC_DEFAULTS.items()]
    for comp in ordered:
        if f"{comp.prefix}__init" in unit_subs.get(comp.id, {}):
            init.append(f"{comp.prefix}__init()")
    out += _sub("st__init", init)

    receive, send = [], []
    # comms-v1 builds: the comms runtime owns receive, arbitration, sending and the batch line. Its
    # Codec receives dispatch once per accepted message; send priority is shared with
    # the wire model, and the compact batch flushes after the other telemetry.
    decoded, flush = [], []
    if plan is not None:
        receive += [f"{comps[c].prefix}__got = 0" for c in plan.values()]
        receive.append("cm__receive()")
        for wire, comp_id in plan.items():
            comp = comps[comp_id]
            p = comp.prefix
            body = [f"{p}__{CODEC_PACKET}(0) = cm__pa", f"{p}__{CODEC_PACKET}(1) = cm__pb",
                    f"{p}__got = {p}__got + 1", f"{p}__from = cm__speaker"]
            if "recv" in comp.directions:
                body.append(f"{p}__recv()")
            decoded += _if(f"cm__type = {wire}", body)
        send.append("cm__sent = 0")
        send += [f"{comps[c].prefix}__sent = 0" for c in plan.values()]
        for wire in sc.PRIORITY:  # comms.md §7 priority; the runtime allows one successful shout
            comp = comps.get(plan.get(wire, ""))
            if comp is None or "send" not in comp.directions:
                continue
            p = comp.prefix
            send += _if("cm__sent = 0", [f"{p}__send()", f"{p}__sent = cm__sent"] + _if(
                f"{p}__sent <> 0", [f"{p}__{CODEC_PACKET}(0) = cm__pa", f"{p}__{CODEC_PACKET}(1) = cm__pb"]))
        flush.append("cm__flush()")
    for comp in (strategy.of_kind("COM") if plan is None else ()):
        p = comp.prefix
        values = []
        for name in (comp.log.fields if comp.log else ()):
            out_decl = comp.output(name)
            values += [f"{p}__{name}"] if out_decl.cells == 1 else [f"{p}__{name}({i})" for i in range(out_decl.cells)]
        for direction, sub, flag, seat, bucket in ((2, "recv", "got", f"{p}__from", receive),
                                                   (1, "send", "sent", "selfId", send)):
            if sub not in comp.directions:
                continue
            ev, by = _pwc_cost(len(values), comp.code, direction)
            tail = f'" d="; {_print_values(values)}' if values else '" d=0"'
            bucket += [f"{p}__{flag} = 0", f"{p}__{sub}()"]
            bucket += _if(f"{p}__{flag} <> 0 AND telemetryOff = 0", [
                f'PRINT "PWC v=2 t="; worldTick; " m={comp.code} s={direction} w="; {seat}; {tail}',
                f"rt__pe = rt__pe + {ev}", f"rt__pb = rt__pb + {by}"])
    out += _sub("st__receive", receive)
    out += _sub("st__decoded", decoded)
    out += _sub("st__knowledge", [f"{c.prefix}__update()" for c in strategy.of_kind("K")])
    sits = []
    for comp in strategy.of_kind("S"):
        sits += [f"{comp.prefix}__eval()", f"rt__flag({comp.code}) = 0"]
        sits += _if(f"{comp.prefix}__on <> 0", [f"rt__flag({comp.code}) = 1"])
    out += _sub("st__situations", sits)
    adapt = []
    for comp in adaptations:
        adapt += [f"{comp.prefix}__eval()", f"rt__adapt_step({comp.code}, {comp.prefix}__fire)"]
    out += _sub("st__adapt", adapt)
    conds = []
    for rule in strategy.rules:
        expr = _cond_expr(rule.condition, sit_codes)
        if rule.roles:
            set_name, names = rule.roles
            role_codes = codes["role"][set_name]
            roles = " OR ".join(f"st__role_{set_name} = {role_codes[n]}" for n in names)
            expr = f"({expr} AND ({roles}))"
        conds.append(f"rt__ok({rule.code}) = {expr}")
    out += _sub("st__conditions", conds)
    bind = ["rt__in0 = 0", "rt__in1 = 0", "rt__in2 = 0"]
    for rule in strategy.rules:
        cap = comps[rule.capability]
        body = []
        for index, name in enumerate(cap.inputs):
            body += [f"{cap.prefix}__in_{name} = {_arg_expr(strategy, rule.args[name])}",
                     f"rt__in{index} = {cap.prefix}__in_{name}"]
        if body:
            bind += _if(f"rt__rule = {rule.code}", body)
    out += _sub("st__bind", bind)
    start, tick = [], []
    for comp in strategy.of_kind("C"):
        p = comp.prefix
        start += _if(f"rt__cap = {comp.code}", [f"{p}__status = 0", f"{p}__cond = 0", f"{p}__start()"])
        tick += _if(f"rt__cap = {comp.code}", [f"{p}__tick()", f"rt__status = {p}__status", f"rt__cond = {p}__cond"])
    tick += ["rt__valid = 0"]
    for comp in strategy.of_kind("C"):
        done = sum(1 for c in comp.conditions if c.kind == "done")
        total = len(comp.conditions)
        checks = ["IF rt__status = 0 THEN", "  rt__valid = 1", "END IF"]
        if done:
            checks += [f"IF rt__status = 1 AND rt__cond >= 1 AND rt__cond <= {done} THEN", "  rt__valid = 1", "END IF"]
        if total > done:
            checks += [f"IF rt__status = 2 AND rt__cond > {done} AND rt__cond <= {total} THEN", "  rt__valid = 1",
                       "END IF"]
        tick += _if(f"rt__cap = {comp.code}", checks)
    tick += _if("rt__valid = 0", ["rt__status = 2", "rt__cond = -1"])
    out += _sub("st__start", start)
    out += _sub("st__tick", tick)
    out += _sub("st__send", send)
    offsets, _ = log_offsets(strategy)
    beliefs = []
    for comp in strategy.of_kind("K"):
        if not comp.log:
            continue
        values = []
        for name in comp.log.fields:
            o = comp.output(name)
            values += [f"{comp.prefix}__{name}"] if o.cells == 1 else [f"{comp.prefix}__{name}({i})" for i in range(o.cells)]
        ev, by = _pwb_cost(len(values), comp.code)
        beliefs += _if(f"telemetryOff = 0 AND worldTick MOD {comp.log.every} = {offsets[comp.id]}", [
            f'PRINT "PWB v=2 t="; worldTick; " k={comp.code} d="; {_print_values(values)}',
            f"rt__pe = rt__pe + {ev}", f"rt__pb = rt__pb + {by}"])
    out += _sub("st__beliefs", beliefs)
    def print_decision(goal_refs=()):
        items = pwd_items(strategy, goal_refs)
        ev, by = line_cost(items)
        return [_print_statement(items), f"rt__pe = rt__pe + {ev}", f"rt__pb = rt__pb + {by}"]

    decision_print = print_decision()
    if strategy.decision_version() == 3:
        # Labels are literals on the selected rule's branch; no strings or goal state persist.
        decision_print = []
        for rule in strategy.rules:
            decision_print += _if(f"rt__rule = {rule.code}", print_decision(rule.goal_refs) + ["EXIT SUB"])
        decision_print += print_decision()  # No selected rule.
    out += _sub("st__pwd_print", decision_print)
    out += _sub("st__flush", flush)  # after rt__snapshot: the comms batch line (empty for legacy builds)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- assembly

def read_verbatim(path: Path) -> str:
    """Text exactly as stored (no newline translation), so skills and runtime stay byte-identical."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def policy_stats(policy: str, *, backend: str = "paintbot") -> dict:
    scan = scan_basic(policy, backend=backend)
    limits = LIMITS
    if backend == "gota":
        from gota_host import LIMITS as limits
    return {"globals": len(scan.globals), "globals_limit": limits["globals"],
            "array_cells": sum(scan.arrays.values()), "array_cells_limit": limits["array_cells"],
            "arrays": len(scan.arrays), "arrays_limit": limits["arrays"],
            "source_bytes": len(policy.encode()), "source_limit": limits["source_bytes"]}


def assemble(strategy: sf.Strategy, units: dict[str, str], runtime_dir: Path, build_id: str,
             source_commit: str, *, baseline_bindings: dict | None = None) -> dict:
    """Check every unit, generate the tables and join policy.bas. Raises BuildError."""
    diags = [d for d in sf.lint_strategy(strategy) if d.level == "error"]
    plan, comms_diags = comms_plan(strategy)
    diags += comms_diags
    if diags:
        raise BuildError(diags)
    llm_ids = [c.id for c in strategy.llm_components()]
    for missing in [i for i in llm_ids if i not in units]:
        diags.append(sf.Diagnostic("error", "unit-missing", "no unit text for this component", missing))
    for extra in [i for i in units if i not in llm_ids]:
        diags.append(sf.Diagnostic("error", "unit-extra", "units given for a component that has no LLM unit", extra))
    texts: dict[str, str] = {}
    for comp_id in llm_ids:
        if comp_id in units:
            texts[comp_id] = normalize_unit(strategy.components[comp_id], units[comp_id])
    for comp in strategy.of_kind("SK"):
        texts[comp.id] = read_verbatim(strategy.root / comp.code_path)
    for comp_id, text in texts.items():
        diags += check_unit(strategy, comp_id, text)
    files = dict(RUNTIME_FILES, **({COMMS_RUNTIME[0]: COMMS_RUNTIME[1]} if plan is not None else {}))
    runtime = {name: read_verbatim(Path(runtime_dir) / filename) for name, filename in files.items()}
    if diags:
        raise BuildError(diags)
    unit_subs = {comp_id: scan_basic(text).subs for comp_id, text in texts.items()}
    if plan is not None:
        unit_subs[COMMS_RUNTIME[0]] = scan_basic(runtime[COMMS_RUNTIME[0]]).subs
    tables = generate_tables(strategy, unit_subs)
    order = [c.id for kind in KIND_ORDER for c in strategy.of_kind(kind)]
    blocks = [("runtime.lib", runtime["runtime.lib"])]
    blocks += [(COMMS_RUNTIME[0], runtime[COMMS_RUNTIME[0]])] if plan is not None else []
    blocks += [("generated.tables", tables)]
    blocks += [(comp_id, texts[comp_id]) for comp_id in order]
    blocks.append(("runtime.main", runtime["runtime.main"]))
    header = [f"' policy.bas | build {build_id} | source {source_commit} | strategy {strategy.name}",
              (f"' GENERATED from {strategy.path.name} by the GotA strategy compiler. Do not edit:"
               if strategy.backend == "gota" else
               "' GENERATED from paintbot_pw_lab/strategy/STRATEGY.md by the strategy compiler. Do not edit:"),
              "' change the source and recompile."]
    # Blocks are embedded byte for byte; a block without a final newline gets one so the next
    # block's marker starts a new line.
    policy = "\n".join(header) + "\n\n" + "".join(
        f"' ==== {name} ====\n{text}{'' if text.endswith(chr(10)) else chr(10)}\n" for name, text in blocks)
    packing = None
    if strategy.backend == "gota":
        from gota_packing import pack
        try:
            policy, packing = pack(policy, baseline_bindings)
        except (ValueError, TypeError) as error:
            raise BuildError([sf.Diagnostic("error", "gota-packing", str(error))]) from error
    budget = {"telemetry": telemetry_worst_case(strategy),
              **policy_stats(policy, backend=strategy.backend)}
    over = [key for key in ("globals", "array_cells", "arrays", "source_bytes")
            if budget[key] > budget[key + "_limit" if key != "source_bytes" else "source_limit"]]
    if over:
        raise BuildError([sf.Diagnostic("error", "engine-limit", f"{key} = {budget[key]} exceeds the engine limit")
                          for key in over])
    out_units = dict(texts)
    out_units.update(runtime)
    out_units["generated.tables"] = tables
    mapping = build_map(strategy, out_units, build_id, source_commit, budget)
    mapping["backend"] = strategy.backend
    if packing is not None:
        mapping["packing"] = packing
    return {"policy": policy, "units": out_units, "map": mapping, "budget": budget}


def build_map(strategy: sf.Strategy, units: dict[str, str], build_id: str, source_commit: str, budget: dict) -> dict:
    offsets = budget["telemetry"]["log_offsets"]
    components = {}
    for comp in strategy.components.values():
        unit = comp.id if comp.id in units else ("generated.tables" if comp.kind == "ST" else None)
        entry = {"kind": comp.kind, "code": comp.code, "prefix": comp.prefix, "text_hash": comp.text_hash,
                 "interface": comp.interface, "llm": comp.llm, "uses": list(comp.uses),
                 "unit": unit, "generated": comp.kind == "ST",
                 "unit_sha256": _sha(units[unit]) if unit else None,
                 "checks": [{"level": c.level, "text": c.text, "reads": list(c.reads)} for c in comp.checks]}
        if comp.log:
            entry["log_fields"] = [{"name": n, "cells": comp.output(n).cells} for n in comp.log.fields]
            entry["log_every"] = comp.log.every or None
            entry["log_offset"] = offsets.get(comp.id)
        components[comp.id] = entry
    goal_metadata = {c.id: {"claim": c.fields["Claim"], "priority": int(c.fields["Priority"]),
                          "provenance": c.fields["Provenance"], "status": c.status,
                          "status_date": c.status_date, "evidence": c.fields.get("Evidence", ""),
                          "rationale": c.fields.get("Rationale", "")}
                     for c in strategy.of_kind("G")}
    return {
        "schema": "pw-strategy-map/1", "build_id": build_id, "source_commit": source_commit,
        "components": components, "codes": strategy.codes(),
        "rules": [{"id": r.id, "code": r.code, "priority": r.priority, "capability": r.capability,
                   "inputs": list(strategy.components[r.capability].inputs),
                   "args": {k: ({"int": a.value} if a.kind == "int" else {"ref": a.ref, "name": a.name})
                            for k, a in r.args.items()},
                   "roles": list(r.roles[1]) if r.roles else None, "role_set": r.roles[0] if r.roles else None,
                   "condition": sf.condition_text(r.condition),
                   **({"goal_refs": list(r.goal_refs)} if strategy.decision_version() == 3 else {})}
                  for r in strategy.rules],
        **({"goals": goal_metadata} if goal_metadata else {}),
        "priorities": {r.id: r.priority for r in strategy.rules},
        "adaptations": [{"id": c.id, "code": c.code, "rule": c.effect.rule, "op": c.effect.op,
                         "amount": c.effect.amount, "duration": c.effect.duration} for c in strategy.of_kind("A")],
        "commitment": dict(strategy.commitment),
        "roles": {s: {r: list(seats) for r, seats in m.items()} for s, m in strategy.roles.items()},
        "flag_words": flag_words(strategy),
        "telemetry": {"version": 2, "lines": {k: list(v) for k, v in strategy.telemetry_keys().items()},
                      **({"decision_version": 3} if strategy.decision_version() == 3 else {}),
                      "list_keys": {"PWD": ["i", "f"], "PWB": ["d"], "PWC": ["d"]},
                      "worst_case": budget["telemetry"]},
        "runtime": {"files": {name: _sha(units[name]) for name in (*RUNTIME_FILES, COMMS_RUNTIME[0]) if name in units}},
        "skills": {c.id: {"path": c.code_path, "sha256": _sha(units[c.id])} for c in strategy.of_kind("SK")},
        **({"comms": comms_map(plan)} if (plan := comms_plan(strategy)[0]) is not None else {}),
    }
