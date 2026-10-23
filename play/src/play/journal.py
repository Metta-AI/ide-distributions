"""The program journal: one JSON file in the program workspace that remembers the user's program.

``journal.json`` holds the program's name, every uploaded version (what changed, its file's
sha256, its practices and their plain summaries, whether it was released) and the task card for
the user's current request. A copy of each uploaded file is kept under ``versions/`` so any two
versions can be compared. The Play tools are its only writers.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

JOURNAL = "journal.json"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Practice(BaseModel):
    request_id: str
    league_id: str
    title: str
    episodes: int
    seats: list[int]
    attempt: int
    requested_at: str
    summary: dict | None = None  # results.summarize, once the games are in


class Release(BaseModel):
    league_id: str
    submission_id: str
    released_at: str


class Version(BaseModel):
    name: str  # the user's name for the program
    platform_name: str  # the policy name on the platform (the name, or the name with a suffix)
    number: int
    sha256: str
    change: str  # what changed, in the user's words
    uploaded_at: str
    practices: list[Practice] = []
    release: Release | None = None

    @property
    def label(self) -> str:
        return f"{self.platform_name}:v{self.number}"

    @property
    def display(self) -> str:
        return f"{self.name} v{self.number}"


class Card(BaseModel):
    key: str
    body: dict  # the Task record body as last written


class Journal(BaseModel):
    name: str | None = None  # the current program's name
    platform_name: str | None = None
    change: str | None = None  # what program_change last changed, not yet uploaded
    versions: list[Version] = []
    card: Card | None = None

    @classmethod
    def load(cls, workspace: Path) -> "Journal":
        path = workspace / JOURNAL
        return cls.model_validate_json(path.read_text()) if path.is_file() else cls()

    def save(self, workspace: Path) -> None:
        write_text(workspace / JOURNAL, self.model_dump_json(indent=2))

    def latest(self, name: str | None = None) -> Version | None:
        mine = [v for v in self.versions if v.name == (name or self.name)]
        return max(mine, key=lambda v: v.number) if mine else None

    def find(self, name: str, number: int) -> Version | None:
        return next((v for v in self.versions if v.name == name and v.number == number), None)

    def find_practice(self, request_id: str) -> tuple[Version, Practice] | None:
        for version in self.versions:
            for practice in version.practices:
                if practice.request_id == request_id:
                    return version, practice
        return None

    def previous_result(self, version: Version) -> tuple[Version, dict] | None:
        """The newest practice summary of an earlier version of the same program."""
        earlier = [v for v in self.versions if v.name == version.name and v.number < version.number]
        for candidate in reversed(earlier):
            for practice in reversed(candidate.practices):
                if practice.summary is not None:
                    return candidate, practice.summary
        return None


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
