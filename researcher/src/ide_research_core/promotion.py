"""Promotion: the orchestrator submits a candidate to the live league and records the champion.

Researchers never submit (their deny list blocks the commands). A researcher's card ends with
an experiment whose ``decision.outcome`` is ``promote``, a stored ``stats evaluate`` outcome,
a candidate binding (the uploaded version) and the candidate strategy at
``strategy/candidates/<E>/STRATEGY.md``, all merged into canonical ``main``. The orchestrator
then runs ``promotion submit <E>`` in its own clone.

A promotion is one JSON file ``promotions/<P-id>.json`` on canonical ``main`` (only the
orchestrator writes there; ``workspace.integrate`` refuses it from a card). Stages, in
write-ahead order so an interrupted submit is never sent twice blindly:

- ``intent``: committed and pushed before anything reaches the platform;
- ``submitted``: the platform accepted the submission (``submission_id``);
- ``recorded``: the league shows the policy as our champion; for an experiment the candidate
  strategy is then copied to ``strategy/STRATEGY.md``;
- ``published``: the champion file is on ``main``. Terminal, like ``aborted`` and ``rejected``.

``status`` advances the one promotion in flight by at most one platform read and returns
where things stand, including a champion conflict: the league shows a policy for us that the
loop did not record as its champion (someone submitted by hand, or the league dropped ours).
A coach refusal (a permit ask, no league chosen) leaves the promotion at ``intent`` with
``waiting`` set; ``status`` sends again only after the coach answers. ``accept`` records the
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
from ide_research_core.softmax_tool.identity import identity_problems
from ide_research_core.softmax_tool.protocol import (
    ANSWER_NO,
    ANSWER_USED,
    ANSWER_WAITING,
    LeagueNotChosen,
    PermitRequired,
    Refused,
    Softmax,
)
from ide_research_core.stats.design import EvaluationDesign, design_sha256

PROMOTIONS_DIR = "promotions"
CHAMPION_PATH = "strategy/STRATEGY.md"
PROMOTION_LOCK = "promotion.lock"
IN_FLIGHT = ("intent", "submitted", "recorded")
CHAMPIONS = ("published",)  # the stage that makes a promotion's policy the loop's champion


def candidate_path(experiment: str) -> str:
    """Where the researcher's card commits the candidate strategy."""
    return f"strategy/candidates/{experiment}/STRATEGY.md"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


@dataclass
class Promotion:
    id: str
    kind: str  # experiment | bootstrap | accepted
    version_id: str | None
    platform_policy_version_id: str | None
    league_id: str
    stage: str
    experiment: str | None = None
    submission_id: str | None = None
    waiting: dict | None = None  # the coach's refusal while at intent
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _path(clone: Path, promotion_id: str) -> Path:
    return clone / PROMOTIONS_DIR / f"{promotion_id}.json"


def load_all(clone: Path) -> list[Promotion]:
    """Every promotion on canonical ``main``, oldest first (ids are ULIDs). Read from the bare
    repository, not the clone's checkout: a researcher's clone moves only when it integrates,
    and the orchestrator publishes promotions from its own clone."""
    bare = workspace.canonical_of(clone)
    listed = gitops._run(bare, "ls-tree", "--name-only", workspace.MAIN, f"{PROMOTIONS_DIR}/")
    paths = sorted(
        path for path in listed.stdout.decode().split() if Path(path).name.startswith("P-")
    )
    return [Promotion(**json.loads(gitops.show(bare, workspace.MAIN, path))) for path in paths]


@contextmanager
def _one_at_a_time(clone: Path) -> Iterator[None]:
    """Hold the host's promotion lock (a file in the bare repository) from the checks to the
    recorded platform result. The orchestrator's chat turn and its supervisor both reach this
    code; without the lock two submits could both pass the busy check, and a ``status`` between
    a submit's ``intent`` and its send would abort a live submit."""
    with file_lock(workspace.canonical_of(clone) / PROMOTION_LOCK, timeout_s=workspace.LOCK_TIMEOUT_S):
        workspace.sync(clone)
        yield


def in_flight(clone: Path) -> Promotion | None:
    flying = [p for p in load_all(clone) if p.stage in IN_FLIGHT]
    return flying[-1] if flying else None


def latest_champion(clone: Path) -> Promotion | None:
    champions = [p for p in load_all(clone) if p.stage in CHAMPIONS]
    return champions[-1] if champions else None


def _move(clone: Path, promotion: Promotion, stage: str, detail: str, extra: tuple[str, ...] = ()):
    """Record ``stage`` and publish it (with ``extra`` paths) to canonical ``main``."""
    promotion.stage = stage
    promotion.history.append({"stage": stage, "at": _now(), "detail": detail})
    relative = _path(clone, promotion.id).relative_to(clone).as_posix()
    content = json.dumps(promotion.to_dict(), indent=2, sort_keys=True) + "\n"
    files = {relative: content.encode(), **workspace.read_files(clone, list(extra))}
    workspace.publish(clone, files, f"promotion {promotion.id}: {stage}")


# --- the league ---


