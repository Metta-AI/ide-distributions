"""Record content and mutability rules (System Design §3.1, §3.3, §3.5).

``check_change(entity, before, after)`` is the single judge of whether a record write is
allowed. ``before is None`` means creation; ``after is None`` means deletion. It returns
problem strings (empty list = allowed). The store calls it on every write, and handoff
validation calls it on git snapshots, so it must depend only on the two Docs.

Creation (and ``records validate``) checks content only: schema and per-entity content rules.
Updates also check transition rules: immutability, frozen design, set-once fields,
append-only lists, and status graphs with recorded reasons.
"""

from dataclasses import dataclass

from autoresearch.records.registry import Doc, normalize, schema_problems

IMMUTABLE = frozenset({"result", "evidence", "version", "version_event"})

EXPERIMENT_DESIGN_FIELDS = (
    "hypothesis",
    "baseline",
    "change",
    "evaluation_design",
    "environment",
    "parents",
)

HYPOTHESIS_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"under-test", "abandoned"}),
    "under-test": frozenset({"supported", "falsified", "abandoned"}),
    "supported": frozenset(),
    "falsified": frozenset(),
    "abandoned": frozenset(),
}

EXPERIMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "designed": frozenset({"built"}),
    "built": frozenset({"uploaded"}),
    # A credit refusal pauses an evaluation; partial batches resume under the same design.
    "uploaded": frozenset({"evaluating", "awaiting-credits"}),
    "evaluating": frozenset({"reviewed", "awaiting-credits"}),
    "awaiting-credits": frozenset({"evaluating"}),
    "reviewed": frozenset(),
}


@dataclass(frozen=True)
class StatusRule:
    """How an entity's status field may change.

    When ``reason_field`` is set, a change needs a non-empty reason (and a ref when
    ``ref_required``), and ``history_field`` must gain exactly one ``{from, to, reason, ref}``
    entry describing it. ``transitions`` (if given) is the allowed graph.
    """

    field: str
    reason_field: str | None = None
    ref_field: str | None = None
    ref_required: bool = False
    history_field: str | None = None
    transitions: dict[str, frozenset[str]] | None = None


STATUS_RULES: dict[str, StatusRule] = {
    "hypothesis": StatusRule(
        "state", "state_reason", "state_ref", True, "state_history", HYPOTHESIS_TRANSITIONS
    ),
    "claim": StatusRule("status", "status_reason", "status_ref", False, "status_history"),
    "topic": StatusRule("status", "status_reason", "status_ref", False, "status_history"),
    "experiment": StatusRule("status", transitions=EXPERIMENT_TRANSITIONS),
    "lesson": StatusRule("status"),
}

APPEND_ONLY: dict[str, tuple[str, ...]] = {
    "experiment": ("results", "xp_requests"),
    "hypothesis": ("experiments", "state_history"),
    "claim": ("evidence", "status_history"),
    "topic": ("status_history",),
}

SET_ONCE: dict[str, tuple[str, ...]] = {
    "experiment": ("decision",),
    "claim": ("superseded_by",),
}

DECISIONS_HEADING = "## Decisions"


def check_change(entity: str, before: Doc | None, after: Doc | None) -> list[str]:
    if before is None and after is None:
        return []
    if after is None:
        record_id = before[0].get("id", "?")
        return [
            (
                f"{entity} {record_id} was deleted; records are never deleted "
                "(supersede or close them instead)"
            )
        ]
    after = (normalize(after[0]), after[1])
    problems = content_problems(entity, after)
    if before is not None:
        before = (normalize(before[0]), before[1])
        problems += transition_problems(entity, before, after)
    return problems


# --- content rules (any single snapshot) ---


def content_problems(entity: str, doc: Doc) -> list[str]:
    meta, body = doc
    problems = schema_problems(entity, meta)
    if entity in ("version", "version_event", "evidence") and body.strip():
        problems.append(f"{entity} is a JSON record and has no body")
    if entity == "journal" and not _has_decisions_section(body):
        problems.append(f"journal body needs a '{DECISIONS_HEADING}' section")
    if entity == "experiment":
        problems += _experiment_content(meta)
    if entity == "claim":
        problems += _claim_content(meta)
    return problems


def _has_decisions_section(body: str) -> bool:
    return any(line.strip() == DECISIONS_HEADING for line in body.splitlines())


