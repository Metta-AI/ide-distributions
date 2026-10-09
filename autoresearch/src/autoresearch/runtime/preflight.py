"""Start-of-run checks; a failed preflight pauses the agent (C6, amendments A3, A5, A10).

Checks, in order: the credential's identity (C6), free disk under canonical, XP credits
(recorded in ``credits``; read as the Player, A10; a failed read or a zero balance is a
warning only: xp-poll relaunches credit-denied experiments when the balance returns), the host
dispatcher config (A3), a stale ``AUTORESEARCH_QUEUE`` in the profile ``.env`` (a warning: the
option was removed, A13), and the OpenRouter key's remaining credit (A5).
``host_requirement_problems`` (the game's declared host env and tools, as workers see them)
is not part of ``preflight``: queue-keeper runs it next and only warns, because a missing
build tool stops builds, not the agent.

Identity. A failed ``whoami`` pauses at once only when Softmax refused the credential
(``CredentialRejected``) or the credential is missing or malformed (``CredentialError``); an
answered ``whoami`` that is not the expected Player fails too (``identity_problems``). Any
other ``whoami`` failure (network error, timeout, 5xx) says nothing about the credential: the
result is ``skipped`` (queue-keeper does nothing else this run, and does not pause) with a
warning, and the count of consecutive such failures is kept in
``.supervisor/runtime/whoami_failures.json`` under canonical (an unreadable file counts as
none). At ``WHOAMI_FAILURE_LIMIT`` in
a row the run fails, with ``pause_key`` naming the streak (``whoami-unavailable:<first
failure>``) so the pause sends one help request per streak. A ``whoami`` that answers resets
the count. Mutating calls do not depend on this: they check ``whoami`` themselves and refuse
on any failure (C6).

Host config (A3). One gateway per host runs the Kanban dispatcher with the host root
profile's ``kanban.*`` config. When the profile home is ``<root>/profiles/<name>``, preflight
reads ``<root>/config.yaml`` (read only) and fails when ``kanban.default_assignee`` is set
(unassigned cards would run, breaking pause, C7), when ``kanban.dispatch_profiles`` is set and
leaves out our profile, or when ``kanban.dispatch_in_gateway`` is false. A config that cannot
be read or parsed fails too: pause safety depends on it. Any other home layout skips the
check with a warning.

OpenRouter (A5). ``GET https://openrouter.ai/api/v1/key`` with the key; ``limit_remaining``
below the credit floor fails (Hermes would retry 402s forever). The floor is
``AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD``, default ``OPENROUTER_MIN_CREDIT_USD`` ($10; a
fresh $10 key is not below it); a small key can set a lower one. A value that is not a finite
non-negative number falls back to the default with a warning. A cron script
under the gateway never receives provider keys (hermes-facts item 7), so the key comes from
the environment or else the profile ``.env``. No key, or ``AUTORESEARCH_SKIP_OPENROUTER_CHECK=1``
(fake-Softmax test runs), skips the check with a warning; a failed request or a response
without a numeric ``limit_remaining`` is a warning too.
The key never appears in a reason or warning.
"""

import json
import math
import re
import shutil
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from autoresearch.io.docs import read_json, write_json
from autoresearch.softmax_tool.identity import CredentialRejected, identity_problems
from autoresearch.softmax_tool.platform_cli import CredentialError
from autoresearch.softmax_tool.protocol import Softmax

EXPIRY_WARNING = timedelta(hours=2)
MIN_FREE_BYTES = 1024**3
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
SKIP_OPENROUTER_ENV = "AUTORESEARCH_SKIP_OPENROUTER_CHECK"
MIN_CREDIT_ENV = "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD"
OPENROUTER_MIN_CREDIT_USD = 10.0  # default floor (A5)
HTTP_TIMEOUT_S = 15
STALE_QUEUE_ENV = "AUTORESEARCH_QUEUE"  # removed installer setting (A13), still a test hook
WHOAMI_FAILURE_LIMIT = 3
WHOAMI_FAILURES = Path(".supervisor") / "runtime" / "whoami_failures.json"

