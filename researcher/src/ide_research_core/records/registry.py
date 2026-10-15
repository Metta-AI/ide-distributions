"""Entity registry: each record type's id prefix, location, file format, and JSON Schema.

File formats (one record per file):

- ``md``: Markdown with YAML front matter, for prose entities (topic, hypothesis, experiment,
  result, claim, hub, lesson, journal). Front matter is the record; the body is its prose
  (a Result's analysis, a Claim's assertion, a Journal's narrative and Decisions section).
  Files are ``<dir>/<id>--<slug>.md`` or ``<dir>/<id>.md``.
- ``json``: structured records with no prose (version, version_event, evidence), at
  ``versions/<V-id>/version.json``, ``versions/<V-id>/events/<VE-id>.json`` and
  ``evidence/index/<EV-id>.json``. Their Doc body is always ``""``.

Archive. An entity may have a second, archive location (claims: ``kb/archive``). An archived
record keeps its id and file name; lookups (``path_for``, ``parse_record_path``,
``iter_record_files``) search both locations, and new records go to the primary one. The move
itself is ``RecordStore.archive``; ``mutability.move_problems`` judges it.

Schemas live in the package, ``ide_research_core/schemas/entity/<name>.json`` (draft 2020-12). There is no ``lever`` entity:
a closed lever is a refuted mechanism claim.
"""

import datetime
import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from ide_research_core.ids import ENTITY_PREFIXES, parse_id
from ide_research_core.paths import Layout

Doc = tuple[dict, str]

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "entity"
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"


@dataclass(frozen=True)
class EntityType:
    name: str
    prefix: str
    subdir: str  # name of the Layout property holding this entity's directory
    fmt: str  # "md" or "json"
    schema: str  # schema file name under SCHEMA_DIR
    archive_subdir: str | None = None  # Layout property of the archive location, if any


def _entity(name: str, subdir: str, fmt: str, archive_subdir: str | None = None) -> EntityType:
    return EntityType(name, ENTITY_PREFIXES[name], subdir, fmt, f"{name}.json", archive_subdir)


REGISTRY: dict[str, EntityType] = {
    entity.name: entity
    for entity in (
        _entity("topic", "topics", "md"),
        _entity("hypothesis", "hypotheses", "md"),
        _entity("experiment", "experiments", "md"),
        _entity("result", "results", "md"),
        _entity("claim", "claims", "md", archive_subdir="archive"),
        _entity("hub", "hubs", "md"),
        _entity("lesson", "lessons", "md"),
        _entity("journal", "journal", "md"),
        _entity("version", "versions", "json"),
        _entity("version_event", "versions", "json"),
        _entity("evidence", "evidence_index", "json"),
    )
}

_ENTITY_BY_PREFIX = {entity.prefix: entity for entity in REGISTRY.values()}


def entity_of_id(id_: str) -> EntityType:
    """Entity type for an id; ValueError if malformed or not a registered record type."""
    prefix, _ = parse_id(id_)
    if prefix not in _ENTITY_BY_PREFIX:
        raise ValueError(f"{id_!r} is not a record id")
    return _ENTITY_BY_PREFIX[prefix]


def record_dirs(layout: Layout, entity_name: str) -> list[Path]:
    """The entity's directories: its primary one, then its archive (if it has one)."""
    entity = REGISTRY[entity_name]
    subdirs = [entity.subdir, *([entity.archive_subdir] if entity.archive_subdir else [])]
    return [getattr(layout, subdir) for subdir in subdirs]


def archive_dir(layout: Layout, entity_name: str) -> Path | None:
    entity = REGISTRY[entity_name]
    return getattr(layout, entity.archive_subdir) if entity.archive_subdir else None


def is_archived_path(layout: Layout, rel_path: str) -> bool:
    """True if ``rel_path`` is a record file in its entity's archive directory."""
    parsed = parse_record_path(layout, rel_path)
    if parsed is None:
        return False
    directory = archive_dir(layout, parsed[0])
    return directory is not None and (layout.root / rel_path).parent == directory


