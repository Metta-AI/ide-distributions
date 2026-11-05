"""Play's operations on the user's one program, run in the profile venv as the profile's Player.

The cogent changes the program itself: ``program_change`` checks a full new text with
``play.basic`` and writes ``program.bas`` in the program workspace. ``program.bas`` is uploaded as
a new version of one program with a stable name, practiced, and released only when the user says
so. ``journal.json`` (``play.journal``) remembers every version, practice and release, so the
cogent names things as the user does ("towerless v2") and never needs a platform id.

Every platform call goes through the shared ``softmax_tool`` client: the credential is installed
into the profile-local Softmax home before each CLI call, and every mutating call (upload,
practice, release) first runs ``whoami`` and refuses unless the credential is the Player
``RESEARCH_PLAYER_ID`` (``SoftmaxClient.require_player``). League-scoped calls receive their league explicitly.

The coach's refusals come back as data, never as an error and never retried:
``{"outcome": "refused", "refusal": "credit_denied",
"reason", "state"?, "ask"?}``. The same call made again sends the same request (practice reuses
its idempotency key; release looks up the league's submissions first), so asking again after the
coach answers is safe.
"""

import difflib
import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ide_research_core.softmax_tool import xp
from ide_research_core.softmax_tool.client import UPLOAD_TIMEOUT_S, SoftmaxClient, _line_value
from ide_research_core.softmax_tool.platform_cli import (
    SoftmaxCliError,
    describe_failure,
)
from ide_research_core.softmax_tool.protocol import CreditDenied

from . import basic, results
from .games import GOTA, CommissionerConfig, Selection
from .journal import Card, Journal, Practice, Release, Version, now, write_text
from .limits import (
    DEFAULT_EPISODES,
    MAX_EPISODES,
    MAX_WAIT_SECONDS,
    NAME_MAX,
    STEP_STATUS,
    STEPS,
    SUFFIX_LENGTH,
)

SHA256 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_TAKEN = re.compile(r"is already taken")
LABEL = re.compile(r"(?P<name>.+):v(?P<number>[0-9]+)")
# How the user names a version: "towerless v2", "towerless:v2", or "towerless" for the latest.
LABEL_REF = re.compile(r"(?P<name>[a-z0-9][a-z0-9_-]*?)(?:(?::v| v| version )(?P<number>[0-9]+))?")
DIFF_LINES = 150
POLL_SECONDS = 20
FETCH_TIMEOUT_S = 60
USER_AGENT = "softmax-play"
PROGRAM = "program.bas"
STARTER_NAMES = ("starter", "the starter")
DONE_WHEN = "The change is in a new version, practiced, and the result told to you."


@dataclass(frozen=True)
class Ref:
    """A version the user named, resolved: their name for it, its number, its platform label."""

    name: str
    number: int
    label: str
    entry: Version | None  # what the journal keeps about it, when this workspace uploaded it

    @property
    def display(self) -> str:
        return f"{self.name} v{self.number}"


# url -> response bytes; raises urllib.error.HTTPError / URLError.
Fetch = Callable[[str], bytes]


