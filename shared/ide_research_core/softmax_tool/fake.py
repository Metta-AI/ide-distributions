"""Persistent in-filesystem fake of the Softmax platform, with env-driven fault injection.

State lives in ``<canonical>/.supervisor/runtime/fake_softmax.json``; every operation is a
read-modify-write under a file lock, so separate processes share one fake platform.

Faults come from ``RESEARCH_FAKE_FAULTS`` (semicolon-separated ``name=value``):
``credits=N``, ``identity=user``, ``identity=rejected`` (``whoami`` raises
``CredentialRejected``, as for a revoked token), ``player=<id>``, ``expires_in=<seconds>``,
``fail_after=<op>`` (raise right after that op's state change is saved), ``xp_limit=N``
(creating a new XP request raises once N exist, like the platform's pending-request cap), and
``crash_after=<label>`` (read by runtime code through :func:`fault`). The coach's refusals of
Softmax hosting: ``permit=<acts>`` (comma-separated ``spend``, ``submit``) refuses a new XP
request (``spend``) or a league submission (``submit``) with ``PermitRequired``, as when the
coach's IDE Controls ask before it. Like the server, the same act names the same waiting ask
(state ``asks``: ref -> key); the coach's answers are state ``answers`` (key -> ``yes`` or
``no``; tests set them): a yes grants the act once (then ``used``), and after a no or a used
yes the act gets a new ask (``-<n+1>``, reason "the coach said no; a new ask waits" or "the
last yes was used; ..."). The platform still enforces these refusals; profiles do not poll their cogent records.
``over_cap=1`` makes a spend refusal the over-today's-cap one. ``league=none`` refuses both
acts with ``LeagueNotChosen`` (no league chosen), ``league=<id>`` likewise (the coach chose that
one). A request whose state
entry has ``"failed": true`` (tests set it in the state file) reports status ``failed``.

``whoami`` answers for the credential, like the platform: when ``SOFTMAX_PLAYER_CREDENTIAL`` is
JSON with a ``player_id`` (a fake-only field), that is the player; otherwise the credential is
taken to belong to ``RESEARCH_PLAYER_ID``. So a wrong ``RESEARCH_PLAYER_ID`` with a
credential that names its player fails the C6 check, as on the real platform. A credential
starting ``cga_`` (an agent token) has no expiry, as on the real platform, and ignores
``expires_in``.

Like the real client, the mutating calls (``upload``, a new ``xp_create``, ``submit``) re-check
``whoami`` first and raise ``IdentityError`` unless it is the expected unexpired Player (C6), so
an ``identity=``, ``player=`` or ``expires_in=`` fault stops a running worker at the boundary.

Seeds (A12): like the platform, a spec's ``game_config_overrides.seed`` pins the request's
episodes to that seed. Results of a seeded request depend only on (seed, our roster policy,
episode index, seat), so the same seed and policy give the same episode in any request;
unseeded results depend on the request id.

Mechanics of seeded results (``_seeded_metrics``; fakegame's ``GAME.md`` documents them for the
agent). ``upload`` keeps the ``parameters`` of a fakegame ``policy.json`` artifact; a policy
uploaded without them (or never uploaded here) plays ``DEFAULT_PARAMETERS`` with noise keyed
on its id. ``score`` = 50 + a per-seed board term (+-4) + 0.5 per ``aggression`` point above 3
+ episode noise (+-1) keyed on (seed, episode, seat, aggression). ``errors`` = an integer 0..2
keyed on (seed, episode, seat, patience) + ``max(0, 8 - patience)``. So a policy that differs
only in ``patience`` plays the same score on every seed and fewer errors when patience is
higher (down to the floor at 8): a real, measurable lever for the fake research cycle.

League reads: ``league_episodes`` is generated, not stored. Episode ``i`` plays seed
``1000 + i // 3`` with ``LEAGUE_POLICIES[i % 3]`` in seat 0 and ``LEAGUE_HOUSE`` in seat 1, so
each seed is played by three policies against the same opponent (paired noise is measurable).
Its ``results`` (``{seed, game_version, episodes: [{episode, seat, seed, score, errors}]}``) is
what fakegame's evaluate binding reads; score is a policy shift plus a shared per-seed term plus
per-policy noise. ``standings`` is two house players plus our player once it has submitted.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ide_research_core.io.docs import read_json, write_json
from ide_research_core.io.locks import file_lock
from ide_research_core.softmax_tool.identity import (
    CredentialRejected,
    IdentityError,
    identity_problems,
)
from ide_research_core.softmax_tool.protocol import (
    ANSWER_NO,
    ANSWER_USED,
    ANSWER_YES,
    PERMIT_DECLINED,
    PERMIT_OVER_CAP,
    PERMIT_WAITING,
    ArtifactType,
    CoachRefusal,
    CreditDenied,
    Identity,
    LeagueNotChosen,
    PermitRequired,
    SubmissionState,
    XpHandle,
    XpStatus,
)
from ide_research_core.softmax_tool.xp import handle_order

FAULTS_ENV = "RESEARCH_FAKE_FAULTS"
CREDENTIAL_ENV = "SOFTMAX_PLAYER_CREDENTIAL"
DEFAULT_PLAYER_ID = "ply_fake"
DEFAULT_CREDITS = 100
DEFAULT_EPISODES = 2
LEAGUE_POLICIES = ("fake-alpha:v1", "fake-beta:v1", "fake-gamma:v1")
LEAGUE_HOUSE = "fake-house:v1"
LEAGUE_FIRST_SEED = 1000
DEFAULT_PARAMETERS = {"aggression": 3, "patience": 5}  # fakegame's seed STRATEGY.md
# The server's words (metta observatory_core.cogents.league).
NOT_CHOSEN = (
    "Your coach has not chosen a league for this cogent yet; ask them to pick one in the IDE."
)


def parse_faults(env: Mapping[str, str]) -> dict[str, str]:
    faults: dict[str, str] = {}
    for item in env.get(FAULTS_ENV, "").split(";"):
        if item.strip():
            name, _, value = item.partition("=")
            faults[name.strip()] = value.strip()
    return faults


def fault(env: Mapping[str, str], name: str) -> str | None:
    return parse_faults(env).get(name)


def credential_player(env: Mapping[str, str]) -> str | None:
    """The ``player_id`` a fake credential names, or None (module docstring)."""
    try:
        credential = json.loads(env.get(CREDENTIAL_ENV) or "")
    except ValueError:
        return None
    player = credential.get("player_id") if isinstance(credential, dict) else None
    return player if isinstance(player, str) and player else None


def _short_hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _unit(*parts: object) -> float:
    return int(_short_hash(*parts), 16) / 16**12


def _league_metrics(policy: str, seed: int) -> dict:
    score = 50 + (_unit(policy, "shift") - 0.5) * 4
    score += (_unit(seed, "board") - 0.5) * 8 + (_unit(policy, seed, "own") - 0.5) * 2
    return {"score": round(score, 4), "errors": int(_unit(policy, seed, "errors") * 5)}


def _league_episode(league_id: str, index: int, game_version: str) -> dict:
    seed = LEAGUE_FIRST_SEED + index // len(LEAGUE_POLICIES)
    policies = (LEAGUE_POLICIES[index % len(LEAGUE_POLICIES)], LEAGUE_HOUSE)
    return {
        "episode_id": f"ereq_fake_{index:05d}",
        "round_id": f"round_fake_{index // 30:03d}",
        "seed": seed,
        "game_version": game_version,
        "coworld_id": "cow_fake",
        "seats": [
            {"position": seat, "policy_version_id": policy, "player_id": None, "is_filler": False}
            for seat, policy in enumerate(policies)
        ],
        "results": {
            "seed": seed,
            "game_version": game_version,
            "episodes": [
                {"episode": 0, "seat": seat, "seed": seed, **_league_metrics(policy, seed)}
                for seat, policy in enumerate(policies)
            ],
        },
    }


def _artifact_parameters(artifact: Path) -> dict | None:
    """The ``parameters`` of a fakegame ``policy.json`` artifact, or None for any other file."""
    try:
        policy = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    parameters = policy.get("parameters") if isinstance(policy, dict) else None
    return parameters if isinstance(parameters, dict) else None


def _seeded_metrics(
    seed: int, index: int, slot: int, policy: str | None, parameters: dict | None
) -> dict:
    """One seat's metrics in a seeded episode (module docstring, "Mechanics")."""
    played = {**DEFAULT_PARAMETERS, **(parameters or {})}
    aggression, patience = played["aggression"], played["patience"]
    score_key = aggression if parameters is not None else policy
    errors_key = patience if parameters is not None else policy
    score = 50 + (_unit(seed, "board") - 0.5) * 8 + 0.5 * (aggression - 3)
    score += (_unit(seed, index, slot, "score", score_key) - 0.5) * 2
    errors = int(_unit(seed, index, slot, "errors", errors_key) * 3) + max(0, 8 - patience)
    return {"score": round(score, 4), "errors": errors}


