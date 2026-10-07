"""Persistent in-filesystem fake of the Softmax platform, with env-driven fault injection.

State lives in ``<canonical>/.supervisor/runtime/fake_softmax.json``; every operation is a
read-modify-write under a file lock, so separate processes share one fake platform.

Faults come from ``AUTORESEARCH_FAKE_FAULTS`` (semicolon-separated ``name=value``):
``credits=N``, ``identity=user``, ``player=<id>``, ``expires_in=<seconds>``,
``fail_after=<op>`` (raise right after that op's state change is saved), and
``crash_after=<label>`` (read by runtime code through :func:`fault`).

Like the real client, the mutating calls (``upload``, a new ``xp_create``, ``submit``) re-check
``whoami`` first and raise ``IdentityError`` unless it is the expected unexpired Player (C6), so
an ``identity=``, ``player=`` or ``expires_in=`` fault stops a running worker at the boundary.

Seeds (A12): like the platform, a spec's ``game_config_overrides.seed`` pins the request's
episodes to that seed. Results of a seeded request depend only on (seed, our roster policy,
episode index), so the same seed and policy give the same episode in any request; unseeded
results depend on the request id.
"""

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autoresearch.io.docs import read_json, write_json
from autoresearch.io.locks import file_lock
from autoresearch.softmax_tool.identity import IdentityError, identity_problems
from autoresearch.softmax_tool.protocol import CreditDenied, Identity, XpHandle, XpStatus
from autoresearch.softmax_tool.xp import handle_order

FAULTS_ENV = "AUTORESEARCH_FAKE_FAULTS"
DEFAULT_PLAYER_ID = "ply_fake"
DEFAULT_CREDITS = 100
DEFAULT_EPISODES = 2


def parse_faults(env: Mapping[str, str]) -> dict[str, str]:
    faults: dict[str, str] = {}
    for item in env.get(FAULTS_ENV, "").split(";"):
        if item.strip():
            name, _, value = item.partition("=")
            faults[name.strip()] = value.strip()
    return faults


def fault(env: Mapping[str, str], name: str) -> str | None:
    return parse_faults(env).get(name)


def _short_hash(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


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
        self._player_id = env.get("AUTORESEARCH_PLAYER_ID") or DEFAULT_PLAYER_ID
        self._faults = parse_faults(env)
        self._clock = clock

    # --- state plumbing -------------------------------------------------

    def _initial_state(self) -> dict:
        return {
            "credits": DEFAULT_CREDITS,
            "game_version": "fake-1",
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
        lifetime = timedelta(seconds=float(self._faults.get("expires_in", 24 * 3600)))
        identity = Identity(
            subject_type="user" if self._faults.get("identity") == "user" else "player",
            player_id=self._faults.get("player", self._player_id),
            expires_at=self._clock() + lifetime,
        )
        return self._operate("whoami", lambda _state: identity, persist=False)

    def _require_player(self) -> None:
        problems = identity_problems(self.whoami(), self._player_id, self._clock())
        if problems:
            raise IdentityError("; ".join(problems))

    def _current_credits(self, state: dict) -> int:
        return int(self._faults["credits"]) if "credits" in self._faults else state["credits"]

    def credits(self) -> int:
        return self._operate("credits", self._current_credits, persist=False)

    def game_version(self, league_id: str) -> str:
        return self._operate("game_version", lambda state: state["game_version"], persist=False)

    # --- XP requests ----------------------------------------------------

    def xp_create(
        self,
        experiment: str,
        batch: int,
        attempt: int,
        spec: dict,
        seed: int | None = None,
        arm: str | None = None,
    ) -> XpHandle | CreditDenied:
        key = (
            (experiment, batch, attempt)
            if seed is None
            else (experiment, batch, attempt, seed, arm)
        )
        request_id = f"xp_{_short_hash(*key)}"
        known = self._operate("xp_lookup", lambda state: request_id in state["xp"], persist=False)
        if not known:  # like the client: an existing request is reused without a new check
            self._require_player()

        def action(state: dict):
            if request_id not in state["xp"]:
                if self._current_credits(state) <= 0:
                    return CreditDenied("no credits")
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
                }
                state["credits"] = max(0, state["credits"] - 1)
            return XpHandle(request_id, experiment, batch, attempt, seed, arm)

        return self._operate("xp_create", action)

    def xp_status(self, handle: XpHandle) -> XpStatus:
        def action(state: dict) -> XpStatus:
            request = state["xp"][handle.request_id]
            request["polls"] += 1
            total = request["episodes"]
            if request["polls"] >= state["polls_to_complete"]:
                return XpStatus("completed", total, total)
            return XpStatus("running", 0, total)

        return self._operate("xp_status", action)

    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]:
        request = self._operate(
            "xp_artifacts", lambda state: dict(state["xp"][handle.request_id]), persist=False
        )
        game_seed = request.get("game_seed")
        source = handle.request_id if game_seed is None else f"{game_seed}|{request['policy']}"
        episodes = []
        for index in range(request["episodes"]):
            episode = {
                "episode": index,
                "score": int(_short_hash(source, index, "score"), 16) % 100,
                "errors": int(_short_hash(source, index, "errors"), 16) % 5,
            }
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

    def upload(self, artifact: Path, name: str) -> str:
        self._require_player()

        def action(state: dict) -> str:
            policy_version_id = f"pv_{_short_hash(name, len(state['uploads']))}"
            state["uploads"][policy_version_id] = {"name": name}
            return policy_version_id

        return self._operate("upload", action)

    def submit(self, policy_version_id: str, league_id: str) -> str:
        self._require_player()

        def action(state: dict) -> str:
            submission_id = (
                f"sub_{_short_hash(policy_version_id, league_id, len(state['submissions']))}"
            )
            state["submissions"].append(
                {
                    "submission_id": submission_id,
                    "policy_version_id": policy_version_id,
                    "league_id": league_id,
                    "player_id": self._player_id,
                }
            )
            return submission_id

        return self._operate("submit", action)

    def submissions(self, player_id: str) -> list[dict]:
        def action(state: dict) -> list[dict]:
            return [s for s in state["submissions"] if s["player_id"] == player_id]

        return self._operate("submissions", action, persist=False)

    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None:
        matching = [
            s["policy_version_id"]
            for s in self.submissions(player_id)
            if s["league_id"] == league_id
        ]
        return matching[-1] if matching else None
