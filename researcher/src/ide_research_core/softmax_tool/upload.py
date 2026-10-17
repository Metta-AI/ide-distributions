"""Upload a built candidate and bind it to its experiment (Plan 03 Task 3.3).

``upload`` runs in a card worker and writes only the worker's worktree:

1. Read ``<build_dir>/version.json`` (written by the game's ``build`` binding) and refuse unless
   its gates passed.
2. Create the immutable ``version`` record with the build provenance.
3. ``Softmax.upload`` the artifact under ``policy_name(experiment, artifact hash)`` (the client
   re-checks ``whoami`` immediately before, C6). The name uses the artifact hash, not the
   version id, because the immutable version record must carry its name and the store assigns
   the id only on creation; experiment + artifact hash is just as deterministic and unique.
4. Add the version's ``uploaded`` event ``{policy_version_id}``.
5. Set ``experiment.candidate_binding`` once, moving the experiment ``built -> uploaded`` in the
   same write when needed (the binding may only be set in status ``uploaded``).

Retries are safe: when the experiment is already bound to a version with this build's
artifact hash, that version id is returned without uploading; a version record or an
``uploaded`` event left by a crashed attempt is reused. (A crash between the platform upload
and the event write uploads again on retry, which creates a second platform version under the
same name; the binding still names exactly one.)

``upload_bootstrap`` uploads the first champion on a fresh install (amendment A14; a
researcher's bootstrap card runs it, then the orchestrator submits the version with
``promotion bootstrap``, which checks the identity and that no champion exists):
the same build reader, version provenance and retry safety, with ``experiment: null`` and the
policy name ``policy_name("bootstrap", artifact hash)``. The same build again finds its
version by that name and does not upload again, within what the card's worktree sees (its own
records plus canonical's): a card that died unmerged leaves its record invisible to a later
card, which uploads again under the same name (a second platform version; harmless).

``version.json`` contract (every build binding writes it; ``games/fakegame`` and the GotA
build both follow it):

- ``artifact`` (required): path of the policy file or directory, relative to the build dir.
- ``gates`` (required, non-empty): ``{<gate>: {"passed": bool, "advisory": bool?, ...}}``.
  An errored outcome always rejects upload, including advisory gates.
  The build passed when every non-advisory gate has ``passed: true`` and ``status`` (if
  present) is ``"passed"``.
- ``strategy_sha256``, or ``source_hashes[source_path]``: the STRATEGY the build compiled.
- ``source_commit`` (optional): defaults to the worker worktree's HEAD.
- ``policy_sha256`` (optional): when present it must equal the artifact hash we compute.
- ``build_id``, ``compiler``, ``source_hashes``, ``source_path``, ``created``, ``started``,
  ``finished``, ``engine``, ``driver`` (optional): copied into the version record under
  ``build``. ``started``/``finished`` (ISO UTC) give the build's wall time and a gate's
  optional ``seconds`` its own.

The artifact hash is the sha256 of the file, or for a directory the sha256 of the sorted
``<relative path>\\0<file sha256>\\n`` lines of every file in it.
"""

import hashlib
import json
from pathlib import Path

from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext
from ide_research_core.softmax_tool.protocol import Refused, Softmax

PROVENANCE_FIELDS = (
    "build_id",
    "compiler",
    "source_hashes",
    "source_path",
    "created",
    "started",
    "finished",
    "engine",
    "driver",
)


def policy_name(experiment: str, artifact_hash: str, player_id: str) -> str:
    """Player-scoped deterministic name within the platform's 64-character limit."""
    player_hash = hashlib.sha256(player_id.encode()).hexdigest()[:16]
    return f"ar-{player_hash}-{experiment}-{artifact_hash[:12]}".lower()


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def artifact_sha256(artifact: Path) -> str:
    if artifact.is_file():
        return _file_sha256(artifact)
    lines = sorted(
        f"{path.relative_to(artifact).as_posix()}\0{_file_sha256(path)}\n"
        for path in artifact.rglob("*")
        if path.is_file()
    )
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def worker_store(ctx: RunContext) -> RecordStore:
    """The worker worktree's store; card-side record writes never touch canonical."""
    if ctx.worker is None:
        raise Refused("no_worker", "this step needs a card worker worktree")
    return RecordStore(Layout(ctx.worker))


def _gate_failures(build: dict) -> list[str]:
    gates = build.get("gates")
    if not isinstance(gates, dict) or not gates:
        return ["version.json lists no gates"]
    failures = [
        name
        for name, result in sorted(gates.items())
        if not isinstance(result, dict)
        or result.get("outcome") == "errored"
        or (not result.get("advisory") and result.get("passed") is not True)
    ]
    if build.get("status", "passed") != "passed":
        failures.append(f"status {build['status']!r}")
    return failures


