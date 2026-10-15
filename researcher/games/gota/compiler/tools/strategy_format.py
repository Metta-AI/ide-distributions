#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_format.py
"""Parse and lint the Paintbot PW strategy file (strategy/STRATEGY.md).

The format is defined in docs/designs/2026-09-30-strategy-file-format.md. This module turns the
Markdown into a typed model (`Strategy`, `Component`, `Rule`) and checks everything the compile
loop depends on: IDs, references and layering, the machine fields that Python compiles (Params,
Inputs, Outputs, Log, Done/Abort, Effect, Roles, rules, commitment), checks' `Reads:` coverage,
and the numeric/goal-aware telemetry print budget. The destination goal grammar and wire
extension are documented in docs/goal-layer.md. Python-facing contract:
docs/strategy-compiler-maintainers.md (source model and hashes).

    import strategy_format as sf
    strategy = sf.parse_strategy(Path("paintbot_pw_lab/strategy/STRATEGY.md"))
    errors = [d for d in sf.lint_strategy(strategy) if d.level == "error"]

parse_strategy never raises on content problems: they become diagnostics, so a loop can report
all of them at once. Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- vocabulary

SECTIONS = {  # section heading -> component kind allowed in it
    "Primitives": "P", "Knowledge": "K", "Situations": "S", "Skills": "SK", "Capabilities": "C",
    "Strategy": "ST", "Adaptations": "A", "Communication": "COM", "Goals": "G", "Open questions": None,
}
LLM_KINDS = ("K", "S", "C", "A", "COM")
ST_IDS = ("ST.roles", "ST.rules", "ST.commitment")
COMMITMENT_PARAMS = ("min_hold", "preempt_margin", "interrupt_at")
# What each kind may reference (design §3). "R" = rule IDs.
LAYERS = {
    "P": set(), "K": {"P", "COM"}, "S": {"K"}, "SK": {"P", "K", "S"}, "C": {"K", "S", "SK"},
    "A": {"K", "S", "R"}, "ST": {"P", "K", "S", "SK", "C", "A", "R", "G"}, "G": set(),
    "COM": {"P", "K", "S", "SK", "C", "A", "R", "ST"},
}
# Fields allowed per kind. Metadata fields (Evidence, Status, Rationale) are never compiled.
COMMON = {"Summary", "Spec", "Uses", "Checks", "Evidence", "Status", "Rationale", "Accepts"}
FIELDS = {
    "P": {"Summary", "Spec", "Evidence", "Status", "Rationale"},
    "G": {"Claim", "Priority", "Provenance", "Checks", "Evidence", "Status", "Rationale"},
    "K": COMMON | {"Sources", "Memory", "Log", "Params", "Outputs"},
    "S": COMMON | {"Params", "Outputs"},
    "SK": COMMON | {"Params", "Outputs", "Code"},
    "C": COMMON | {"Params", "Inputs", "Done when", "Abort when"},
    "ST": COMMON | {"Params"},  # ST.roles adds Roles / Roles <set>
    "A": COMMON | {"Params", "Effect"},
    "COM": COMMON | {"Content", "Encoding", "Send when", "On receipt", "Params", "Outputs", "Log", "Directions"},
}
METADATA_FIELDS = {"Evidence", "Status", "Rationale"}
CHECK_LEVELS = ("True", "Believed", "Acted properly", "Acted", "Result")
STATUSES = ("idea", "specified", "compiled", "tested", "proven")
TELEMETRY_KEYS = {"PWD": ("t", "r", "c", "i", "h", "p", "f"), "PWP": ("t", "a", "r", "o", "n", "p"),
                  "PWE": ("t", "c", "e", "k"), "PWB": ("t", "k", "d"), "PWC": ("t", "m", "s", "w", "d")}
CAPS = {"rule": 63, "S": 124, "C": 63, "A": 31, "K": 63, "COM": 15}
MAX_INPUTS = 3
INT32 = (-2**31, 2**31 - 1)
EFFECT_BOUND = 1000  # |amount| of an Effect; keeps runtime priority arithmetic far from int32 wrap
# Local names the unit ABI and generated code own; Params/Outputs/Inputs/conditions must not use them.
RESERVED_LOCALS = {"on", "fire", "status", "cond", "got", "sent", "from", "init", "update", "eval", "start",
                   "tick", "recv", "send"}
DIRECTIONS = {"send": ("send",), "receive": ("recv",), "both": ("recv", "send")}
SEATS = 16

SNAKE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
ID_RE = re.compile(rf"^(?:(?P<kind>K|S|SK|C|A|COM|ST|R|G)\.(?P<name>{SNAKE})|P\.(?P<pname>[A-Za-z][A-Za-z0-9_]*))$")
REF_RE = re.compile(r"`((?:P|K|S|SK|C|A|COM|ST|R|G)\.[A-Za-z0-9_]+)`")
FIELD_RE = re.compile(r"^- (?P<name>[A-Z][A-Za-z ]*?):(?:\s+(?P<text>.*))?$")
SUB_BULLET_RE = re.compile(r"^\s{2,}- (?P<text>.*)$")
PARAM_RE = re.compile(
    rf"^(?P<name>{SNAKE})\s*=\s*(?P<value>-?\d+)(?P<unit>(?:\s+[A-Za-z%/]+)*)"
    r"(?:\s*\[\s*(?P<low>-?\d+)\s*,\s*(?P<high>-?\d+)\s*,\s*(?P<step>\d+)\s*\])?\s+--\s+(?P<why>\S.*)$")
OUTPUT_RE = re.compile(rf"^(?P<name>{SNAKE})(?:\[(?P<cells>\d+)\])?\s+--\s+(?P<desc>\S.*)$")
COND_ITEM_RE = re.compile(rf"^(?P<name>{SNAKE})\s+--\s+(?P<desc>\S.*)$")
LOG_RE = re.compile(rf"^(?P<names>{SNAKE}(?:\s*,\s*{SNAKE})*)(?:\s+every\s+(?P<every>\d+)\s+ticks?)?$")
EFFECT_RE = re.compile(
    rf"^`(?P<rule>R\.{SNAKE})`\s*(?P<op>\+=|-=|=)\s*(?P<amount>-?\d+|{SNAKE})"
    rf"\s+(?:FOR\s+(?P<duration>\d+|{SNAKE})|(?P<forever>FOREVER))$")
ROLE_RE = re.compile(rf"^(?P<name>{SNAKE})\s*=\s*seats\s+(?P<seats>\d+(?:\s*,\s*\d+)*)$")
RULE_RE = re.compile(
    rf"^- `(?P<id>R\.{SNAKE})` \[(?P<prio>-?\d+)\]:\s*(?P<cond>.*?)\s+DO\s+`(?P<cap>C\.{SNAKE})`"
    rf"(?:\((?P<args>[^)]*)\))?(?:\s+AS\s+(?P<set>{SNAKE})\s*=\s*(?P<roles>{SNAKE}(?:\s*,\s*{SNAKE})*))?"
    rf"(?:\s+FOR\s+(?P<goals>(?:`[A-Z]+\.{SNAKE}`|[A-Z]+\.{SNAKE})"
    rf"(?:\s*,\s*(?:`[A-Z]+\.{SNAKE}`|[A-Z]+\.{SNAKE}))*))?\s*$")
STATUS_RE = re.compile(r"^(?P<status>[a-z]+)(?:\s*\((?P<date>\d{4}-\d{2}-\d{2})\))?\s*$")
GUESS_RE = re.compile(r"^G-(?:K|S|SK|C|A|COM|ST)\.[a-z0-9_]+-\d+$")
READ_RE = re.compile(rf"^(?:(?P<kind>PWD|PWP|PWE|PWC)\.(?P<key>[a-z0-9]+)|`(?P<comp>(?:K|COM)\.{SNAKE})`\.(?P<field>{SNAKE})|replay)$")
STE_MODALS = re.compile(r"\b(may|should|would|might|could)\b", re.IGNORECASE)
PROSE_FIELDS = {"Summary", "Spec", "Sources", "Memory", "Content", "Encoding", "Send when", "On receipt", "Rationale"}


# ---------------------------------------------------------------- model

@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str
    component: str | None = None
    line: int | None = None

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message,
                "component": self.component, "line": self.line}


@dataclass(frozen=True)
class Param:
    name: str
    value: int
    unit: str
    low: int | None
    high: int | None
    step: int | None
    why: str

    @property
    def tunable(self) -> bool:
        return self.low is not None


@dataclass(frozen=True)
class Output:
    name: str
    cells: int
    description: str


@dataclass(frozen=True)
class Condition:
    name: str
    kind: str  # "done" | "abort"
    code: int
    description: str


@dataclass(frozen=True)
class LogSpec:
    fields: tuple[str, ...]
    every: int


@dataclass(frozen=True)
class Check:
    level: str
    text: str
    reads: tuple[str, ...]


@dataclass(frozen=True)
class Effect:
    rule: str
    op: str
    amount: int | str
    duration: int | str | None  # None = FOREVER


@dataclass(frozen=True)
class Arg:
    kind: str  # "int" | "ref"
    value: int | None = None
    ref: str | None = None
    name: str | None = None


Cond = tuple


@dataclass(frozen=True)
class Rule:
    id: str
    priority: int
    condition: Cond
    capability: str
    args: dict
    roles: tuple | None
    line: int
    code: int
    goal_refs: tuple[str, ...] = ()


@dataclass
class Component:
    id: str
    kind: str
    line: int
    fields: dict[str, str]
    prose: str = ""
    field_lines: dict[str, int] = field(default_factory=dict)
    prefix: str = ""
    text_hash: str = ""
    compiled_text: str = ""
    uses: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[Output, ...] = ()
    conditions: tuple[Condition, ...] = ()
    log: LogSpec | None = None
    checks: tuple[Check, ...] = ()
    effect: Effect | None = None
    code_path: str | None = None
    accepts: tuple[str, ...] = ()
    directions: tuple[str, ...] = ()  # COM: ("recv",), ("send",) or ("recv", "send")
    status: str = ""
    status_date: str | None = None
    code: int | None = None
    llm: bool = False
    interface: dict = field(default_factory=dict)

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def output(self, name: str) -> Output | None:
        return next((o for o in self.outputs if o.name == name), None)


@dataclass
class Strategy:
    path: Path
    root: Path
    name: str = ""
    backend: str = "paintbot"
    components: dict[str, Component] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    roles: dict[str, dict[str, tuple[int, ...]]] = field(default_factory=dict)
    commitment: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[Component]:
        return [c for c in self.components.values() if c.kind == kind]

    def llm_components(self) -> list[Component]:
        return [c for c in self.components.values() if c.llm]

    def decision_version(self) -> int:
        return 3 if any(r.goal_refs for r in self.rules) else 2

    def telemetry_keys(self) -> dict:
        keys = dict(TELEMETRY_KEYS)
        if self.decision_version() == 3:
            keys["PWD"] = ("g", *keys["PWD"])
        return keys

    def codes(self) -> dict:
        """Telemetry code tables (maintainer guide: source model and hashes). 0 means none in every space."""
        conditions = {c.id: {k.name: k.code for k in c.conditions} for c in self.of_kind("C")}
        roles = {name: {role: i + 1 for i, role in enumerate(members)} for name, members in self.roles.items()}
        return {
            "rule": {r.id: r.code for r in self.rules},
            "capability": {c.id: c.code for c in self.of_kind("C")},
            "situation": {c.id: c.code for c in self.of_kind("S")},
            "knowledge": {c.id: c.code for c in self.of_kind("K")},
            "adaptation": {c.id: c.code for c in self.of_kind("A")},
            "message": {c.id: c.code for c in self.of_kind("COM")},
            "condition": conditions,
            "role": roles,
            "event": {"start": 1, "done": 2, "abort": 3, "preempted": 4, "died": 5},
            "held": {"new_or_idle": 0, "kept": 1},
            "direction": {"send": 1, "receive": 2},
        }


def prefix_for(component_id: str) -> str:
    """BASIC name prefix: `K.enemy_contacts` -> `k_enemy_contacts`. Names are prefix + "__" + local.
    IDs never contain `__` or a trailing `_`, so two prefixes plus `__` can never produce one name."""
    kind, _, name = component_id.partition(".")
    return f"{kind.lower()}_{name.lower()}"


# ---------------------------------------------------------------- parsing

class _Parser:
    def __init__(self, path: Path, text: str, backend: str = "paintbot"):
        self.strategy = Strategy(path=path, root=path.parent, backend=backend)
        self.lines = text.splitlines()

    def error(self, code: str, message: str, component: str | None = None, line: int | None = None) -> None:
        self.strategy.diagnostics.append(Diagnostic("error", code, message, component, line))

    def warn(self, code: str, message: str, component: str | None = None, line: int | None = None) -> None:
        self.strategy.diagnostics.append(Diagnostic("warning", code, message, component, line))

    def run(self) -> Strategy:
        section: str | None = None
        current: list[tuple[int, str]] | None = None
        blocks: list[tuple[str | None, int, str, list[tuple[int, str]]]] = []
        in_fence = False
        for number, raw in enumerate(self.lines, start=1):
            if raw.startswith("```"):
                in_fence = not in_fence
            if in_fence or raw.startswith("```"):
                if current is not None:
                    current.append((number, raw))
                continue
            if raw.startswith("# ") and not self.strategy.name:
                title = raw[2:].strip()
                if not title.startswith("Strategy:"):
                    self.error("title", "the first heading must be `# Strategy: <name>`", line=number)
                self.strategy.name = title.partition(":")[2].strip()
                continue
            if raw.startswith("## "):
                section = raw[3:].strip()
                current = None
                if section not in SECTIONS:
                    self.error("section", f"unknown section `## {section}` (allowed: {', '.join(SECTIONS)})",
                               line=number)
                continue
            if raw.startswith("### "):
                heading = raw[4:].strip()
                current = []
                blocks.append((section, number, heading, current))
                continue
            if current is not None:
                current.append((number, raw))
        if not self.strategy.name:
            self.error("title", "missing `# Strategy: <name>` heading")
        for section_name, number, heading, body in blocks:
            self.component(section_name, number, heading, body)
        self.number_codes()
        return self.strategy

    def component(self, section: str | None, number: int, heading: str, body: list[tuple[int, str]]) -> None:
        if section == "Open questions":
            return
        match = ID_RE.match(heading)
        if not match or heading.startswith("R."):
            self.error("id", f"`### {heading}` is not a component ID (prefix.snake_case, no `__`)", line=number)
            return
        kind = match.group("kind") or "P"
        if section is None or SECTIONS.get(section) != kind:
            self.error("section", f"{heading} is under `## {section}`; a {kind}. component belongs in "
                       f"`## {next(s for s, k in SECTIONS.items() if k == kind)}`", heading, number)
        if kind == "ST" and heading not in ST_IDS:
            self.error("id", f"Strategy components are exactly {', '.join(ST_IDS)}", heading, number)
        if heading in self.strategy.components:
            self.error("duplicate-id", f"{heading} is defined twice", heading, number)
            return
        comp = Component(id=heading, kind=kind, line=number, fields={}, prefix=prefix_for(heading),
                         llm=kind in LLM_KINDS)
        rule_lines: list[tuple[int, str]] = []
        name: str | None = None
        prose: list[str] = []
        for line_no, raw in body:
            if heading == "ST.rules" and raw.startswith("- `R."):
                rule_lines.append((line_no, raw))
                name = None
                continue
            m = FIELD_RE.match(raw)
            if m and not prose:
                name = m.group("name")
                allowed = FIELDS[kind] | ({"Roles"} if heading == "ST.roles" else set())
                if not (name in allowed or (heading == "ST.roles" and re.fullmatch(rf"Roles {SNAKE}", name))):
                    self.error("field", f"field `{name}` is not allowed on {kind}. components", heading, line_no)
                if name in comp.fields:
                    self.error("field", f"field `{name}` appears twice", heading, line_no)
                comp.fields[name] = (m.group("text") or "").strip()
                comp.field_lines[name] = line_no
            elif name is not None and (raw.startswith("  ") or not raw.strip()) and not prose:
                comp.fields[name] += "\n" + raw.rstrip()
            elif raw.strip():
                name = None
                prose.append(raw.rstrip())
            elif prose:
                prose.append("")
        for key in comp.fields:
            comp.fields[key] = comp.fields[key].rstrip()
        comp.prose = "\n".join(prose).strip()
        if comp.prose:
            self.warn("prose", "text after the field list is not compiled; move it into Spec or Rationale",
                      heading, number)
        self.strategy.components[heading] = comp
        self.fields(comp)
        if heading == "ST.rules":
            self.rules(comp, rule_lines)
        comp.compiled_text = compiled_text(comp, rule_lines)
        comp.text_hash = "sha256:" + hashlib.sha256(comp.compiled_text.encode()).hexdigest()

    # -- field parsers

    def sub_bullets(self, comp: Component, name: str) -> list[str] | None:
        """Sub-bullet items of a field; None (and an error) when the field has inline text or no items."""
        text = comp.fields[name]
        head, *rest = text.split("\n")
        items: list[str] = []
        for raw in rest:
            if not raw.strip():
                continue
            m = SUB_BULLET_RE.match(raw)
            if m:
                items.append(m.group("text").strip())
            elif items and raw.startswith("    "):
                items[-1] += " " + raw.strip()
            else:
                self.error(f"{slug(name)}-syntax", f"`{name}`: expected `  - item` sub-bullets, got {raw.strip()!r}",
                           comp.id, comp.field_lines[name])
                return None
        if head.strip():
            self.error(f"{slug(name)}-syntax", f"`{name}` takes one sub-bullet per item, not inline text "
                       f"({head.strip()!r})", comp.id, comp.field_lines[name])
            return None
        return items

    def fields(self, comp: Component) -> None:
        f = comp.fields
        line = comp.field_lines
        required = ["Claim", "Priority", "Provenance", "Status"] if comp.kind == "G" else ["Summary", "Status", "Spec"]
        if comp.kind not in ("P", "G"):
            required.append("Checks")
        if comp.kind == "SK":
            required.append("Code")
        if comp.kind == "COM":
            required.append("Directions")
        for name in required:
            if not f.get(name, "").strip():
                self.error("missing-field", f"required field `{name}` is missing or empty", comp.id, comp.line)
        if comp.kind == "G" and "Priority" in f:
            priority = f["Priority"]
            if not re.fullmatch(r"[0-9]+", priority) or not 0 <= int(priority) <= 1000:
                self.error("goal-priority", "goal Priority must be an integer 0..1000 (descriptive only)",
                           comp.id, line["Priority"])
        if "Status" in f:
            m = STATUS_RE.match(f["Status"])
            if not m or m.group("status") not in STATUSES:
                self.error("status-syntax", f"Status must be one of {', '.join(STATUSES)} with an optional "
                           f"(YYYY-MM-DD) date", comp.id, line["Status"])
            else:
                comp.status, comp.status_date = m.group("status"), m.group("date")
        if "Uses" in f:
            uses = [u.strip() for u in f["Uses"].replace("\n", " ").split(",") if u.strip()]
            bad = [u for u in uses if not re.fullmatch(r"`[A-Za-z0-9_.]+`", u)]
            if bad:
                self.error("uses-syntax", f"Uses must be backticked IDs separated by commas: {bad}", comp.id, line["Uses"])
            comp.uses = tuple(u.strip("`") for u in uses if u not in bad)
        if "Params" in f:
            params = []
            for item in self.sub_bullets(comp, "Params") or []:
                m = PARAM_RE.match(item)
                if not m:
                    self.error("params-syntax", f"`{item}`: expected `name = INT [unit] [[low, high, step]] -- why`",
                               comp.id, line["Params"])
                    continue
                low, high, step = (int(m.group(k)) if m.group(k) is not None else None for k in ("low", "high", "step"))
                value = int(m.group("value"))
                if low is not None and not (low <= value <= high and step > 0):
                    self.error("params-syntax", f"{m.group('name')}: need low <= value <= high and step > 0",
                               comp.id, line["Params"])
                params.append(Param(m.group("name"), value, m.group("unit").strip(), low, high, step, m.group("why").strip()))
            comp.params = tuple(params)
            dupes = {p.name for p in params if sum(q.name == p.name for q in params) > 1}
            if dupes:
                self.error("params-syntax", f"duplicate params {sorted(dupes)}", comp.id, line["Params"])
        if "Inputs" in f:
            inputs = [i.strip() for i in f["Inputs"].split(",") if i.strip()]
            bad = [i for i in inputs if not re.fullmatch(SNAKE, i)]
            if bad or not inputs:
                self.error("inputs-syntax", f"Inputs must be snake_case names separated by commas: {f['Inputs']!r}",
                           comp.id, line["Inputs"])
            if len(inputs) > MAX_INPUTS:
                self.error("inputs-syntax", f"at most {MAX_INPUTS} inputs (PWD logs 3)", comp.id, line["Inputs"])
            if len(set(inputs)) != len(inputs):
                self.error("inputs-syntax", "duplicate input names", comp.id, line["Inputs"])
            comp.inputs = tuple(i for i in inputs if i not in bad)
        if "Outputs" in f:
            outputs = []
            for item in self.sub_bullets(comp, "Outputs") or []:
                m = OUTPUT_RE.match(item)
                if not m:
                    self.error("outputs-syntax", f"`{item}`: expected `name -- meaning` or `name[N] -- meaning`",
                               comp.id, line["Outputs"])
                    continue
                cells = int(m.group("cells") or 1)
                if m.group("cells") is not None and not 2 <= cells <= 64:
                    self.error("outputs-syntax", f"{m.group('name')}: array outputs have 2..64 cells", comp.id, line["Outputs"])
                if comp.kind == "S" and m.group("name") == "on":
                    self.error("outputs-syntax", "`on` is implicit on every Situation; do not declare it",
                               comp.id, line["Outputs"])
                outputs.append(Output(m.group("name"), cells, m.group("desc").strip()))
            comp.outputs = tuple(outputs)
        if comp.kind == "S":
            comp.outputs = (Output("on", 1, "1 when the situation holds"),) + comp.outputs
        if comp.kind == "C":
            conditions = []
            for kind, name in (("done", "Done when"), ("abort", "Abort when")):
                if name not in f:
                    continue
                if kind == "done" and f[name].strip() == "never":
                    continue
                for item in self.sub_bullets(comp, name) or []:
                    m = COND_ITEM_RE.match(item)
                    if not m:
                        self.error(f"{slug(name)}-syntax", f"`{item}`: expected `name -- description`",
                                   comp.id, line[name])
                        continue
                    conditions.append(Condition(m.group("name"), kind, len(conditions) + 1, m.group("desc").strip()))
            if len({c.name for c in conditions}) != len(conditions):
                self.error("conditions-syntax", "Done/Abort condition names must be unique", comp.id, comp.line)
            comp.conditions = tuple(conditions)
        if "Log" in f:
            m = LOG_RE.match(" ".join(f["Log"].split()))
            every = int(m.group("every")) if m and m.group("every") else 0
            if comp.kind == "K" and (not m or every < 1):
                self.error("log-syntax", f"Log must be `name, name every N ticks` (N >= 1): {f['Log']!r}",
                           comp.id, line["Log"])
            elif comp.kind == "COM" and (not m or m.group("every")):
                self.error("log-syntax", f"a COM Log is `name, name` (logged on every send/receive): {f['Log']!r}",
                           comp.id, line["Log"])
            else:
                comp.log = LogSpec(tuple(n.strip() for n in m.group("names").split(",")), every)
        if "Checks" in f:
            checks = []
            for item in self.sub_bullets(comp, "Checks") or []:
                level = next((lv for lv in CHECK_LEVELS if item.startswith(lv + ":")), None)
                if level is None:
                    self.error("checks-syntax", f"`{item[:60]}`: a check starts with one of {', '.join(CHECK_LEVELS)} "
                               f"and a colon", comp.id, line["Checks"])
                    continue
                text, _, reads_text = item[len(level) + 1:].partition("Reads:")
                reads = tuple(r.strip().rstrip(".") for r in reads_text.split(",") if r.strip())
                checks.append(Check(level, text.strip(), reads))
            comp.checks = tuple(checks)
        if "Effect" in f:
            m = EFFECT_RE.match(" ".join(f["Effect"].split()))
            if not m:
                self.error("effect-syntax", f"Effect must be `` `R.x` +=|-=|= AMOUNT FOR DURATION|FOREVER ``: "
                           f"{f['Effect']!r}", comp.id, line["Effect"])
            else:
                amount = m.group("amount")
                duration = m.group("duration")
                comp.effect = Effect(m.group("rule"), m.group("op"),
                                     int(amount) if re.fullmatch(r"-?\d+", amount) else amount,
                                     None if m.group("forever") else
                                     (int(duration) if duration.isdigit() else duration))
        if comp.kind == "A" and "Effect" not in f:
            self.error("missing-field", "an Adaptation needs an `Effect`", comp.id, comp.line)
        if "Code" in f:
            expected = f"skills/{comp.id.partition('.')[2]}/skill.bas"
            if f["Code"].strip().strip("`") != expected:
                self.error("code-syntax", f"Code must be `{expected}`", comp.id, line["Code"])
            comp.code_path = expected
        if "Directions" in f:
            if f["Directions"].strip() not in DIRECTIONS:
                self.error("directions-syntax", f"Directions must be one of {', '.join(DIRECTIONS)}", comp.id,
                           line["Directions"])
            else:
                comp.directions = DIRECTIONS[f["Directions"].strip()]
        self.reserved(comp)
        if "Accepts" in f:
            accepts = tuple(a.strip() for a in f["Accepts"].split(",") if a.strip())
            bad = [a for a in accepts if not GUESS_RE.match(a)]
            if bad:
                self.error("accepts-syntax", f"Accepts takes guess IDs `G-<ID>-<n>`: {bad}", comp.id, line["Accepts"])
            comp.accepts = accepts
        if comp.id == "ST.roles":
            self.roles(comp)
        if comp.id == "ST.commitment":
            names = [p.name for p in comp.params]
            if sorted(names) != sorted(COMMITMENT_PARAMS):
                self.error("commitment", f"ST.commitment Params must be exactly {', '.join(COMMITMENT_PARAMS)}",
                           comp.id, comp.line)
            else:
                self.strategy.commitment = {p.name: p.value for p in comp.params}
                if self.strategy.commitment["min_hold"] < 0 or self.strategy.commitment["preempt_margin"] < 0:
                    self.error("commitment", "min_hold and preempt_margin must be >= 0", comp.id, comp.line)

    def reserved(self, comp: Component) -> None:
        """Declared names become BASIC globals <prefix>__<name>; keep them clear of the ABI's names and
        of int32 overflow."""
        names = [p.name for p in comp.params] + [o.name for o in comp.outputs if not (comp.kind == "S" and o.name == "on")]
        names += [c.name for c in comp.conditions] + list(comp.inputs)
        for name in names:
            if name in RESERVED_LOCALS or name.startswith(("in_", "k_")):
                self.error("reserved-name", f"`{name}` is reserved by the unit ABI (also in_*/k_* prefixes)",
                           comp.id, comp.line)
        for prm in comp.params:
            values = [v for v in (prm.value, prm.low, prm.high, prm.step) if v is not None]
            if any(not INT32[0] <= v <= INT32[1] for v in values):
                self.error("params-syntax", f"{prm.name}: values must fit in int32", comp.id, comp.line)

    def roles(self, comp: Component) -> None:
        for name, text in comp.fields.items():
            if not name.startswith("Roles"):
                continue
            set_name = name[len("Roles"):].strip() or "role"
            members: dict[str, tuple[int, ...]] = {}
            for item in self.sub_bullets(comp, name) or []:
                m = ROLE_RE.match(item)
                if not m:
                    self.error("roles-syntax", f"`{item}`: expected `role = seats 0,2,4`", comp.id, comp.field_lines[name])
                    continue
                if m.group("name") in members:
                    self.error("roles-syntax", f"role {m.group('name')} listed twice", comp.id, comp.field_lines[name])
                members[m.group("name")] = tuple(int(s) for s in m.group("seats").split(","))
            seats = sorted(s for group in members.values() for s in group)
            seat_count = 10 if self.strategy.backend == "gota" else SEATS
            if members and seats != list(range(seat_count)):
                self.error("roles-syntax", f"role set `{set_name}` must assign every seat 0..{seat_count - 1} exactly once",
                           comp.id, comp.field_lines[name])
            self.strategy.roles[set_name] = members

    def rules(self, comp: Component, rule_lines: list[tuple[int, str]]) -> None:
        for line_no, raw in rule_lines:
            m = RULE_RE.match(raw.strip())
            if not m:
                self.error("rule-syntax", "expected - `R.id` [P]: WHEN <cond>|ALWAYS DO `C.x`(input=arg, ...) "
                           f"[AS set=role,...] [FOR G.id, G.id]: {raw.strip()!r}", comp.id, line_no)
                continue
            priority = int(m.group("prio"))
            if not 0 <= priority <= 1000:
                self.error("rule-syntax", f"{m.group('id')}: priority must be 0..1000", comp.id, line_no)
            cond_text = m.group("cond").strip()
            try:
                condition = parse_condition(cond_text)
            except ValueError as exc:
                self.error("rule-syntax", f"{m.group('id')}: {exc}", comp.id, line_no)
                continue
            args: dict[str, Arg] = {}
            for part in [p.strip() for p in (m.group("args") or "").split(",") if p.strip()]:
                key, eq, value = part.partition("=")
                key, value = key.strip(), value.strip()
                if not eq or not re.fullmatch(SNAKE, key):
                    self.error("rule-syntax", f"{m.group('id')}: argument {part!r} must be input=value", comp.id, line_no)
                    continue
                if key in args:
                    self.error("rule-syntax", f"{m.group('id')}: input {key} bound twice", comp.id, line_no)
                if re.fullmatch(r"-?\d+", value):
                    args[key] = Arg("int", value=int(value))
                elif am := re.fullmatch(rf"`([A-Z]+\.{SNAKE})`\.({SNAKE})", value):
                    args[key] = Arg("ref", ref=am.group(1), name=am.group(2))
                else:
                    self.error("rule-syntax", f"{m.group('id')}: argument value {value!r} must be an integer or "
                               "`ID`.name", comp.id, line_no)
            roles = None
            if m.group("set"):
                roles = (m.group("set"), tuple(r.strip() for r in m.group("roles").split(",")))
            if any(r.id == m.group("id") for r in self.strategy.rules):
                self.error("duplicate-id", f"{m.group('id')} is defined twice", comp.id, line_no)
            self.strategy.rules.append(Rule(m.group("id"), priority, condition, m.group("cap"), args, roles,
                                            line_no, len(self.strategy.rules) + 1,
                                            tuple(g.strip().strip("`") for g in (m.group("goals") or "").split(",")
                                                  if g.strip())))

    def number_codes(self) -> None:
        for kind in ("K", "S", "C", "A", "COM"):
            for index, comp in enumerate(self.strategy.of_kind(kind), start=1):
                comp.code = index
        for comp in self.strategy.components.values():
            comp.interface = declared_interface(comp, self.strategy)


def slug(field_name: str) -> str:
    return field_name.lower().replace(" ", "-")


def parse_condition(text: str) -> Cond:
    """`WHEN a AND NOT (b OR c)` or `ALWAYS` -> nested tuples. AND binds tighter than OR."""
    if text == "ALWAYS":
        return ("always",)
    if not text.startswith("WHEN "):
        raise ValueError("condition must start with WHEN or be ALWAYS")
    tokens = re.findall(r"\(|\)|`[^`]+`|[A-Za-z_.]+|\S", text[5:])
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def parse_or() -> Cond:
        node = parse_and()
        while peek() == "OR":
            take()
            node = ("or", node, parse_and())
        return node

    def parse_and() -> Cond:
        node = parse_unary()
        while peek() == "AND":
            take()
            node = ("and", node, parse_unary())
        return node

    def parse_unary() -> Cond:
        token = peek()
        if token == "NOT":
            take()
            return ("not", parse_unary())
        if token == "(":
            take()
            node = parse_or()
            if peek() != ")":
                raise ValueError("unbalanced parentheses in condition")
            take()
            return node
        if token and re.fullmatch(rf"`S\.{SNAKE}`", token):
            take()
            return ("sit", token.strip("`"))
        raise ValueError(f"expected `S.id`, NOT or ( in condition, got {token!r}")

    node = parse_or()
    if pos != len(tokens):
        raise ValueError(f"unexpected {tokens[pos]!r} in condition")
    return node


def condition_refs(cond: Cond) -> list[str]:
    if cond[0] == "sit":
        return [cond[1]]
    return [ref for child in cond[1:] if isinstance(child, tuple) for ref in condition_refs(child)]


def condition_text(cond: Cond) -> str:
    kind = cond[0]
    if kind == "always":
        return "ALWAYS"
    if kind == "sit":
        return cond[1]
    if kind == "not":
        return f"NOT {condition_text(cond[1])}"
    return f"({condition_text(cond[1])} {kind.upper()} {condition_text(cond[2])})"


def compiled_text(comp: Component, rule_lines: list[tuple[int, str]]) -> str:
    """Exactly what the compiler agent sees and what the hash covers: the ID and every
    field except metadata, in source order; Checks contributes only its Reads lists.
    A descriptive Goal contributes its identity only; the source/map bind its metadata."""
    out = [f"### {comp.id}"]
    if comp.kind == "G":
        return out[0] + "\n"  # Descriptive metadata is bound by map/source, never compiled into gameplay.
    for name, text in comp.fields.items():
        if name in METADATA_FIELDS:
            continue
        if name == "Checks":
            reads = [f"{c.level}: Reads: {', '.join(c.reads)}" for c in comp.checks if c.reads]
            if reads:
                out.append("- Checks (reads):")
                out.extend(f"  - {r}" for r in reads)
            continue
        out.append(f"- {name}: {text}".rstrip())
    out.extend(raw.rstrip() for _, raw in rule_lines)
    text = "\n".join(line.rstrip() for line in "\n".join(out).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def declared_interface(comp: Component, strategy: Strategy) -> dict:
    """The component's interface as declared in the source. Skills add their SUB
    signatures, read from skill.bas (source) when it exists."""
    interface: dict = {"outputs": {o.name: o.cells for o in comp.outputs},
                       "params": [p.name for p in comp.params]}
    if comp.kind == "C":
        interface["inputs"] = list(comp.inputs)
        interface["conditions"] = {c.name: c.code for c in comp.conditions}
    if comp.kind == "SK" and comp.code_path:
        path = strategy.root / comp.code_path
        interface["subs"] = skill_subs(path.read_text()) if path.is_file() else {}
    if comp.kind == "COM":
        interface["directions"] = list(comp.directions)
    return interface


def skill_subs(source: str) -> dict[str, int]:
    subs = {}
    for m in re.finditer(r"(?im)^\s*sub\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?", source):
        params = [p for p in (m.group(2) or "").split(",") if p.strip()]
        subs[m.group(1).lower()] = len(params)
    return subs


def parse_strategy(path: Path, *, backend: str = "paintbot") -> Strategy:
    """Parse STRATEGY.md into the typed model. Content problems become `strategy.diagnostics`."""
    path = Path(path)
    if backend not in ("paintbot", "gota"):
        raise ValueError(f"unknown backend: {backend}")
    return _Parser(path, path.read_text(encoding="utf-8"), backend).run()


# ---------------------------------------------------------------- lint

def lint_strategy(strategy: Strategy) -> list[Diagnostic]:
    """Parse diagnostics plus every cross-component check and the telemetry budget."""
    diags = list(strategy.diagnostics)

    def error(code: str, message: str, comp: str | None = None, line: int | None = None) -> None:
        diags.append(Diagnostic("error", code, message, comp, line))

    def warn(code: str, message: str, comp: str | None = None, line: int | None = None) -> None:
        diags.append(Diagnostic("warning", code, message, comp, line))

    if strategy.backend == "gota":
        for comp in strategy.of_kind("COM"):
            error("gota-com", "GotA does not support COM components", comp.id)
        for kind, limit in (("A", 4), ("S", 124)):
            if len(strategy.of_kind(kind)) > limit:
                error("gota-capacity", f"GotA {kind} capacity is {limit}")
        if len(strategy.rules) > 63:
            error("gota-capacity", "GotA rule capacity is 63")

    comps = strategy.components
    rule_ids = {r.id for r in strategy.rules}

    def kind_of(ref: str) -> str:
        return ref.partition(".")[0]

    def exists(ref: str) -> bool:
        return ref in comps or ref in rule_ids

    for comp in comps.values():
        allowed = LAYERS[comp.kind]
        compiled_refs = set(REF_RE.findall(comp.compiled_text)) - {comp.id}
        for ref in sorted(compiled_refs | set(comp.uses)):
            if not exists(ref):
                error("unresolved-ref", f"`{ref}` does not exist", comp.id, comp.line)
                continue
            if kind_of(ref) not in allowed:
                error("layer", f"{comp.kind}. components cannot refer to `{ref}` (allowed: "
                      f"{', '.join(sorted(allowed)) or 'nothing'})", comp.id, comp.line)
        if comp.kind != "ST":
            missing = sorted(r for r in compiled_refs if kind_of(r) not in ("P", "R") and r not in comp.uses
                             and exists(r))
            if missing:
                error("uses-missing", f"referenced but not in Uses: {', '.join(missing)}", comp.id, comp.line)
        if comp.kind == "S":
            for dep in comp.uses:
                if kind_of(dep) == "S":
                    error("layer", "a Situation cannot use another Situation", comp.id, comp.line)
        for check in comp.checks:
            for item in check.reads:
                m = READ_RE.match(item)
                if not m:
                    error("reads-syntax", f"Reads item {item!r}: use PWD.key, PWE.key, PWP.key, `K.id`.field or replay",
                          comp.id, comp.line)
                elif m.group("kind") and m.group("key") not in strategy.telemetry_keys()[m.group("kind")]:
                    error("reads-syntax", f"{item}: {m.group('kind')} keys are {', '.join(strategy.telemetry_keys()[m.group('kind')])}",
                          comp.id, comp.line)
                elif m.group("comp"):
                    target = comps.get(m.group("comp"))
                    if target is None or target.log is None or m.group("field") not in target.log.fields:
                        error("unlogged-read", f"{item}: no K/COM component logs that field", comp.id, comp.line)
        if comp.kind in ("K", "COM"):
            if comp.kind == "K" and any(c.level == "Believed" for c in comp.checks) and comp.log is None:
                error("missing-field", "a Believed check needs a Log field", comp.id, comp.line)
            if comp.log:
                for name in comp.log.fields:
                    if comp.output(name) is None:
                        error("log-syntax", f"Log field `{name}` is not a declared Output", comp.id, comp.line)
        if comp.effect:
            eff = comp.effect
            if eff.rule not in rule_ids:
                error("unresolved-ref", f"Effect names unknown rule `{eff.rule}`", comp.id, comp.line)
            for value in (eff.amount, eff.duration):
                if isinstance(value, str) and comp.param(value) is None:
                    error("effect-syntax", f"Effect uses `{value}`, which is not one of this component's Params",
                          comp.id, comp.line)
            amount = eff.amount if isinstance(eff.amount, int) else comp.param(eff.amount)
            if amount is not None:
                values = [amount] if isinstance(amount, int) else [v for v in (amount.value, amount.low, amount.high)
                                                                   if v is not None]
                low = 0 if eff.op == "=" else -EFFECT_BOUND
                if any(not low <= v <= EFFECT_BOUND for v in values):
                    error("effect-syntax", f"Effect amount (and its tune range) must be within {low}..{EFFECT_BOUND}",
                          comp.id, comp.line)
            if eff.duration is not None:
                duration = eff.duration if isinstance(eff.duration, int) else (comp.param(eff.duration) or Param("", 1, "", None, None, None, "")).value
                if duration < 1:
                    error("effect-syntax", "Effect duration must be at least 1 tick", comp.id, comp.line)
        if comp.kind == "SK" and comp.code_path and not (strategy.root / comp.code_path).is_file():
            error("missing-skill", f"{comp.code_path} does not exist", comp.id, comp.line)
        style(comp, warn)

    # Strategy components
    for required in ST_IDS:
        if required not in comps:
            error("missing-component", f"{required} is required")
    for rule in strategy.rules:
        if len(set(rule.goal_refs)) != len(rule.goal_refs):
            error("rule-goals", f"{rule.id}: duplicate goal references", "ST.rules", rule.line)
        for goal in rule.goal_refs:
            if not goal.startswith("G."):
                error("rule-goals", f"{rule.id}: `{goal}` is not a Goal", "ST.rules", rule.line)
            elif goal not in comps:
                error("unresolved-ref", f"{rule.id}: goal `{goal}` does not exist", "ST.rules", rule.line)
        cap = comps.get(rule.capability)
        if cap is None or cap.kind != "C":
            error("unresolved-ref", f"{rule.id}: `{rule.capability}` is not a Capability", "ST.rules", rule.line)
            continue
        for sit in condition_refs(rule.condition):
            if sit not in comps:
                error("unresolved-ref", f"{rule.id}: `{sit}` does not exist", "ST.rules", rule.line)
        unbound = [i for i in cap.inputs if i not in rule.args]
        extra = [a for a in rule.args if a not in cap.inputs]
        if unbound or extra:
            error("rule-args", f"{rule.id}: {rule.capability} inputs are ({', '.join(cap.inputs)}); "
                  f"unbound {unbound}, unknown {extra}", "ST.rules", rule.line)
        for name, arg in rule.args.items():
            if arg.kind != "ref":
                continue
            target = comps.get(arg.ref)
            if target is None:
                error("unresolved-ref", f"{rule.id}: `{arg.ref}` does not exist", "ST.rules", rule.line)
            elif target.output(arg.name) is None and target.param(arg.name) is None:
                error("rule-args", f"{rule.id}: `{arg.ref}` has no Output or Param named {arg.name}", "ST.rules", rule.line)
            elif target.output(arg.name) is not None and target.output(arg.name).cells > 1:
                error("rule-args", f"{rule.id}: {arg.ref}.{arg.name} is an array; inputs are scalars", "ST.rules", rule.line)
        if rule.roles:
            set_name, names = rule.roles
            members = strategy.roles.get(set_name)
            if members is None:
                error("rule-roles", f"{rule.id}: no role set `{set_name}` in ST.roles", "ST.rules", rule.line)
            else:
                unknown = [n for n in names if n not in members]
                if unknown:
                    error("rule-roles", f"{rule.id}: unknown roles {unknown} in set {set_name}", "ST.rules", rule.line)
    if "ST.rules" in comps and not strategy.rules:
        error("rule-syntax", "ST.rules has no rules", "ST.rules", comps["ST.rules"].line)

    # Usage warnings and caps
    used_caps = {r.capability for r in strategy.rules}
    used_sits = {s for r in strategy.rules for s in condition_refs(r.condition)}
    used_sits |= {u for c in comps.values() for u in c.uses}
    used_sits |= {a.ref for r in strategy.rules for a in r.args.values() if a.kind == "ref"}
    for comp in comps.values():
        if comp.kind == "C" and comp.id not in used_caps:
            error("unused", f"no rule uses {comp.id}", comp.id, comp.line)
        if comp.kind == "S" and comp.id not in used_sits:
            error("unused", f"nothing uses {comp.id}", comp.id, comp.line)
    for kind, cap in CAPS.items():
        count = len(strategy.rules) if kind == "rule" else len(strategy.of_kind(kind))
        if count > cap:
            error("caps", f"{count} {kind} entries; the runtime supports at most {cap}")

    if not any(d.level == "error" for d in diags):
        import strategy_basic  # lazy: strategy_basic imports this module
        _, comms_errors = strategy_basic.comms_plan(strategy)
        diags.extend(comms_errors)
        budget = strategy_basic.telemetry_worst_case(strategy)
        if budget["bytes"] > budget["limit_bytes"] or budget["events"] > budget["limit_events"]:
            error("telemetry-budget", f"worst-case telemetry per tick is {budget['bytes']} bytes / {budget['events']} "
                  f"print events; the limit is {budget['limit_bytes']} / {budget['limit_events']} (half the engine's). "
                  f"Log fewer fields, log less often, or use fewer Adaptations. Lines: {budget['lines']}")
    return diags


def style(comp: Component, warn) -> None:
    """Mechanical STE checks on prose fields (design §4.8). Warnings only."""
    for name, text in comp.fields.items():
        if name not in PROSE_FIELDS:
            continue
        plain = re.sub(r"`[^`]*`", "X", text)
        if ";" in plain:
            warn("ste", f"`{name}` uses a semicolon; write two sentences", comp.id, comp.field_lines.get(name))
        if STE_MODALS.search(plain):
            warn("ste", f"`{name}` uses may/should/would/might/could; state the requirement", comp.id,
                 comp.field_lines.get(name))
        for sentence in re.split(r"(?<=[.!?])\s+", plain):
            if len(sentence.split()) > 25:
                warn("ste", f"`{name}` has a sentence over 25 words", comp.id, comp.field_lines.get(name))
                break
