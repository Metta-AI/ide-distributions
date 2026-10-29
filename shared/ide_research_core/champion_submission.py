"""Champion submission: the orchestrator submits a candidate to the live league and records the champion.

Researchers never submit (their deny list blocks the commands). A researcher's card ends with
an experiment whose ``decision.outcome`` is ``submit``, a stored ``stats evaluate`` outcome,
a candidate binding (the uploaded version) and the candidate strategy at
``strategy/candidates/<E>/STRATEGY.md``, all merged into canonical ``main``. The orchestrator
then runs ``champion submit <E>`` in its own clone.

A champion submission is one JSON file ``champion-submissions/<CS-id>.json`` on canonical ``main`` (only the
orchestrator writes there; ``workspace.integrate`` refuses it from a card). Stages, in
write-ahead order so an interrupted submit is never sent twice blindly:

- ``intent``: committed and pushed before anything reaches the platform;
- ``submitted``: the platform accepted the submission (``submission_id``);
- ``recorded``: the league shows the policy as our champion; for an experiment (or a
  requested version compiled from a STRATEGY.md) the strategy is then copied to
  ``strategy/STRATEGY.md``. A requested prebuilt artifact keeps only its saved source;
- ``published``: the champion file is on ``main``. Terminal, like ``aborted`` and ``rejected``.

``status`` advances the one submission in flight by at most one platform read and returns
where things stand, including a champion conflict: the league shows a policy for us that the
loop did not record as its champion (someone submitted by hand, or the league dropped ours).
A permit refusal aborts this attempt and remains visible in its record; status never polls
cogent asks or retries a refused submission. A later explicit submission is a new attempt.
A missing league leaves the submission at ``intent`` with ``waiting`` set. ``accept`` records the
league's current champion as the loop's when a human says so.
"""

import datetime
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ulid import ULID

from ide_research_core import workspace
from ide_research_core.io import gitops
from ide_research_core.io.docs import read_json
from ide_research_core.io.locks import file_lock
from ide_research_core.paths import Layout
from ide_research_core.records.store import RecordStore
from ide_research_core.softmax_tool.identity import IdentityError
from ide_research_core.softmax_tool.protocol import (
    LeagueNotChosen,
    PermitRequired,
    Refused,
    Softmax,
)
from ide_research_core.stats.design import EvaluationDesign, design_sha256

SUBMISSIONS_DIR = "champion-submissions"
CHAMPION_PATH = "strategy/STRATEGY.md"
SUBMISSION_LOCK = "champion-submission.lock"
IN_FLIGHT = ("intent", "submitted", "recorded")
ACCEPTED = ("submitted", "recorded", "published")  # the platform holds the submission
CHAMPIONS = ("published",)  # the stage that makes a submission's policy the loop's champion


def candidate_path(experiment: str) -> str:
    """Where the researcher's card commits the candidate strategy."""
    return f"strategy/candidates/{experiment}/STRATEGY.md"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


@dataclass
class ChampionSubmission:
    id: str
    kind: str  # experiment | requested | bootstrap | accepted
    version_id: str | None
    platform_policy_version_id: str | None
    league_id: str
    stage: str
    experiment: str | None = None
    submission_id: str | None = None
    waiting: dict | None = None  # league-selection refusal while at intent
    # The player submitted for: the workspace's own player, or an owned player the conversation
    # selected (the IDE workspace credential may publish for it). None only in records written
    # before submissions named their player; those were all for the workspace's own player.
    player_id: str | None = None
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _path(clone: Path, record_id: str) -> Path:
    return clone / SUBMISSIONS_DIR / f"{record_id}.json"


def load_all(clone: Path) -> list[ChampionSubmission]:
    """Every submission on canonical ``main``, oldest first (ids are ULIDs). Read from the bare
    repository, not the clone's checkout: a researcher's clone moves only when it integrates,
    and the orchestrator publishes submissions from its own clone."""
    bare = workspace.canonical_of(clone)
    listed = gitops._run(bare, "ls-tree", "--name-only", workspace.MAIN, f"{SUBMISSIONS_DIR}/")
    paths = sorted(
        path for path in listed.stdout.decode().split() if Path(path).name.startswith("CS-")
    )
    return [ChampionSubmission(**json.loads(gitops.show(bare, workspace.MAIN, path))) for path in paths]