def _read_build(build_dir: Path) -> tuple[dict, Path]:
    """(version.json, artifact path); Refused unless the build passed and is well formed."""
    try:
        build = json.loads((build_dir / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused("bad_build", f"{build_dir}/version.json is unreadable: {error}") from None
    if not isinstance(build, dict) or not isinstance(build.get("artifact"), str):
        raise Refused("bad_build", f"{build_dir}/version.json names no artifact")
    failures = _gate_failures(build)
    if failures:
        raise Refused("gates_failed", f"build gates did not pass: {', '.join(failures)}")
    artifact = (build_dir / build["artifact"]).resolve()
    if not artifact.is_relative_to(build_dir.resolve()) or not artifact.exists():
        raise Refused("bad_build", f"artifact {build['artifact']!r} is not in {build_dir}")
    return build, artifact


def checked_build(build_dir: Path) -> tuple[dict, Path, str]:
    """(version.json, artifact path, artifact sha256) of a passed build; Refused otherwise."""
    build, artifact = _read_build(build_dir)
    artifact_hash = artifact_sha256(artifact)
    declared = build.get("policy_sha256")
    if declared is not None and declared != artifact_hash:
        raise Refused(
            "artifact_mismatch",
            f"artifact sha256 is {artifact_hash} but version.json says {declared}",
        )
    return build, artifact, artifact_hash


def _strategy_sha256(build: dict) -> str:
    value = build.get("strategy_sha256")
    if value is None:
        value = (build.get("source_hashes") or {}).get(build.get("source_path"))
    if not value:
        raise Refused("bad_build", "version.json gives no strategy_sha256 (or source hash)")
    return value


def _version_meta(
    ctx: RunContext, build: dict, artifact_hash: str, name: str, player_id: str | None
) -> dict:
    """The provenance every version record carries; callers add ``experiment``."""
    return {
        "parents": list(build.get("parents") or []),
        "strategy_sha256": _strategy_sha256(build),
        "source_commit": build.get("source_commit") or gitops.head(ctx.worker),
        "artifact_sha256": artifact_hash,
        "policy_name": name,
        "gates": build["gates"],
        "player_id": player_id,
        "build": {key: build[key] for key in PROVENANCE_FIELDS if key in build},
    }


def _existing_version(store: RecordStore, experiment: str, artifact_hash: str) -> str | None:
    found = store.find("version", experiment=experiment, artifact_sha256=artifact_hash)
    return found[0] if found else None


def _uploaded_policy(store: RecordStore, version_id: str) -> str | None:
    for event_id in store.find("version_event", version=version_id, kind="uploaded"):
        return store.load(event_id)[0]["data"]["policy_version_id"]
    return None


def _upload_once(store: RecordStore, sm: Softmax, version_id: str, artifact: Path) -> str:
    """The platform id of ``version_id``: its ``uploaded`` event, or a new upload (C6 is
    checked by the client) recorded as that event."""
    platform_id = _uploaded_policy(store, version_id)
    if platform_id is None:
        name = store.load(version_id)[0]["policy_name"]
        platform_id = sm.upload(artifact, name)
        store.add_event(version_id, "uploaded", {"policy_version_id": platform_id})
    return platform_id


def upload(
    ctx: RunContext,
    sm: Softmax,
    build_dir: Path,
    experiment: str,
    player_id: str,
) -> str:
    """Upload the build in ``build_dir`` as ``experiment``'s candidate; return the version id.
    Raises ``Refused`` (nothing uploaded) or the platform's errors (``IdentityError``, ...)."""
    store = worker_store(ctx)
    build_dir = Path(build_dir)
    build, artifact, artifact_hash = checked_build(build_dir)
    meta, _ = store.load(experiment)
    binding = meta.get("candidate_binding")
    if binding:
        bound = store.load(binding["version_id"])[0]
        if bound["artifact_sha256"] == artifact_hash:
            return binding["version_id"]
        raise Refused(
            "binding_conflict",
            f"{experiment} is already bound to {binding['version_id']} (another build); "
            "the candidate binding is set once",
        )
    if meta["status"] not in ("built", "uploaded"):
        raise Refused(
            "experiment_status",
            f"{experiment} is {meta['status']}; upload needs status built or uploaded",
        )
    version_id = _existing_version(store, experiment, artifact_hash)
    if version_id is None:
        name = policy_name(experiment, artifact_hash, player_id)
        version_meta = _version_meta(ctx, build, artifact_hash, name, player_id)
        version_id = store.create("version", {**version_meta, "experiment": experiment})
    platform_id = _upload_once(store, sm, version_id, artifact)
    patch = {
        "candidate_binding": {"version_id": version_id, "platform_policy_version_id": platform_id}
    }
    if meta["status"] == "built":
        patch["status"] = "uploaded"
    store.update(experiment, patch)
    return version_id


BOOTSTRAP_NAME = "bootstrap"  # policy_name("bootstrap", hash) for the first champion (A14)


def upload_bootstrap(
    ctx: RunContext, sm: Softmax, build_dir: Path, player_id: str
) -> tuple[str, str]:
    """Upload the build in ``build_dir`` as the first champion's version (module docstring);
    return ``(version id, platform policy version id)``. Raises ``Refused`` (nothing
    uploaded) or the platform's errors (``IdentityError``, ...)."""
    store = worker_store(ctx)
    build, artifact, artifact_hash = checked_build(Path(build_dir))
    name = policy_name(BOOTSTRAP_NAME, artifact_hash, player_id)
    found = store.find("version", policy_name=name)
    if found:
        version_id = found[0]  # a retry: reuse the record (and its upload, when recorded)
    else:
        version_meta = _version_meta(ctx, build, artifact_hash, name, player_id)
        version_id = store.create("version", {**version_meta, "experiment": None})
    return version_id, _upload_once(store, sm, version_id, artifact)