def _experiment_content(meta: dict) -> list[str]:
    if meta.get("xp_requests") and not meta.get("candidate_binding"):
        return ["experiment has xp_requests but no candidate_binding; bind the candidate first"]
    return []


def _claim_content(meta: dict) -> list[str]:
    problems = []
    seen_refs: set[str] = set()
    for entry in meta.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("stance") == "resolves" and entry.get("target") not in seen_refs:
            problems.append(
                f"claim evidence {entry.get('ref')} resolves {entry.get('target')}, "
                "which is not an earlier evidence ref"
            )
        seen_refs.add(entry.get("ref"))
    return problems


# --- transition rules (before -> after) ---


def transition_problems(entity: str, before: Doc, after: Doc) -> list[str]:
    (old, old_body), (new, new_body) = before, after
    record_id = old.get("id", "?")
    if old.get("id") != new.get("id"):
        return [f"id is immutable ({old.get('id')} -> {new.get('id')})"]
    changed = old != new or old_body.strip() != new_body.strip()
    if entity in IMMUTABLE:
        if changed:
            return [
                (
                    f"{entity} {record_id} is immutable; record a correction as a new {entity} "
                    "with supersedes"
                )
            ]
        return []
    if entity == "journal" and old.get("outcome") is not None:
        if changed:
            return [f"journal {record_id} is immutable once outcome is set"]
        return []

    problems = []
    for name in APPEND_ONLY.get(entity, ()):
        problems += _append_only(name, old.get(name) or [], new.get(name) or [])
    for name in SET_ONCE.get(entity, ()):
        if old.get(name) is not None and new.get(name) != old.get(name):
            problems.append(f"{name} is set once and cannot change")
    if entity in STATUS_RULES:
        problems += _status_change(STATUS_RULES[entity], old, new)
    if entity == "experiment":
        problems += _experiment_transition(old, new)
    return problems


def _append_only(name: str, old: list, new: list) -> list[str]:
    if new[: len(old)] != old:
        return [f"{name} is append-only: existing entries cannot be changed or removed"]
    return []


def _status_change(rule: StatusRule, old: dict, new: dict) -> list[str]:
    old_state, new_state = old.get(rule.field), new.get(rule.field)
    if old_state == new_state:
        return []
    problems = []
    if rule.transitions is not None and new_state not in rule.transitions.get(old_state, ()):
        problems.append(f"{rule.field} transition {old_state} -> {new_state} is not allowed")
    if rule.reason_field is None:
        return problems
    reason = new.get(rule.reason_field)
    ref = new.get(rule.ref_field) if rule.ref_field else None
    if not reason:
        problems.append(f"{rule.field} change needs {rule.reason_field}")
    if rule.ref_required and not ref:
        problems.append(f"{rule.field} change needs {rule.ref_field}")
    old_history = old.get(rule.history_field) or []
    new_history = new.get(rule.history_field) or []
    added = new_history[len(old_history) :]
    expected = {"from": old_state, "to": new_state, "reason": reason, "ref": ref}
    if len(added) != 1 or {key: added[0].get(key) for key in expected} != expected:
        problems.append(
            f"{rule.field} change must append one {rule.history_field} entry "
            f"{{from: {old_state}, to: {new_state}, reason, ref}}"
        )
    return problems


def _experiment_transition(old: dict, new: dict) -> list[str]:
    problems = []
    if old.get("status") != "designed":
        for name in EXPERIMENT_DESIGN_FIELDS:
            if old.get(name) != new.get(name):
                problems.append(
                    f"{name} is part of the pre-registered design and is frozen once status "
                    f"leaves designed (status was {old.get('status')})"
                )
    old_binding, new_binding = old.get("candidate_binding"), new.get("candidate_binding")
    if old_binding is not None and new_binding != old_binding:
        problems.append("candidate_binding is set once and cannot change")
    elif old_binding is None and new_binding is not None:
        if new.get("status") != "uploaded":
            problems.append("candidate_binding can only be set in status uploaded")
        if old.get("xp_requests"):
            problems.append("candidate_binding must be set before any XP request")
    old_attempts = (old.get("lifecycle") or {}).get("launch_attempts", 0)
    new_attempts = (new.get("lifecycle") or {}).get("launch_attempts", 0)
    if new_attempts < old_attempts:
        problems.append(
            f"lifecycle.launch_attempts cannot decrease ({old_attempts} -> {new_attempts})"
        )
    return problems
