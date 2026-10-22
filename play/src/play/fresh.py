"""``fresh_start``: forget the user, so the next conversation is a first meeting again.

Runs in the Hermes process (stdlib only). It empties exactly the two files of Hermes's built-in
memory in this profile, ``memories/MEMORY.md`` (the cogent's own notes) and ``memories/USER.md``
(its notes about the user). The workspace, programs, credentials, Kanban board and the other
profiles are never touched. The running conversation keeps the notes it started with; the next
conversation loads the empty ones.
"""

from pathlib import Path

NOTES = ("MEMORY.md", "USER.md")


def clear_notes(home: Path) -> dict:
    memories = Path(home) / "memories"
    cleared = []
    for name in NOTES:
        path = memories / name
        if path.is_file() and path.read_text(encoding="utf-8").strip():
            path.write_text("", encoding="utf-8")
            cleared.append(name)
    return {"cleared": cleared, "note": "The next conversation is a first meeting."}
