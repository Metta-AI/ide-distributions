"""The real ``Softmax`` implementation: the pinned coworld/softmax CLIs, run as the profile's Player.

Every mutating method (``upload``, ``xp_create``, ``submit``) runs ``whoami`` with the same
credential immediately before acting and raises ``IdentityError`` unless it is a Player whose id
is ``AUTORESEARCH_PLAYER_ID`` with an unexpired token (C6). Nothing about identity is cached.

Policy identifiers: ``upload-policy`` prints only ``Upload complete: <name>:v<N>`` and ``submit``
takes ``<name>:v<N>`` (coworld 0.1.57 has no ``--json`` for either). The ``policy_version_id`` this
client returns from ``upload``, accepts in ``submit``, and returns from ``leaderboard_champion`` is
therefore that label, which the platform resolves uniquely. Submission rows still carry the UUID
under ``policy_version.id`` next to ``policy_version.label``.

League reads (read-only, coworld 0.1.57 ``tournament_cli.py``):

- ``league_episodes``: ``coworld rounds --league L --status completed --json`` (cursor pages) →
  ``coworld episodes --round R --json`` (cursor pages of ``V2EpisodeRequestSummary``; only
  ``status == "completed"`` rows) → per episode ``coworld episodes <ereq> --json``
  (``V2EpisodeRequestRow``: ``seed``, ``coworld_id``, ``participants`` with ``position`` and the
  ``<name>:v<N>`` ``label``) and ``coworld episode-results <ereq>`` (prints the raw results JSON;
  it has no ``--json`` flag). The game version is ``coworld show <coworld_id> --json``
  ``version``, cached per Coworld. There is no league-wide episode listing, so episodes are
  reached through rounds. Caveat: coworld's COOKBOOK lists ``episode-results`` as team-only
  (403 for other accounts); a 403 is reported as ``SoftmaxCliError`` naming that.
- ``standings``: ``coworld results <league_id> --json`` (the division ladder:
  ``[{id, name, level, member_count}]``) → ``coworld results <div_id> --json`` per division
  (``LeaderboardEntryPublic``: ``rank``, ``player_id``, ``player_name``, ``score``,
  ``rounds_played``).
"""

import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from autoresearch.softmax_tool import xp
from autoresearch.softmax_tool.identity import require_player, whoami
from autoresearch.softmax_tool.platform_cli import PlatformCli, Runner, SoftmaxCliError
from autoresearch.softmax_tool.protocol import CreditDenied, Identity, XpHandle, XpStatus

UPLOAD_TIMEOUT_S = 600
ROUND_PAGE_LIMIT = 200  # coworld rounds --limit max
EPISODE_PAGE_LIMIT = 100  # coworld episodes --limit max


class CreditsUnavailable(SoftmaxCliError):
    """The credit balance cannot be read with a Player credential."""


