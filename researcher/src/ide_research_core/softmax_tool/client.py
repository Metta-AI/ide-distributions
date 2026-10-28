"""The real ``Softmax`` implementation: the pinned coworld/softmax CLIs, run as the profile's Player.

Every mutating method (``upload``, ``xp_create``, ``submit``) runs ``whoami`` with the same
credential immediately before acting and raises ``IdentityError`` unless it is a Player whose id
is ``RESEARCH_PLAYER_ID`` with an unexpired token (C6). Nothing about identity is cached.

Policy identifiers: ``upload-policy`` prints only ``Upload complete: <name>:v<N>`` and ``submit``
takes ``<name>:v<N>`` (coworld 0.1.58 has no ``--json`` for either). The ``policy_version_id`` this
client returns from ``upload``, accepts in ``submit``, and returns from ``leaderboard_champion`` is
therefore that label, which the platform resolves uniquely. Submission rows still carry the UUID
under ``policy_version.id`` next to ``policy_version.label``.

League reads (read-only, coworld 0.1.57 ``tournament_cli.py``):

- ``league_episodes``: ``coworld rounds --league L --status completed --json`` (cursor pages) →
  ``coworld episodes --round R --json`` (cursor pages of ``V2EpisodeRequestSummary``; only
  ``status == "completed"`` rows) → per episode ``coworld episodes <ereq> --json``
  (``V2EpisodeRequestRow``: ``coworld_id``, ``coworld_version``, ``participants`` with
  ``position`` and the ``<name>:v<N>`` ``label``) and ``coworld episode-results <ereq>``
  (prints the raw results JSON; it has no ``--json`` flag). There is no league-wide episode
  listing, so episodes are reached through rounds. A Player credential can read all of these
  for other players' league episodes (live probe, ``researcher/docs/plans/findings/softmax-client.md``).
- Seed: the per-episode seed is ``results["seed"]``. The detail row's top-level ``seed`` was
  null live, and ``game_config.seed`` is a config value, not the episode's seed.
- Game version: the detail's ``coworld_version``; only when it is missing,
  ``coworld show <coworld_id> --json`` ``version`` (cached per Coworld).
- Throughput: episode details are fetched by ``LEAGUE_FETCH_WORKERS`` threads ahead of the
  consumer (each thread runs the CLI as a subprocess; ``PlatformCli.run`` rewrites the same
  credential atomically, so concurrent calls are safe). Serially, 200 episodes took over 5
  minutes on the box (about 2 s each); with 4 workers the fetch is about 4 times faster.
- ``standings``: ``coworld results <league_id> --json`` (the division ladder:
  ``[{id, name, level, member_count}]``) → ``coworld results <div_id> --json`` per division
  (``LeaderboardEntryPublic``: ``rank``, ``player_id``, ``player_name``, ``score``,
  ``rounds_played``).
"""

import json
import re
import subprocess
import urllib.parse
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from ide_research_core.budget import EpisodeBudget
from ide_research_core.games.scope import budget_path
from ide_research_core.softmax_tool import xp
from ide_research_core.softmax_tool.identity import require_player, whoami
from ide_research_core.softmax_tool.platform_cli import (
    HttpGet,
    PlatformCli,
    Runner,
    SoftmaxCliError,
    coach_refusal,
    describe_failure,
)
from ide_research_core.softmax_tool.protocol import (
    ANSWER_NO,
    ANSWER_USED,
    ANSWER_WAITING,
    ANSWER_YES,
    ArtifactType,
    CreditDenied,
    Identity,
    LeagueNotChosen,
    PermitRequired,
    SubmissionState,
    XpHandle,
    XpStatus,
)

UPLOAD_TIMEOUT_S = 600
_FULL_SHA = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
ROUND_PAGE_LIMIT = 200  # coworld rounds --limit max
EPISODE_PAGE_LIMIT = 100  # coworld episodes --limit max
LEAGUE_FETCH_WORKERS = 4  # concurrent episode fetches (each one runs two coworld processes)


