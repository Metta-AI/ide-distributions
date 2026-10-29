"""RecordStore: create, read, update, and validate records under one workspace root.

Every write goes through ``check_change`` (mutability.py); a write with problems raises
ValueError listing them and leaves the file untouched. Writes are atomic per file. The store
takes no lock: one writer per entity is a workspace rule (System Design §3.7), and runtime
writers to the canonical workspace hold ``canonical.lock`` themselves.
"""

import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml

from ide_research_core.ids import new_id
from ide_research_core.io.docs import read_doc, read_json, write_doc, write_json
from ide_research_core.paths import Layout
from ide_research_core.records.mutability import STATUS_RULES, check_change, move_problems
from ide_research_core.records.registry import (
    REGISTRY,
    Doc,
    archive_dir,
    entity_of_id,
    event_path,
    in_record_dir,
    is_archived_path,
    iter_record_files,
    normalize,
    parse_record_path,
    path_for,
)

Change = tuple[str, Doc | None, Doc | None]  # (path relative to the root, before, after)


class RecordNotFound(LookupError):
    pass


class RecordProblems(ValueError):
    """A write was rejected; ``problems`` lists every rule it broke."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _raise_if(problems: list[str]) -> None:
    if problems:
        raise RecordProblems(problems)


class RecordStore:
    def __init__(self, layout: Layout):
        self.layout = layout

    # --- reading ---

    def path_of(self, id_: str) -> Path:
        """Path of an existing record; RecordNotFound if there is none."""
        try:
            path = path_for(self.layout, id_)
        except LookupError as error:
            raise RecordNotFound(f"record {id_} not found") from error
        if not path.exists():
            raise RecordNotFound(f"record {id_} not found")
        return path

    def load(self, id_: str) -> tuple[dict, str]:
        return self._read(entity_of_id(id_).name, self.path_of(id_))

    def _read(self, entity: str, path: Path) -> Doc:
        if REGISTRY[entity].fmt == "json":
            return normalize(read_json(path)), ""
        meta, body = read_doc(path)
        return normalize(meta), body

    def find(self, entity: str, **eq) -> list[str]:
        """Ids of ``entity`` records whose front matter equals every ``field=value`` given."""
        ids = []
        for path in iter_record_files(self.layout, entity):
            parsed = parse_record_path(self.layout, self._rel(path))
            if parsed is None or parsed[0] != entity:
                continue
            meta, _ = self._read(entity, path)
            if all(meta.get(key) == value for key, value in eq.items()):
                ids.append(parsed[1])
        return sorted(ids)

    # --- writing ---

    def create(self, entity: str, meta: dict, body: str = "", slug: str | None = None) -> str:
        if entity not in REGISTRY:
            raise ValueError(f"unknown entity {entity!r}")
        if "id" in meta:
            raise ValueError("id is assigned by the store; omit it from meta")
        id_ = new_id(entity)
        meta = {"id": id_, **meta}
        _raise_if(check_change(entity, None, (meta, body)))
        if entity == "version_event":
            self.path_of(meta["version"])  # the version must exist
            path = event_path(self.layout, meta["version"], id_)
        else:
            path = path_for(self.layout, id_, slug)
        self._write(entity, path, meta, body)
        return id_

    def update(self, id_: str, patch: dict, body: str | None = None) -> None:
        """Shallow-merge ``patch`` into the front matter; replace the body if given.

        A status change on an entity with a status history (hypothesis, claim, topic) appends
        the history entry automatically from the patched reason and ref fields.
        """
        entity = entity_of_id(id_).name
        path = self.path_of(id_)
        before = self._read(entity, path)
        meta = {**before[0], **patch}
        self._append_status_history(entity, before[0], meta, patch)
        after = (meta, before[1] if body is None else body)
        _raise_if(check_change(entity, before, after))
        if after != before:
            self._write(entity, path, *after)

    def set_status(
        self, id_: str, state: str, reason: str | None = None, ref: str | None = None
    ) -> None:
        entity = entity_of_id(id_).name
        rule = STATUS_RULES.get(entity)
        if rule is None:
            raise ValueError(f"{entity} has no status")
        patch = {rule.field: state}
        if rule.reason_field:
            patch[rule.reason_field] = reason
        if rule.ref_field:
            patch[rule.ref_field] = ref
        self.update(id_, patch)

    def link(self, id_: str, field: str, target) -> None:
        """Append ``target`` to the list field ``field`` (created if absent)."""
        current = self.load(id_)[0].get(field) or []
        if not isinstance(current, list):
            raise RecordProblems([f"{field} is not a list field"])
        self.update(id_, {field: [*current, target]})

    def archive(self, id_: str, reason: str, now: datetime.datetime) -> Path:
        """Move a record into its entity's archive directory, keeping its id, file name, status,
        links and body, and adding ``archived_at`` and ``archive_reason``. Return the new path."""
        entity = entity_of_id(id_).name
        target_dir = archive_dir(self.layout, entity)
        if target_dir is None:
            raise RecordProblems([f"{entity} has no archive"])
        path = self.path_of(id_)
        if path.parent == target_dir:
            raise RecordProblems([f"{entity} {id_} is already archived"])
        before = self._read(entity, path)
        after = ({**before[0], "archived_at": now.isoformat(), "archive_reason": reason}, before[1])
        _raise_if(move_problems(entity, before, after))
        self._write(entity, target_dir / path.name, *after)
        path.unlink()
        return target_dir / path.name

    def add_event(self, version_id: str, kind: Literal["uploaded", "submitted"], data: dict) -> str:
        meta = {"version": version_id, "kind": kind, "recorded_at": _now(), "data": data}
        return self.create("version_event", meta)

    def _append_status_history(self, entity: str, old: dict, new: dict, patch: dict) -> None:
        rule = STATUS_RULES.get(entity)
        if rule is None or rule.history_field is None or rule.history_field in patch:
            return
        if old.get(rule.field) == new.get(rule.field):
            return
        entry = {
            "from": old.get(rule.field),
            "to": new.get(rule.field),
            "reason": new.get(rule.reason_field),
            "ref": new.get(rule.ref_field),
            "at": _now(),
        }
        new[rule.history_field] = [*(old.get(rule.history_field) or []), entry]

    def _write(self, entity: str, path: Path, meta: dict, body: str) -> None:
        if REGISTRY[entity].fmt == "json":
            write_json(path, meta)
        else:
            write_doc(path, meta, body)

    # --- validation ---

    def validate_change(self, rel_path: str, before: Doc | None, after: Doc | None) -> list[str]:
        """Problems with changing the file at ``rel_path`` from ``before`` to ``after``.

        Used by handoff validation on git snapshots. Paths outside record directories are
        not records and yield no problems; a badly named file inside one is a problem.
        """
        parsed = parse_record_path(self.layout, rel_path)
        if parsed is None:
            if in_record_dir(self.layout, rel_path) and after is not None:
                return [f"{rel_path} is not a valid record path"]
            return []
        entity, id_ = parsed
        problems = [
            f"{rel_path}: id {doc[0].get('id')!r} does not match the file name ({id_})"
            for doc in (before, after)
            if doc is not None and doc[0].get("id") != id_
        ]
        return problems + check_change(entity, before, after)

    def validate_changes(self, changes: list[Change]) -> dict[str, list[str]]:
        """{relative path: problems} for a set of file changes (for example one commit's diff).

        An archive move, a record deleted from its directory and added with the same id and file
        name in its archive directory, is judged as one change by ``move_problems`` and reported under the
        archive path. Every other change goes through ``validate_change``.
        """
        moves = self._archive_moves(changes)
        moved_from = {rel for rel, _, _ in moves.values()}
        problems: dict[str, list[str]] = {}
        for rel, before, after in changes:
            if rel in moved_from:
                continue
            if rel in moves:
                _, old_doc, _ = moves[rel]
                entity = parse_record_path(self.layout, rel)[0]
                found = self.validate_change(rel, None, after)  # id matches the file name
                found = list(dict.fromkeys(found + move_problems(entity, old_doc, after)))
            else:
                found = self.validate_change(rel, before, after)
            if found:
                problems[rel] = found
        return problems

    def _archive_moves(self, changes: list[Change]) -> dict[str, Change]:
        """{archive path: the matching deletion} for each archive move among ``changes``."""
        deletions = {}
        for change in changes:
            rel, before, after = change
            parsed = parse_record_path(self.layout, rel)
            is_deletion = before is not None and after is None
            if parsed and is_deletion and not is_archived_path(self.layout, rel):
                deletions[parsed] = change
        moves = {}
        for rel, before, after in changes:
            parsed = parse_record_path(self.layout, rel)
            is_addition = before is None and after is not None
            if is_addition and parsed in deletions and is_archived_path(self.layout, rel):
                same_name = PurePosixPath(rel).name == PurePosixPath(deletions[parsed][0]).name
                if same_name:
                    moves[rel] = deletions[parsed]
        return moves

    def validate(self, id_: str) -> list[str]:
        """Content problems of one stored record."""
        path = self.path_of(id_)
        return self.validate_change(self._rel(path), None, self.load(id_))

    def validate_all(self) -> tuple[int, dict[str, list[str]]]:
        """(records checked, {relative path: problems}) for every file in record directories."""
        checked, problems = 0, {}
        first_path: dict[str, str] = {}  # id -> first file seen (primary directory first)
        for entity in REGISTRY:
            for path in iter_record_files(self.layout, entity):
                rel = self._rel(path)
                parsed = parse_record_path(self.layout, rel)
                if parsed is None:
                    problems[rel] = [f"{rel} is not a valid record path"]
                    continue
                if parsed[0] != entity:
                    continue
                checked += 1
                if parsed[1] in first_path:
                    # e.g. an archive move interrupted between writing the copy and deleting
                    # the original; path_for and the resolver use the primary-directory file.
                    problems[rel] = [
                        f"{rel}: duplicate id {parsed[1]} (also at {first_path[parsed[1]]})"
                    ]
                    continue
                first_path[parsed[1]] = rel
                try:
                    doc = self._read(entity, path)
                except (yaml.YAMLError, ValueError) as error:  # unparseable file: a finding
                    problems[rel] = [f"{rel} cannot be parsed: {error}"]
                    continue
                found = self.validate_change(rel, None, doc)
                if found:
                    problems[rel] = found
        return checked, problems

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.layout.root).as_posix()