def _relative_dirs(layout: Layout, entity: EntityType) -> list[str]:
    return [path.relative_to(layout.root).as_posix() for path in record_dirs(layout, entity.name)]


def _path_regex(layout: Layout, entity: EntityType) -> re.Pattern:
    directory = "(?:" + "|".join(re.escape(d) for d in _relative_dirs(layout, entity)) + ")"
    id_group = rf"(?P<id>{entity.prefix}-{_ULID})"
    if entity.name == "version":
        return re.compile(rf"{directory}/{id_group}/version\.json")
    if entity.name == "version_event":
        return re.compile(rf"{directory}/V-{_ULID}/events/{id_group}\.json")
    if entity.fmt == "json":
        return re.compile(rf"{directory}/{id_group}\.json")
    return re.compile(rf"{directory}/{id_group}(?:--{SLUG_PATTERN.pattern})?\.md")


def _glob_pattern(entity: EntityType) -> str:
    if entity.name == "version":
        return "*/version.json"
    if entity.name == "version_event":
        return "*/events/*.json"
    return "*.json" if entity.fmt == "json" else "*.md"


def parse_record_path(layout: Layout, rel_path: str) -> tuple[str, str] | None:
    """(entity name, id) for a record path relative to the layout root, else None."""
    for entity in REGISTRY.values():
        match = _path_regex(layout, entity).fullmatch(rel_path)
        if match:
            return entity.name, match["id"]
    return None


def entity_of_path(layout: Layout, rel_path: str) -> str | None:
    parsed = parse_record_path(layout, rel_path)
    return parsed[0] if parsed else None


def in_record_dir(layout: Layout, rel_path: str) -> bool:
    """True if the path sits where only records belong (a record directory or below)."""
    return any(
        rel_path.startswith(directory + "/")
        for entity in REGISTRY.values()
        for directory in _relative_dirs(layout, entity)
    )


def iter_record_files(layout: Layout, entity_name: str) -> list[Path]:
    """Files in the entity's directories matching its naming glob (not yet checked by name):
    the primary directory's files first, then the archive's."""
    pattern = _glob_pattern(REGISTRY[entity_name])
    return [path for d in record_dirs(layout, entity_name) for path in sorted(d.glob(pattern))]


def path_for(layout: Layout, id_: str, slug: str | None = None) -> Path:
    """Path of record ``id_``. Finds an existing Markdown file regardless of slug; otherwise
    returns where a new file would go (``<id>--<slug>.md`` if a slug is given)."""
    entity = entity_of_id(id_)
    directory = getattr(layout, entity.subdir)
    if entity.name == "version":
        return directory / id_ / "version.json"
    if entity.name == "version_event":
        matches = sorted(directory.glob(f"*/events/{id_}.json"))
        if not matches:
            raise LookupError(f"version event {id_} not found; its version is unknown")
        return matches[0]
    if entity.fmt == "json":
        return directory / f"{id_}.json"
    for location in record_dirs(layout, entity.name):
        existing = sorted([*location.glob(f"{id_}--*.md"), *location.glob(f"{id_}.md")])
        if existing:
            return existing[0]
    if slug is not None:
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValueError(f"invalid slug {slug!r}: use lowercase letters, digits and hyphens")
        return directory / f"{id_}--{slug}.md"
    return directory / f"{id_}.md"


def event_path(layout: Layout, version_id: str, event_id: str) -> Path:
    return path_for(layout, version_id).parent / "events" / f"{event_id}.json"


@functools.cache
def _validator(entity_name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / REGISTRY[entity_name].schema).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def normalize(value):
    """Convert YAML-parsed dates and datetimes to ISO strings so they validate as JSON."""
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def schema_problems(entity_name: str, meta: dict) -> list[str]:
    problems = []
    for error in sorted(_validator(entity_name).iter_errors(normalize(meta)), key=str):
        location = "/".join(str(part) for part in error.absolute_path) or "(record)"
        problems.append(f"{entity_name} schema: {location}: {error.message}")
    return problems