@contextmanager
def _one_at_a_time(clone: Path) -> Iterator[None]:
    """Hold the host's submission lock (a file in the bare repository) from the checks to the
    recorded platform result. The orchestrator's chat turn and its supervisor both reach this
    code; without the lock two submits could both pass the busy check, and a ``status`` between
    a submit's ``intent`` and its send would abort a live submit."""
    with file_lock(workspace.canonical_of(clone) / SUBMISSION_LOCK, timeout_s=workspace.LOCK_TIMEOUT_S):
        workspace.sync(clone)
        yield


def in_flight(clone: Path) -> ChampionSubmission | None:
    flying = [p for p in load_all(clone) if p.stage in IN_FLIGHT]
    return flying[-1] if flying else None


def latest_champion(clone: Path) -> ChampionSubmission | None:
    champions = [p for p in load_all(clone) if p.stage in CHAMPIONS]
    return champions[-1] if champions else None


def _move(clone: Path, submission: ChampionSubmission, stage: str, detail: str, extra: tuple[str, ...] = ()):
    """Record ``stage`` and publish it (with ``extra`` paths) to canonical ``main``."""
    submission.stage = stage
    submission.history.append({"stage": stage, "at": _now(), "detail": detail})
    relative = _path(clone, submission.id).relative_to(clone).as_posix()
    content = json.dumps(submission.to_dict(), indent=2, sort_keys=True) + "\n"
    files = {relative: content.encode(), **workspace.read_files(clone, list(extra))}
    workspace.publish(clone, files, f"champion submission {submission.id}: {stage}")


# --- the league ---


def conflict(clone: Path, sm: Softmax, league_id: str, player_id: str) -> str | None:
    """Why the league and the loop disagree about our champion, or None.

    The loop's champion is the latest ``published`` submission. A submission still at
    ``submitted`` explains a league that does not show it yet."""
    shown = sm.leaderboard_champion(player_id, league_id)
    champion = latest_champion(clone)
    expected = champion.platform_policy_version_id if champion else None
    if shown == expected:
        return None
    flying = in_flight(clone)
    if flying is not None and flying.stage == "submitted":
        return None
    if champion is None and shown is None:
        return None
    return (
        f"the {league_id} league shows {shown or 'no policy'} competing for us, but the "
        f"champion the loop recorded is {expected or 'none'}"
    )


def baseline_problem(
    clone: Path, sm: Softmax, league_id: str, player_id: str, baseline: str | None
) -> tuple[str, str] | None:
    """Why an experiment may not use ``baseline`` now, as ``(reason, message)``, or None when
    it is the current champion. ``stats design`` and ``softmax xp create`` refuse on it: an
    experiment against any other policy can never be submitted."""
    disagreement = conflict(clone, sm, league_id, player_id)
    if disagreement is not None:
        return "champion_conflict", f"{disagreement}. Do not design or launch until it is resolved"
    champion = sm.leaderboard_champion(player_id, league_id)
    if champion is None:
        return "champion_unknown", (
            f"the {league_id} league shows no champion for us yet: the first champion comes from "
            "the starter-policy bootstrap, then design against it"
        )
    if baseline != champion:
        return "baseline_not_champion", (
            f"baseline {baseline!r} is not the current champion {champion}: design against the "
            "champion"
        )
    return None


# --- guards ---


def _refuse_if_busy(clone: Path) -> None:
    flying = in_flight(clone)
    if flying is not None:
        raise Refused(
            "submission_in_flight",
            f"submission {flying.id} is {flying.stage}; one submission runs at a time",
            {"submission": flying.id},
        )


def _check_outcome(store: RecordStore, experiment: str, meta: dict, platform_id: str) -> dict:
    """The stored ``stats evaluate`` outcome, refused unless eligible and still current."""
    layout = store.layout
    outcome_path = layout.stats / "outcomes" / f"{experiment}.json"
    if not outcome_path.exists():
        raise Refused("no_outcome", f"no stored outcome {outcome_path}; run stats evaluate")
    outcome = read_json(outcome_path)
    if outcome.get("submit_eligible") is not True:
        failures = outcome.get("failures") or []
        raise Refused(
            "not_eligible",
            f"stats evaluate did not find {experiment} eligible: {', '.join(failures) or '?'}",
            {"failures": failures},
        )
    if outcome.get("candidate_platform_id") != platform_id:
        raise Refused(
            "stale_outcome",
            f"the outcome was evaluated for {outcome.get('candidate_platform_id')}, "
            f"but {experiment} is bound to {platform_id}",
        )
    rows_path = layout.stats / "rows" / f"{experiment}.jsonl"
    rows_hash = hashlib.sha256(rows_path.read_bytes()).hexdigest() if rows_path.exists() else None
    if rows_hash is None or outcome.get("rows_sha256") != rows_hash:
        raise Refused("stale_outcome", f"the rows file {rows_path} changed since stats evaluate")
    design = EvaluationDesign.from_dict(meta["evaluation_design"])
    if outcome.get("design_sha256") != design_sha256(design):
        raise Refused("stale_outcome", "the outcome was computed for another evaluation design")
    return outcome


