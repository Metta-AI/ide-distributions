#!/usr/bin/env python3
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/replay_stats.py
"""Hash-verified GotA replay statistics for one episode or an artifact batch.

Requires --ref (recording commit) or --binary. Rewards-only expansion is the default;
--actions enables costly action-acceptance probes. Caches live beside each replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPLAY_NAMES = ('replay.bin', 'replay.json', 'replay', 'replay.json.z')
CACHE_VERSION = 1


def digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def replay_path(directory: Path) -> Path:
    for name in REPLAY_NAMES:
        path = directory / name
        if path.is_file():
            return path
    raise ValueError(f'no replay in {directory}')


def episode_directories(root: Path) -> list[Path]:
    if any((root / name).is_file() for name in REPLAY_NAMES):
        return [root]
    # Include discovered episodes even if replay/results are missing: report failures.
    directories = {p.parent for name in (*REPLAY_NAMES, 'episode.json', 'results.json')
                   for p in root.rglob(name)}
    if not directories:
        raise ValueError(f'no episode artifacts under {root}')
    return sorted(directories)


@dataclass
class ExpandedEpisode:
    path: Path
    meta: dict
    summary: dict
    cache_hit: bool


def read_expanded(path: Path) -> tuple[dict, dict]:
    meta = summary = None
    with path.open() as source:
        for line in source:
            row = json.loads(line)
            if row['type'] == 'meta':
                meta = row
            if row['type'] == 'summary':
                summary = row
    if not meta or not summary or not summary.get('verified'):
        raise ValueError(f'missing verified summary in {path}')
    return meta, summary


def expand_episode(directory: Path, binary: Path, *, actions: bool = False,
                   snapshot_every: int = 0, refresh: bool = False) -> ExpandedEpisode:
    replay = replay_path(directory)
    results = directory / 'results.json'
    episode = directory / 'episode.json'
    if not results.is_file():
        raise ValueError(f'missing results.json in {directory}')
    cache = directory / 'expanded-replay.jsonl'
    receipt = directory / 'expanded-replay.cache.json'
    signature = {'cache_version': CACHE_VERSION, 'replay': digest(replay),
                     'results': digest(results), 'episode': digest(episode) if episode.exists() else None,
                     'binary': digest(binary), 'actions': actions, 'snapshot_every': snapshot_every}
    if not refresh and cache.exists() and receipt.exists():
        try:
            saved = json.loads(receipt.read_text())
            if saved['inputs'] == signature and saved['output_sha256'] == digest(cache):
                meta, summary = read_expanded(cache)
                return ExpandedEpisode(cache, meta, summary, True)
        except (OSError, ValueError, KeyError):
            pass  # Incomplete/corrupt cache is regenerated; never used as evidence.
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.expand-', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        command = [str(binary), str(replay), str(temporary),
                   '--actions' if actions else '--no-actions', '--snapshot-every', str(snapshot_every)]
        if episode.exists():
            command += ['--episode', str(episode)]
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        if run.returncode:
            raise ValueError(f'{directory.name}: expansion failed: {run.stderr.strip() or run.stdout.strip()}')
        meta, summary = read_expanded(temporary)
        if not summary.get('results_verified'):
            raise ValueError(f'{directory.name}: results were not verified')
        temporary.replace(cache)
        saved = {'inputs': signature, 'output_sha256': digest(cache)}
        temporary.write_text(json.dumps(saved, indent=2) + '\n')
        temporary.replace(receipt)
        return ExpandedEpisode(cache, meta, summary, False)
    finally:
        temporary.unlink(missing_ok=True)


def episode_stats(directory: Path, expanded: ExpandedEpisode) -> dict:
    summary = expanded.summary
    ticks = summary['ticks']
    if ticks <= 0:
        raise ValueError('nonpositive recorded duration')
    seats = [dict(hero) for hero in summary['heroes']]
    deaths = []
    if expanded.path.exists():
        with expanded.path.open() as source:
            for line in source:
                event = json.loads(line)
                if event.get('kind') == 'hero_death':
                    deaths.append(event)
    for seat in seats:
        if not seat.get('reward_xp_verified') or not seat.get('reward_gold_verified'):
            raise ValueError(f'engine reward totals unverified in {directory.name} seat {seat["slot"]}')
        allies = [s for s in seats if s['team'] == seat['team']]
        team_lh = sum(s['last_hits'] for s in allies)
        seat['xp_per_1000_ticks'] = 1000 * seat['xp'] / ticks
        seat['team_last_hit_share'] = seat['last_hits'] / team_lh if team_lh else None
        seat['rank_within_team'] = 1 + sum(s['last_hits'] > seat['last_hits'] for s in allies)
        enemies = [event for event in deaths if event['team'] != seat['team']]
        seat['nearby_enemy_deaths'] = None
        seat['nearby_enemy_kills'] = None
        seat['nearby_death_share'] = None
        seat['nearby_kill_share'] = None
        if expanded.path.exists() and all('heroes_before' in event for event in enemies):
            nearby = []
            for event in enemies:
                positions = {hero['slot']: hero for hero in event['heroes_before']}
                own = positions[seat['slot']]
                victim = positions[event['slot']]
                dx = own['pos']['x'] - victim['pos']['x']
                dy = own['pos']['y'] - victim['pos']['y']
                if own['hp'] > 0 and dx * dx + dy * dy <= 144:
                    nearby.append(event)
            kills = sum(event['killer_slot'] == seat['slot'] for event in nearby)
            unknown = sum(event['killer_slot'] is None for event in nearby)
            seat['nearby_enemy_deaths'] = len(nearby)
            seat['nearby_enemy_kills'] = kills if not unknown else None
            seat['nearby_death_share'] = len(nearby) / len(enemies) if enemies else None
            seat['nearby_kill_share'] = kills / len(nearby) if nearby and not unknown else None
    teams = []
    for team in sorted({s['team'] for s in seats}):
        allies = [s for s in seats if s['team'] == team]
        row = {'team': team, 'policy_names': sorted({s['policy_name'] for s in allies if s['policy_name']}),
                   'mean_level': sum(s['level'] for s in allies) / len(allies)}
        for key in ('last_hits', 'hero_kills', 'building_kills', 'tower_kills', 'barracks_kills',
                    'deaths', 'assists', 'xp', 'gold_earned', 'banked_gold',
                    'tower_kills_min', 'tower_kills_max', 'barracks_kills_min', 'barracks_kills_max'):
            values = [s[key] for s in allies]
            row[key] = sum(values) if all(v is not None for v in values) else None
        row['xp_per_1000_ticks'] = 1000 * row['xp'] / ticks
        teams.append(row)
    metadata = directory / 'episode.json'
    episode_id = json.loads(metadata.read_text())['id'] if metadata.exists() else directory.name
    return {'episode_id': episode_id, 'directory': str(directory), 'ticks': ticks,
                'outcome': summary['outcome'], 'verified': True,
                'attribution_complete': summary['attribution_complete'],
                'actions_enabled': summary['actions_enabled'], 'cache_hit': expanded.cache_hit,
                'expanded': str(expanded.path), 'seats': seats, 'teams': teams}


def cell(value) -> str:
    if value is None:
        return 'unknown'
    if isinstance(value, float):
        return f'{value:.3f}'
    return str(value).replace('|', '\\|').replace('\n', ' ')


def markdown(report: dict) -> str:
    lines = []
    for episode in report['episodes']:
        lines += [f'### {episode["episode_id"]}', '',
                  (f'{episode["ticks"]:,} ticks; {episode["outcome"]}; hashes verified; '
                  f'attribution complete: {episode["attribution_complete"]}.'), '',
                  '| Seat | Team | Class | Policy | LH | Hero kills | Buildings | Towers | Barracks | Deaths | Level | XP | XP/1k | Team LH share | Rank |',
                  '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for row in episode['seats']:
            keys = ('slot', 'team', 'class', 'policy_name', 'last_hits', 'hero_kills', 'building_kills',
                    'tower_kills', 'barracks_kills', 'deaths', 'level', 'xp', 'xp_per_1000_ticks',
                    'team_last_hit_share', 'rank_within_team')
            lines.append('| ' + ' | '.join(cell(row[k]) for k in keys) + ' |')
        lines += ['', '| Team | Policies | LH | Hero kills | Buildings | Towers | Barracks | Deaths | Mean level | XP | XP/1k |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for row in episode['teams']:
            values = [row['team'], ', '.join(row['policy_names'])] + [row[k] for k in
                ('last_hits', 'hero_kills', 'building_kills', 'tower_kills', 'barracks_kills',
                 'deaths', 'mean_level', 'xp', 'xp_per_1000_ticks')]
            lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
        lines.append('')
    if report['errors']:
        lines += ['### Failed episodes (not included in tables)', '']
        lines += [f'- {cell(e["directory"])}: {cell(e["error"])}' for e in report['errors']]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    engine = parser.add_mutually_exclusive_group(required=True)
    engine.add_argument('--binary', type=Path)
    engine.add_argument('--ref', help='exact recording source commit; builds through the build script')
    parser.add_argument('--actions', action='store_true')
    parser.add_argument('--snapshot-every', type=int, default=0)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--json', nargs='?', const='-', metavar='PATH', help='JSON to PATH, or stdout if omitted')
    args = parser.parse_args()
    if args.snapshot_every < 0:
        parser.error('--snapshot-every must be nonnegative')
    binary = args.binary
    if binary is None:
        run = subprocess.run([str(TOOLS / 'build_nim.sh'), args.ref],
                             check=True, capture_output=True, text=True)
        binary = Path(run.stdout.strip().splitlines()[-1])
    binary = binary.resolve(strict=True)
    report = {'episodes': [], 'errors': []}
    for directory in episode_directories(args.directory.resolve()):
        try:
            expanded = expand_episode(directory, binary, actions=args.actions,
                                      snapshot_every=args.snapshot_every, refresh=args.refresh)
            report['episodes'].append(episode_stats(directory, expanded))
            print(f'{directory.name}: {"cached" if expanded.cache_hit else "expanded"}', file=sys.stderr)
        except (OSError, ValueError, KeyError) as error:
            report['errors'].append({'directory': str(directory), 'error': str(error)})
    if args.json == '-':
        print(json.dumps(report, indent=2))
    else:
        print(markdown(report))
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=2) + '\n')
    return int(bool(report['errors']))


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'replay_stats: {error}', file=sys.stderr)
        raise SystemExit(1)
