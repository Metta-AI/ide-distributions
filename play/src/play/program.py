"""Play's operations on the user's one program, run in the profile venv as the profile's Player.

The cogent changes the program itself: ``program_change`` checks a full new text with
``play.basic`` and writes ``program.bas`` in the program workspace. ``program.bas`` is uploaded as
a new version of one program with a stable name, practiced, and released only when the user says
so. ``journal.json`` (``play.journal``) remembers every version, practice and release, so the
cogent names things as the user does ("towerless v2") and never needs a platform id.

Every platform call goes through the shared ``softmax_tool`` client: the credential is installed
into the profile-local Softmax home before each CLI call, and every mutating call (upload,
practice, release) first runs ``whoami`` and refuses unless the credential is the Player
``RESEARCH_PLAYER_ID`` (``SoftmaxClient._require_player``). The league is the one the coach chose
(the cogent's ``state/league`` record), else GotA's default league.

The coach's refusals come back as data, never as an error and never retried:
``{"outcome": "refused", "refusal": "permit_required" | "credit_denied" | "league_not_chosen",
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ide_research_core.softmax_tool import xp
from ide_research_core.softmax_tool.client import UPLOAD_TIMEOUT_S, SoftmaxClient, _line_value
from ide_research_core.softmax_tool.platform_cli import (
    CREDENTIAL_ENV,
    SoftmaxCliError,
    coach_refusal,
    describe_failure,
    parse_credential,
)
from ide_research_core.softmax_tool.protocol import CreditDenied, LeagueNotChosen, PermitRequired

from . import basic, results
from .games import GOTA
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
from .records import CogentRecords

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


def refused(refusal: CreditDenied | PermitRequired | LeagueNotChosen) -> dict:
    if isinstance(refusal, CreditDenied):
        return {"outcome": "refused", "refusal": "credit_denied", "reason": refusal.reason}
    if isinstance(refusal, LeagueNotChosen):
        return {"outcome": "refused", "refusal": "league_not_chosen", "reason": refusal.reason}
    return {
        "outcome": "refused",
        "refusal": "permit_required",
        "reason": refusal.reason,
        "state": refusal.state,
        "ask": refusal.ask,
    }


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


NAME_LIMIT = 255
# The soul record the server validates strictly; plain, truthful values for a cogent with none yet.
NEW_SOUL = {
    "temperament": "neutral",
    "voice": "Still finding my voice.",
    "aboutMistakes": "I say what went wrong and fix it.",
    "neverSilently": ["submit", "spend"],
    "body": "New here, getting to know my user.",
}


class Program:
    def __init__(
        self,
        env: Mapping[str, str],
        client: SoftmaxClient | None = None,
        fetch: Fetch = fetch_public,
        records: CogentRecords | None = None,
    ):
        if not env.get("PLAY_WORKSPACE"):
            raise PlayError("not_configured", "PLAY_WORKSPACE is not set")
        self.workspace = Path(env["PLAY_WORKSPACE"])
        self.player = env.get("RESEARCH_PLAYER_ID", "")
        self.game = GOTA
        self.client = client or SoftmaxClient(env)
        self._fetch = fetch
        self._credential = env.get(CREDENTIAL_ENV)
        self._records = records
        self._league: str | None = None
        self._sleep = time.sleep

    @property
    def records(self) -> CogentRecords:
        if self._records is None:
            if not self.player:
                raise PlayError("not_configured", "RESEARCH_PLAYER_ID is not set")
            token = parse_credential(self._credential).token
            self._records = CogentRecords(self.client._cli.server, self.player, token)
        return self._records

    def league_id(self) -> str:
        """The league the coach chose for this cogent, else the game's default league. A chosen
        league this module cannot build for is refused: its programs are built elsewhere."""
        if self._league is None:
            choice = self.records.get("state", "league")
            league = choice["body"]["leagueId"] if choice else self.game.league_id
            try:
                here = self._builds_here(league)
            except (OSError, KeyError, ValueError) as error:  # URLError is an OSError
                raise PlayError(
                    "league_unreadable", f"could not read the chosen league {league}: {error}"
                ) from error
            if not here:
                raise PlayError(
                    "builds_elsewhere",
                    "this coworld's programs are built on the user's own computer by their "
                    "coding agent, not here; guide them to its 'Copy prompt for your local agent' button",
                )
            self._league = league
        return self._league

    def _league_game(self, league_id: str) -> dict:
        """The game block of a league's public read (no credential)."""
        url = (
            f"{self.client._cli.server}/observatory/v2/leagues/"
            f"{urllib.parse.quote(league_id, safe='')}"
        )
        return json.loads(self._fetch(url))["game"]

    def _builds_here(self, league_id: str, game: dict | None = None) -> bool:
        """Whether programs for this league's coworld are built here: it is GotA's coworld, judged
        by the league's game (so any GotA league counts), not by the league's id. The two games are
        compared on the first coworld field both carry; with none in common it is not GotA's."""
        if league_id == self.game.league_id:
            return True
        game = game if game is not None else self._league_game(league_id)
        home = self._league_game(self.game.league_id)
        for field in ("canonical_coworld_id", "coworld_id", "coworld_name"):
            if game.get(field) and home.get(field):
                return game[field] == home[field]
        return False

    @property
    def program_file(self) -> Path:
        return self.workspace / PROGRAM

    @property
    def starter_file(self) -> Path:
        return self.workspace / "starter" / f"{self.game.starter_player}{self.game.suffix}"

    def _starter_text(self) -> str | None:
        return self.starter_file.read_text() if self.starter_file.is_file() else None

    # --- who we are -----------------------------------------------------

    def who_we_are(self) -> dict:
        """Whether the cogent has a name yet, its name, and its user's.

        ``named`` is true when the cogent's soul record holds a name; ``cogent_name`` is that name
        (the page renames the player to match shortly after, so the soul is the truth first), or
        None when the cogent is not named yet: the player's placeholder name is never its name.
        ``user_name`` comes from the public cogent profile (no credential) and is the user's
        account name, which ``softmax status`` and ``whoami`` also print; None when the user set
        none. ``coworld`` is None until the cogent has been brought into a coworld; then it is
        ``{"name", "builds_here"}``: the coworld's name and whether programs for it are built here
        (only GotA's are; any other coworld's are built by the user's own coding agent)."""
        if not self.player:
            raise PlayError("not_configured", "RESEARCH_PLAYER_ID is not set")
        url = f"{self.client._cli.server}/observatory/v2/cogents/{urllib.parse.quote(self.player, safe='')}"
        try:
            profile = json.loads(self._fetch(url))
        except urllib.error.HTTPError as error:
            raise PlayError(
                "who_unavailable", f"GET {url} answered HTTP {error.code}", {"status": error.code}
            ) from error
        soul = self.records.get("profile", "soul")
        name = (soul["body"].get("name") or "").strip() if soul else ""
        choice = self.records.get("state", "league")
        coworld = None
        if choice:
            try:
                league_id = choice["body"]["leagueId"]
                game = self._league_game(league_id)
                coworld = {
                    "name": game.get("coworld_name") or game["name"],
                    "builds_here": self._builds_here(league_id, game),
                }
            except (OSError, KeyError, TypeError, ValueError):
                # Optional (any read failure, URLError and timeouts included): never fail the first call.
                coworld = None
        return {
            "named": bool(name),
            "cogent_name": name or None,
            "user_name": profile.get("owner_name"),
            "coworld": coworld,
        }

    def take_name(self, name: str) -> dict:
        """Take the name the user chose: write it into the cogent's soul record."""
        name = name.strip()
        if not name:
            raise PlayError("bad_name", "the name is empty")
        if len(name) > NAME_LIMIT:
            raise PlayError("bad_name", f"the name is longer than {NAME_LIMIT} characters")
        live = self.records.get("profile", "soul")
        rev = live["rev"] if live else None
        old = live["body"] if live else {}
        body = {"name": name}
        for field, default in NEW_SOUL.items():
            value = old.get(field)
            body[field] = value if isinstance(value, type(default)) and value else default
        key = f"play:soul-name:{hashlib.sha256(name.encode()).hexdigest()[:16]}:{rev or 0}"
        self.records.put("profile", "soul", body, rev, key)
        return {"name": name}

    # --- starter --------------------------------------------------------

    def starter_fetch(self, player: str | None = None) -> dict:
        """Download the coworld's starter player file into ``starter/`` and check its sha256."""
        coworld = self.client._league_coworld(self.league_id())
        entries = [p for p in (coworld.get("manifest") or {}).get("player") or [] if p.get("file")]
        wanted = player or self.game.starter_player
        entry = next((p for p in entries if p.get("id") == wanted), None)
        if entry is None and player is None and entries:
            entry = entries[0]  # softmax.com's rule: the `baseline` entry, else the first
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
        target = self.workspace / "starter" / f"{entry['id']}{self.game.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {
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
        problems, warnings = basic.check(text, starter)
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
        self.league_id()
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
            self.workspace
            / "versions"
            / version.platform_name
            / f"v{version.number}{self.game.suffix}"
        )

    # --- upload ---------------------------------------------------------

    def program_upload(self, name: str | None = None) -> dict:
        """Upload ``program.bas`` as the next version of the program (``coworld upload-policy``).
        The same file uploaded again is the same version, not a new one."""
        self.league_id()
        if not self.program_file.is_file():
            raise PlayError("no_program", "there is no changed program to upload yet")
        data = self.program_file.read_bytes()
        problems, _ = basic.check(data.decode("utf-8", "replace"))
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
        self.client._require_player()
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

    # --- practice -------------------------------------------------------

    def practice_request(
        self,
        program: str | None = None,
        episodes: int = DEFAULT_EPISODES,
        seats: list[int] | None = None,
        attempt: int = 1,
    ) -> dict:
        """One private practice request (``POST /v2/experience-requests``) of ``episodes`` games.
        The platform needs the whole roster: the program on ``seats``, and on every other seat a
        player the platform draws from the league at random."""
        seats = [0] if seats is None else seats
        if (
            not seats
            or len(set(seats)) != len(seats)
            or not set(seats) <= set(range(self.game.seat_count))
        ):
            raise PlayError("bad_arguments", "seats must be distinct GotA seats 0-9")
        if not 1 <= episodes <= MAX_EPISODES or attempt < 1:
            raise PlayError("bad_arguments", f"episodes must be 1-{MAX_EPISODES}; attempt positive")
        journal = Journal.load(self.workspace)
        ref = self._resolve(journal, program)
        display, label, entry = ref.display, ref.label, ref.entry
        league = self.league_id()
        key = f"practice:{label}:a{attempt}"
        title = f"Practice: {display}" + (f" (run {attempt})" if attempt > 1 else "")

        def player(seat: int) -> dict:
            return {"policy_ref": label} if seat in seats else {"random": True}

        body = {
            "target": {"league_id": league},
            "roster": [
                {"slot": seat, "player": player(seat)} for seat in range(self.game.seat_count)
            ],
            "num_episodes": episodes,
            "private": True,
            "title": title,
            "idempotency_key": key,
        }
        self.client._require_player()
        args = ["xp-request", "create", "-", "--json"]
        result = self.client._cli.run("coworld", args, input=json.dumps(body), ok_codes=(0, 1))
        out = {"program": display}
        if result.returncode != 0:
            stderr = result.stderr or ""
            if xp.CREDIT_DENIAL.search(stderr):
                return {**out, **refused(CreditDenied(reason=xp._error_detail(stderr)))}
            refusal = coach_refusal(stderr)
            if refusal is not None:
                return {**out, **refused(refusal)}
            raise SoftmaxCliError(describe_failure("coworld", args, result))
        try:
            request_id = str(json.loads(result.stdout)["id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise SoftmaxCliError("xp-request create printed no request id") from None
        if journal.find_practice(request_id) is None:
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
        summary = results.summarize(
            display, games, (previous[0].display, previous[1]) if previous else None
        )
        if found:
            found[1].summary = summary
            journal.save(self.workspace)
        return {**out, "summary": summary, "games": games}

    # --- release --------------------------------------------------------

    def program_release(self, program: str, user_said_yes: bool) -> dict:
        """``coworld submit <name>:v<N> --league L --player P``, once: an existing submission of
        the same version to the league is reported as released. Either way the cogent's other
        programs in that league are retired: one program per league, a release replaces."""
        if user_said_yes is not True:
            raise PlayError("bad_arguments", "release only after the user said yes to this version")
        if self._split(program)[1] is None:
            raise PlayError("bad_arguments", 'a release names one version, like "towerless v2"')
        journal = Journal.load(self.workspace)
        ref = self._resolve(journal, program)
        display, label, entry = ref.display, ref.label, ref.entry
        league = self.league_id()
        out = {"program": display}
        self.client._require_player()
        found = [
            row
            for row in self.client.submissions(self.player)
            if row["policy_version_id"] == label and row["league_id"] == league
        ]
        if found:
            return {**out, "outcome": "released", "already": True, **self._replaced(label, league)}
        try:
            result = self.client.submit(label, league)
        except SoftmaxCliError as error:
            # The shared client raises a 402 as a failure; it is the same refusal as practice's.
            if not xp.CREDIT_DENIAL.search(str(error)):
                raise
            stderr = re.split(r" exited -?[0-9]+: ", str(error), maxsplit=1)[-1]
            return {**out, **refused(CreditDenied(reason=xp._error_detail(stderr)))}
        if isinstance(result, (PermitRequired, LeagueNotChosen)):
            return {**out, **refused(result)}
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
        """Keep one task card per user request current in the cogent's ``tasks`` records, which the
        Mind on /play shows. A ``title`` starts the card for a new request."""
        if step not in STEPS or status not in STEP_STATUS or not detail.strip():
            raise PlayError(
                "bad_arguments", f"step must be one of {', '.join(STEPS)}; detail is required"
            )
        journal = Journal.load(self.workspace)
        stamp = now()
        open_card = journal.card if journal.card and journal.card.body["state"] != "done" else None
        if title is not None:
            if open_card is not None:
                previous = {
                    **open_card.body,
                    "state": "done",
                    "status": "dropped",
                    "outcome": f"Set aside for a new request: {title}",
                }
                self._write_card(open_card.key, previous)
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
        self._write_card(open_card.key, body)
        journal.card = Card(key=open_card.key, body=body)
        journal.save(self.workspace)
        return {
            "card": body["text"],
            "step": STEPS[step][1],
            "status": status,
            "finished": body["state"] == "done",
        }

    def _write_card(self, key: str, body: dict) -> None:
        live = self.records.get("tasks", key)
        rev = live["rev"] if live else None
        self.records.put("tasks", key, body, rev, f"play:{key}:{rev or 0}")