def _require_identity(sm: Softmax) -> None:
    """The credential is this workspace's own live Player (C6); it may publish for ``player_id``
    only when the platform grants it (an owned player selected through the IDE workspace)."""
    try:
        sm.require_player()
    except IdentityError as error:
        raise Refused("identity", str(error)) from None


def _strategy_source_hashes(meta: dict) -> list[str]:
    """The STRATEGY.md hashes a version's build says it compiled: its ``source_hashes``
    entries for a ``STRATEGY.md``, and ``strategy_sha256`` when ``source_path`` is one. Empty
    for a build that names no STRATEGY.md source (a prebuilt artifact): the game's starter
    policy section and the build provenance answer for that."""
    build = meta.get("build") or {}
    hashes = [
        value for path, value in (build.get("source_hashes") or {}).items()
        if Path(path).name == "STRATEGY.md"
    ]
    if Path(build.get("source_path") or "").name == "STRATEGY.md" and meta.get("strategy_sha256"):
        hashes.append(meta["strategy_sha256"])
    return hashes


def _requested_source_matches(meta: dict, source: bytes) -> bool:
    """Whether ``source`` is what the version's build was made from. A build compiled from a
    STRATEGY.md must match that hash. A prebuilt artifact (no STRATEGY.md source, see
    ``_strategy_source_hashes``) may match any committed input its build recorded: the
    artifact and the input it was made from are both its source."""
    digest = hashlib.sha256(source).hexdigest()
    if _strategy_source_hashes(meta):
        return digest == meta["strategy_sha256"]
    recorded = ((meta.get("build") or {}).get("source_hashes") or {}).values()
    return digest == meta["strategy_sha256"] or digest in recorded


def _uploaded_policy(store: RecordStore, version_id: str) -> str | None:
    for event_id in store.find("version_event", version=version_id, kind="uploaded"):
        return store.load(event_id)[0]["data"]["policy_version_id"]
    return None


# --- submit, bootstrap, accept ---


def _new(
    kind: str, version_id: str | None, platform_id: str | None, league_id: str, player_id: str, **fields
):
    return ChampionSubmission(
        f"CS-{ULID()}", kind, version_id, platform_id, league_id, "new", player_id=player_id, **fields
    )


def submit(clone: Path, sm: Softmax, experiment: str, league_id: str, player_id: str) -> dict:
    """Run the guards, record ``intent``, then submit (``_send``)."""
    with _one_at_a_time(clone):
        return _submit(clone, sm, experiment, league_id, player_id)


def _submit(clone: Path, sm: Softmax, experiment: str, league_id: str, player_id: str) -> dict:
    _refuse_if_busy(clone)
    store = RecordStore(Layout(clone))
    meta, _ = store.load(experiment)
    decision = (meta.get("decision") or {}).get("outcome")
    if decision != "submit":
        raise Refused("not_submit", f"{experiment} decision is {decision!r}, not 'submit'")
    binding = meta.get("candidate_binding")
    if not binding:
        raise Refused("no_binding", f"{experiment} has no candidate_binding")
    if not (clone / candidate_path(experiment)).is_file():
        raise Refused("no_candidate", f"{candidate_path(experiment)} is not on main")
    platform_id = binding["platform_policy_version_id"]
    outcome = _check_outcome(store, experiment, meta, platform_id)
    _require_identity(sm)
    disagreement = conflict(clone, sm, league_id, player_id)
    if disagreement is not None:
        raise Refused("champion_conflict", disagreement)
    champion = sm.leaderboard_champion(player_id, league_id)
    if champion is None:
        raise Refused("champion_unknown", f"the {league_id} league shows no champion for us")
    if outcome.get("champion") != champion:
        raise Refused(
            "baseline_not_champion",
            f"{experiment} was evaluated against {outcome.get('champion')}, but the current "
            f"champion is {champion}; evaluate again",
        )
    submission = _new(
        "experiment", binding["version_id"], platform_id, league_id, player_id, experiment=experiment
    )
    _move(clone, submission, "intent", f"submit {experiment}")
    return _send(clone, sm, submission)