CREDITS_PATH = "/observatory/usage/me/credits"
# Our cogent's record ``asks/<key>`` (metta observatory-api routes/app/v2/cogents/records,
# ``cogent_get``): a player credential reads its own cogent (``cogents/queries._owned``). The
# body is a ``PermitAsk``: ``{kind, action, ref, why, at, amount?, answer?: {yes, at},
# used?: {at}}``.
ASK_PATH = "/observatory/v2/cogents/{player}/c/asks/{key}"


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
        http_get: HttpGet | None = None,
    ):
        self._cli = PlatformCli(env, run=run, bin_dir=bin_dir, http_get=http_get)
        self._expected_player = env.get("RESEARCH_PLAYER_ID", "")
        self._clock = clock
        self._budget = (
            EpisodeBudget(budget_path(env), int(env["RESEARCH_EPISODE_LIMIT"]))
            if "RESEARCH_EPISODE_LIMIT" in env
            else None
        )

    def _require_player(self) -> Identity:
        return require_player(self._cli, self._expected_player, self._clock())

    # --- identity and account -------------------------------------------

    def whoami(self) -> Identity:
        return whoami(self._cli)

    def credits(self) -> float:
        """The owning account's XP credit balance (``status.balance_credits``), read as the
        Player (A10; verified live 2026-10-09). Raises ``SoftmaxCliError`` when it cannot be
        read; callers then fall back to the credit-denial backoff."""
        payload = self._cli.api_get_json(CREDITS_PATH)
        try:
            return float(payload["status"]["balance_credits"])
        except (KeyError, TypeError, ValueError):
            raise SoftmaxCliError(f"{CREDITS_PATH} has no status.balance_credits") from None

    def permit_answer(self, ask: str) -> str | None:
        """The coach's answer to permit ask ``ask`` (``Softmax.permit_answer``), read as the
        Player over HTTP (``ASK_PATH``); None when the read fails or the body is not an ask."""
        path = ASK_PATH.format(
            player=urllib.parse.quote(self._expected_player, safe=""),
            key=urllib.parse.quote(ask, safe=""),
        )
        try:
            record = self._cli.api_get_json(path)
        except SoftmaxCliError:
            return None
        body = record.get("body") if isinstance(record, dict) else None
        if not isinstance(body, dict) or body.get("kind") != "permit":
            return None
        answer = body.get("answer")
        if answer is None:
            return ANSWER_WAITING
        if not isinstance(answer, dict) or not isinstance(answer.get("yes"), bool):
            return None
        if not answer["yes"]:
            return ANSWER_NO
        return ANSWER_USED if body.get("used") else ANSWER_YES

    def episode_cost(self) -> float | None:
        """Mean credits per episode over the account's recent XP requests (the same route's
        ``recent_requests``: ``episodes``, ``compute_cost_credits``); None without episodes."""
        rows = self._cli.api_get_json(CREDITS_PATH).get("recent_requests") or []
        episodes = sum(int(row.get("episodes") or 0) for row in rows)
        cost = sum(float(row.get("compute_cost_credits") or 0) for row in rows)
        return cost / episodes if episodes > 0 and cost > 0 else None

    def _league_coworld(self, league_id: str) -> dict:
        league = self._cli.run_json("coworld", ["leagues", league_id])
        game = league["game"]
        coworld_id = game.get("canonical_coworld_id") or game.get("coworld_id")
        if not coworld_id:
            raise SoftmaxCliError(f"league {league_id} names no Coworld")
        return self._cli.run_json("coworld", ["show", coworld_id])

    def game_version(self, league_id: str) -> str:
        return str(self._league_coworld(league_id)["version"]).strip()

    def game_source_pin(self, league_id: str) -> str | None:
        """The full commit sha in ``manifest.game.runnable.source_url`` of the league's
        Coworld (``coworld show --json`` includes the manifest; coworld 0.1.57
        ``CoworldUploadResponse``). ``source_url`` is optional and may name a branch or a
        bare repository, so None when it carries no 40-hex sha."""
        manifest = self._league_coworld(league_id).get("manifest") or {}
        runnable = (manifest.get("game") or {}).get("runnable") or {}
        match = _FULL_SHA.search(str(runnable.get("source_url") or ""))
        return match.group(0).lower() if match else None

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
        key = xp.tag(experiment, batch, attempt, seed, arm)
        if self._budget is not None:
            self._budget.reserve(key, spec)
        result = xp.create(self._cli, experiment, batch, attempt, spec, seed, arm)
        if self._budget is not None and isinstance(
            result, (CreditDenied, PermitRequired, LeagueNotChosen)
        ):
            self._budget.release_refused(key)
        return result

    def xp_status(self, handle: XpHandle) -> XpStatus:
        return xp.status(self._cli, handle)

    def xp_artifacts(self, handle: XpHandle, dest: Path) -> list[Path]:
        return xp.artifacts(self._cli, handle, dest)

    def xp_recover(self, experiment: str) -> list[XpHandle]:
        return xp.recover(self._cli, experiment)

    # --- policies -------------------------------------------------------

    def upload(self, artifact: Path, name: str, *, artifact_type: ArtifactType = "file") -> str:
        """Upload a game-hosted file or OCI image archive; return its ``<name>:v<N>`` label."""
        self._require_player()
        option = "--image-archive" if artifact_type == "oci-image" else "--file"
        args = ["upload-policy", option, str(Path(artifact).resolve()), "--name", name]
        result = self._cli.run("coworld", args, timeout=UPLOAD_TIMEOUT_S)
        label = _line_value(result.stdout, "Upload complete:")
        if label is None:
            raise SoftmaxCliError(f"upload-policy printed no 'Upload complete:' line for {name}")
        return label

    def submit(
        self, policy_version_id: str, league_id: str
    ) -> str | PermitRequired | LeagueNotChosen:
        """Submit ``<name>:v<N>`` as the expected player; return the submission id, or the
        coach's refusal (``platform_cli.coach_refusal``: nothing was submitted)."""
        self._require_player()
        args = ["submit", policy_version_id, "--league", league_id]
        args += ["--player", self._expected_player, "--no-open-browser"]
        result = self._cli.run("coworld", args, ok_codes=(0, 1))
        if result.returncode != 0:
            refusal = coach_refusal(result.stderr or "")
            if refusal is not None:
                return refusal
            raise SoftmaxCliError(describe_failure("coworld", args, result))
        submission_id = _line_value(result.stdout, "Submission:")
        if submission_id is None:
            raise SoftmaxCliError(f"submit printed no 'Submission:' line for {policy_version_id}")
        return submission_id

    def retire_other_memberships(
        self, league_id: str, keep_policy_version_id: str
    ) -> tuple[list[str], list[str]]:
        """One program per league: retire this player's other active memberships there
        (``coworld memberships --league L --player P --active-only --json``, then
        ``coworld retire-membership <id>``). Only rows whose policy version belongs to the
        expected player are retired. Returns the ``<name>:v<N>`` labels retired, and those the
        platform would not retire (said, not raised: the release itself stands)."""
        self._require_player()
        args = ["memberships", "--league", league_id, "--player", self._expected_player]
        rows = self._cli.run_json_pages("coworld", [*args, "--active-only"])
        retired: list[str] = []
        refused: list[str] = []
        for row in rows:
            version = row["policy_version"]
            if version["player_id"] != self._expected_player:
                continue
            if version["label"] == keep_policy_version_id:
                continue
            reason = f"replaced by {keep_policy_version_id}"
            try:
                self._cli.run("coworld", ["retire-membership", row["id"], "--reason", reason])
            except SoftmaxCliError:
                refused.append(version["label"])
                continue
            retired.append(version["label"])
        return retired, refused

    def submissions(self, player_id: str) -> list[dict]:
        """``coworld submissions --player P --json`` rows (``LeagueSubmissionPublic``), each
        with the flat keys the protocol uses added: ``submission_id``, ``policy_version_id``
        (the ``<name>:v<N>`` label), ``league_id`` and ``player_id``."""
        rows = self._cli.run_json_pages("coworld", ["submissions", "--player", player_id])
        return [
            {
                **row,
                "submission_id": row["id"],
                "policy_version_id": row["policy_version"]["label"],
                "league_id": row["league"]["id"],
                "player_id": (row.get("player") or {}).get("id", player_id),
            }
            for row in rows
        ]

    def submission_state(
        self, player_id: str, league_id: str, policy_version_id: str
    ) -> SubmissionState:
        """Our latest league membership of the policy (``coworld memberships --league L
        --player P --policy <label> --json``: ``status`` submitted|qualifying|competing|
        disqualified, ``substatus``, ``is_champion``; verified live 2026-10-09), else our
        latest submission of it (``status`` pending|processing|placed|rejected|withdrawn)."""
        args = ["memberships", "--league", league_id, "--player", player_id]
        rows = self._cli.run_json_pages("coworld", [*args, "--policy", policy_version_id])
        rows = [r for r in rows if r["policy_version"]["label"] == policy_version_id]
        if rows:
            row = max(rows, key=lambda r: r.get("start_time") or r.get("created_at") or "")
            status, substatus = row["status"], row.get("substatus")
            detail = f"membership {status}" + (f" ({substatus})" if substatus else "")
            if status == "competing":
                return SubmissionState("competing", bool(row.get("is_champion")), detail)
            if status == "disqualified":
                return SubmissionState("rejected", False, detail)
            if status in ("submitted", "qualifying"):  # PolicyMembershipStatus, metta
                return SubmissionState("pending", False, detail)
            return SubmissionState("unknown", False, detail)  # a status we do not know
        mine = [
            s
            for s in self.submissions(player_id)
            if s["policy_version_id"] == policy_version_id and s["league_id"] == league_id
        ]
        if not mine:
            return SubmissionState("unknown")
        status = max(mine, key=lambda s: s.get("created_at") or "")["status"]
        stages = {"pending": "pending", "processing": "pending"}
        stages |= {"rejected": "rejected", "withdrawn": "rejected"}
        stage = stages.get(status, "unknown")  # "placed" with no membership: unknown
        return SubmissionState(stage, False, f"submission {status}")

    def leaderboard_champion(self, player_id: str, league_id: str) -> str | None:
        args = ["memberships", "--league", league_id, "--player", player_id, "--champions-only"]
        rows = self._cli.run_json_pages("coworld", args)
        return rows[0]["policy_version"]["label"] if rows else None

    # --- league reads (read-only) ----------------------------------------

    def latest_league_result(self, player_id: str, league_id: str) -> dict | None:
        """Newest recorded league episode for this player, including round and replay evidence."""
        league = urllib.parse.quote(league_id, safe="")
        player = urllib.parse.quote(player_id, safe="")
        return self._cli.api_get_json(
            f"/observatory/v2/leagues/{league}/players/{player}/latest-replay"
        )

    def league_episodes(self, league_id: str, limit: int) -> Iterator[dict]:
        """Episodes in listing order, as they arrive. Each episode costs two coworld calls
        (about 2 s on the box, mostly CLI start-up), so ``LEAGUE_FETCH_WORKERS`` threads fetch
        details ahead of the consumer, which evaluates one while the next ones load. The
        window is bounded, so an early stop or an error leaves little work in flight."""
        versions: dict[str, str] = {}  # a race only repeats one `coworld show`
        pool = ThreadPoolExecutor(max_workers=LEAGUE_FETCH_WORKERS)
        window: deque[Future] = deque()
        try:
            for episode_id in self._completed_episode_ids(league_id, limit):
                window.append(pool.submit(self._fetch_episode, episode_id, versions))
                if len(window) > 2 * LEAGUE_FETCH_WORKERS:
                    yield window.popleft().result()
            while window:
                yield window.popleft().result()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _fetch_episode(self, episode_id: str, versions: dict[str, str]) -> dict:
        """One episode, retried once: a transient CLI or HTTP failure on one of hundreds of
        episodes should not throw away the whole read (``--out`` is written only at the end)."""
        try:
            return self._league_episode(episode_id, versions)
        except SoftmaxCliError:
            return self._league_episode(episode_id, versions)

    def _completed_episode_ids(self, league_id: str, limit: int) -> Iterator[str]:
        """Up to ``limit`` completed episode ids, round by round (no league-wide listing)."""
        found = 0
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
                    yield summary["id"]
                    found += 1
                    if found >= limit:
                        return
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def _league_episode(self, episode_id: str, versions: dict[str, str]) -> dict:
        detail = self._cli.run_json("coworld", ["episodes", episode_id])
        coworld_id = detail.get("coworld_id")
        version = detail.get("coworld_version")
        if not version and coworld_id:
            if coworld_id not in versions:
                versions[coworld_id] = self._cli.run_json("coworld", ["show", coworld_id])[
                    "version"
                ]
            version = versions[coworld_id]
        raw = self._cli.run("coworld", ["episode-results", episode_id]).stdout
        try:
            results = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SoftmaxCliError(f"episode-results {episode_id} is not JSON: {error}") from None
        seed = results.get("seed") if isinstance(results, dict) else None
        return {
            "episode_id": episode_id,
            "round_id": detail.get("round_id"),
            "seed": seed if seed is not None else detail.get("seed"),
            "game_version": str(version).strip() if version else None,
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
