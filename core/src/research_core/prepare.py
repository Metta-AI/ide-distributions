"""Profile preparation, run by the host after ``hermes profile install`` or ``update``.

``<profile>/scripts/prepare.py`` loads this module from the release checkout and calls
``prepare``. It runs under Hermes's own interpreter, so it is stdlib only (no PyYAML): the
profile config is read and written through ``hermes config get|set|unset`` and the cron store
is edited as JSON. In order, ``prepare``:

1. records the core source path in ``<profile>/local/core-path.txt`` for the plugin shim;
2. re-applies the protected config keys, removes the retired ones and points
   ``skills.external_dirs`` at the core skills (the active Coworld's mixin skills and
   ``skills.trusted_project_dirs`` follow once the Coworld registry exists);
3. (Orchestrator) reconciles the watcher cron job with the shipped definition and, on the first
   preparation with ``activate_watcher``, resumes it;
4. builds or checks the workspace environment under a file lock;
5. writes ``<profile>/.softmax-prepared`` and prints one JSON summary line.

Config comes before the (slow, network-bound) environment build so that a failed or timed-out
``uv sync`` still leaves the profile's gates in their protected state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from research_core import layout
from research_core.layout import Workspace
from research_core.locks import file_lock

ROLES = ("orchestrator", "researcher")
ROLE_PLACEHOLDER = "<role>"
WATCHER_JOB_ID = "watcher"
PREPARED_MARKER = ".softmax-prepared"
CORE_PATH_FILE = Path("local") / "core-path.txt"
LOCK_STAMP = ".lock-sha"

# Config keys the profile machinery relies on, re-applied at every preparation so that an edit
# by the owner or an agent never switches a gate back on. ``<role>`` is substituted.
PROTECTED_KEYS: dict[str, Any] = {
    "approvals.mode": "off",
    "approvals.cron_mode": "approve",
    "approvals.single_query_mode": "approve",
    "approvals.unattended_mode": "approve",
    "memory.memory_enabled": False,
    "memory.user_profile_enabled": False,
    "tools.tool_search.enabled": "off",
    "cron.catch_up_missed": True,
    "plugins.enabled": ["<role>"],
    "platform_toolsets.api_server": ["hermes-api-server", "ide-<role>"],
    "known_plugin_toolsets.cron": ["ide-<role>"],
}

# Orchestrator chats are long: compaction protects more of the head and tail and compacts less
# aggressively, so the user's instructions stay word for word (design F.5).
ORCHESTRATOR_PROTECTED_KEYS: dict[str, Any] = {
    "compression.protect_first_n": 6,
    "compression.protect_last_n": 30,
    "compression.threshold": 0.6,
    "compression.target_ratio": 0.3,
}

# Keys of earlier distributions that must not survive an update. ``key[element]`` means: drop
# that element from the list at ``key`` if it is present.
RETIRED_KEYS: tuple[str, ...] = (
    "approvals.deny",
    "agent.max_turns",
    "agent.run_budget_seconds",
    "timeouts",
    "kanban",
    "skills.external_dirs[../../games]",
)

# Shipped values that are not protected: a retained value is replaced only when it is unset or
# equals a known earlier default, so a shipped change reaches updated profiles while a value the
# owner chose survives (amendment A7.4).
SHIPPED_VALUES: dict[str, Any] = {
    "model.default": "moonshotai/kimi-k3",
    "model.provider": "openrouter",
    "terminal.timeout": 1800,
    "updates.check": False,
    "curator.prune_builtins": False,
}
EARLIER_DEFAULTS: dict[str, set[Any]] = {
    "terminal.timeout": {180},
    "model.default": {"anthropic/claude-sonnet-4", "openai/gpt-4o"},
}
FALLBACK_PROVIDERS: list[dict[str, str]] = [{"provider": "openrouter", "model": "z-ai/glm-5.3"}]
# Lists the shipped config contributes to: the union of the shipped and retained entries is kept.
ENV_PASSTHROUGH: dict[str, list[str]] = {
    "common": [
        "SSL_CERT_FILE",
        "SOFTMAX_PLAYER_ID",
        "SOFTMAX_PLAYER_CREDENTIAL",
        "COGAMES_API_URL",
        "SOFTMAX_CONFIG_DIR",
        "IDE_WORKSPACE",
        "IDE_WORKSPACE_ENV",
        "IDE_PLATFORM",
        "IDE_FAKE_FAULTS",
        "OPENROUTER_MODEL",
        "TMPDIR",
    ],
    "orchestrator": ["IDE_RESEARCHER_PROFILES"],
    "researcher": [],
}

# The authored fields of the watcher job that the shipped definition owns. Everything else in
# the live record (pause state, run history, next_run_at, ...) belongs to the store and is kept.
WATCHER_JOB_FIELDS: tuple[str, ...] = (
    "name",
    "prompt",
    "script",
    "no_agent",
    "schedule",
    "schedule_display",
    "enabled_toolsets",
    "deliver",
)

Runner = Callable[..., subprocess.CompletedProcess]


class PrepareError(RuntimeError):
    pass


# --- keys -------------------------------------------------------------------------------------


def protected_keys_for(role: str) -> dict[str, Any]:
    """The protected keys of one role with ``<role>`` substituted."""
    keys = dict(PROTECTED_KEYS)
    if role == "orchestrator":
        keys.update(ORCHESTRATOR_PROTECTED_KEYS)
    return {key: _substitute_role(value, role) for key, value in keys.items()}


def _substitute_role(value: Any, role: str) -> Any:
    if isinstance(value, str):
        return value.replace(ROLE_PLACEHOLDER, role)
    if isinstance(value, list):
        return [_substitute_role(item, role) for item in value]
    return value


def plain_retired_keys() -> tuple[str, ...]:
    """The retired keys that are whole keys rather than list elements."""
    return tuple(key for key in RETIRED_KEYS if _split_retired(key)[1] is None)


def _split_retired(key: str) -> tuple[str, str | None]:
    """``"a.b[x]"`` -> ``("a.b", "x")``; a plain key -> ``(key, None)``."""
    if key.endswith("]") and "[" in key:
        name, _, element = key[:-1].partition("[")
        return name, element
    return key, None


def skill_dirs_for(core_src: Path, role: str) -> list[str]:
    """``skills.external_dirs``: the common core skills and the role's own (may not exist yet)."""
    skills = core_src / "research_core" / "skills"
    return [str(skills / "common"), str(skills / role)]


