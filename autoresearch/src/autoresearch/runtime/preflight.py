"""Start-of-run checks; a failed preflight pauses the agent (C6, amendments A3, A5, A10).

Checks, in order: the credential's identity (C6), free disk under canonical, XP credits
(unreadable with a Player credential: a warning only, A10), the host dispatcher config (A3)
and the OpenRouter key's remaining credit (A5).

Host config (A3). One gateway per host runs the Kanban dispatcher with the host root
profile's ``kanban.*`` config. When the profile home is ``<root>/profiles/<name>``, preflight
reads ``<root>/config.yaml`` (read only) and fails when ``kanban.default_assignee`` is set
(unassigned cards would run, breaking pause, C7), when ``kanban.dispatch_profiles`` is set and
leaves out our profile, or when ``kanban.dispatch_in_gateway`` is false. A config that cannot
be read or parsed fails too: pause safety depends on it. Any other home layout skips the
check with a warning.

OpenRouter (A5). ``GET https://openrouter.ai/api/v1/key`` with the key; ``limit_remaining``
below ``OPENROUTER_MIN_CREDIT_USD`` fails (Hermes would retry 402s forever). A cron script
under the gateway never receives provider keys (hermes-facts item 7), so the key comes from
the environment or else the profile ``.env``. No key, or ``AUTORESEARCH_SKIP_OPENROUTER_CHECK=1``
(fake-Softmax test runs), skips the check with a warning; a failed request or a response
without a numeric ``limit_remaining`` is a warning too.
The key never appears in a reason or warning.
"""

import json
import shutil
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from autoresearch.softmax_tool.identity import identity_problems
from autoresearch.softmax_tool.protocol import Softmax

EXPIRY_WARNING = timedelta(hours=2)
MIN_FREE_BYTES = 1024**3
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
SKIP_OPENROUTER_ENV = "AUTORESEARCH_SKIP_OPENROUTER_CHECK"
OPENROUTER_MIN_CREDIT_USD = 10.0
HTTP_TIMEOUT_S = 15

# (url, headers) -> response body text; raises on failure.
HttpGet = Callable[[str, dict[str, str]], str]


@dataclass
class PreflightResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    credits: int | None = None
    identity_problems: list[str] = field(default_factory=list)  # also in reasons (signals)


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


def openrouter_problems(
    env: Mapping[str, str], hermes_home: Path | None, http_get: HttpGet
) -> tuple[list[str], list[str]]:
    """(reasons, warnings) from the OpenRouter key's remaining credit (A5)."""
    if env.get(SKIP_OPENROUTER_ENV) == "1":
        return [], [f"OpenRouter credit not checked ({SKIP_OPENROUTER_ENV}=1)"]
    key = env.get(OPENROUTER_KEY_ENV)
    if not key and hermes_home is not None:
        key = env_file_value(Path(hermes_home) / ".env", OPENROUTER_KEY_ENV)
    if not key:
        return [], [f"OpenRouter credit not checked: no {OPENROUTER_KEY_ENV}"]
    try:
        payload = json.loads(http_get(OPENROUTER_KEY_URL, {"Authorization": f"Bearer {key}"}))
        remaining = (payload.get("data") or {}).get("limit_remaining")
        remaining = None if remaining is None else float(remaining)
    except Exception as error:  # noqa: BLE001 - a lookup failure must not pause the agent
        return [], [f"OpenRouter credit unavailable: {type(error).__name__}: {error}"]
    if remaining is not None and remaining < OPENROUTER_MIN_CREDIT_USD:
        reason = (
            f"OpenRouter key has ${remaining:.2f} left, below "
            f"${OPENROUTER_MIN_CREDIT_USD:.0f}: top it up (A5)"
        )
        return [reason], []
    return [], []


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

    try:
        identity = sm.whoami()
    except Exception as error:  # noqa: BLE001 - any failure means the credential cannot be trusted
        result.reasons.append(f"whoami failed: {error}")
        result.ok = False
        return result

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
        if result.credits == 0:
            result.warnings.append("no credits remaining")

    if hermes_home is not None:
        reasons, warnings = host_config_problems(hermes_home)
        result.reasons += reasons
        result.warnings += warnings
    reasons, warnings = openrouter_problems(env, hermes_home, http_get)
    result.reasons += reasons
    result.warnings += warnings

    result.ok = not result.reasons
    return result