class PlayError(RuntimeError):
    """A refused or failed operation: ``reason`` is a stable code, ``details`` is safe to show."""

    def __init__(self, reason: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = details or {}

    @property
    def envelope(self) -> dict:
        error = {"reason": self.reason, "message": str(self)}
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


def fetch_public(url: str) -> bytes:
    """GET without a credential: published player files are anyone's to download."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return response.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def refused(refusal: CreditDenied) -> dict:
    return {"outcome": "refused", "refusal": "credit_denied", "reason": refusal.reason}


def diff(before: str, after: str, before_name: str, after_name: str) -> dict:
    lines = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), before_name, after_name, n=2, lineterm=""
        )
    )
    body = [line for line in lines[2:] if not line.startswith("@@")]
    return {
        "lines_added": sum(line.startswith("+") for line in body),
        "lines_removed": sum(line.startswith("-") for line in body),
        "diff": "\n".join(lines[:DIFF_LINES]),
        "diff_truncated": len(lines) > DIFF_LINES,
    }


class Program:
    def __init__(
        self,
        env: Mapping[str, str],
        client: SoftmaxClient | None = None,
        fetch: Fetch = fetch_public,
    ):
        if not env.get("PLAY_WORKSPACE"):
            raise PlayError("not_configured", "PLAY_WORKSPACE is not set")
        self.root = Path(env["PLAY_WORKSPACE"])
        self.player = env.get("RESEARCH_PLAYER_ID", "")
        self.client = client or SoftmaxClient(env)
        self._fetch = fetch
        self._sleep = time.sleep

    @property
    def selection(self) -> Selection | None:
        path = self.root / "game.json"
        return Selection.model_validate_json(path.read_text()) if path.is_file() else None

    @property
    def game(self) -> str:
        selection = self.selection
        # Existing GoTA journals retain their original location and reader.
        return selection.game if selection else "gota"

    @property
    def workspace(self) -> Path:
        return self.root / "generals" if self.game == "generals" else self.root

    @property
    def program_file(self) -> Path:
        return self.workspace / PROGRAM

    @property
    def starter_file(self) -> Path:
        selection = self.selection
        starter = selection.starter if selection else GOTA.starter_player
        return self.workspace / "starter" / f"{starter}.bas"

    def _starter_text(self) -> str | None:
        return self.starter_file.read_text() if self.starter_file.is_file() else None

    # --- starter --------------------------------------------------------

    def _published_game(self, league: str) -> tuple[dict, Selection]:
        coworld = self.client._league_coworld(league)
        manifest = coworld["manifest"]
        game = manifest["game"]
        name = game["name"]
        if name not in ("generals-competition", "Gods of the Arena"):
            raise PlayError(
                "unsupported_game", "This game's BASIC execution contract is not supported here."
            )
        runtimes = game["player_runtime"]
        if "game-hosted" not in ([runtimes] if isinstance(runtimes, str) else runtimes):
            raise PlayError(
                "unsupported_runtime", "The published game does not accept file programs."
            )
        generals = name == "generals-competition"
        league_record = self.client._cli.run_json("coworld", ["leagues", league])
        config = CommissionerConfig.model_validate(league_record["commissioner_config"] or {})
        # The platform uses manifest order when the league has no explicit default.
        variant_id = config.default_variant_id or manifest["variants"][0]["id"]
        variant = next(v for v in manifest["variants"] if v["id"] == variant_id)
        selection = Selection(
            game="generals" if generals else "gota",
            league_id=league,
            coworld_id=coworld["id"],
            starter="basic" if generals else "baseline",
            variant_id=variant_id,
            seat_count=len(variant["game_config"]["players"]),
        )
        return coworld, selection

    def starter_fetch(self, player: str | None = None, *, league: str) -> dict:
        """Download the coworld's starter player file into ``starter/`` and check its sha256."""
        coworld, selection = self._published_game(league)
        manifest = coworld["manifest"]
        game = manifest["game"]
        generals = selection.game == "generals"
        variant_id = selection.variant_id
        if player is not None:
            selection.starter = player
        entries = [p for p in manifest["player"] if p.get("file")]
        wanted = selection.starter
        entry = next((p for p in entries if p.get("id") == wanted), None)
        if entry is None:
            raise PlayError(
                "starter_not_found",
                f"the coworld has no player file {wanted!r}",
                {"available": [p.get("id") for p in entries]},
            )
        digest = str(entry["file"]).removeprefix("sha256:")
        if not SHA256.fullmatch(digest):
            raise PlayError(
                "starter_unavailable", f"player file is not content-addressed: {entry['file']}"
            )
        url = f"{self.client._cli.server}/observatory/v2/coworlds/{coworld['id']}/player-files/{digest}"
        try:
            data = self._fetch(url)
        except urllib.error.HTTPError as error:
            raise PlayError(
                "starter_unavailable",
                f"GET {url} answered HTTP {error.code}",
                {"url": url, "status": error.code},
            ) from None
        except (urllib.error.URLError, OSError) as error:
            raise PlayError(
                "starter_unavailable", f"GET {url} failed: {error}", {"url": url}
            ) from None
        if sha256(data) != digest:
            raise PlayError(
                "sha256_mismatch",
                "the downloaded bytes do not match the published sha256",
                {"url": url, "expected": digest, "actual": sha256(data)},
            )
        selection.starter = entry["id"]
        destination = self.root / "generals" if generals else self.root
        target = destination / "starter" / f"{entry['id']}.bas"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        write_text(self.root / "game.json", selection.model_dump_json(indent=2))
        return {
            "game": selection.game,
            "league": league,
            "variant": variant_id,
            "seat_count": selection.seat_count,
            "readme": game["docs"]["readme"],
            "protocol": game["protocols"]["player"],
            "starter": entry.get("name") or entry["id"],
            "player": entry["id"],
            "sha256": digest,
            "size_bytes": len(data),
            "lines": len(data.decode("utf-8", "replace").splitlines()),
        }

    # --- the program ----------------------------------------------------

    def program_show(self, program: str | None = None) -> dict:
        """The program as it is now (or the starter when nothing has been changed yet), or the
        text of an uploaded version the user names ("towerless v1"), or of the starter."""
        journal = Journal.load(self.workspace)
        if program is not None and program.strip().lower() in STARTER_NAMES:
            text = self._starter_text()
            if text is None:
                raise PlayError("no_program", "the starter is not fetched yet; fetch it first")
            return {
                "program": "the starter",
                "source": "starter",
                "lines": len(text.splitlines()),
                "size_bytes": len(text.encode()),
                "text": text,
            }
        if program is not None:
            ref = self._resolve(journal, program)
            if ref.entry is None or not self._version_file(ref.entry).is_file():
                raise PlayError(
                    "not_kept",
                    f"{ref.display} is one of your programs, but its text is not kept in this workspace",
                )
            text = self._version_file(ref.entry).read_text()
            return {
                "program": ref.display,
                "source": "version",
                "change": ref.entry.change,
                "lines": len(text.splitlines()),
                "size_bytes": len(text.encode()),
                "text": text,
            }
        if self.program_file.is_file():
            text, source = self.program_file.read_text(), "program"
        elif (starter := self._starter_text()) is not None:
            text, source = starter, "starter"
        else:
            raise PlayError("no_program", "there is no program yet; fetch the starter first")
        latest = journal.latest()
        digest = sha256(text.encode())
        return {
            "program": journal.name,
            "source": source,
            "last_version": latest.display if latest else None,
            "changed_since_last_version": latest is None or latest.sha256 != digest,
            "pending_change": journal.change,
            "lines": len(text.splitlines()),
            "size_bytes": len(text.encode()),
            "text": text,
        }

    def program_change(self, text: str, change: str) -> dict:
        """Check the full new text and write it as ``program.bas``; nothing is written if the check
        finds a problem."""
        change = self._described(change)
        return self._write_checked(text, change)

    def program_edit(self, edits: list, change: str) -> dict:
        """Apply small ``{find, replace}`` edits, in order, to the program now (or the starter), then
        check and write the result exactly as ``program_change`` does. Each ``find`` must occur
        exactly once in the text as the earlier edits left it; nothing is written otherwise."""
        change = self._described(change)
        if not isinstance(edits, list) or not edits:
            raise PlayError("bad_arguments", "edits must be a list of {find, replace}")
        if self.program_file.is_file():
            text = self.program_file.read_text()
        elif (starter := self._starter_text()) is not None:
            text = starter
        else:
            raise PlayError("no_program", "there is no program yet; fetch the starter first")
        for number, edit in enumerate(edits, start=1):
            find = edit.get("find") if isinstance(edit, dict) else None
            replace = edit.get("replace") if isinstance(edit, dict) else None
            if not isinstance(find, str) or not find or not isinstance(replace, str):
                raise PlayError(
                    "bad_arguments", f"edit {number} needs a non-empty find and a replace string"
                )
            found = text.count(find)
            if found == 0:
                raise PlayError(
                    "edit_not_found",
                    f"edit {number}: the find text is not in the program; nothing was written",
                    {"edit": number, "find": find[:200]},
                )
            if found > 1:
                raise PlayError(
                    "edit_ambiguous",
                    f"edit {number}: the find text occurs {found} times; include more of the "
                    "surrounding lines so it occurs once. Nothing was written",
                    {"edit": number, "find": find[:200], "occurrences": found},
                )
            text = text.replace(find, replace)
        return self._write_checked(text, change)

    @staticmethod
    def _described(change: str) -> str:
        change = change.strip()
        if not change:
            raise PlayError("bad_arguments", "say in a few words what this change does")
        return change

    def _write_checked(self, text: str, change: str) -> dict:
        starter = self._starter_text()
        problems, warnings = basic.check(text, starter, game=self.game)
        if problems:
            raise PlayError(
                "program_invalid",
                f"the new program would not run ({len(problems)} problem"
                f"{'s' * (len(problems) != 1)}); nothing was written",
                {"problems": basic.as_dicts(problems)},
            )
        if self.program_file.is_file():
            before, before_name = self.program_file.read_text(), "before"
        else:
            before, before_name = starter or "", "starter"
        if text == before:
            raise PlayError("no_change", "the new text is the same as the program now")
        write_text(self.program_file, text)
        journal = Journal.load(self.workspace)
        journal.change = change
        journal.save(self.workspace)
        return {
            "program": journal.name,
            "change": change,
            "lines": len(text.splitlines()),
            "size_bytes": len(text.encode()),
            "warnings": basic.as_dicts(warnings),
            **diff(before, text, before_name, "after"),
        }

    def program_versions(self, program: str | None = None) -> dict:
        """Every program of this player on the platform with its versions, what this workspace
        keeps about each version of the program (what changed, practice results, release), and
        how the program now differs from its last version, or from the version ``program`` names
        ("towerless v1")."""
        journal = Journal.load(self.workspace)
        name, number = self._split(program) if program is not None else (journal.name, None)
        ref = self._resolve(journal, program) if number is not None else None
        programs: dict[str, set[int]] = {}
        for row in self._mine():
            programs.setdefault(self.display_name(row["name"]), set()).add(int(row["version"]))
        for version in journal.versions:
            programs.setdefault(version.name, set()).add(version.number)
        kept = {v.number: v for v in journal.versions if v.name == name}

        def listed(number: int) -> dict:
            entry = kept.get(number)
            return {
                "version": number,
                "program": f"{name} v{number}",
                "change": entry.change if entry else None,
                "practiced": [p.summary["plain"] for p in entry.practices if p.summary]
                if entry
                else [],
                "released": entry.release is not None if entry else None,
            }

        out: dict = {
            "program": name,
            "versions": [listed(n) for n in sorted(programs.get(name, ()))] if name else [],
            "all_programs": [
                {"program": key, "versions": sorted(numbers)}
                for key, numbers in sorted(programs.items())
            ],
        }
        if not self.program_file.is_file():
            return out
        base = ref.entry if ref is not None else (journal.latest(name) if name else None)
        if (ref is not None and base is None) or (
            base is not None and not self._version_file(base).is_file()
        ):
            shown = ref.display if ref is not None else base.display
            out["compared_with"] = None
            out["note"] = (
                f"the text of {shown} is not kept in this workspace, so it cannot be compared"
            )
            return out
        if base is None:
            before, before_name = self._starter_text() or "", "starter"
        else:
            before, before_name = self._version_file(base).read_text(), base.display
        out["compared_with"] = before_name
        out.update(diff(before, self.program_file.read_text(), before_name, "now"))
        return out

    def _version_file(self, version: Version) -> Path:
        return (
            self.workspace / "versions" / version.platform_name / f"v{version.number}{GOTA.suffix}"
        )

    # --- upload ---------------------------------------------------------

    def program_upload(self, name: str | None = None) -> dict:
        """Upload ``program.bas`` as the next version of the program (``coworld upload-policy``).
        The same file uploaded again is the same version, not a new one."""
        if not self.program_file.is_file():
            raise PlayError("no_program", "there is no changed program to upload yet")
        data = self.program_file.read_bytes()
        problems, _ = basic.check(data.decode("utf-8", "replace"), game=self.game)
        if problems:
            raise PlayError(
                "program_invalid",
                "the program would not run; nothing was uploaded",
                {"problems": basic.as_dicts(problems)},
            )
        journal = Journal.load(self.workspace)
        name = name or journal.name
        if not name:
            raise PlayError("bad_arguments", "the program needs a name for its first upload")
        if not NAME.fullmatch(name) or len(name) > NAME_MAX:
            raise PlayError(
                "bad_arguments",
                f"name must be lowercase words separated by hyphens, at most {NAME_MAX} characters",
            )
        digest = sha256(data)
        latest = journal.latest(name)
        if latest is not None and latest.sha256 == digest:
            return {
                "program": latest.display,
                "version": latest.number,
                "change": latest.change,
                "already": True,
            }
        change = journal.change or "no description"
        platform = journal.platform_name if name == journal.name and journal.platform_name else name
        label = None if self._sibling_owns(platform) else self._upload(platform, change)
        if label is None:  # the name is someone else's: the platform's own suffix rule
            platform = f"{name}-{self.player.removeprefix('ply_')[:SUFFIX_LENGTH]}"
            label = self._upload(platform, change)
            if label is None:
                raise PlayError("name_taken", f"the name {name} is taken; choose another name")
        match = LABEL.fullmatch(label)
        if match is None or match["name"] != platform:
            raise SoftmaxCliError(f"upload-policy reported {label!r} for {platform}")
        version = Version(
            name=name,
            platform_name=platform,
            number=int(match["number"]),
            sha256=digest,
            change=change,
            uploaded_at=now(),
        )
        target = self._version_file(version)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.program_file, target)
        journal.versions.append(version)
        journal.name, journal.platform_name, journal.change = name, platform, None
        journal.save(self.workspace)
        return {
            "program": version.display,
            "version": version.number,
            "change": change,
            "already": False,
            "platform_name": platform,
        }

    def _sibling_owns(self, platform: str) -> bool:
        """True when another player of this user already owns the policy name: uploading to it would
        add this program as a version of that player's policy, so the name counts as taken."""
        return bool(self._mine(platform, any_player=True)) and not self._mine(platform)

    def _upload(self, platform: str, change: str) -> str | None:
        """The new ``<name>:v<N>`` label, or None when the name belongs to someone else."""
        self.client.require_player()
        args = ["upload-policy", "--file", str(self.program_file.resolve()), "--name", platform]
        args += ["--title", change[:50]]
        result = self.client._cli.run("coworld", args, timeout=UPLOAD_TIMEOUT_S, ok_codes=(0, 1))
        if result.returncode != 0:
            if NAME_TAKEN.search(f"{result.stderr}\n{result.stdout}"):
                return None
            raise SoftmaxCliError(describe_failure("coworld", args, result))
        label = _line_value(result.stdout, "Upload complete:")
        if label is None:
            raise SoftmaxCliError(
                f"upload-policy printed no 'Upload complete:' line for {platform}"
            )
        return label

    def display_name(self, platform_name: str) -> str:
        """The user's name for a platform policy name: without a player suffix this tool (or an
        earlier Play, which appended the whole player id) added."""
        for suffix in (self.player, self.player.removeprefix("ply_")[:SUFFIX_LENGTH]):
            if suffix and platform_name.endswith(f"-{suffix}"):
                return platform_name.removesuffix(f"-{suffix}")
        return platform_name

    def _split(self, program: str) -> tuple[str, int | None]:
        match = LABEL_REF.fullmatch(program.strip().lower())
        if match is None:
            raise PlayError(
                "bad_arguments",
                f'name a program as the user does, like "towerless v2": {program!r}',
            )
        number = match["number"]
        return self.display_name(match["name"]), int(number) if number else None

    def _mine(
        self,
        platform_name: str | None = None,
        version: int | None = None,
        *,
        any_player: bool = False,
    ) -> list[dict]:
        """This player's policy versions on the platform (``GET /stats/policy-versions`` with
        ``player_id``): rows with ``name`` and ``version``. ``mine=true`` alone means every player of
        this user, so it is narrowed to this player unless ``any_player`` asks for the whole user."""
        query = {"mine": "true", "limit": "200"}
        if not any_player:
            query["player_id"] = self.player
        if platform_name is not None:
            query["name_exact"] = platform_name
        if version is not None:
            query["version"] = str(version)
        rows: list[dict] = []
        cursor = None
        while True:
            page_query = {**query, "cursor": cursor} if cursor else query
            path = f"/observatory/stats/policy-versions?{urllib.parse.urlencode(page_query)}"
            page = self.client._cli.api_get_json(path)
            rows += page["entries"]
            cursor = page.get("next_cursor")
            if not cursor:
                return rows

    def _resolve(self, journal: Journal, program: str | None) -> Ref:
        """The version the user names ("towerless v2", "towerless:v2", or "towerless" for its
        latest; nothing for the current program's latest): from the journal, else from this
        player's versions on the platform under the plain or a suffixed name."""
        if program is None:
            latest = journal.latest()
            if latest is None:
                raise PlayError("no_program", "no version has been uploaded yet")
            return Ref(latest.name, latest.number, latest.label, latest)
        name, number = self._split(program)
        kept = journal.find(name, number) if number else journal.latest(name)
        if kept is not None:
            return Ref(name, kept.number, kept.label, kept)
        suffixes = ("", f"-{self.player.removeprefix('ply_')[:SUFFIX_LENGTH]}", f"-{self.player}")
        for platform in dict.fromkeys(f"{name}{suffix}" for suffix in suffixes):
            rows = self._mine(platform, number)
            if rows:
                found = max(int(row["version"]) for row in rows)
                return Ref(name, found, f"{platform}:v{found}", None)
        known = sorted({self.display_name(row["name"]) for row in self._mine()})
        wanted = f"{name} v{number}" if number else name
        raise PlayError(
            "unknown_version", f"none of your programs is {wanted}", {"your_programs": known}
        )

    def _remember(self, journal: Journal, ref: Ref) -> Version:
        """A journal entry for a version uploaded before this workspace kept one, so its practices
        and release are remembered; its text is not kept."""
        entry = Version(
            name=ref.name,
            platform_name=ref.label.rsplit(":v", 1)[0],
            number=ref.number,
            sha256="",
            change="uploaded before this workspace kept versions",
            uploaded_at="",
        )
        journal.versions.append(entry)
        return entry

    # --- live league (read-only) ----------------------------------------

    def league_status(self, *, league: str, top: int = 10) -> dict:
        """What the league looks like now, from the platform's public reads (no credits, no
        writes): whether it is paused, its last finished round, each division's top ``top``
        with scores, this player's own rank and score, what changed since the last look (a small
        snapshot kept in the workspace) and this player's latest league game. A part the
        platform does not answer is named in ``unavailable``."""
        top = max(1, min(int(top), 25))
        unavailable: list[str] = []
        out: dict = {"league": league}
        cli = self.client._cli

        def attempt(part: str, read):
            try:
                return read()
            except (SoftmaxCliError, KeyError, TypeError, ValueError, AttributeError):
                unavailable.append(part)
                return None

        record = attempt("league", lambda: cli.run_json("coworld", ["leagues", league]))
        if isinstance(record, dict):
            out["name"] = record.get("name")
            out["running"] = not (record.get("disabled_at") or record.get("rounds_paused_at"))

        def last_round():
            # Rounds of every division arrive newest created first, mostly pending ones that
            # have not started, so ask for the newest finished round.
            args = ["rounds", "--league", league, "--status", "completed", "--limit", "1"]
            page = cli.run_json("coworld", args)
            rows = page["entries"] if isinstance(page, dict) else page
            if not rows:
                return None
            row = rows[0]
            keep = ("id", "round_number", "status", "started_at", "completed_at")
            found = {key: row[key] for key in keep if row.get(key) is not None}
            division = row.get("division")
            if isinstance(division, dict) and division.get("name"):
                found["division"] = division["name"]
            return found

        round_now = attempt("round", last_round)
        if round_now is not None:
            out["last_finished_round"] = round_now
        rows = attempt("leaderboard", lambda: self._standings(league))
        mine: dict[str, dict] = {}
        if rows is not None:
            divisions: dict[str, dict] = {}
            for row in rows:
                division = divisions.setdefault(
                    row["division_id"],
                    {
                        "id": row["division_id"],
                        "name": row["division_name"],
                        "level": row["level"],
                        "size": 0,
                        "top": [],
                    },
                )
                division["size"] += 1
                entry = {
                    key: row.get(key)
                    for key in ("rank", "player_name", "score", "score_label", "rounds_played")
                }
                if row.get("settling"):
                    entry["settling"] = True
                if len(division["top"]) < top:
                    division["top"].append(entry)
                if row["player_id"] == self.player:
                    mine[row["division_id"]] = {**entry, "division": row["division_name"]}
            out["divisions"] = list(divisions.values())
            out["mine"] = list(mine.values())
            out["changes"] = self._league_changes(league, round_now, mine)
        latest = attempt("latest_result", lambda: self._latest_result(league))
        if latest is not None:
            out["latest_result"] = latest
        if unavailable:
            out["unavailable"] = unavailable
        return out

    def _standings(self, league: str) -> list[dict]:
        """Every division's board: the ladder, then the boards read concurrently."""
        cli = self.client._cli
        ladder = cli.run_json("coworld", ["results", league])

        def board(division: dict) -> list[dict]:
            return cli.run_json("coworld", ["results", division["id"]]) or []

        with ThreadPoolExecutor(max_workers=4) as pool:
            boards = list(pool.map(board, ladder))
        rows = []
        for division, entries in zip(ladder, boards, strict=True):
            for index, entry in enumerate(entries, start=1):
                rows.append(
                    {
                        "division_id": division["id"],
                        "division_name": division.get("name"),
                        "level": division.get("level"),
                        "rank": entry["rank"] if entry.get("rank") is not None else index,
                        "player_id": entry["player_id"],
                        "player_name": entry.get("player_name"),
                        "score": entry.get("score"),
                        "score_label": entry.get("score_label"),
                        "rounds_played": entry.get("rounds_played"),
                        "settling": entry.get("settling"),
                    }
                )
        return rows

    def _latest_result(self, league: str) -> dict | None:
        """This player's newest game in the league: ``{round, episode}`` as the platform
        answers; None when there is no game yet (a 404) or nothing usable in the answer."""
        try:
            data = self.client.latest_league_result(self.player, league)
        except SoftmaxCliError as error:
            if "404" in str(error):
                return None
            raise
        if data is None:  # the platform answers null when the player has no game yet
            return None
        if not isinstance(data, dict):
            raise TypeError("latest result is not an object")
        found: dict = {}
        round_row, episode = data.get("round"), data.get("episode")
        if isinstance(round_row, dict):
            round_part = {
                key: round_row[key]
                for key in ("id", "round_number", "status", "completed_at")
                if round_row.get(key) is not None
            }
            division = round_row.get("division")
            if isinstance(division, dict) and division.get("name"):
                round_part["division"] = division["name"]
            if round_part:
                found["round"] = round_part
        if isinstance(episode, dict):
            part = {
                key: episode[key]
                for key in ("episode_id", "status", "created_at", "replay_url", "error")
                if episode.get(key) is not None
            }
            for entry in episode.get("participants") or []:
                if isinstance(entry, dict) and entry.get("player_id") == self.player:
                    part["your_entry"] = {
                        k: v for k, v in entry.items() if isinstance(v, str | int | float | bool)
                    }
            if part:
                found["episode"] = part
        if not found:
            raise ValueError("latest result has no usable field")
        return found

    def _league_changes(self, league: str, round_now: dict | None, mine: dict[str, dict]) -> dict:
        """Compare with the snapshot of the last look, then keep this look. A snapshot of an
        unexpected shape counts as no earlier look."""
        path = self.root / "league_snapshot.json"
        try:
            snapshots = json.loads(path.read_text()) if path.is_file() else {}
        except (ValueError, OSError):
            snapshots = {}
        if not isinstance(snapshots, dict):
            snapshots = {}
        before = snapshots.get(league)
        round_id = (round_now or {}).get("id")
        snapshots[league] = {"at": now(), "round": round_id, "mine": mine}
        try:
            write_text(path, json.dumps(snapshots, indent=2))
        except OSError:
            pass  # the look is still reported; only the comparison next time is lost
        try:
            seen = before["mine"]
            moved = []
            for division, current in mine.items():
                old = seen.get(division)
                if old is None or (old["rank"], old["score"]) != (
                    current["rank"],
                    current["score"],
                ):
                    moved.append({"division": current["division"], "now": current, "was": old})
            for division, old in seen.items():
                if division not in mine:
                    moved.append({"division": old["division"], "now": None, "was": old})
            return {
                "since": before["at"],
                "new_finished_round": round_id != before["round"],
                "moved": moved,
            }
        except (KeyError, TypeError, AttributeError):
            return {"since": None, "note": "first look at this league from here"}

    # --- practice -------------------------------------------------------

    def practice_request(
        self,
        program: str | None = None,
        episodes: int = DEFAULT_EPISODES,
        seats: list[int] | None = None,
        attempt: int = 1,
        *,
        league: str,
    ) -> dict:
        """One private practice request (``POST /v2/experience-requests``) of ``episodes`` games.
        The platform needs the whole roster: the program on ``seats``, and on every other seat a
        player the platform draws from the league at random."""
        _, selection = self._published_game(league)
        if selection.game != self.game:
            raise PlayError(
                "wrong_game", "Fetch the selected game's starter before practising here."
            )
        count = selection.seat_count
        seats = [0] if seats is None else seats
        if not seats or len(set(seats)) != len(seats) or not set(seats) <= set(range(count)):
            raise PlayError("bad_arguments", f"seats must be distinct positions 0-{count - 1}")
        if not 1 <= episodes <= MAX_EPISODES or attempt < 1:
            raise PlayError("bad_arguments", f"episodes must be 1-{MAX_EPISODES}; attempt positive")
        journal = Journal.load(self.workspace)
        ref = self._resolve(journal, program)
        display, label, entry = ref.display, ref.label, ref.entry
        key = f"practice:{league}:{label}:a{attempt}"
        title = f"Practice: {display}" + (f" (run {attempt})" if attempt > 1 else "")

        def player(seat: int) -> dict:
            return {"policy_ref": label} if seat in seats else {"random": True}

        body = {
            "target": {"league_id": league, "variant_id": selection.variant_id},
            "roster": [{"slot": seat, "player": player(seat)} for seat in range(count)],
            "num_episodes": episodes,
            "private": True,
            "title": title,
            "idempotency_key": key,
        }
        self.client.require_player()
        args = ["xp-request", "create", "-", "--json"]
        result = self.client._cli.run("coworld", args, input=json.dumps(body), ok_codes=(0, 1))
        out = {"program": display}
        if result.returncode != 0:
            stderr = result.stderr or ""
            if xp.CREDIT_DENIAL.search(stderr):
                return {**out, **refused(CreditDenied(reason=xp._error_detail(stderr)))}
            raise SoftmaxCliError(describe_failure("coworld", args, result))
        try:
            request_id = str(json.loads(result.stdout)["id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SoftmaxCliError("xp-request create printed no request id") from None
        # The platform replays on the key alone, so a repeat keeps the first request's games.
        first = journal.find_practice(request_id)
        if first is not None:
            episodes, seats = first[1].episodes, first[1].seats
        else:
            entry = entry or self._remember(journal, ref)
            entry.practices.append(
                Practice(
                    request_id=request_id,
                    league_id=league,
                    title=title,
                    episodes=episodes,
                    seats=seats,
                    attempt=attempt,
                    requested_at=now(),
                )
            )
            journal.save(self.workspace)
        return {
            **out,
            "outcome": "requested",
            "request_id": request_id,
            "games": episodes,
            "seats": seats,
            "already": first is not None,
        }

    def practice_status(self, request_id: str | None = None, wait_seconds: int = 0) -> dict:
        """How far the practice is; once it is over, every game's result in plain words, a summary
        against the previous version's practice, and a link to watch each game. With
        ``wait_seconds`` it waits up to that long for the practice to end."""
        if not 0 <= wait_seconds <= MAX_WAIT_SECONDS:
            raise PlayError("bad_arguments", f"wait_seconds must be 0-{MAX_WAIT_SECONDS}")
        journal = Journal.load(self.workspace)
        if request_id is None:
            practices = [(v, p) for v in journal.versions for p in v.practices]
            if not practices:
                raise PlayError("no_practice", "no practice has been requested yet")
            found = max(reversed(practices), key=lambda vp: vp[1].requested_at)  # ties: the last
            request_id = found[1].request_id
        elif not re.fullmatch(r"xreq_[A-Za-z0-9_-]+", request_id):
            raise PlayError("bad_arguments", "request_id must be one practice_request returned")
        else:
            found = journal.find_practice(request_id)
        cli = self.client._cli
        deadline = time.monotonic() + wait_seconds
        while True:
            detail = cli.run_json("coworld", ["xp-request", "get", request_id])
            state = xp.STATE_BY_SERVER_STATUS.get(
                str(detail.get("status")), xp.UNKNOWN_STATUS_STATE
            )
            if state in ("completed", "failed") or time.monotonic() + POLL_SECONDS > deadline:
                break
            self._sleep(POLL_SECONDS)
        done, total = int(detail["completed_count"]), int(detail["episode_count"])
        display = (
            found[0].display if found else (detail.get("title") or "").removeprefix("Practice: ")
        )
        out = {
            "program": display,
            "request_id": request_id,
            "state": state,
            "games_finished": done,
            "games_total": total,
        }
        if state not in ("completed", "failed"):
            out["plain"] = f"Practice for {display}: {done} of {total} games finished so far."
            return out
        result_files: dict[str, Path] = {}
        if done:  # a practice whose games all failed has nothing to download
            dest = self.workspace / "practice" / request_id
            handle = xp.XpHandle(request_id, "practice", 1, 0, None, None)
            try:
                xp.artifacts(cli, handle, dest)
            except xp.ArtifactsIncomplete as error:
                # ``not_produced`` is terminal, as in the CLI (coworld episode_downloads): the
                # episode never made that artifact, so Play does not wait for it.
                receipts = xp.latest_receipts(dest / "manifest.jsonl")
                if any(not xp._completed(r) and r.get("state") != "not_produced" for r in receipts):
                    return {**out, "results_pending": str(error)}
            receipts = [r for r in xp.latest_receipts(dest / "manifest.jsonl") if xp._completed(r)]
            result_files = {
                r["episode_request_id"]: xp.verify_receipt(dest, r)
                for r in receipts
                if r.get("filename") == "results.json"
            }
        seats = (
            found[1].seats
            if found
            else [
                row["slot"]
                for row in detail["requested"]["roster"]
                if "policy_ref" in row["player"]
            ]
        )
        site = cli.server.removesuffix("/api") if cli.server.endswith("/api") else None
        games = []
        for number, row in enumerate(detail.get("episodes") or [], start=1):
            episode_id = row.get("episode_id")
            watch = f"{site}/observatory/episodes/{episode_id}" if site and episode_id else None
            path = result_files.get(row["id"])
            if path is not None:
                if self.game == "generals":
                    parsed = results.GENERALS_RESULTS.validate_json(path.read_text())
                    games.append(results.generals_game_summary(number, seats, parsed, watch))
                else:
                    parsed = results.GameResults.model_validate_json(path.read_text())
                    games.append(results.game_summary(number, seats, parsed, watch))
            else:
                why = (
                    f"the game did not finish: {row.get('error') or 'the platform gave no reason'}"
                    if row.get("status") == "failed"
                    else "the platform saved no results for this game"
                )
                games.append(results.failed_game(number, why, watch))
        previous = journal.previous_result(found[0]) if found else None
        summarize = results.summarize_generals if self.game == "generals" else results.summarize
        summary = summarize(
            display, games, (previous[0].display, previous[1]) if previous else None
        )
        if found:
            found[1].summary = summary
            journal.save(self.workspace)
        return {**out, "summary": summary, "games": games}

    # --- release --------------------------------------------------------

    def program_release(self, program: str, user_said_yes: bool, league: str) -> dict:
        """``coworld submit <name>:v<N> --league L --player P``, once: an existing submission of
        the same version to the league is reported as released. Either way the cogent's other
        programs in that league are retired: one program per league, a release replaces."""
        if user_said_yes is not True:
            raise PlayError("bad_arguments", "release only after the user said yes to this version")
        _, selection = self._published_game(league)
        if selection.game != self.game:
            raise PlayError("wrong_game", "This program belongs to another game.")
        if self._split(program)[1] is None:
            raise PlayError("bad_arguments", 'a release names one version, like "towerless v2"')
        journal = Journal.load(self.workspace)
        ref = self._resolve(journal, program)
        display, label, entry = ref.display, ref.label, ref.entry
        out = {"program": display}
        self.client.require_player()
        found = [
            row
            for row in self.client.submissions(self.player)
            if row["policy_version_id"] == label and row["league_id"] == league
        ]
        if found:
            return {**out, "outcome": "released", "already": True, **self._replaced(label, league)}
        try:
            result = self.client.submit(label, league, self.player)
        except SoftmaxCliError as error:
            # The shared client raises a 402 as a failure; it is the same refusal as practice's.
            if not xp.CREDIT_DENIAL.search(str(error)):
                raise
            stderr = re.split(r" exited -?[0-9]+: ", str(error), maxsplit=1)[-1]
            return {**out, **refused(CreditDenied(reason=xp._error_detail(stderr)))}
        entry = entry or self._remember(journal, ref)
        entry.release = Release(league_id=league, submission_id=result, released_at=now())
        journal.save(self.workspace)
        return {**out, "outcome": "released", "already": False, **self._replaced(label, league)}

    def _replaced(self, label: str, league: str) -> dict:
        """``{"replaced": ["towerless v1"]}`` for the programs just retired from the league, and
        ``replace_failed`` for any the platform would not retire, in the user's names; empty
        when this release replaced nothing."""
        retired, refused = self.client.retire_other_memberships(league, label)

        def spoken(other: str) -> str:
            name, _, version = other.rpartition(":v")
            return f"{self.display_name(name)} v{version}"

        out: dict = {}
        if retired:
            out["replaced"] = [spoken(x) for x in retired]
        if refused:
            out["replace_failed"] = [spoken(x) for x in refused]
        return out

    # --- the task card --------------------------------------------------

    def task_update(
        self,
        step: str,
        status: str,
        detail: str,
        title: str | None = None,
        version: int | None = None,
        outcome: str | None = None,
    ) -> dict:
        """Update the existing workspace task card. A ``title`` starts the card for a new request."""
        if step not in STEPS or status not in STEP_STATUS or not detail.strip():
            raise PlayError(
                "bad_arguments", f"step must be one of {', '.join(STEPS)}; detail is required"
            )
        journal = Journal.load(self.workspace)
        stamp = now()
        open_card = journal.card if journal.card and journal.card.body["state"] != "done" else None
        if title is not None:
            key = "play-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            body = {
                "id": key,
                "text": title.strip(),
                "origin": "player",
                "prediction": "",
                "doneWhen": DONE_WHEN,
                "createdAt": stamp,
                "status": "active",
                "state": "active",
                "order": 0,
                "steps": [
                    {"rung": rung, "status": "pending", "detail": label}
                    for rung, label in STEPS.values()
                ],
            }
            open_card = Card(key=key, body=body)
        elif open_card is None:
            raise PlayError("no_card", "there is no open card; start one with the request's title")
        body = open_card.body
        index = list(STEPS).index(step)
        entry = {**body["steps"][index], "status": STEP_STATUS[status], "detail": detail.strip()}
        if status == "running":
            entry["startedAt"] = stamp
        else:
            entry.setdefault("startedAt", stamp)
            entry["endedAt"] = stamp
        body["steps"][index] = entry
        if version is not None:
            body["version"] = version
        if outcome is not None:
            body.update(
                state="done",
                status="failed" if status == "failed" else "kept",
                outcome=outcome.strip(),
            )
        journal.card = Card(key=open_card.key, body=body)
        journal.save(self.workspace)
        return {
            "card": body["text"],
            "step": STEPS[step][1],
            "status": status,
            "finished": body["state"] == "done",
        }
