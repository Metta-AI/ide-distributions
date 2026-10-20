"""The profile ``.env`` as Hermes loads it, and small helpers to read and set its lines.
STDLIB ONLY."""

import re
from collections.abc import Mapping
from pathlib import Path

# Hermes reads the profile .env with python-dotenv's parser (quotes stripped, no quote flag
# kept) and then expands only ``${VAR}`` and ``${VAR:-default}``, in every value, quoted or
# not (``hermes_cli/env_loader._load_dotenv_with_fallback``; python-dotenv ``variables.py``).
# A bare ``$VAR`` stays literal. These are the same rules, ported (python-dotenv is not one
# of our dependencies).
_DOTENV_REFERENCE = re.compile(r"\$\{(?P<name>[^}:]*)(?::-(?P<default>[^}]*))?\}")
BARE_REFERENCE = re.compile(r"\$(?!\{)[A-Za-z_]")


def _dotenv_value(raw: str) -> str:
    """A dotenv value with its quotes (and escapes) or inline comment removed."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return re.sub(r"\s+#.*", "", raw).rstrip()


def dotenv_entries(path: Path) -> list[tuple[str, str]]:
    """``(name, value)`` per assignment line of a dotenv file, values unexpanded."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ")
        key, separator, raw = line.partition("=")
        key = key.strip()
        if separator and key and not key.startswith("#"):
            entries.append((key, _dotenv_value(raw)))
    return entries


def worker_env(env: Mapping[str, str], hermes_home: Path | None) -> dict[str, str]:
    """What a card worker's terminal sees, as near as a cron job can tell: ``env`` with the
    profile ``.env`` laid over it as Hermes loads it (override; ``${VAR}`` and
    ``${VAR:-default}`` expanded against ``env`` and the lines before, in any quoting; a bare
    ``$VAR`` kept literal), so ``PATH=/x:${PATH}`` extends the PATH. Provider keys, which
    Hermes strips from terminals, are not removed here; the host requirements never name
    them."""
    found = dict(env)
    path = Path(hermes_home) / ".env" if hermes_home is not None else None
    if path is None or not path.exists():
        return found

    def resolve(match: re.Match) -> str:
        value = found.get(match.group("name"), match.group("default") or "")
        return value if value is not None else ""

    for key, value in dotenv_entries(path):
        found[key] = _DOTENV_REFERENCE.sub(resolve, value)
    return found


def set_env_lines(path: Path, values: dict[str, str]) -> bool:
    """Set ``KEY=value`` lines in a dotenv file: replace a line for the key in place, else
    append. Other lines are kept as they are. Returns True if the file changed."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    updated = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    updated += [f"{key}={value}" for key, value in remaining.items()]
    if updated == lines:
        return False
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return True


def env_file_value(path: Path, name: str) -> str | None:
    """The value of ``name`` in the dotenv file at ``path`` (quotes stripped), or None."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip("'\"") or None
    return None
