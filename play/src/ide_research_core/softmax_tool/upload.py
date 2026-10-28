"""Upload checked builds under an agent-chosen, player-scoped policy lineage.

Both upload commands require a name. The full player id scopes that name globally;
the artifact hash remains provenance, not a naming input. Version records persist the
resolved name before upload. Retrying the same build and name reuses that record and
its uploaded event. Choosing a new name creates a new immutable record, allowing a
failed legacy name to be recovered without rewriting history. Bound experiments stay
bound to their original successful version.

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
import re
from pathlib import Path

from ide_research_core import build_sources
from ide_research_core.io import gitops
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.runctx import RunContext
from ide_research_core.softmax_tool.protocol import ArtifactType, Refused, Softmax

PROVENANCE_FIELDS = (
    "artifact_type",
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


def policy_name(name: str, player_id: str) -> str:
    """A readable lineage scoped by the full player identity, within the platform limit."""
    if not player_id:
        raise Refused("bad_arguments", "a player identity is required for policy naming")
    limit = 64 - len(player_id) - 1
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > limit:
        raise Refused(
            "bad_arguments",
            f"policy name must be lowercase words separated by hyphens, at most {limit} characters",
        )
    return f"{name}-{player_id}"


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
    if build.get("artifact_type", "file") not in ("file", "oci-image"):
        raise Refused("bad_build", "artifact_type must be file or oci-image")
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


def _existing_version(
    store: RecordStore, experiment: str, artifact_hash: str, name: str
) -> str | None:
    found = store.find(
        "version",
        experiment=experiment,
        artifact_sha256=artifact_hash,
        policy_name=name,
    )
    return found[0] if found else None


def _uploaded_policy(store: RecordStore, version_id: str) -> str | None:
    for event_id in store.find("version_event", version=version_id, kind="uploaded"):
        return store.load(event_id)[0]["data"]["policy_version_id"]
    return None


def register_sources(
    ctx: RunContext, store: RecordStore, version_id: str, build: dict, build_dir: Path
) -> None:
    """Attach verified source evidence without changing an immutable publication record."""
    if "source_path" not in build or "source_hashes" not in build:
        return
    if store.find("version_event", version=version_id, kind="sources_registered"):
        return
    handoff = build_dir / "retained-sources.json"
    if handoff.exists():
        retained = build_sources.StagedSources.model_validate_json(handoff.read_bytes())
        retained.verify(
            ctx.worker,
            (build_dir / "version.json").read_bytes(),
            store.load(version_id)[0]["artifact_sha256"],
        )
        source_path, source_hashes = retained.source_path, retained.source_hashes
    else:
        source_path, source_hashes = build_sources.retain(
            ctx.worker,
            build["source_path"],
            build["source_hashes"],
            source_root=Path(build["source_root"]) if "source_root" in build else ctx.worker,
        )
    gitops.git(ctx.worker, "--literal-pathspecs", "add", "-f", "--", *source_hashes)
    store.add_event(
        version_id,
        "sources_registered",
        {
            "artifact_sha256": store.load(version_id)[0]["artifact_sha256"],
            "source_path": source_path,
            "source_hashes": source_hashes,
        },
    )


def _upload_once(
    store: RecordStore,
    sm: Softmax,
    version_id: str,
    artifact: Path,
    artifact_type: ArtifactType,
) -> str:
    """The platform id of ``version_id``: its ``uploaded`` event, or a new upload (C6 is
    checked by the client) recorded as that event."""
    platform_id = _uploaded_policy(store, version_id)
    if platform_id is None:
        name = store.load(version_id)[0]["policy_name"]
        platform_id = sm.upload(artifact, name, artifact_type=artifact_type)
        store.add_event(version_id, "uploaded", {"policy_version_id": platform_id})
    return platform_id


def upload(
    ctx: RunContext,
    sm: Softmax,
    build_dir: Path,
    experiment: str,
    player_id: str,
    name: str,
) -> str:
    """Upload the build in ``build_dir`` as ``experiment``'s candidate; return the version id.
    Raises ``Refused`` (nothing uploaded) or the platform's errors (``IdentityError``, ...)."""
    if (ctx.hermes_home / "plugins/researcher_guard").is_dir():
        raise Refused(
            "coordinator_required",
            "Stage the verified build; the coordinator owns upload",
        )
    store = worker_store(ctx)
    build_dir = Path(build_dir)
    build, artifact, artifact_hash = checked_build(build_dir)
    name = policy_name(name, player_id)
    meta, _ = store.load(experiment)
    binding = meta.get("candidate_binding")
    if binding:
        bound = store.load(binding["version_id"])[0]
        if bound["artifact_sha256"] == artifact_hash:
            register_sources(ctx, store, binding["version_id"], build, build_dir)
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
    version_id = _existing_version(store, experiment, artifact_hash, name)
    if version_id is None:
        version_meta = _version_meta(ctx, build, artifact_hash, name, player_id)
        version_id = store.create("version", {**version_meta, "experiment": experiment})
    register_sources(ctx, store, version_id, build, Path(build_dir))
    platform_id = _upload_once(store, sm, version_id, artifact, build.get("artifact_type", "file"))
    patch = {
        "candidate_binding": {
            "version_id": version_id,
            "platform_policy_version_id": platform_id,
        }
    }
    if meta["status"] == "built":
        patch["status"] = "uploaded"
    store.update(experiment, patch)
    return version_id


def upload_build(
    ctx: RunContext, sm: Softmax, build_dir: Path, player_id: str, name: str
) -> tuple[str, str]:
    """Upload a checked build without an experiment, evaluation, or league submission;
    return ``(version id, platform policy version id)``. Raises ``Refused`` (nothing
    uploaded) or the platform's errors (``IdentityError``, ...)."""
    if (ctx.hermes_home / "plugins/researcher_guard").is_dir():
        raise Refused(
            "coordinator_required",
            "Stage the verified build; the coordinator owns upload",
        )
    store = worker_store(ctx)
    build, artifact, artifact_hash = checked_build(Path(build_dir))
    name = policy_name(name, player_id)
    found = store.find("version", policy_name=name, artifact_sha256=artifact_hash, experiment=None)
    if found:
        version_id = found[0]  # a retry: reuse the record (and its upload, when recorded)
    else:
        version_meta = _version_meta(ctx, build, artifact_hash, name, player_id)
        version_id = store.create("version", {**version_meta, "experiment": None})
    register_sources(ctx, store, version_id, build, Path(build_dir))
    return version_id, _upload_once(
        store, sm, version_id, artifact, build.get("artifact_type", "file")
    )
