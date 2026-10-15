"""Typed, sortable entity ids: ``<PREFIX>-<ULID>`` (e.g. ``C-01K6Z3V8QX...``)."""

import re

from ulid import ULID

ENTITY_PREFIXES = {
    "topic": "T",
    "hypothesis": "H",
    "experiment": "E",
    "result": "R",
    "version": "V",
    "claim": "C",
    "hub": "HUB",
    "lesson": "L",
    "journal": "J",
    "message": "M",
    "inbound": "I",
    "card_request": "Q",
    "evidence": "EV",
    "version_event": "VE",
}

_KNOWN_PREFIXES = frozenset(ENTITY_PREFIXES.values())
_ULID_PATTERN = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
SHORT_SUFFIX_LENGTH = 6


def new_id(entity: str) -> str:
    if entity not in ENTITY_PREFIXES:
        raise ValueError(f"unknown entity type: {entity!r}")
    return f"{ENTITY_PREFIXES[entity]}-{ULID()}"


def parse_id(value: str) -> tuple[str, str]:
    """Split an id into (prefix, ulid); raise ValueError if it is malformed."""
    prefix, separator, ulid = value.rpartition("-")
    if not separator or prefix not in _KNOWN_PREFIXES:
        raise ValueError(f"unknown id prefix in {value!r}")
    if not _ULID_PATTERN.fullmatch(ulid):
        raise ValueError(f"malformed ULID in {value!r}")
    return prefix, ulid


def short(value: str) -> str:
    """Human-friendly form: the prefix plus the last 6 characters of the ULID."""
    prefix, ulid = parse_id(value)
    return f"{prefix}-{ulid[-SHORT_SUFFIX_LENGTH:]}"
