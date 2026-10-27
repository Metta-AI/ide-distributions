"""Which engine the Pudge Wars Basic league runs now, and whether our knowledge matches it.

The league's game changes version (and Coworld id, and source commit) without notice, so the
tools never trust a pin. `live()` reads the public Observatory API, no credentials needed:

    GET {API}/observatory/v2/leagues/<LEAGUE_ID>   -> game.canonical_coworld_id
    GET {API}/observatory/v2/coworlds/<coworld id> -> version, manifest (runnable.source_url
                                                       pins the commit; player[] file shas)

`API` is $COGAMES_API_URL or https://softmax.com/api. The answer is cached in
$HERMES_HOME/cache/pudge-wars-basic/live-engine.json for one hour, so every tool run
re-checks at least hourly.
When the API cannot be reached the tools fall back to the cache (any age) or, without one,
to the last-known engine in tools/engine.env + tools/coworld_manifest.json, and every
envelope says so (`engine.live: false`, `stale_risk: true`, a warning). Never silently.

$PUDGE_LIVE_ENGINE may name a JSON engine record to use instead of the API (tests and
air-gapped hosts); envelopes report it as `source: override`.

Knowledge freshness: the shipped docs, seed and skills cite one game commit
(KNOWLEDGE_COMMIT in engine.env). `knowledge()` compares it with the live commit, and
`starters()` compares the live manifest's starter player files with baselines/.
"""
from __future__ import annotations

import functools
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import cli_contract

TOOLS = cli_contract.TOOLS
CACHE = cli_contract.CACHE
LIVE_CACHE = CACHE / 'live-engine.json'
COWORLD_CACHE = CACHE / 'coworlds'
VENDORED_MANIFEST = TOOLS / 'coworld_manifest.json'
TTL_SECONDS = 3600
OVERRIDE_ENV = 'PUDGE_LIVE_ENGINE'
SOURCE_URL = re.compile(r'https://github\.com/([^/]+)/([^/]+)/tree/([0-9a-f]{40})/?')


def api_base() -> str:
    return (os.environ.get('COGAMES_API_URL') or 'https://softmax.com/api').rstrip('/')


def fetch_json(url: str, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, headers={'Accept': 'application/json',
                                                   'User-Agent': 'pudge-wars-basic-tools'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read())
    if not isinstance(data, dict):
        raise ValueError(f'{url} returned no JSON object')  # noqa: TRY004 - malformed API answer
    return data


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def record_from_coworld(row: dict) -> dict:
    """The engine identity in one Coworld record (or a downloaded manifest)."""
    manifest = row.get('manifest', row)
    url = manifest['game']['runnable']['source_url']
    match = SOURCE_URL.fullmatch(url)
    if not match:
        raise ValueError(f'Coworld source_url does not pin a GitHub commit: {url!r}')
    players = {}
    for player in manifest.get('player', []):
        sha = re.search(r'([0-9a-f]{64})', player.get('file', ''))
        players[player['id']] = sha[1] if sha else None
    return {'coworld_id': row.get('id'), 'name': manifest['game'].get('name'),
            'version': row.get('version') or manifest['game'].get('version'),
            'commit': match[3], 'source_url': url,
            'repo': f'https://github.com/{match[1]}/{match[2]}.git',
            'manifest_sha256': row.get('manifest_hash'), 'players': players, 'manifest': manifest}


def coworld(coworld_id: str, *, fetch=fetch_json) -> dict:
    """One Coworld version's engine record; versions are immutable, so cached forever."""
    if not re.fullmatch(r'cow_[0-9a-f-]{36}', coworld_id or ''):
        raise ValueError(f'not a Coworld id: {coworld_id!r}')
    override = os.environ.get(OVERRIDE_ENV)
    if override:
        record = json.loads(Path(override).read_text())
        if record.get('coworld_id') == coworld_id:
            return record
    path = COWORLD_CACHE / f'{coworld_id}.json'
    if path.is_file():
        return json.loads(path.read_text())
    record = record_from_coworld(fetch(f'{api_base()}/observatory/v2/coworlds/{coworld_id}'))
    record['coworld_id'] = coworld_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + '\n')
    return record


def fallback() -> dict:
    """The last-known engine shipped with the tools (engine.env + coworld_manifest.json)."""
    pin = cli_contract.engine()
    record = record_from_coworld(json.loads(VENDORED_MANIFEST.read_text()))
    record.update(coworld_id=pin['COWORLD_ID'], version=pin['COWORLD_VERSION'], league_id=pin['LEAGUE_ID'])
    if record['commit'] != pin['POLYWORLD_PUDGE_COMMIT']:
        raise ValueError('tools/coworld_manifest.json and tools/engine.env name different commits')
    return record


def fetch_live(*, fetch=fetch_json) -> dict:
    league_id = cli_contract.engine()['LEAGUE_ID']
    league = fetch(f'{api_base()}/observatory/v2/leagues/{league_id}')
    game = league.get('game') or {}
    coworld_id = game.get('canonical_coworld_id') or game.get('coworld_id')
    if not coworld_id:
        raise ValueError(f'league {league_id} names no Coworld')
    record = dict(coworld(coworld_id, fetch=fetch))
    record['league_id'] = league_id
    return record


def _stamp(record: dict, *, live: bool, source: str, warning: str | None = None) -> dict:
    return {**record, 'live': live, 'source': source, 'stale_risk': not live, 'warning': warning}