# (url, headers) -> response body text; raises on failure.
HttpGet = Callable[[str, dict[str, str]], str]


@dataclass
class PreflightResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    credits: float | None = None
    identity_problems: list[str] = field(default_factory=list)  # also in reasons (signals)
    skipped: bool = False  # whoami unavailable this run: do nothing else, do not pause
    pause_key: str | None = None  # the help request's runtime_key when it is not per pause


def _urllib_get(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read().decode("utf-8")


def host_root(hermes_home: Path) -> Path | None:
    """The host root of a profile home ``<root>/profiles/<name>``, else None."""
    home = Path(hermes_home)
    return home.parent.parent if home.parent.name == "profiles" else None


def host_config_problems(hermes_home: Path) -> tuple[list[str], list[str]]:
    """(reasons, warnings) from the host root config (A3); see the module docstring."""
    root = host_root(hermes_home)
    if root is None:
        return [], [f"host config not checked: {hermes_home} is not <root>/profiles/<name>"]
    path = root / "config.yaml"
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        return [f"host config {path} cannot be read: {type(error).__name__}: {error}"], []
    config = config or {}
    if not isinstance(config, dict) or not isinstance(config.get("kanban") or {}, dict):
        return [f"host config {path} is not a mapping with a kanban mapping"], []
    kanban = config.get("kanban") or {}
    profile = Path(hermes_home).name
    reasons = []
    if kanban.get("default_assignee"):
        reasons.append(
            f"host kanban.default_assignee is {kanban['default_assignee']!r} in {path}: "
            "unassigned cards would run, so pause cannot hold (C7); unset it"
        )
    profiles = kanban.get("dispatch_profiles")
    if isinstance(profiles, str):
        profiles = [name.strip() for name in profiles.split(",") if name.strip()]
    if profiles and profile not in profiles:
        reasons.append(
            f"host kanban.dispatch_profiles {profiles} in {path} leaves out {profile!r}: "
            "our cards would never run"
        )
    if kanban.get("dispatch_in_gateway") is False:
        reasons.append(
            f"host kanban.dispatch_in_gateway is false in {path}: no dispatcher runs our cards"
        )
    return reasons, []


def env_file_value(path: Path, name: str) -> str | None:
    """The value of ``name`` in the dotenv file at ``path`` (quotes stripped), or None."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip("'\"") or None
    return None


# Hermes reads the profile .env with python-dotenv's parser (quotes stripped, no quote flag
# kept) and then expands only ``${VAR}`` and ``${VAR:-default}``, in every value, quoted or
# not (``hermes_cli/env_loader._load_dotenv_with_fallback``; python-dotenv ``variables.py``).
# A bare ``$VAR`` stays literal. These are the same rules, ported (python-dotenv is not one
# of our dependencies).
_DOTENV_REFERENCE = re.compile(r"\$\{(?P<name>[^}:]*)(?::-(?P<default>[^}]*))?\}")
_BARE_REFERENCE = re.compile(r"\$(?!\{)[A-Za-z_]")


def _dotenv_value(raw: str) -> str:
    """A dotenv value with its quotes (and escapes) or inline comment removed."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return re.sub(r"\s+#.*", "", raw).rstrip()


def _dotenv_entries(path: Path) -> list[tuple[str, str]]:
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

    for key, value in _dotenv_entries(path):
        found[key] = _DOTENV_REFERENCE.sub(resolve, value)
    return found


def host_requirement_problems(
    env_names: Iterable[str],
    tools: Iterable[str],
    env: Mapping[str, str],
    hermes_home: Path | None,
) -> list[str]:
    """What workers lack of a game's ``host_requirements`` (``Game.host_env``,
    ``Game.host_tools``), one plain line each: a variable that is unset (or names an absolute
    path that does not exist), a tool that is not on the workers' PATH, or a bare ``$VAR`` in
    one of these .env values (or ``PATH``), which Hermes leaves literal."""
    env_names, tools = list(env_names), list(tools)
    seen = worker_env(env, hermes_home)
    problems = []
    path = Path(hermes_home) / ".env" if hermes_home is not None else None
    checked = set(env_names) | ({"PATH"} if tools else set())
    for key, value in _dotenv_entries(path) if path is not None and path.exists() else []:
        if key in checked and _BARE_REFERENCE.search(value):
            problems.append(
                f"{key} in the profile .env has a bare $ reference ({value}), which Hermes "
                "leaves as literal text: write ${NAME} with braces"
            )
    for name in env_names:
        value = (seen.get(name) or "").strip()
        if not value:
            problems.append(f"{name} is not set in the profile .env")
        elif value.startswith("/") and not Path(value).exists():
            problems.append(f"{name} is {value}, which does not exist")
    for tool in tools:
        if shutil.which(tool, path=seen.get("PATH", "")) is None:
            problems.append(f"{tool} is not on the workers' PATH")
    return problems


def credit_floor(env: Mapping[str, str]) -> tuple[float, list[str]]:
    """The OpenRouter credit floor below which preflight pauses, and a warning when the
    setting is unusable (then the default applies)."""
    raw = env.get(MIN_CREDIT_ENV)
    if not raw:
        return OPENROUTER_MIN_CREDIT_USD, []
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    # NaN would never pause (every comparison is false); infinity would always pause.
    if not math.isfinite(value) or value < 0:
        warning = (
            f"{MIN_CREDIT_ENV}={raw!r} is not a non-negative number; "
            f"using ${OPENROUTER_MIN_CREDIT_USD:g}"
        )
        return OPENROUTER_MIN_CREDIT_USD, [warning]
    return value, []


def fetch_key_data(
    env: Mapping[str, str], hermes_home: Path | None, http_get: HttpGet
) -> tuple[dict | None, list[str]]:
    """(the ``data`` object of ``GET /api/v1/key``, warnings). None, with a warning, when the
    check is skipped, there is no key, or the lookup fails. The key never appears in a warning."""
    if env.get(SKIP_OPENROUTER_ENV) == "1":
        return None, [f"OpenRouter credit not checked ({SKIP_OPENROUTER_ENV}=1)"]
    key = env.get(OPENROUTER_KEY_ENV)
    if not key and hermes_home is not None:
        key = env_file_value(Path(hermes_home) / ".env", OPENROUTER_KEY_ENV)
    if not key:
        return None, [f"OpenRouter credit not checked: no {OPENROUTER_KEY_ENV}"]
    try:
        payload = json.loads(http_get(OPENROUTER_KEY_URL, {"Authorization": f"Bearer {key}"}))
        return dict(payload.get("data") or {}), []
    except Exception as error:  # noqa: BLE001 - a lookup failure must not pause the agent
        return None, [f"OpenRouter credit unavailable: {type(error).__name__}: {error}"]


def openrouter_problems(
    env: Mapping[str, str], hermes_home: Path | None, http_get: HttpGet
) -> tuple[list[str], list[str]]:
    """(reasons, warnings) from the OpenRouter key's remaining credit (A5)."""
    floor, warnings = credit_floor(env)
    data, lookup_warnings = fetch_key_data(env, hermes_home, http_get)
    warnings = [*warnings, *lookup_warnings]
    if data is None:
        return [], warnings
    try:
        remaining = data.get("limit_remaining")
        remaining = None if remaining is None else float(remaining)
    except (TypeError, ValueError, OverflowError) as error:
        return [], [*warnings, f"OpenRouter credit unavailable: {type(error).__name__}: {error}"]
    if remaining is not None and remaining < floor:
        reason = f"OpenRouter key has ${remaining:.2f} left, below ${floor:g}: top it up (A5)"
        return [reason], warnings
    return [], warnings


def _count_whoami_failure(path: Path, now: datetime) -> tuple[int, str]:
    """Add one to the consecutive whoami failure count; return (count, first failure time).
    A missing or unreadable file counts as no failures yet."""
    try:
        streak = read_json(path)
    except (OSError, ValueError):
        streak = None
    valid = (
        isinstance(streak, dict)
        and isinstance(streak.get("count"), int)
        and isinstance(streak.get("since"), str)
    )
    if not valid:
        streak = {"count": 0, "since": now.isoformat()}
    streak["count"] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, streak)
    return streak["count"], streak["since"]