def submit_requested(
    clone: Path, sm: Softmax, version: str, strategy: str,
    request: str, league_id: str, player_id: str,
) -> dict:
    """Submit an uploaded version directly (the user asked, or the orchestrator chose it), without an
    improvement claim; ``request`` records why."""
    with _one_at_a_time(clone):
        _refuse_if_busy(clone)
        if not request.strip():
            raise Refused("missing_request", "State why this version should compete (quote the user or give the reason)")
        path = Path(strategy)
        if path.is_absolute() or ".." in path.parts:
            raise Refused("bad_strategy_path", "The strategy must be a repository-relative path")
        store = RecordStore(Layout(clone))
        meta, _ = store.load(version)
        platform_id = _uploaded_policy(store, version)
        if platform_id is None:
            raise Refused("no_upload", f"{version} has no uploaded event on main")
        source = gitops.show(clone, "HEAD", strategy)
        if source is None:
            raise Refused("no_candidate", f"{strategy} is not on canonical main")
        if not _requested_source_matches(meta, source):
            source_path = (meta.get("build") or {}).get("source_path")
            raise Refused(
                "strategy_mismatch",
                f"{strategy} does not match the uploaded build; its recorded source is "
                f"{source_path or 'unknown'}",
                {"source_path": source_path},
            )
        _require_identity(sm)
        disagreement = conflict(clone, sm, league_id, player_id)
        if disagreement is not None:
            raise Refused("champion_conflict", disagreement)
        current = latest_champion(clone)
        if current is not None and current.platform_policy_version_id == platform_id:
            return current.to_dict()
        record = _new("requested", version, platform_id, league_id, player_id)
        snapshot = f"{SUBMISSIONS_DIR}/sources/{record.id}.md"
        (clone / snapshot).parent.mkdir(parents=True, exist_ok=True)
        (clone / snapshot).write_bytes(source)
        _move(clone, record, "intent", f"User requested submission: {request}", (snapshot,))
        return _send(clone, sm, record)


def bootstrap(clone: Path, sm: Softmax, version_id: str, league_id: str, player_id: str) -> dict:
    """Submit the uploaded starter-policy version as the first champion (fresh install)."""
    with _one_at_a_time(clone):
        return _bootstrap(clone, sm, version_id, league_id, player_id)


def _bootstrap(clone: Path, sm: Softmax, version_id: str, league_id: str, player_id: str) -> dict:
    _refuse_if_busy(clone)
    if latest_champion(clone) is not None:
        raise Refused(
            "champion_exists",
            "the loop already recorded a champion; a bootstrap is only for a fresh install",
        )
    store = RecordStore(Layout(clone))
    meta, _ = store.load(version_id)
    platform_id = _uploaded_policy(store, version_id)
    if platform_id is None:
        raise Refused("no_upload", f"{version_id} has no uploaded event on main")
    shipped = gitops.show(clone, "HEAD", CHAMPION_PATH)
    shipped_hash = hashlib.sha256(shipped).hexdigest() if shipped is not None else None
    built = [value for value in _strategy_source_hashes(meta) if value != shipped_hash]
    if built:
        raise Refused(
            "not_shipped_strategy",
            f"{version_id} compiled a STRATEGY.md with sha256 {built[0]}, not the shipped "
            f"{CHAMPION_PATH} ({shipped_hash}); a bootstrap submits only the shipped strategy",
        )
    _require_identity(sm)
    shown = sm.leaderboard_champion(player_id, league_id)
    if shown is not None:
        raise Refused("champion_exists", f"the {league_id} league already shows {shown} for us")
    submission = _new("bootstrap", version_id, platform_id, league_id, player_id)
    _move(clone, submission, "intent", f"bootstrap {version_id}")
    return _send(clone, sm, submission)


def _send(clone: Path, sm: Softmax, submission: ChampionSubmission) -> dict:
    """The single platform submit of an ``intent`` submission."""
    result = sm.submit(submission.platform_policy_version_id, submission.league_id, submission.player_id)
    if isinstance(result, PermitRequired):
        submission.waiting = None
        _move(
            clone, submission, "aborted",
            f"platform refused submission (ask {result.ask}): {result.reason}",
        )
        raise Refused(
            "permit_required", f"this attempt ended: {result.reason}; after the user answers "
            "the ask, an explicit submit is a new attempt",
            {"submission": submission.id, "ask": result.ask},
        )
    if isinstance(result, LeagueNotChosen):
        submission.waiting = {"reason": "league_not_chosen", "detail": result.reason}
        _move(clone, submission, "intent", "waiting for the coach to choose the league")
        raise Refused(
            "league_not_chosen", result.reason, {"submission": submission.id}
        )
    submission.waiting = None
    submission.submission_id = result
    extra: tuple[str, ...] = ()
    if submission.version_id is not None:
        store = RecordStore(Layout(clone))
        event = store.add_event(
            submission.version_id,
            "submitted",
            {
                "submission_id": result,
                "policy_version_id": submission.platform_policy_version_id,
                "league_id": submission.league_id,
                "champion_submission_id": submission.id,
            },
        )
        extra = (store.path_of(event).relative_to(clone).as_posix(),)
    _move(clone, submission, "submitted", f"submission {result}", extra)
    return submission.to_dict()