def conflict(clone: Path, sm: Softmax, league_id: str, player_id: str) -> str | None:
    """Why the league and the loop disagree about our champion, or None.

    The loop's champion is the latest ``published`` promotion. A promotion still at
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
    experiment against any other policy can never promote."""
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
            "promotion_in_flight",
            f"promotion {flying.id} is {flying.stage}; one promotion runs at a time",
            {"promotion": flying.id},
        )


def _check_outcome(store: RecordStore, experiment: str, meta: dict, platform_id: str) -> dict:
    """The stored ``stats evaluate`` outcome, refused unless eligible and still current."""
    layout = store.layout
    outcome_path = layout.stats / "outcomes" / f"{experiment}.json"
    if not outcome_path.exists():
        raise Refused("no_outcome", f"no stored outcome {outcome_path}; run stats evaluate")
    outcome = read_json(outcome_path)
    if outcome.get("promote_eligible") is not True:
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


def _require_identity(sm: Softmax, player_id: str) -> None:
    problems = identity_problems(sm.whoami(), player_id, datetime.datetime.now(datetime.UTC))
    if problems:
        raise Refused("identity", "; ".join(problems))


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


def _uploaded_policy(store: RecordStore, version_id: str) -> str | None:
    for event_id in store.find("version_event", version=version_id, kind="uploaded"):
        return store.load(event_id)[0]["data"]["policy_version_id"]
    return None


# --- submit, bootstrap, accept ---


def _new(kind: str, version_id: str | None, platform_id: str | None, league_id: str, **fields):
    return Promotion(f"P-{ULID()}", kind, version_id, platform_id, league_id, "new", **fields)


def submit(clone: Path, sm: Softmax, experiment: str, league_id: str, player_id: str) -> dict:
    """Run the guards, record ``intent``, then submit (``_send``)."""
    with _one_at_a_time(clone):
        return _submit(clone, sm, experiment, league_id, player_id)


def _submit(clone: Path, sm: Softmax, experiment: str, league_id: str, player_id: str) -> dict:
    _refuse_if_busy(clone)
    store = RecordStore(Layout(clone))
    meta, _ = store.load(experiment)
    decision = (meta.get("decision") or {}).get("outcome")
    if decision != "promote":
        raise Refused("not_promote", f"{experiment} decision is {decision!r}, not 'promote'")
    binding = meta.get("candidate_binding")
    if not binding:
        raise Refused("no_binding", f"{experiment} has no candidate_binding")
    if not (clone / candidate_path(experiment)).is_file():
        raise Refused("no_candidate", f"{candidate_path(experiment)} is not on main")
    platform_id = binding["platform_policy_version_id"]
    outcome = _check_outcome(store, experiment, meta, platform_id)
    _require_identity(sm, player_id)
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
    promotion = _new(
        "experiment", binding["version_id"], platform_id, league_id, experiment=experiment
    )
    _move(clone, promotion, "intent", f"submit {experiment}")
    return _send(clone, sm, promotion)


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
            f"{CHAMPION_PATH} ({shipped_hash}); a bootstrap promotes only the shipped strategy",
        )
    _require_identity(sm, player_id)
    shown = sm.leaderboard_champion(player_id, league_id)
    if shown is not None:
        raise Refused("champion_exists", f"the {league_id} league already shows {shown} for us")
    promotion = _new("bootstrap", version_id, platform_id, league_id)
    _move(clone, promotion, "intent", f"bootstrap {version_id}")
    return _send(clone, sm, promotion)


def _send(clone: Path, sm: Softmax, promotion: Promotion) -> dict:
    """The single platform submit of an ``intent`` promotion."""
    result = sm.submit(promotion.platform_policy_version_id, promotion.league_id)
    if isinstance(result, PermitRequired):
        promotion.waiting = {"reason": "permit_required", "ask": result.ask, "detail": result.reason}
        _move(clone, promotion, "intent", "waiting for the coach's permit")
        raise Refused(
            "permit_required", f"the coach must approve the submission: {result.reason}",
            {"promotion": promotion.id, "ask": result.ask},
        )
    if isinstance(result, LeagueNotChosen):
        promotion.waiting = {"reason": "league_not_chosen", "detail": result.reason}
        _move(clone, promotion, "intent", "waiting for the coach to choose the league")
        raise Refused(
            "league_not_chosen", result.reason, {"promotion": promotion.id}
        )
    promotion.waiting = None
    promotion.submission_id = result
    extra: tuple[str, ...] = ()
    if promotion.version_id is not None:
        store = RecordStore(Layout(clone))
        event = store.add_event(
            promotion.version_id,
            "submitted",
            {
                "submission_id": result,
                "policy_version_id": promotion.platform_policy_version_id,
                "league_id": promotion.league_id,
                "promotion_id": promotion.id,
            },
        )
        extra = (store.path_of(event).relative_to(clone).as_posix(),)
    _move(clone, promotion, "submitted", f"submission {result}", extra)
    return promotion.to_dict()