def _roster_slots(spec: dict) -> list[int]:
    """The roster's fixed seat slots in order, or ``[0]`` (one seat) without a roster."""
    slots = sorted(
        entry["slot"] for entry in spec.get("roster") or [] if entry.get("slot", -1) >= 0
    )
    return slots or [0]


def _roster_policy(spec: dict) -> str | None:
    """Our policy: the first fixed ``policy_ref`` in the roster (our seats come first)."""
    for entry in spec.get("roster") or []:
        ref = (entry.get("player") or {}).get("policy_ref")
        if ref:
            return ref
    return None


class FakeSoftmax:
    def __init__(
        self,
        canonical: Path,
        env: Mapping[str, str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        runtime_dir = Path(canonical) / ".supervisor" / "runtime"
        self._state_path = runtime_dir / "fake_softmax.json"
        self._lock_path = runtime_dir / "fake_softmax.lock"
        self._player_id = env.get("RESEARCH_PLAYER_ID") or DEFAULT_PLAYER_ID
        self._credential_player = credential_player(env) or self._player_id
        self._agent_token = (env.get(CREDENTIAL_ENV) or "").startswith("cga_")
        self._faults = parse_faults(env)
        self._clock = clock

    # --- state plumbing -------------------------------------------------

    def _initial_state(self) -> dict:
        return {
            "credits": DEFAULT_CREDITS,
            "game_version": "fake-1",
            "game_source_pin": None,
            "polls_to_complete": 1,
            "xp": {},
            "uploads": {},
            "submissions": [],
        }

    def _operate(self, op: str, action: Callable[[dict], object], *, persist: bool = True):
        """Run ``action`` on the state under the lock, save it, then apply ``fail_after``."""
        with file_lock(self._lock_path):
            state = (
                read_json(self._state_path) if self._state_path.exists() else self._initial_state()
            )
            result = action(state)
            if persist:
                write_json(self._state_path, state)
        if self._faults.get("fail_after") == op:
            raise RuntimeError(f"injected fault after {op}")
        return result

    # --- identity and account -------------------------------------------

    def whoami(self) -> Identity:
        if self._faults.get("identity") == "rejected":
            raise CredentialRejected("Softmax did not accept the player credential (fake)")
        lifetime = timedelta(seconds=float(self._faults.get("expires_in", 24 * 3600)))
        identity = Identity(
            subject_type="user" if self._faults.get("identity") == "user" else "player",
            player_id=self._faults.get("player", self._credential_player),
            expires_at=None if self._agent_token else self._clock() + lifetime,
        )
        return self._operate("whoami", lambda _state: identity, persist=False)

    def require_player(self) -> Identity:
        identity = self.whoami()
        problems = identity_problems(identity, self._player_id, self._clock())
        if problems:
            raise IdentityError("; ".join(problems))
        return identity

    def _current_credits(self, state: dict) -> int:
        return int(self._faults["credits"]) if "credits" in self._faults else state["credits"]

    def _coach_refusal(
        self, state: dict, action: str, ref: str, league_id: str
    ) -> CoachRefusal | None:
        """The ``permit``/``league`` faults (module docstring): the league choice first, as
        the server checks it before the permit."""
        chosen = self._faults.get("league")
        if chosen is not None:
            if chosen == "none":
                return LeagueNotChosen(NOT_CHOSEN)
            act = "a submission to" if action == "submit" else "a league game in"
            return LeagueNotChosen(
                f"This cogent plays {chosen}; your coach chose it, and {act} {league_id} is "
                "refused."
            )
        if action not in self._faults.get("permit", "").split(","):
            return None
        asks, answers = state.setdefault("asks", {}), state.setdefault("answers", {})
        prefix = f"permit-{action}-{_short_hash(action, ref)}-"
        ask = asks.setdefault(ref, f"{prefix}1")
        answer = answers.get(ask)
        if answer == ANSWER_YES:
            answers[ask] = ANSWER_USED
            return None
        why, status = "the ask waits for the coach", PERMIT_WAITING
        if answer in (ANSWER_NO, ANSWER_USED):
            ask = asks[ref] = f"{prefix}{int(ask.rsplit('-', 1)[1]) + 1}"
            why = "the coach said no; a new ask waits"
            if answer == ANSWER_USED:
                why = "the last yes was used; a new ask waits"
            status = PERMIT_DECLINED if answer == ANSWER_NO else PERMIT_WAITING
        elif action == "spend" and self._faults.get("over_cap"):
            why, status = "40 credits is more than the 10 left today", PERMIT_OVER_CAP
        return PermitRequired(f"{action} on {ref}: {why} (ask asks/{ask})", ask, status)


    def episode_cost(self) -> float | None:
        return 1.0  # the fake deducts one credit per (one-episode) request

    def credits(self) -> int:
        return self._operate("credits", self._current_credits, persist=False)

    def game_version(self, league_id: str) -> str:
        return self._operate(
            "game_version", lambda state: str(state["game_version"]).strip(), persist=False
        )

    def game_source_pin(self, league_id: str) -> str | None:
        return self._operate(
            "game_source_pin", lambda state: state.get("game_source_pin"), persist=False
        )

    # --- XP requests ----------------------------------------------------

    def xp_create(
        self,
        experiment: str,
        batch: int,
        attempt: int,
        spec: dict,
        seed: int | None = None,
        arm: str | None = None,
        known: list[XpHandle] | None = None,
    ) -> XpHandle | CreditDenied | PermitRequired | LeagueNotChosen:
        """``known`` is accepted for protocol parity and ignored: the fake's state is the truth."""
        key = (
            (experiment, batch, attempt)
            if seed is None
            else (experiment, batch, attempt, seed, arm)
        )
        request_id = f"xp_{_short_hash(*key)}"
        exists = self._operate("xp_lookup", lambda state: request_id in state["xp"], persist=False)
        if not exists:  # like the client: an existing request is reused without a new check
            self.require_player()

        def action(state: dict):
            if request_id not in state["xp"]:
                if self._current_credits(state) <= 0:
                    return CreditDenied("no credits")
                tag = ":".join(str(part) for part in key)
                league = str(spec.get("league_id", "-"))
                refusal = self._coach_refusal(state, "spend", f"spend:{tag}", league)
                if refusal is not None:
                    return refusal
                if "xp_limit" in self._faults and len(state["xp"]) >= int(self._faults["xp_limit"]):
                    raise RuntimeError("injected fault: pending XP request limit (429)")
                state["xp"][request_id] = {
                    "experiment": experiment,
                    "batch": batch,
                    "attempt": attempt,
                    "episodes": int(
                        spec.get("num_episodes", spec.get("episodes", DEFAULT_EPISODES))
                    ),
                    "polls": 0,
                    "seed": seed,
                    "arm": arm,
                    "game_seed": (spec.get("game_config_overrides") or {}).get("seed"),
                    "policy": _roster_policy(spec),
                    "slots": _roster_slots(spec),
                }
                state["credits"] = max(0, state["credits"] - 1)
            return XpHandle(request_id, experiment, batch, attempt, seed, arm)

        return self._operate("xp_create", action)

    def xp_status(self, handle: XpHandle) -> XpStatus:
        def action(state: dict) -> XpStatus:
            request = state["xp"][handle.request_id]
            request["polls"] += 1
            total = request["episodes"]
            if request.get("failed"):  # set by tests in the state file: a failed request
                return XpStatus("failed", 0, total)
            if request["polls"] >= state["polls_to_complete"]:
                return XpStatus("completed", total, total)
            return XpStatus("running", 0, total)

        return self._operate("xp_status", action)

    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]:
        def read(state: dict) -> dict:
            request = dict(state["xp"][handle.request_id])
            upload = state["uploads"].get(request.get("policy") or "") or {}
            request["parameters"] = upload.get("parameters")
            return request

        request = self._operate("xp_artifacts", read, persist=False)
        game_seed = request.get("game_seed")
        source = handle.request_id if game_seed is None else f"{game_seed}|{request['policy']}"
        episodes = []  # one entry per (episode, seat), like a multi-seat game's results
        for index in range(request["episodes"]):
            for slot in request.get("slots") or [0]:
                if game_seed is None:
                    metrics = {
                        "score": int(_short_hash(source, index, slot, "score"), 16) % 100,
                        "errors": int(_short_hash(source, index, slot, "errors"), 16) % 5,
                    }
                else:
                    metrics = _seeded_metrics(
                        game_seed, index, slot, request["policy"], request.get("parameters")
                    )
                episode = {"episode": index, "seat": slot, **metrics}
                if game_seed is not None:
                    episode["seed"] = game_seed
                episodes.append(episode)
        results = {"request_id": handle.request_id, "episodes": episodes}
        if game_seed is not None:
            results["seed"] = game_seed
        results_path = Path(dest) / "results.json"
        write_json(results_path, results)
        return [results_path]

    def xp_recover(self, experiment: str) -> list[XpHandle]:
        def action(state: dict) -> list[XpHandle]:
            return [
                XpHandle(
                    request_id,
                    r["experiment"],
                    r["batch"],
                    r["attempt"],
                    r.get("seed"),
                    r.get("arm"),
                )
                for request_id, r in state["xp"].items()
                if r["experiment"] == experiment
            ]

        return sorted(self._operate("xp_recover", action, persist=False), key=handle_order)

    # --- policies -------------------------------------------------------

    def upload(
        self, artifact: Path, name: str, *, player_id: str, artifact_type: ArtifactType = "file"
    ) -> str:
        self.require_player()
        parameters = _artifact_parameters(Path(artifact))

        def action(state: dict) -> str:
            policy_version_id = f"pv_{_short_hash(name, len(state['uploads']))}"
            state["uploads"][policy_version_id] = {"name": name, "player_id": player_id}
            if parameters is not None:
                state["uploads"][policy_version_id]["parameters"] = parameters
            return policy_version_id

        return self._operate("upload", action)

    def submit(
        self, policy_version_id: str, league_id: str, player_id: str
    ) -> str | PermitRequired | LeagueNotChosen:
        self.require_player()

        def action(state: dict) -> str | PermitRequired | LeagueNotChosen:
            ref = f"league:{league_id}:{policy_version_id}"
            refusal = self._coach_refusal(state, "submit", ref, league_id)
            if refusal is not None:
                return refusal
            submission_id = (
                f"sub_{_short_hash(policy_version_id, league_id, len(state['submissions']))}"
            )
            state["submissions"].append(
                {
                    "submission_id": submission_id,
                    "policy_version_id": policy_version_id,
                    "league_id": league_id,
                    "player_id": player_id,
                }
            )
            return submission_id

        return self._operate("submit", action)

    def retire_other_memberships(
        self, league_id: str, keep_policy_version_id: str
    ) -> tuple[list[str], list[str]]:
        # The fake backend admits every submission as its own membership and never retires one.
        return [], []

    def submissions(self, player_id: str) -> list[dict]:
        def action(state: dict) -> list[dict]:
            return [s for s in state["submissions"] if s["player_id"] == player_id]

        return self._operate("submissions", action, persist=False)

    def submission_state(
        self, player_id: str, league_id: str, policy_version_id: str
    ) -> SubmissionState:
        """Like the platform with immediate admission: our latest submission in the league is
        the champion, earlier ones are benched. A state entry ``memberships[<policy>] =
        {stage, is_champion}`` (tests set it) overrides that."""

        def action(state: dict) -> SubmissionState:
            override = (state.get("memberships") or {}).get(policy_version_id)
            if override is not None:
                return SubmissionState(override["stage"], bool(override.get("is_champion")))
            ours = [
                s["policy_version_id"]
                for s in state["submissions"]
                if s["player_id"] == player_id and s["league_id"] == league_id
            ]
            if policy_version_id not in ours:
                return SubmissionState("unknown")
            return SubmissionState("competing", ours[-1] == policy_version_id)

        return self._operate("submission_state", action, persist=False)

    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None:
        matching = [
            s["policy_version_id"]
            for s in self.submissions(player_id)
            if s["league_id"] == league_id
        ]
        return matching[-1] if matching else None

    # --- league reads ---------------------------------------------------

    def latest_league_result(self, player_id: str, league_id: str) -> dict | None:
        # The fake backend does not execute league matches.
        return None

    def league_episodes(self, league_id: str, limit: int) -> list[dict]:
        version = self.game_version(league_id)
        return [_league_episode(league_id, index, version) for index in range(limit)]

    def standings(self, league_id: str) -> list[dict]:
        board = [("ply_house_a", "House A", 61.5, 12), ("ply_house_b", "House B", 48.25, 12)]
        if self.leaderboard_champion(self._player_id, league_id) is not None:
            board.append((self._player_id, "Autoresearch", 55.0, 1))
        board.sort(key=lambda row: -row[2])
        return [
            {
                "division_id": "div_fake",
                "division_name": "Fake Division",
                "level": 1,
                "rank": rank,
                "player_id": player_id,
                "player_name": name,
                "score": score,
                "rounds_played": rounds,
            }
            for rank, (player_id, name, score, rounds) in enumerate(board, start=1)
        ]
