"""The one reference parser every tool accepts (design B.2, C.4).

A reference is a record id in display form (``G-3``, ``T-42#failure``), a platform id
(``ply_…``, ``league_…``, ``div_…``, ``ereq_…``, ``xreq_…``, ``sub_…``, ``cow_…``, a policy version
UUID or a ``name:v3`` label) or a platform URL that carries one of those ids. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RefKind = Literal[
    "record",
    "player",
    "policy_version",
    "policy_label",
    "league",
    "division",
    "episode",
    "xp_request",
    "submission",
    "coworld",
    "url",
    "unknown",
]

RECORD_PREFIXES: dict[str, str] = {
    "G": "goal",
    "T": "task",
    "V": "version",
    "C": "claim",
    "X": "experiment",
    "D": "directive",
    "I": "intent",
    "N": "notice",
}

RECORD_PREFIXES_BY_KIND: dict[str, str] = {kind: prefix for prefix, kind in RECORD_PREFIXES.items()}

_RECORD = re.compile(
    r"^(?P<prefix>[A-Z])-(?P<number>[1-9][0-9]*)(?:#(?P<fragment>[A-Za-z0-9_.-]+))?$"
)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LABEL = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9 ._-]*):v(?P<version>[1-9][0-9]*)$")
_PLATFORM_PREFIXES: dict[str, RefKind] = {
    "ply_": "player",
    "league_": "league",
    "div_": "division",
    "ereq_": "episode",
    "xreq_": "xp_request",
    "sub_": "submission",
    "cow_": "coworld",
}
_PLATFORM_ID = re.compile(r"\b((?:ply|league|div|ereq|xreq|sub|cow)_[A-Za-z0-9-]+)")
_URL = re.compile(r"^https?://", re.IGNORECASE)
_UUID_IN_TEXT = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_RECORD_IN_TEXT = re.compile(r"\b([A-Z]-[1-9][0-9]*(?:#[A-Za-z0-9_.-]+)?)\b")
_TOKEN = re.compile(r"\S+")


@dataclass(frozen=True)
class Ref:
    kind: RefKind
    value: str
    record_kind: str | None = None
    number: int | None = None
    fragment: str | None = None
    display: str | None = None

    @property
    def is_record(self) -> bool:
        return self.kind == "record"


def parse(text: str) -> Ref:
    """Parse one reference. Unknown text comes back as ``kind="unknown"``, never an exception."""
    token = text.strip()
    match = _RECORD.match(token)
    if match and match["prefix"] in RECORD_PREFIXES:
        number = int(match["number"])
        return Ref(
            kind="record",
            value=f"{match['prefix']}-{number}",
            record_kind=RECORD_PREFIXES[match["prefix"]],
            number=number,
            fragment=match["fragment"],
            display=token,
        )
    for prefix, kind in _PLATFORM_PREFIXES.items():
        if token.startswith(prefix) and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            return Ref(kind=kind, value=token, display=token)
    if _UUID.match(token):
        return Ref(kind="policy_version", value=token.lower(), display=token)
    label = _LABEL.match(token)
    if label:
        return Ref(kind="policy_label", value=token, display=token)
    if _URL.match(token):
        return _parse_url(token)
    return Ref(kind="unknown", value=token, display=token)


def _parse_url(url: str) -> Ref:
    """A platform URL is reduced to the strongest id it carries; otherwise it stays a URL."""
    ids = _PLATFORM_ID.findall(url)
    for platform_id in ids:
        inner = parse(platform_id)
        if inner.kind != "unknown":
            return Ref(kind=inner.kind, value=inner.value, display=url)
    uuid = _UUID_IN_TEXT.search(url)
    if uuid:
        return Ref(kind="policy_version", value=uuid.group(0).lower(), display=url)
    return Ref(kind="url", value=url, display=url)


def parse_many(text: str) -> list[Ref]:
    """Every reference found in free text, in order of appearance, without duplicates."""
    found: list[Ref] = []
    seen: set[tuple[str, str]] = set()

    def add(ref: Ref) -> None:
        key = (ref.kind, ref.value)
        if ref.kind != "unknown" and key not in seen:
            seen.add(key)
            found.append(ref)

    for token in _TOKEN.findall(text):
        cleaned = token.strip("()[]{}<>,;:'\"`.")
        if _URL.match(cleaned):
            add(parse(cleaned))
    for match in _RECORD_IN_TEXT.finditer(text):
        add(parse(match.group(1)))
    for match in _PLATFORM_ID.finditer(text):
        add(parse(match.group(1)))
    for match in _UUID_IN_TEXT.finditer(text):
        add(parse(match.group(0)))
    for token in _TOKEN.findall(text):
        cleaned = token.strip("()[]{}<>,;:'\"`.")
        if _LABEL.match(cleaned):
            add(parse(cleaned))
    return found


def resolve_record(store, ref: Ref | str) -> dict:
    """The record a reference names, or a ``NotFound`` error listing the problem."""
    from research_core.store import registry
    from research_core.store.errors import NotFound, StoreError

    if isinstance(ref, str):
        ref = parse(ref)
    if ref.kind != "record" or ref.record_kind is None or ref.number is None:
        raise StoreError([f"{ref.display or ref.value} is not a record reference"])
    spec = registry.RECORDS.get(ref.record_kind)
    if spec is None:
        raise StoreError([f"{ref.value}: no record kind for prefix {ref.value[0]}"])
    row = store.one(f"SELECT * FROM {spec.table} WHERE id = ?", (ref.number,))
    if row is None:
        raise NotFound([f"{ref.value} does not exist"])
    return dict(row)