def preflight(
    env: Mapping[str, str],
    sm: Softmax,
    canonical: Path,
    now: datetime,
    disk_free: Callable = shutil.disk_usage,
    *,
    hermes_home: Path | None = None,
    http_get: HttpGet = _urllib_get,
) -> PreflightResult:
    """Run every check (module docstring). ``hermes_home`` (the profile home) enables the
    host config check and the ``.env`` key lookup; without it the host check is skipped."""
    result = PreflightResult(ok=True)
    expected_player = env.get("AUTORESEARCH_PLAYER_ID", "")

    failures_path = Path(canonical) / WHOAMI_FAILURES
    try:
        identity = sm.whoami()
    except (CredentialRejected, CredentialError) as error:
        failures_path.unlink(missing_ok=True)
        result.reasons.append(f"whoami failed: {error}")
        result.ok = False
        return result
    except Exception as error:  # noqa: BLE001 - not a verdict on the credential (docstring)
        count, since = _count_whoami_failure(failures_path, now)
        result.ok = False
        if count < WHOAMI_FAILURE_LIMIT:
            result.skipped = True
            result.warnings.append(
                f"whoami failed ({count} in a row; pausing at {WHOAMI_FAILURE_LIMIT}): {error}"
            )
        else:
            result.reasons.append(f"whoami failed {count} times in a row since {since}: {error}")
            result.pause_key = f"whoami-unavailable:{since}"
        return result
    failures_path.unlink(missing_ok=True)

    result.identity_problems = identity_problems(identity, expected_player, now)
    result.reasons.extend(result.identity_problems)
    expires_at = identity.expires_at  # None for a cga_ agent token: nothing to warn about
    if expires_at is not None and now < expires_at and expires_at - now < EXPIRY_WARNING:
        result.warnings.append(f"credential expires at {expires_at.isoformat()}")

    try:
        free_bytes = disk_free(canonical).free
    except OSError as error:
        result.reasons.append(f"cannot check disk space under {canonical}: {error}")
        free_bytes = MIN_FREE_BYTES
    if free_bytes < MIN_FREE_BYTES:
        result.reasons.append(
            f"only {free_bytes // 1024**2} MB free under {canonical}; need 1024 MB"
        )

    try:
        result.credits = sm.credits()
    except Exception as error:  # noqa: BLE001 - credit lookup failure is non-fatal
        result.warnings.append(f"credits unavailable: {error}")
    else:
        if result.credits <= 0:
            result.warnings.append("no credits remaining")

    if hermes_home is not None:
        reasons, warnings = host_config_problems(hermes_home)
        result.reasons += reasons
        result.warnings += warnings
        if env_file_value(Path(hermes_home) / ".env", STALE_QUEUE_ENV):
            result.warnings.append(
                f"{STALE_QUEUE_ENV} is set in the profile .env: remove it. It is a test hook "
                "(A13); worker terminals read the whole .env and would use a file queue while "
                "queue-keeper uses Kanban"
            )
    reasons, warnings = openrouter_problems(env, hermes_home, http_get)
    result.reasons += reasons
    result.warnings += warnings

    result.ok = not result.reasons
    return result
