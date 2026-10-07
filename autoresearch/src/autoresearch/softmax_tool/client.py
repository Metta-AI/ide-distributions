"""The real ``Softmax`` implementation: the pinned coworld/softmax CLIs, run as the profile's Player.

Every mutating method (``upload``, ``xp_create``, ``submit``) runs ``whoami`` with the same
credential immediately before acting and raises ``IdentityError`` unless it is a Player whose id
is ``AUTORESEARCH_PLAYER_ID`` with an unexpired token (C6). Nothing about identity is cached.

Policy identifiers: ``upload-policy`` prints only ``Upload complete: <name>:v<N>`` and ``submit``
takes ``<name>:v<N>`` (coworld 0.1.57 has no ``--json`` for either). The ``policy_version_id`` this
client returns from ``upload``, accepts in ``submit``, and returns from ``leaderboard_champion`` is
therefore that label, which the platform resolves uniquely. Submission rows still carry the UUID
under ``policy_version.id`` next to ``policy_version.label``.
"""

import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from autoresearch.softmax_tool import xp
from autoresearch.softmax_tool.identity import require_player, whoami
from autoresearch.softmax_tool.platform_cli import PlatformCli, Runner, SoftmaxCliError
from autoresearch.softmax_tool.protocol import CreditDenied, Identity, XpHandle, XpStatus

UPLOAD_TIMEOUT_S = 600


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
    ) -> XpHandle | CreditDenied:
        """Reuse an existing request for (experiment, batch, attempt, seed, arm); else check
        identity, create.

        The existing request is reused whatever its state, including failed or cancelled ones;
        callers retry by bumping ``attempt``.
        """
        for handle in xp.recover(self._cli, experiment):
            if (handle.batch, handle.attempt, handle.seed, handle.arm) == (
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
