#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_public.py
"""Anonymous reads of PUBLIC Paintbot PW league data: rounds, episodes, replays (no auth, no credits).

One polite fetcher for every lab tool that samples the public league (pw_scout.py fetch,
pw_winprob.py fetch):

  * a pause before every request (REQUEST_PAUSE_SECONDS), so a loop cannot hammer the API;
  * back-off on HTTP 429 and 5xx (Retry-After when the server sends one, else doubling from
    BACKOFF_SECONDS), giving up after MAX_RETRIES retries;
  * an explicit User-Agent: the API answers urllib's default agent ("Python-urllib/3.x") with
    HTTP 403, which looks like an auth problem but is not;
  * replays are stored gzip-compressed as replay.gz whatever the server sent (gzip or a raw
    POLYWORLDREPLAY tape); anything else is refused.

Public endpoints work anonymously with their own budget (docs/field.md). Keep samples small:
the callers cap episodes per run.

    import pw_public
    rounds = pw_public.rounds(league_id=pw_public.MAIN_LEAGUE, limit=6)
    rows = pw_public.round_episodes(rounds[0]["id"])
    pw_public.save_episode(out / rows[0]["id"], rows[0], rounds[0])   # episode.json + replay.gz
"""
from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://softmax.com/api/observatory"
MAIN_LEAGUE = "league_ae677105-0ab8-4561-81ec-c9cf6735821c"          # paintbot-pw teams ladder (docs/field.md)
COMPETITION_DIVISION = "div_63c08219-c269-4bc5-84ae-5ccfc55b0a90"    # its Competition division
USER_AGENT = "paintbot-pw-lab/1.0 (+personal_labs paintbot_pw_lab)"  # urllib's default agent gets HTTP 403
REQUEST_PAUSE_SECONDS = 1.0
MAX_RETRIES = 3
BACKOFF_SECONDS = 10.0
RETRY_STATUS = (429, 500, 502, 503, 504)
TIMEOUT_SECONDS = 60
TAPE_MAGIC = b"POLYWORLDREPLAY"
GZIP_MAGIC = b"\x1f\x8b"


class PublicFetchError(RuntimeError):
    """A public read failed after retries (network, HTTP error, rate limit)."""


class RateLimited(PublicFetchError):
    """HTTP 429 persisted through every retry. Not an environment problem: wait and retry.
    CLIs report failure code `rate_limited` with exit 1."""


class BadReplay(PublicFetchError):
    """The replay_url answered with bytes that are not a tape: skip that episode, keep going."""


def get(url: str, *, sleep=time.sleep, opener=urllib.request.urlopen) -> bytes:
    """One polite GET: pause first, back off on 429/5xx, give up after MAX_RETRIES retries."""
    delay = BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES + 1):
        sleep(REQUEST_PAUSE_SECONDS)
        request = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUS or attempt == MAX_RETRIES:
                kind = RateLimited if error.code == 429 else PublicFetchError
                raise kind(f"GET {url}: HTTP {error.code}") from error
            wait = float(error.headers.get("Retry-After") or delay) if error.headers else delay
            print(f"  HTTP {error.code} from {urllib.parse.urlparse(url).path}; waiting {wait:.0f} s",
                  file=sys.stderr, flush=True)
            sleep(wait)
            delay *= 2
        except urllib.error.URLError as error:
            raise PublicFetchError(f"GET {url}: {error.reason} (check the network)") from error
    raise AssertionError("unreachable")


def get_json(path: str, **params) -> dict:
    """GET API + path with query params (None values dropped) and parse the JSON body."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return json.loads(get(f"{API}{path}" + (f"?{query}" if query else "")))


def rounds(*, league_id: str | None = None, division_id: str | None = None, limit: int = 6) -> list[dict]:
    """The most recent rounds of a league or a division (newest first), any status."""
    if not (league_id or division_id):
        raise ValueError("give league_id or division_id")
    return get_json("/v2/rounds", league_id=league_id, division_id=division_id, limit=limit)["entries"]


def leaderboard(division_id: str) -> list[dict]:
    """The division's public leaderboard rows (rank, player_id, player_name, owner_name, score = MMR,
    policy_label, which is null for some champions: use round attributions for identity)."""
    return get_json(f"/v2/divisions/{division_id}/leaderboard", include_recent_rounds="false")


def round_episodes(round_id: str, limit: int | None = 50) -> list[dict]:
    """One page of a round's episode rows. Warns on stderr when the round has more."""
    listing = get_json(f"/v2/rounds/{round_id}/episodes", limit=limit)
    if listing.get("next_cursor"):
        print(f"  round {round_id}: more episodes than one page; only the first page is used", file=sys.stderr)
    return listing["entries"]


def gzip_tape(data: bytes) -> bytes:
    """The replay bytes as gzip; raises BadReplay unless they are gzip or a raw tape."""
    if data[:2] == GZIP_MAGIC:
        return data
    if data.startswith(TAPE_MAGIC):
        return gzip.compress(data)
    raise BadReplay(f"replay is neither gzip nor a {TAPE_MAGIC.decode()} tape (starts {data[:16]!r})")


def download_replay(url: str, dest: Path) -> Path:
    """Download a public replay_url to dest (gzip). Written atomically; an existing file is kept."""
    if dest.is_file():
        return dest
    data = gzip_tape(get(url))
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(data)
    partial.replace(dest)
    return dest


def episode_ready(directory: Path) -> bool:
    """True when a previous run already saved this episode (both files present)."""
    return (directory / "episode.json").is_file() and (directory / "replay.gz").is_file()


def save_episode(directory: Path, row: dict, round_row: dict) -> bool:
    """Write <directory>/episode.json (the public row + round_id/round_number) and replay.gz.

    Idempotent: returns False without any request when both files already exist."""
    if episode_ready(directory):
        return False
    directory.mkdir(parents=True, exist_ok=True)
    download_replay(row["replay_url"], directory / "replay.gz")
    (directory / "episode.json").write_text(json.dumps(
        {**row, "round_id": round_row["id"], "round_number": round_row["round_number"]}, indent=1) + "\n")
    return True


def skip_reason(row: dict, versions: set[str] | None = None) -> str | None:
    """Why a listing row is not a usable episode (None when it is)."""
    if row.get("status") != "completed":
        return f"status={row.get('status')}"
    if versions is not None and row.get("coworld_version") not in versions:
        return "other_version"
    if not row.get("replay_url"):
        return "no_replay_url"
    return None