def accept(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    """Record the league's current champion as the loop's (resolves a champion conflict)."""
    with _one_at_a_time(clone):
        return _accept(clone, sm, league_id, player_id)


def _accept(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    _refuse_if_busy(clone)
    shown = sm.leaderboard_champion(player_id, league_id)
    submission = _new("accepted", None, shown, league_id, player_id)
    _move(clone, submission, "published", f"accepted the league's champion {shown or 'none'}")
    return submission.to_dict()


# --- status: advance the submission in flight ---


def _advance_intent(clone: Path, sm: Softmax, submission: ChampionSubmission) -> None:
    waiting = submission.waiting or {}
    # Permit refusals already ended in _send. Remaining league-selection waits use
    # the normal submit route; interrupted sends are reconciled by platform identity.
    submission.waiting = None
    if waiting:
        _move(clone, submission, "intent", "sending again")
        try:
            _send(clone, sm, submission)
        except Refused:
            pass  # still waiting; _send recorded why
        return
    # An intent with no recorded outcome: the submit may or may not have reached the platform.
    found = [
        s for s in sm.submissions(submission.player_id)
        if s["policy_version_id"] == submission.platform_policy_version_id
    ]
    if found:
        submission.submission_id = found[-1]["submission_id"]
        _move(clone, submission, "submitted", "found on the platform after an interruption")
    else:
        _move(clone, submission, "aborted", "interrupted before the platform had a submission")


def _advance_submitted(clone: Path, sm: Softmax, submission: ChampionSubmission) -> None:
    state = sm.submission_state(
        submission.player_id, submission.league_id, submission.platform_policy_version_id
    )
    if state.stage == "rejected":
        _move(clone, submission, "rejected", f"the league rejected it ({state.detail})")
    elif state.stage == "competing" and state.is_champion:
        _move(clone, submission, "recorded", "the league shows it as our champion")


def _advance_recorded(clone: Path, submission: ChampionSubmission) -> None:
    extra: tuple[str, ...] = ()
    if submission.kind in ("experiment", "requested"):
        # Always published: a local copy that already matches (a failed publish writes its
        # content back) says nothing about canonical main.
        source = (
            candidate_path(submission.experiment) if submission.kind == "experiment"
            else f"{SUBMISSIONS_DIR}/sources/{submission.id}.md"
        )
        content = (clone / source).read_bytes()
        compiled = True
        if submission.kind == "requested":
            meta, _ = RecordStore(Layout(clone)).load(submission.version_id)
            if not _requested_source_matches(meta, content):
                raise Refused("strategy_mismatch", "The saved submission source does not match the build")
            compiled = bool(_strategy_source_hashes(meta))
        # A prebuilt artifact is not a STRATEGY.md: as at bootstrap, strategy/STRATEGY.md keeps
        # the strategy candidates build from, and the saved source is the provenance.
        if compiled:
            (clone / CHAMPION_PATH).write_bytes(content)
            extra = (CHAMPION_PATH,)
    _move(clone, submission, "published", "champion recorded on main", extra)


def status(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    """Advance the submission in flight by one step, then report."""
    with _one_at_a_time(clone):
        return _status(clone, sm, league_id, player_id)


def _status(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    submission = in_flight(clone)
    if submission is not None and submission.player_id is None:
        submission.player_id = player_id  # a record from before submissions named their player
    if submission is not None and submission.stage == "intent":
        _advance_intent(clone, sm, submission)
    elif submission is not None and submission.stage == "submitted":
        _advance_submitted(clone, sm, submission)
    if submission is not None and submission.stage == "recorded":
        _advance_recorded(clone, submission)
    flying = in_flight(clone)
    champion = latest_champion(clone)
    return {
        "in_flight": flying.to_dict() if flying else None,
        "champion": champion.to_dict() if champion else None,
        "league_champion": sm.leaderboard_champion(player_id, league_id),
        "conflict": conflict(clone, sm, league_id, player_id),
    }