@functools.cache
def live(*, offline: bool = False) -> dict:
    """The engine the league runs now (cached for an hour); see the module docstring.

    ``offline=True`` never touches the network: the cache of any age, else the fallback.
    """
    return resolve_live(offline=offline)


def resolve_live(*, offline: bool = False, fetch=fetch_json, clock=time.time) -> dict:
    override = os.environ.get(OVERRIDE_ENV)
    if override:
        record = json.loads(Path(override).read_text())
        record.setdefault('manifest', json.loads(VENDORED_MANIFEST.read_text()))
        return _stamp(record, live=record.get('live', True), source='override',
                      warning=record.get('warning'))
    cached = json.loads(LIVE_CACHE.read_text()) if LIVE_CACHE.is_file() else None
    age = clock() - cached['fetched_unix'] if cached else None
    if cached and age is not None and 0 <= age < TTL_SECONDS:
        return _stamp(cached, live=True, source='cache')
    if not offline:
        try:
            record = fetch_live(fetch=fetch)
            record.update(fetched_at=now(), fetched_unix=clock())
            LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            LIVE_CACHE.write_text(json.dumps(record, indent=2) + '\n')
            return _stamp(record, live=True, source='api')
        except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as error:
            reason = f'the live engine lookup failed ({type(error).__name__}: {error})'
    else:
        reason = 'the live engine was not checked (offline)'
    if cached:
        return _stamp(cached, live=False, source='stale-cache',
                      warning=f'{reason}; using the engine cached at {cached.get("fetched_at")}: '
                              'the league may run a newer version')
    return _stamp(fallback(), live=False, source='fallback',
                  warning=f'{reason}; using the last-known engine in tools/engine.env '
                          f'({cli_contract.engine()["COWORLD_VERSION"]}): the league may run a newer version')


def knowledge(record: dict) -> dict:
    """Whether the shipped knowledge (docs, seed, skills) was written for this engine."""
    cited = cli_contract.engine()['KNOWLEDGE_COMMIT']
    current = record['commit'] == cited
    result = {'cited_commit': cited, 'live_commit': record['commit'], 'live_version': record.get('version'),
              'current': current}
    if not current:
        result['message'] = (
            f'The game moved from {cited[:8]} (what docs/, seed/ and the skills cite) to '
            f'{record["commit"][:8]} ({record.get("version")}). Treat game-specific claims as possibly '
            'stale until re-verified, and propose a maintenance card that runs: '
            f'kb renew --game-repo <clone> --old {cited} --new {record["commit"]} --version {record.get("version")}')
    return result


def starters(record: dict) -> dict:
    """Live starter player file shas against baselines/*.provenance.md."""
    changed = []
    for name, sha in (record.get('players') or {}).items():
        provenance = cli_contract.GAME / 'baselines' / f'{name}.provenance.md'
        shipped = re.search(r'\| sha256 \| `([0-9a-f]{64})`', provenance.read_text()) if provenance.is_file() else None
        if sha is None or shipped is None or shipped[1] != sha:
            changed.append({'id': name, 'live_sha256': sha, 'shipped_sha256': shipped[1] if shipped else None})
    result = {'current': not changed, 'changed': changed}
    if changed:
        result['message'] = ('The live Coworld ships different starter player files than baselines/; '
                             'starter names in the tools still run the shipped copies. Refresh baselines/ '
                             'from the live package (coworld download) in a maintenance card.')
    return result


def summary(record: dict) -> dict:
    """The engine block every envelope carries."""
    return {key: record.get(key) for key in ('live', 'source', 'stale_risk', 'warning', 'version',
                                             'coworld_id', 'commit', 'source_url', 'manifest_sha256',
                                             'fetched_at')}


def manifest(record: dict) -> dict:
    return record.get('manifest') or json.loads(VENDORED_MANIFEST.read_text())


def episode_engine(directory: Path) -> tuple[str | None, str]:
    """(commit, how) from episode metadata beside a replay, or (None, why not).

    Reads our own episode.json (`engine_commit`), or any JSON episode row with a `coworld_id`
    (`coworld episodes --json`, `softmax league-results` episode.json), mapped to its commit
    through the public Coworld record. XP downloads carry no episode row.
    """
    for path in sorted(directory.glob('*.json')):
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if isinstance(row.get('engine_commit'), str) and re.fullmatch('[0-9a-f]{40}', row['engine_commit']):
            return row['engine_commit'], f'{path.name} engine_commit'
        if isinstance(row.get('coworld_id'), str):
            record = coworld(row['coworld_id'])
            return record['commit'], f'{path.name} coworld {row["coworld_id"]} ({record.get("version")})'
    return None, 'no episode metadata beside the replay'


def replay_commit(replay: Path, *, commit: str | None = None, coworld_id: str | None = None) -> tuple[str, str]:
    """The engine commit to decode a replay with, and how it was chosen.

    Order: --commit, --coworld-id, episode metadata beside the replay, else the live engine
    (the caller still checks the tape's game version against that engine's).
    """
    if commit:
        return commit, '--commit'
    if coworld_id:
        record = coworld(coworld_id)
        return record['commit'], f'--coworld-id {coworld_id} ({record.get("version")})'
    found, how = episode_engine(replay.parent)
    if found:
        return found, how
    return live()['commit'], f'live engine ({how})'
