#!/usr/bin/env python3
"""Decode a Pudge Wars Basic replay by re-simulation and summarise it per seat.

A replay is an action tape plus one state hash per tick, not a state dump (see
docs/replay-format.md). This tool gunzips it if needed (hosted copies are gzip), checks the
header's game version, runs `pudge_headless --replay R --duellog DIR` (which verifies every
tick's hash) and reads the match log it writes. The decode is cached next to the replay in
`<replay>.decoded-<binary sha12>/` (summary.json, events.jsonl, stdout.log) and reused.

Input: a replay file (raw `.pwr`/`replay` or gzipped `.replay`/`.gz`), or a directory
holding exactly one (a match.py episode, a `coworld run-episode` output, or an XP download).

Per seat (`--seat N` keeps one): enemy_hook_accuracy, the primary accuracy number
(enemy_hooks_landed / hooks thrown: hooks that latched an enemy, including headshots), then
kills, deaths, hooksThrown, hooksLanded and host_landed_ratio (the host counter ratio, which
also counts ally and allied-mine latches; duellog's `accuracy` is this same ratio),
headshots, denies, runes, upgrades, damage dealt/taken by cause, hit rate by distance band
([hits, throws] per 0-15/15-30/30-45/45+ tiles), dodge stats and time in the river.
`--events KIND,...` adds matching events (`all` for every kind) filtered by --seat (the
seat as actor, victim or killer) and --ticks A:B (half-open), capped by --limit.
Player ids in events are 1-based: id = slot + 1. When a results.json sits beside the
replay, it is checked against the results the re-simulation implies (exit 1 on mismatch).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
import episodes
import native
from cli_contract import Rejected

MAGIC = b'POLYWORLDREPLAY'
GZIP_MAGIC = b'\x1f\x8b'
CANDIDATE_NAMES = ('replay.pwr', 'replay', 'replay.gz', 'replay.replay')
SEAT_KEYS = ('kills', 'deaths', 'hooksThrown', 'hooksLanded', 'headshots', 'denies',
             'runes', 'upgrades', 'damageDealt', 'damageTaken', 'hitRateByDist', 'dodge',
             'timeInRiverSec', 'firstBlood')
ACTOR_FIELDS = ('id', 'victim', 'killer', 'by', 'owner')


def find_replay(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise Rejected(f'no such replay file or directory: {path}')
    found = [path / name for name in CANDIDATE_NAMES if (path / name).is_file()]
    found += sorted(p for p in path.glob('*.replay') if p not in found)
    found += sorted(p for p in path.glob('*.pwr') if p not in found)
    if len(found) != 1:
        raise Rejected(f'{path} must hold exactly one replay; found {[p.name for p in found]}')
    return found[0]


def raw_bytes(replay: Path) -> tuple[bytes, bool]:
    data = replay.read_bytes()
    if data[:2] == GZIP_MAGIC:
        return gzip.decompress(data), True
    return data, False


def header(data: bytes) -> dict:
    """Magic, u16 file version, u16 game version, u16 name length, name (tapes.nim:98-115@cd8111a5)."""
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 6:
        raise Rejected('not a Polyworld replay (missing POLYWORLDREPLAY header)')
    offset = len(MAGIC)
    file_version = int.from_bytes(data[offset:offset + 2], 'little')
    game_version = int.from_bytes(data[offset + 2:offset + 4], 'little')
    length = int.from_bytes(data[offset + 4:offset + 6], 'little')
    name = data[offset + 6:offset + 6 + length].decode('ascii', 'replace')
    return {'file_version': file_version, 'game_version': game_version, 'game': name}


def decode(binary: Path, replay: Path, cache_root: Path | None = None) -> tuple[dict, Path, dict]:
    """(replay info, decode dir, summary); reuses a matching cached decode."""
    data, gzipped = raw_bytes(replay)
    info = {'replay': str(replay.resolve()), 'replay_sha256': hashlib.sha256(replay.read_bytes()).hexdigest(),
            'raw_sha256': hashlib.sha256(data).hexdigest(), 'gzip': gzipped, **header(data)}
    expected = int(cli_contract.engine()['REPLAY_GAME_VERSION'])
    if info['game'] != 'pudge_wars' or info['game_version'] != expected:
        raise Rejected(f'replay is {info["game"]} game version {info["game_version"]}; this engine '
                       f'decodes pudge_wars version {expected} only (build the matching commit)')
    binary_sha = episodes.sha256(binary)
    cache = (cache_root or replay.parent) / f'{replay.name}.decoded-{binary_sha[:12]}'
    stamp = {'raw_sha256': info['raw_sha256'], 'binary_sha256': binary_sha}
    record = cache / 'decode.json'
    if record.is_file() and json.loads(record.read_text()) == stamp and (cache / 'summary.json').is_file():
        info['cache'] = {'dir': str(cache), 'reused': True}
        return info, cache, json.loads((cache / 'summary.json').read_text())
    if cache.exists():
        shutil.rmtree(cache)  # a stale or partial decode of our own; rebuilt below
    staging = cache.with_name(cache.name + '.partial')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    tape = staging / 'replay.pwr'
    tape.write_bytes(data)
    decoded = episodes.resimulate(binary, tape, staging / 'decode')
    for name in ('summary.json', 'events.jsonl', 'stdout.log'):
        shutil.move(str(staging / 'decode' / name), str(staging / name))
    shutil.rmtree(staging / 'decode')
    tape.unlink()
    (staging / 'decode.json').write_text(json.dumps(stamp, indent=2) + '\n')
    staging.rename(cache)
    info['cache'] = {'dir': str(cache), 'reused': False}
    summary = decoded['summary']
    summary['dir'] = str(cache)
    return info, cache, summary


def check_results(summary: dict, events_path: Path, results_path: Path) -> dict:
    """Compare a results.json beside the replay with the one the re-simulation implies."""
    results = json.loads(results_path.read_text())
    end = None
    with events_path.open() as stream:
        for line in stream:
            if '"match_end"' in line:
                end = json.loads(line)
    if end is None or not isinstance(results, dict):
        raise cli_contract.Failed(f'cannot check {results_path}: no match_end event or no results object',
                                  reason='results_mismatch')
    implied = episodes.results_from_log(summary, end, results.get('seed'))
    differing = sorted(key for key in implied if implied[key] != results.get(key))
    if differing:
        raise cli_contract.Failed(f'{results_path} disagrees with the replay on {", ".join(differing)}',
                                  reason='results_mismatch', data={'implied': implied})
    return {'results': str(results_path), 'matched': True}


def parse_ticks(text: str | None) -> tuple[int, int] | None:
    if text is None:
        return None
    start, sep, end = text.partition(':')
    if not sep or not start.isdigit() or not end.isdigit() or int(start) >= int(end):
        raise Rejected('--ticks must be A:B with 0 <= A < B')
    return int(start), int(end)


def involves(event: dict, player_id: int) -> bool:
    return any(event.get(field) == player_id for field in ACTOR_FIELDS)


def seat_rows(summary: dict, events_path: Path) -> list[dict]:
    players = sorted(summary['players'], key=lambda player: player['seat'])
    enemy_hits = episodes.enemy_hook_hits(events_path, {player['id']: player['team'] for player in players})
    rows = []
    for player in players:
        thrown = player['hooksThrown']
        row = {'slot': player['seat'], 'id': player['id'], 'team': player['team'],
               'enemy_hook_accuracy': enemy_hits[player['id']] / thrown if thrown else None,
               'enemy_hooks_landed': enemy_hits[player['id']]}
        row.update({key: player.get(key) for key in SEAT_KEYS})
        row['host_landed_ratio'] = player['hooksLanded'] / thrown if thrown else None
        rows.append(row)
    return rows


def select_events(path: Path, kinds: set[str] | None, seat: int | None,
                  ticks: tuple[int, int] | None, limit: int) -> tuple[list[dict], int]:
    selected, total = [], 0
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            if kinds is not None and event.get('ev') not in kinds:
                continue
            if ticks is not None and not ticks[0] <= event.get('t', -1) < ticks[1]:
                continue
            if seat is not None:
                if event.get('ev') == 'state':
                    event = {**event, 'p': [p for p in event.get('p', []) if p.get('id') == seat + 1]}
                elif event.get('ev') not in ('match_start', 'match_end', 'rune_spawn') and not involves(event, seat + 1):
                    continue
            total += 1
            if len(selected) < limit:
                selected.append(event)
    return selected, total


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='replay_inspect.py',
        examples=['python3 games/pudge-wars-basic/tools/replay_inspect.py EPISODE_DIR --json',
                  'python3 games/pudge-wars-basic/tools/replay_inspect.py ereq_x.replay --seat 2 --events kill,hook_missed --json'])
    parser.add_argument('replay', type=Path, help='replay file or a directory holding one')
    parser.add_argument('--seat', type=int, help='keep one seat (slot, 0-based)')
    parser.add_argument('--events', help='comma-separated event kinds to include, or "all"')
    parser.add_argument('--ticks', help='A:B half-open tick window for --events')
    parser.add_argument('--limit', type=int, default=500, help='maximum events returned (default 500)')
    parser.add_argument('--cache-dir', type=Path, help='where to cache the decode (default: beside the replay)')
    parser.add_argument('--binary', type=Path, help='use this pudge_headless instead of native.py path')
    parser.add_argument('--commit', help='engine commit for native.py (default: tools/engine.env)')

    def body(args):
        replay = find_replay(args.replay)
        ticks = parse_ticks(args.ticks)
        if args.limit < 0:
            raise Rejected('--limit must be nonnegative')
        if args.binary is not None:
            if not args.binary.is_file():
                raise Rejected(f'--binary does not exist: {args.binary}')
            binary = args.binary.resolve()
        else:
            binary = native.ensure(args.commit)[0]
        info, cache, summary = decode(binary, replay, args.cache_dir)
        events_path = cache / 'events.jsonl'
        seats = seat_rows(summary, events_path)
        results_path = replay.parent / 'results.json'
        results_check = check_results(summary, events_path, results_path) if results_path.is_file() else None
        if args.seat is not None:
            if args.seat not in range(len(seats)):
                raise Rejected(f'--seat must be in 0..{len(seats) - 1}')
            seats = [seats[args.seat]]
        data = {**info, 'binary': binary, 'verified': True,
                'match': {key: summary.get(key) for key in ('mode', 'seed', 'killGoal', 'ticks', 'outcome',
                                                             'reason', 'score', 'durationSec')},
                'results_check': results_check, 'seats': seats}
        if args.events:
            kinds = None if args.events == 'all' else {kind.strip() for kind in args.events.split(',') if kind.strip()}
            data['events'], data['events_total'] = select_events(events_path, kinds, args.seat, ticks, args.limit)
            data['events_truncated'] = data['events_total'] > len(data['events'])
        return data

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
