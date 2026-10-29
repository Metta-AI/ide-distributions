"""The evidence type registry (design B.7): reference formats, validators, inspectors, skills.

Every evidence link on a claim has a type from this registry. A type names how its reference
is written (``ref_format``, quoted back in refusals), checks one (``validate`` returns the
problems), can show what the evidence says (``inspect``) and points at the skill or tool for
reading it in depth (``skill``). The record types (``claim``, ``experiment``, ``version``) also
``resolve`` against the store: the record must exist, and their inspector returns its summary.
Inspectors that need the workspace or git (``code``, ``doc``, ``measurement``, ...) are wired by
the tools layer and are ``None`` here.

The profile and client types are registered below; a mixin adds game types with
``register_type``. ``replay`` is registered here as well so its format is checked everywhere,
even before a mixin wires its inspector. Stdlib only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from research_core import refs
from research_core.store._common import display_id
from research_core.store.errors import NotFound

if TYPE_CHECKING:
    from research_core.store.db import Store

Validator = Callable[[str], list[str]]
Resolver = Callable[["Store", str], list[str]]
Inspector = Callable[[Any, str], dict[str, Any]]


@dataclass(frozen=True)
class EvidenceType:
    name: str
    ref_format: str
    validate: Validator
    skill: str
    inspect: Inspector | None = None
    resolve: Resolver | None = None


_SHA = r"[0-9a-fA-F]{7,40}"
_CODE = re.compile(rf"^(?P<path>\S+?):(?P<start>[0-9]+)-(?P<end>[0-9]+)@(?P<sha>{_SHA})$")
_DOC_RETRIEVED = re.compile(r"^\S+ \(retrieved [0-9]{4}-[0-9]{2}-[0-9]{2}\): \S", re.DOTALL)
_TRANSCRIPT = re.compile(r"^session:(?P<session>[^/\s]+)/(?P<message>[^#\s]+)#[0-9a-fA-F]{64}$")
_MEASUREMENT = re.compile(rf"^(?P<command>\S.*?) @(?P<sha>{_SHA}) -> (?P<output>\S.*)$")
_REPLAY = re.compile(
    r"^episode:(?P<episode>[^/\s]+)(?:/seat (?P<seat>[0-9]+))?(?:/phase (?P<phase>[^/\s]+))?"
    r" xp:(?P<request>\S+)$"
)
_ANNOTATION = re.compile(r"^annotation:\S+$")


def _format_problem(ref: str, ref_format: str) -> list[str]:
    return [f"{ref!r} does not match {ref_format}"]


def _blank(ref: object) -> bool:
    return not isinstance(ref, str) or not ref.strip()


def _pattern_validator(pattern: re.Pattern[str], ref_format: str) -> Validator:
    def validate(ref: str) -> list[str]:
        if _blank(ref) or pattern.match(ref) is None:
            return _format_problem(ref, ref_format)
        return []

    return validate


def _validate_code(ref: str) -> list[str]:
    match = None if _blank(ref) else _CODE.match(ref)
    if match is None:
        return _format_problem(ref, CODE_FORMAT)
    problems: list[str] = []
    start, end = int(match["start"]), int(match["end"])
    if start < 1:
        problems.append(f"{ref!r}: line numbers start at 1")
    if start > end:
        problems.append(f"{ref!r}: the line range {start}-{end} is backwards")
    return problems


def _validate_doc(ref: str) -> list[str]:
    if _blank(ref):
        return _format_problem(ref, DOC_FORMAT)
    if _DOC_RETRIEVED.match(ref):
        return []
    document, sep, section = ref.partition("#")
    if sep and document.strip() and section.strip():
        return []
    return _format_problem(ref, DOC_FORMAT)


def _record_validator(record_kind: str, ref_format: str) -> Validator:
    def validate(ref: str) -> list[str]:
        if _blank(ref):
            return _format_problem(ref, ref_format)
        parsed = refs.parse(ref)
        if not parsed.is_record or parsed.record_kind != record_kind:
            return _format_problem(ref, ref_format)
        return []

    return validate


def _record_resolver(table: str, prefix: str) -> Resolver:
    def resolve(store: Store, ref: str) -> list[str]:
        number = refs.parse(ref).number
        if store.one(f"SELECT id FROM {table} WHERE id = ?", (number,)) is None:
            return [f"{display_id(prefix, number)} does not exist"]
        return []

    return resolve


def _record_inspector(table: str, prefix: str) -> Inspector:
    def inspect(store: Store, ref: str) -> dict[str, Any]:
        number = refs.parse(ref).number
        row = store.one(f"SELECT * FROM {table} WHERE id = ?", (number,))
        if row is None:
            raise NotFound([f"{display_id(prefix, number)} does not exist"])
        summary: dict[str, Any] = {"id": display_id(prefix, number), "summary": row["summary"]}
        if "status" in row.keys():
            summary["status"] = row["status"]
        return summary

    return inspect


def _record_type(name: str, table: str, prefix: str, skill: str) -> EvidenceType:
    ref_format = f"{prefix}-<n>"
    return EvidenceType(
        name=name,
        ref_format=ref_format,
        validate=_record_validator(name, ref_format),
        skill=skill,
        inspect=_record_inspector(table, prefix),
        resolve=_record_resolver(table, prefix),
    )


CODE_FORMAT = "<path>:<a>-<b>@<sha>"
DOC_FORMAT = "<repo-or-url>#<section> or <url> (retrieved <date>): <quote>"
TRANSCRIPT_FORMAT = "session:<id>/<message id>#<sha256 of the quote>"
MEASUREMENT_FORMAT = "<command> @<sha> -> <stored output path or pin id>"
REPLAY_FORMAT = "episode:<id>[/seat <n>][/phase <p>] xp:<request id>"
ANNOTATION_FORMAT = "annotation:<id>"

EVIDENCE_TYPES: dict[str, EvidenceType] = {
    kind.name: kind
    for kind in (
        _record_type("claim", "claims", "C", "claim"),
        EvidenceType(
            name="code",
            ref_format=CODE_FORMAT,
            validate=_validate_code,
            skill="code-citations",
        ),
        EvidenceType(
            name="doc",
            ref_format=DOC_FORMAT,
            validate=_validate_doc,
            skill="documents",
        ),
        EvidenceType(
            name="transcript",
            ref_format=TRANSCRIPT_FORMAT,
            validate=_pattern_validator(_TRANSCRIPT, TRANSCRIPT_FORMAT),
            skill="session-search",
        ),
        EvidenceType(
            name="measurement",
            ref_format=MEASUREMENT_FORMAT,
            validate=_pattern_validator(_MEASUREMENT, MEASUREMENT_FORMAT),
            skill="measurements",
        ),
        _record_type("experiment", "experiments", "X", "experiment"),
        _record_type("version", "versions", "V", "version"),
        EvidenceType(
            name="replay",
            ref_format=REPLAY_FORMAT,
            validate=_pattern_validator(_REPLAY, REPLAY_FORMAT),
            skill="replay",
        ),
        EvidenceType(
            name="annotation",
            ref_format=ANNOTATION_FORMAT,
            validate=_pattern_validator(_ANNOTATION, ANNOTATION_FORMAT),
            skill="annotations",
        ),
    )
}


def register_type(evidence_type: EvidenceType) -> None:
    """Add a type (a mixin's game type, a client's type). A name already registered is an error:
    a mixin that shadows a profile type would change what existing evidence means."""
    if evidence_type.name in EVIDENCE_TYPES:
        raise ValueError(f"evidence type {evidence_type.name!r} is already registered")
    EVIDENCE_TYPES[evidence_type.name] = evidence_type


def check_ref(store: Store, type_name: object, ref: object) -> list[str]:
    """Every problem with one evidence link: an unknown type, a malformed reference, or a
    referenced record that does not exist. Empty when the link is good."""
    kind = EVIDENCE_TYPES.get(type_name) if isinstance(type_name, str) else None
    if kind is None:
        known = ", ".join(sorted(EVIDENCE_TYPES))
        return [f"evidence type {type_name!r} is not registered (known: {known})"]
    problems = kind.validate(ref)  # type: ignore[arg-type]
    if problems or kind.resolve is None:
        return problems
    return kind.resolve(store, ref)  # type: ignore[arg-type]