def release(sm: Softmax, policy_version_id: str, league_id: str, player_id: str) -> dict:
    """Release a core-loop program: submit the cogent's own uploaded ``<name>:v<N>`` to its league.

    No experiment, no promotion record and no champion check: the platform gates it by the
    coach's league choice and the ``submit`` permit. Nothing is recorded on canonical ``main``,
    so asking again is safe: a program the platform already has is reported as released, and a
    coach refusal comes back as ``released: false`` with its ``state`` and the permit ``ask``
    (``waiting`` until the coach says yes, then the same call sends it; ``declined`` is final).
    Only the orchestrator runs it; researchers' deny list blocks ``promotion release``.
    """
    _require_identity(sm, player_id)
    out = {"policy_version_id": policy_version_id, "league_id": league_id, "released": False}
    found = [
        row for row in sm.submissions(player_id)
        if row["policy_version_id"] == policy_version_id and row["league_id"] == league_id
    ]
    if found:
        return {**out, "released": True, "state": "submitted",
                "submission_id": found[-1]["submission_id"]}
    result = sm.submit(policy_version_id, league_id)
    if isinstance(result, PermitRequired):
        return {**out, "state": result.state, "reason": "permit_required",
                "ask": result.ask, "detail": result.reason}
    if isinstance(result, LeagueNotChosen):
        return {**out, "state": "league_not_chosen", "reason": "league_not_chosen",
                "detail": result.reason}
    return {**out, "released": True, "state": "submitted", "submission_id": result}


def accept(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    """Record the league's current champion as the loop's (a human said so)."""
    with _one_at_a_time(clone):
        return _accept(clone, sm, league_id, player_id)


def _accept(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    _refuse_if_busy(clone)
    shown = sm.leaderboard_champion(player_id, league_id)
    promotion = _new("accepted", None, shown, league_id)
    _move(clone, promotion, "published", f"accepted the league's champion {shown or 'none'}")
    return promotion.to_dict()


# --- status: advance the promotion in flight ---


def _advance_intent(clone: Path, sm: Softmax, promotion: Promotion, player_id: str) -> None:
    waiting = promotion.waiting or {}
    answer = None
    if waiting.get("reason") == "permit_required" and waiting.get("ask"):
        answer = sm.permit_answer(waiting["ask"])
        if answer in (None, ANSWER_WAITING):
            return
        if answer == ANSWER_NO:
            _move(clone, promotion, "aborted", "the coach declined the submission")
            return
    # Clear the wait on main before sending again, so a send that dies after the platform
    # accepted it is recovered by the submissions lookup below, not asked for again. A yes
    # already used means such a send happened: look it up now.
    promotion.waiting = None
    if waiting and answer != ANSWER_USED:
        _move(clone, promotion, "intent", "sending again")
        try:
            _send(clone, sm, promotion)
        except Refused:
            pass  # still waiting; _send recorded why
        return
    # An intent with no recorded outcome: the submit may or may not have reached the platform.
    found = [
        s for s in sm.submissions(player_id)
        if s["policy_version_id"] == promotion.platform_policy_version_id
    ]
    if found:
        promotion.submission_id = found[-1]["submission_id"]
        _move(clone, promotion, "submitted", "found on the platform after an interruption")
    else:
        _move(clone, promotion, "aborted", "interrupted before the platform had a submission")


def _advance_submitted(clone: Path, sm: Softmax, promotion: Promotion, player_id: str) -> None:
    state = sm.submission_state(
        player_id, promotion.league_id, promotion.platform_policy_version_id
    )
    if state.stage == "rejected":
        _move(clone, promotion, "rejected", f"the league rejected it ({state.detail})")
    elif state.stage == "competing" and state.is_champion:
        _move(clone, promotion, "recorded", "the league shows it as our champion")


def _advance_recorded(clone: Path, promotion: Promotion) -> None:
    extra: tuple[str, ...] = ()
    if promotion.kind == "experiment":
        # Always published: a local copy that already matches (a failed publish writes its
        # content back) says nothing about canonical main.
        (clone / CHAMPION_PATH).write_bytes((clone / candidate_path(promotion.experiment)).read_bytes())
        extra = (CHAMPION_PATH,)
    _move(clone, promotion, "published", "champion recorded on main", extra)


def status(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    """Advance the promotion in flight by one step, then report."""
    with _one_at_a_time(clone):
        return _status(clone, sm, league_id, player_id)


def _status(clone: Path, sm: Softmax, league_id: str, player_id: str) -> dict:
    promotion = in_flight(clone)
    if promotion is not None and promotion.stage == "intent":
        _advance_intent(clone, sm, promotion, player_id)
    elif promotion is not None and promotion.stage == "submitted":
        _advance_submitted(clone, sm, promotion, player_id)
    if promotion is not None and promotion.stage == "recorded":
        _advance_recorded(clone, promotion)
    flying = in_flight(clone)
    champion = latest_champion(clone)
    return {
        "in_flight": flying.to_dict() if flying else None,
        "champion": champion.to_dict() if champion else None,
        "league_champion": sm.leaderboard_champion(player_id, league_id),
        "conflict": conflict(clone, sm, league_id, player_id),
    }