# --- the hermes executable --------------------------------------------------------------------


def _failed(result: subprocess.CompletedProcess) -> PrepareError:
    """A command failure with its full output, exit code and command line (design B.15)."""
    command = " ".join(str(part) for part in result.args)
    return PrepareError(
        f"`{command}` failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _key_not_set(result: subprocess.CompletedProcess) -> bool:
    return "Config key not set" in f"{result.stdout}{result.stderr}"


class HermesCli:
    """``hermes config`` and ``hermes cron`` against one profile home.

    Every call runs with ``HERMES_HOME`` set to the profile so Hermes edits that profile's
    ``config.yaml`` and cron store. Verified against Hermes v0.21.6: ``config get <key> --json``
    prints the resolved value as JSON; ``config set <key> <value>`` keeps a string-typed setting's
    text verbatim and parses a JSON list or mapping literal; ``config unset <key>`` exits 1 with
    "Config key not set" when the user's file has no such key.
    """

    dry_run = False
    planned: list[str]

    def __init__(self, hermes: str, profile: Path, env: Mapping[str, str], run: Runner) -> None:
        self.planned = []
        self.hermes = hermes
        self.env = {**env, "HERMES_HOME": str(profile)}
        self.run = run

    def _call(self, *args: str) -> subprocess.CompletedProcess:
        argv = [self.hermes, *args]
        try:
            return self.run(argv, env=self.env, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise PrepareError(f"hermes executable not found: {self.hermes}") from exc

    def config_get(self, key: str) -> Any:
        """The resolved value of ``key`` (defaults merged), or None when Hermes has no such key."""
        result = self._call("config", "get", key, "--json")
        if result.returncode != 0:
            if _key_not_set(result):
                return None
            raise _failed(result)
        return json.loads(result.stdout)

    def config_set(self, key: str, value: Any) -> None:
        if self.dry_run:
            self.planned.append(f"config: set {key}")
            return
        text = value if isinstance(value, str) else json.dumps(value)
        result = self._call("config", "set", key, text)
        if result.returncode != 0:
            raise _failed(result)

    def config_unset(self, key: str) -> bool:
        if self.dry_run:
            self.planned.append(f"config: unset {key}")
            return True
        """Remove ``key`` from the user's config; False when it was not set."""
        result = self._call("config", "unset", key)
        if result.returncode == 0:
            return True
        if _key_not_set(result):
            return False
        raise _failed(result)

    def cron_resume(self, job_id: str) -> None:
        if self.dry_run:
            self.planned.append(f"cron: resume {job_id}")
            return
        result = self._call("cron", "resume", job_id)
        if result.returncode != 0:
            raise _failed(result)


def desired_profile_config(core_src: Path, role: str, active: layout.Coworld | None) -> dict:
    """The skill directories and working directory a profile needs for the active Coworld.

    Every profile of the sandbox gets the same answer (amendment A5.9): the core skills, the
    active Coworld's mixin skills, its code repository as the trusted project (for skill
    overrides under ``.agents/skills``) and as the terminal working directory.
    """
    desired: dict[str, Any] = {"skills.external_dirs": skill_dirs_for(core_src, role)}
    if active is not None:
        desired["skills.external_dirs"].append(str(active.mixin / "skills"))
        desired["skills.trusted_project_dirs"] = [str(active.code)]
        desired["terminal.cwd"] = str(active.code)
    return desired


def apply_skill_dirs(
    profile_home: Path,
    hermes: str,
    *,
    mixin_skills_dir: Path | None,
    code_dir: Path | None,
    env: Mapping[str, str],
    run: Runner = subprocess.run,
) -> dict[str, list[str]]:
    """Point this profile and every Researcher profile at the active Coworld (A5.9).

    Returns ``{"applied": [...], "problems": [...]}``: what was set, and the profiles that
    could not be configured (a missing home, a failing ``hermes`` call).
    """
    core_src = Path(env["IDE_CORE_SRC"]) if env.get("IDE_CORE_SRC") else _core_src_of(profile_home)
    actions: list[str] = []
    problems: list[str] = []
    for profile, role in profile_targets(profile_home, env):
        if not profile.is_dir():
            problems.append(f"profile {profile.name}: no profile home at {profile}")
            continue
        desired: dict[str, Any] = {"skills.external_dirs": skill_dirs_for(core_src, role)}
        if mixin_skills_dir is not None:
            desired["skills.external_dirs"].append(str(mixin_skills_dir))
        if code_dir is not None:
            desired["skills.trusted_project_dirs"] = [str(code_dir)]
            desired["terminal.cwd"] = str(code_dir)
        cli = HermesCli(hermes, profile, env, run)
        try:
            for key, value in desired.items():
                if cli.config_get(key) != value:
                    cli.config_set(key, value)
                    actions.append(f"{profile.name}: set {key}")
        except PrepareError as error:
            problems.append(f"profile {profile.name}: {error}")
    return {"applied": actions, "problems": problems}


def profile_targets(profile_home: Path, env: Mapping[str, str]) -> list[tuple[Path, str]]:
    """This profile and the Researcher profiles beside it, with their roles."""
    role = "researcher" if profile_home.name.startswith("researcher") else "orchestrator"
    names = [n.strip() for n in (env.get("IDE_RESEARCHER_PROFILES") or "researcher").split(",")]
    targets = [(profile_home, role)]
    for name in names:
        sibling = profile_home.parent / name
        if name and sibling.resolve() != profile_home.resolve():
            targets.append((sibling, "researcher"))
    return targets


def _core_src_of(profile_home: Path) -> Path:
    marker = profile_home / CORE_PATH_FILE
    return Path(marker.read_text().strip()) if marker.exists() else Path("core/src").resolve()


def active_coworld(ws: layout.Workspace) -> layout.Coworld | None:
    """The active Coworld from the workspace store, if the store exists and one is active."""
    if not ws.store_path.exists():
        return None
    from research_core.store import coworlds
    from research_core.store.db import Store

    store = Store.open(ws.store_path, kind="workspace")
    try:
        row = coworlds.active(store)
    finally:
        store.close()
    return ws.coworld(row["coworld_id"]) if row else None


def apply_config(
    hermes: HermesCli, role: str, core_src: Path, active: layout.Coworld | None = None
) -> list[str]:
    """Remove the retired keys, set every protected key whose value differs, point the profile
    at the active Coworld, and apply the shipped values the owner has not changed."""
    actions: list[str] = []
    for key in RETIRED_KEYS:
        name, element = _split_retired(key)
        if element is None:
            if hermes.config_unset(name):
                actions.append(f"config: unset {name}")
            continue
        current = hermes.config_get(name)
        if isinstance(current, list) and element in current:
            hermes.config_set(name, [item for item in current if item != element])
            actions.append(f"config: dropped {element!r} from {name}")
    desired = protected_keys_for(role)
    desired.update(desired_profile_config(core_src, role, active))
    for key, value in desired.items():
        if hermes.config_get(key) != value:
            hermes.config_set(key, value)
            actions.append(f"config: set {key}")
    for key, shipped in SHIPPED_VALUES.items():
        current = hermes.config_get(key)
        if current != shipped and (current is None or current in EARLIER_DEFAULTS.get(key, set())):
            hermes.config_set(key, shipped)
            actions.append(f"config: set {key} (shipped value)")
    if hermes.config_get("fallback_providers") in (None, []):
        hermes.config_set("fallback_providers", FALLBACK_PROVIDERS)
        actions.append("config: set fallback_providers")
    shipped_env = ENV_PASSTHROUGH["common"] + ENV_PASSTHROUGH.get(role, [])
    current_env = hermes.config_get("terminal.env_passthrough") or []
    merged = list(dict.fromkeys([*current_env, *shipped_env]))
    if merged != current_env:
        hermes.config_set("terminal.env_passthrough", merged)
        actions.append("config: set terminal.env_passthrough (union)")
    return actions


# --- the watcher cron job ---------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise PrepareError(f"cannot read {path}: {exc}") from exc


def _job_list(payload: Any) -> list[dict[str, Any]]:
    """The jobs of a cron store in any shape Hermes reads: a list, ``{"jobs": [...]}`` or an
    id-keyed ``{"jobs": {...}}``."""
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    if not isinstance(jobs, list):
        raise PrepareError("cron store is not a job list")
    return jobs


def _find_job(jobs: list[dict[str, Any]], job_id: str) -> dict[str, Any] | None:
    return next((job for job in jobs if isinstance(job, dict) and job.get("id") == job_id), None)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.pending")
    pending.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(pending, path)


def reconcile_watcher_job(profile: Path, source: Path) -> str:
    """Make the live watcher job match the shipped authored fields; add it paused if missing.

    The live store's other jobs, the watcher's pause state and its run history are kept. The
    store is rewritten atomically (``jobs.json.pending`` then ``os.replace``).
    """
    shipped_job = _find_job(_job_list(_load_json(source / "cron" / "jobs.json")), WATCHER_JOB_ID)
    if shipped_job is None:
        raise PrepareError(f"{source} ships no '{WATCHER_JOB_ID}' cron job")
    jobs_path = profile / "cron" / "jobs.json"
    payload = _load_json(jobs_path) if jobs_path.exists() else {"jobs": []}
    if isinstance(payload, list):
        payload = {"jobs": payload}
    if not isinstance(payload, dict):
        raise PrepareError(f"{jobs_path} is not a cron store")
    jobs = _job_list(payload)
    live = _find_job(jobs, WATCHER_JOB_ID)
    if live is None:
        jobs.append(copy.deepcopy(shipped_job))
        action = "cron: added the watcher job (paused)"
    else:
        changed = sorted(f for f in WATCHER_JOB_FIELDS if live.get(f) != shipped_job.get(f))
        for field in changed:
            live[field] = copy.deepcopy(shipped_job[field])
        action = (
            f"cron: watcher job updated ({', '.join(changed)})"
            if changed
            else "cron: watcher job unchanged"
        )
    payload["jobs"] = jobs
    _write_json_atomic(jobs_path, payload)
    return action


# --- the workspace environment ----------------------------------------------------------------


def lock_sha(checkout: Path) -> str:
    return hashlib.sha256((checkout / "uv.lock").read_bytes()).hexdigest()


def _read_stamp(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def ensure_workspace_env(
    checkout: Path,
    ws: Workspace,
    run: Runner = subprocess.run,
    env: Mapping[str, str] = os.environ,
) -> bool:
    """Build the workspace environment from the checkout's ``uv.lock``, or verify a prebuilt one.

    Returns True when ``uv sync`` ran. The stamp ``<venv>/.lock-sha`` records the lock the
    environment was built from. A prebuilt environment (``IDE_WORKSPACE_ENV`` points elsewhere
    than ``tools/venv``, or ``tools/venv`` is a symlink hosting provided) is owned by hosting: it
    is never synced, only checked, and a stamp mismatch is an error naming both sides.
    """
    lock_path = checkout / "uv.lock"
    expected = lock_sha(checkout)
    stamp_path = ws.venv / LOCK_STAMP
    if ws.venv != ws.tools / "venv" or ws.venv.is_symlink():
        found = _read_stamp(stamp_path)
        if found != expected:
            raise PrepareError(
                f"prebuilt workspace environment {ws.venv} was built from a different uv.lock: "
                f"its {LOCK_STAMP} is {found or 'missing'} but {lock_path} hashes to {expected}. "
                "Rebuild it from this checkout, or unset IDE_WORKSPACE_ENV to build one here."
            )
        return False
    with file_lock(ws.tools / "venv.lock"):
        if _read_stamp(stamp_path) == expected:
            return False
        argv = ["uv", "sync", "--frozen", "--no-dev", "--project", str(checkout)]
        sync_env = {**env, "UV_PROJECT_ENVIRONMENT": str(ws.venv)}
        try:
            result = run(argv, env=sync_env, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise PrepareError(
                "uv not found on PATH; it is needed to build the workspace environment"
            ) from exc
        if result.returncode != 0:
            raise _failed(result)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(expected + "\n")
        return True


# --- entry point ------------------------------------------------------------------------------


def prepare(
    source: Path,
    hermes: str,
    activate_watcher: bool,
    env: Mapping[str, str] = os.environ,
    run: Runner = subprocess.run,
    *,
    check: bool = False,
) -> dict[str, Any]:
    """Prepare the profile at ``$HERMES_HOME`` from the distribution folder ``source``.

    ``source`` is ``<checkout>/orchestrator`` or ``<checkout>/researcher``; its name is the
    role. ``hermes`` is the Hermes executable. ``activate_watcher`` resumes the watcher job on
    the first preparation only (the ``.softmax-prepared`` marker records that one happened).
    """
    source = Path(source)
    role = source.name
    if role not in ROLES:
        raise PrepareError(f"the distribution folder must be named one of {ROLES}: {source}")
    if not env.get("HERMES_HOME"):
        raise PrepareError("HERMES_HOME must name the profile home to prepare")
    profile = Path(env["HERMES_HOME"])
    checkout = source.parent
    core_src = checkout / "core" / "src"
    actions: list[str] = []

    core_path = profile / CORE_PATH_FILE
    if not check:
        core_path.parent.mkdir(parents=True, exist_ok=True)
        core_path.write_text(str(core_src) + "\n")
    actions.append(f"wrote {CORE_PATH_FILE}")

    ws = layout.from_env(env)
    if not check:
        ws.ensure_dirs()
        actions.extend(ensure_stores(ws))
    active = active_coworld(ws)

    cli = HermesCli(hermes, profile, env, run)
    cli.dry_run = check
    actions.extend(apply_config(cli, role, core_src, active))

    marker = profile / PREPARED_MARKER
    if role == "orchestrator":
        if not check:
            actions.append(reconcile_watcher_job(profile, source))
        if activate_watcher and not marker.exists():
            cli.cron_resume(WATCHER_JOB_ID)
            actions.append("cron: resumed the watcher job")

    if check:
        actions.extend(cli.planned)
        summary = {"role": role, "workspace": str(ws.root), "check": True, "planned": actions}
        print(json.dumps(summary), flush=True)
        return summary

    if ensure_workspace_env(checkout, ws, run=run, env=env):
        actions.append("workspace environment synced")
    else:
        actions.append("workspace environment up to date")

    if role == "orchestrator" and active is None and env.get("IDE_DEFAULT_COWORLD"):
        actions.append(bootstrap_default_coworld(ws, env, run))

    marker.touch()
    summary = {"role": role, "workspace": str(ws.root), "venv": str(ws.venv), "actions": actions}
    print(json.dumps(summary), flush=True)
    return summary


def ensure_stores(ws: layout.Workspace) -> list[str]:
    """Create or migrate the workspace store and every registered Coworld's store."""
    from research_core.store import coworlds
    from research_core.store.db import Store

    actions = []
    store = Store.open(ws.store_path, kind="workspace")
    try:
        actions.append("workspace store ready")
        for row in coworlds.list(store):
            coworld = ws.coworld(row["coworld_id"])
            coworld.ensure_dirs()
            Store.open(coworld.store_path, kind="coworld").close()
            actions.append(f"store ready: {row['coworld_id']}")
    finally:
        store.close()
    return actions


def bootstrap_default_coworld(ws: layout.Workspace, env: Mapping[str, str], run: Runner) -> str:
    """Register and activate ``IDE_DEFAULT_COWORLD`` on a fresh workspace (amendment A6.4).

    The switch needs the platform client, so it runs in the workspace environment; a failure
    is reported, never fatal: the first user request selects the Coworld instead.
    """
    coworld_id = env["IDE_DEFAULT_COWORLD"]
    command = [
        str(ws.venv_python),
        "-m",
        "research_core.cli",
        "coworld",
        "switch",
        coworld_id,
        "--cites",
        "IDE_DEFAULT_COWORLD",
    ]
    try:
        result = run(command, env=dict(env), capture_output=True, text=True, check=False)
    except OSError as error:
        return f"default Coworld {coworld_id}: switch not run ({error})"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-500:]
        return f"default Coworld {coworld_id}: switch failed ({tail.strip()})"
    return f"default Coworld {coworld_id}: registered and set up started"