def _line_value(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip() or None
    return None


class SoftmaxClient:
    def __init__(
        self,
        env: Mapping[str, str],
        run: Runner = subprocess.run,
        bin_dir: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._cli = PlatformCli(env, run=run, bin_dir=bin_dir)
        self._expected_player = env.get("AUTORESEARCH_PLAYER_ID", "")
        self._clock = clock

    def _require_player(self) -> Identity:
        return require_player(self._cli, self._expected_player, self._clock())

    # --- identity and account -------------------------------------------

    def whoami(self) -> Identity:
        return whoami(self._cli)

    def credits(self) -> int:
        # /usage/me/credits answers 403 "Player credentials are not authorized for this route"
        # (verified 2026-10-07), and neither CLI exposes it. Callers treat this as unknown.
        raise CreditsUnavailable("credit balance is not readable with a Player credential")

    def game_version(self, league_id: str) -> str:
        league = self._cli.run_json("coworld", ["leagues", league_id])
        game = league["game"]
        coworld_id = game.get("canonical_coworld_id") or game.get("coworld_id")
        if not coworld_id:
            raise SoftmaxCliError(f"league {league_id} names no Coworld")
        return self._cli.run_json("coworld", ["show", coworld_id])["version"]

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
    ) -> XpHandle | CreditDenied:
        """Reuse an existing request for (experiment, batch, attempt, seed, arm); else check
        identity, create.

        ``known`` is the caller's own recover of this experiment (``xp_recover``); passing it
        skips a second full ``xp-request list``, which pages through every request we own. The
        tag is also the server-side idempotency key, so a request missing from a stale ``known``
        is still never duplicated. The existing request is reused whatever its state, including
        failed or cancelled ones; callers retry by bumping ``attempt``.
        """
        handles = xp.recover(self._cli, experiment) if known is None else known
        for handle in handles:
            if (handle.experiment, handle.batch, handle.attempt, handle.seed, handle.arm) == (
                experiment,
                batch,
                attempt,
                seed,
                arm,
            ):
                return handle
        self._require_player()
        return xp.create(self._cli, experiment, batch, attempt, spec, seed, arm)

    def xp_status(self, handle: XpHandle) -> XpStatus:
        return xp.status(self._cli, handle)

    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]:
        return xp.artifacts(self._cli, handle, dest)

    def xp_recover(self, experiment: str) -> list[XpHandle]:
        return xp.recover(self._cli, experiment)

    # --- policies -------------------------------------------------------

    def upload(self, artifact: Path, name: str) -> str:
        """Upload a game-hosted policy file or directory; return its ``<name>:v<N>`` label."""
        self._require_player()
        args = ["upload-policy", "--file", str(Path(artifact).resolve()), "--name", name]
        result = self._cli.run("coworld", args, timeout=UPLOAD_TIMEOUT_S)
        label = _line_value(result.stdout, "Upload complete:")
        if label is None:
            raise SoftmaxCliError(f"upload-policy printed no 'Upload complete:' line for {name}")
        return label

    def submit(self, policy_version_id: str, league_id: str) -> str:
        """Submit ``<name>:v<N>`` as the expected player; return the submission id."""
        self._require_player()
        args = ["submit", policy_version_id, "--league", league_id]
        args += ["--player", self._expected_player, "--no-open-browser"]
        result = self._cli.run("coworld", args)
        submission_id = _line_value(result.stdout, "Submission:")
        if submission_id is None:
            raise SoftmaxCliError(f"submit printed no 'Submission:' line for {policy_version_id}")
        return submission_id

    def submissions(self, player_id: str) -> list[dict]:
        return self._cli.run_json_pages("coworld", ["submissions", "--player", player_id])

    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None:
        args = ["memberships", "--league", league_id, "--player", player_id, "--champions-only"]
        rows = self._cli.run_json_pages("coworld", args)
        return rows[0]["policy_version"]["label"] if rows else None

    # --- league reads (read-only) ----------------------------------------

    def league_episodes(self, league_id: str, limit: int) -> list[dict]:
        episodes: list[dict] = []
        versions: dict[str, str] = {}
        cursor: str | None = None
        while True:
            args = ["rounds", "--league", league_id, "--status", "completed"]
            args += ["--limit", str(ROUND_PAGE_LIMIT)] + (["--cursor", cursor] if cursor else [])
            page = self._cli.run_json("coworld", args)
            for round_row in page["entries"]:
                listing = ["episodes", "--round", round_row["id"]]
                listing += ["--limit", str(EPISODE_PAGE_LIMIT)]
                for summary in self._cli.run_json_pages("coworld", listing):
                    if summary.get("status") != "completed":
                        continue
                    episodes.append(self._league_episode(summary["id"], versions))
                    if len(episodes) >= limit:
                        return episodes
            cursor = page.get("next_cursor")
            if not cursor:
                return episodes

    def _league_episode(self, episode_id: str, versions: dict[str, str]) -> dict:
        detail = self._cli.run_json("coworld", ["episodes", episode_id])
        coworld_id = detail.get("coworld_id")
        if coworld_id and coworld_id not in versions:
            versions[coworld_id] = self._cli.run_json("coworld", ["show", coworld_id])["version"]
        try:
            raw = self._cli.run("coworld", ["episode-results", episode_id]).stdout
        except SoftmaxCliError as error:
            if "403" in str(error):
                raise SoftmaxCliError(
                    f"episode-results {episode_id} is not readable with this credential "
                    f"(coworld documents it as team-only): {error}"
                ) from None
            raise
        try:
            results = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SoftmaxCliError(f"episode-results {episode_id} is not JSON: {error}") from None
        return {
            "episode_id": episode_id,
            "round_id": detail.get("round_id"),
            "seed": detail.get("seed"),
            "game_version": versions.get(coworld_id) if coworld_id else None,
            "coworld_id": coworld_id,
            "seats": [_seat(p) for p in sorted(detail["participants"], key=_position)],
            "results": results,
        }

    def standings(self, league_id: str) -> list[dict]:
        rows = []
        for division in self._cli.run_json("coworld", ["results", league_id]):
            board = self._cli.run_json("coworld", ["results", division["id"]]) or []
            for index, entry in enumerate(board, start=1):
                rows.append(
                    {
                        "division_id": division["id"],
                        "division_name": division.get("name"),
                        "level": division.get("level"),
                        "rank": entry.get("rank") if entry.get("rank") is not None else index,
                        "player_id": entry["player_id"],
                        "player_name": entry.get("player_name"),
                        "score": entry.get("score"),
                        "rounds_played": entry.get("rounds_played"),
                    }
                )
        return rows


def _position(participant: dict) -> int:
    return participant["position"]


def _seat(participant: dict) -> dict:
    is_policy = participant.get("kind", "policy") == "policy"
    label = participant.get("label") or (
        f"{participant['policy_name']}:v{participant['version']}" if is_policy else None
    )
    return {
        "position": participant["position"],
        "policy_version_id": label if is_policy else None,
        "player_id": participant.get("player_id"),
        "is_filler": bool(participant.get("is_filler", False)),
    }
