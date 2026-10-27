#!/usr/bin/env python3
"""Fetch OUR seats' hosted PRINT logs, annotations and player artifacts for Pudge Wars Basic.

  hosted_logs.py --xp XREQ --dest DIR      every episode of an Experience Request
  hosted_logs.py --episode EREQ --dest DIR one episode (league or XP)

Runs the real coworld CLI as the Player through the shared CLI
(`$RESEARCH_CLI softmax exec -- coworld ...`), which refuses every identity-switching
command; this tool never signs in, switches players or reads a token. The platform only
serves player logs, artifacts and annotations for seats played by policy versions the
Player owns, so "our seats" are exactly the seats it returns.

Layout, per episode (the same names match.py writes, so replay_inspect.py and the skills read
hosted and local episodes alike):

  DIR/<ereq>/episode.json                  the episode row (`coworld episodes EREQ --json`):
                                           seats, policy version ids, coworld_id and version
  DIR/<ereq>/results.json, DIR/<ereq>/replay   results and the (gzipped) replay
  DIR/<ereq>/players/<slot>/log.txt        that seat's PRINT output and host lines
  DIR/<ereq>/players/<slot>/annotations.jsonl  ANNOTATE rows, when the policy wrote any
  DIR/<ereq>/players/<slot>/artifact.zip   the player artifact, when one was uploaded

Commands used: `xp-request download XREQ --include results --include replay --include
player_logs --include player_artifact -d DIR/.downloads` (resumable; receipts in
DIR/.downloads/manifest.jsonl), `episode-logs EREQ --mine -d ...`, `episode-results EREQ -o
...`, `episodes EREQ --json`, `episode-annotations EREQ POLICY_VERSION_ID -o ...`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
from cli_contract import Errored, Rejected

LOG_NAME = re.compile(r'(?:.*-)?policy_agent_(\d+)\.log$')
ARTIFACT_NAME = re.compile(r'(?:.*-)?policy_(?:agent|artifact)_(\d+)\.zip$')
EREQ = re.compile(r'ereq_[0-9a-f-]{36}')
XREQ = re.compile(r'xreq_[0-9a-f-]{36}')


def research_cli() -> str:
    executable = os.environ.get('RESEARCH_CLI')
    if not executable:
        raise Rejected('set RESEARCH_CLI to the installed shared CLI (it runs coworld as the Player)')
    return executable


def coworld(*args: str, timeout: int = 1800, ok=(0,)) -> subprocess.CompletedProcess:
    """`$RESEARCH_CLI softmax exec -- coworld ARGS` as the Player."""
    command = [research_cli(), 'softmax', 'exec', '--', 'coworld', *args]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode not in ok:
        raise Errored(f'coworld {" ".join(args[:2])} failed (exit {completed.returncode}): '
                      f'{(completed.stderr.strip() or completed.stdout.strip())[-800:]}', reason='coworld_failed')
    return completed


def episode_row(ereq: str) -> dict:
    row = json.loads(coworld('episodes', ereq, '--json').stdout)
    if isinstance(row, dict) and isinstance(row.get('episodes'), list):
        row = next((r for r in row['episodes'] if r.get('id') == ereq), row)
    if not isinstance(row, dict):
        raise Errored(f'coworld episodes {ereq} --json printed no episode object', reason='bad_output')
    return row


def policy_by_slot(row: dict) -> dict[int, str]:
    found = {}
    for index, participant in enumerate(row.get('participants') or []):
        slot = participant.get('position', participant.get('agent_idx', index))
        if participant.get('policy_version_id'):
            found[int(slot)] = participant['policy_version_id']
    return found


def place(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def annotations(ereq: str, episode: Path, row: dict, slots: list[int]) -> dict[int, str]:
    """Download annotations for our slots; returns {slot: outcome}."""
    policies = policy_by_slot(row)
    outcomes = {}
    for slot in slots:
        policy = policies.get(slot)
        if policy is None:
            outcomes[slot] = 'no policy version id in the episode row'
            continue
        target = episode / 'players' / str(slot) / 'annotations.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = coworld('episode-annotations', ereq, policy, '-o', str(target), ok=(0, 1, 2))
        outcomes[slot] = 'downloaded' if completed.returncode == 0 and target.is_file() else (
            'none: ' + (completed.stderr.strip() or completed.stdout.strip())[-200:])
    return outcomes


def latest_receipts(manifest: Path) -> list[dict]:
    latest = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            latest[row.get('artifact_id') or row.get('id')] = row
    return [row for row in latest.values() if row.get('kind') == 'artifact' and row.get('state') == 'completed']


def fetch_xp(xreq: str, dest: Path) -> list[dict]:
    downloads = dest / '.downloads'
    coworld('xp-request', 'download', xreq, '--include', 'results', '--include', 'replay',
            '--include', 'player_logs', '--include', 'player_artifact', '-d', str(downloads), ok=(0, 2))
    manifest = downloads / 'manifest.jsonl'
    if not manifest.is_file():
        raise Errored(f'xp-request download wrote no {manifest}', reason='bad_output')
    by_episode: dict[str, list[dict]] = {}
    for receipt in latest_receipts(manifest):
        by_episode.setdefault(receipt['episode_request_id'], []).append(receipt)
    episodes = []
    for ereq, receipts in sorted(by_episode.items()):
        episode = dest / ereq
        slots = []
        for receipt in receipts:
            source, name = downloads / receipt['path'], receipt['filename']
            if (match := LOG_NAME.fullmatch(name)):
                place(source, episode / 'players' / match[1] / 'log.txt')
                slots.append(int(match[1]))
            elif (match := ARTIFACT_NAME.fullmatch(name)):
                place(source, episode / 'players' / match[1] / 'artifact.zip')
            elif name.endswith('results.json') or name == 'results':
                place(source, episode / 'results.json')
            elif 'replay' in name:
                place(source, episode / 'replay')
        episodes.append(finish(ereq, episode, sorted(set(slots))))
    return episodes


def fetch_episode(ereq: str, dest: Path) -> dict:
    episode = dest / ereq
    downloads = episode / '.downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    coworld('episode-logs', ereq, '--mine', '-d', str(downloads), ok=(0, 2))
    slots = []
    for path in sorted(downloads.iterdir()):
        if path.is_file() and (match := LOG_NAME.fullmatch(path.name)):
            place(path, episode / 'players' / match[1] / 'log.txt')
            slots.append(int(match[1]))
        elif path.is_file() and (match := ARTIFACT_NAME.fullmatch(path.name)):
            place(path, episode / 'players' / match[1] / 'artifact.zip')
    coworld('episode-results', ereq, '-o', str(episode / 'results.json'))
    return finish(ereq, episode, slots, fetch_replay=True)


def finish(ereq: str, episode: Path, slots: list[int], *, fetch_replay: bool = False) -> dict:
    row = episode_row(ereq)
    (episode / 'episode.json').write_text(json.dumps(row, indent=2) + '\n')
    if fetch_replay and not (episode / 'replay').is_file() and row.get('replay_url'):
        with urllib.request.urlopen(row['replay_url'], timeout=120) as response:
            (episode / 'replay').write_bytes(response.read())
    notes = annotations(ereq, episode, row, slots)
    return {'episode': ereq, 'dir': str(episode), 'coworld_id': row.get('coworld_id'),
            'coworld_version': row.get('coworld_version'), 'our_slots': slots, 'annotations': notes,
            'artifacts': sorted(int(p.parent.name) for p in episode.glob('players/*/artifact.zip')),
            'replay': (episode / 'replay').is_file(), 'results': (episode / 'results.json').is_file()}


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='hosted_logs.py',
        examples=['python3 games/pudge-wars-basic/tools/hosted_logs.py --xp xreq_... --dest .scratch/xp/E --json',
                  'python3 games/pudge-wars-basic/tools/hosted_logs.py --episode ereq_... --dest .scratch/ep --json'])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--xp', help='Experience Request id (xreq_...)')
    which.add_argument('--episode', help='episode request id (ereq_...)')
    parser.add_argument('--dest', type=Path, required=True, help='directory for the episode folders (reused: resumable)')

    def body(args):
        if args.xp and not XREQ.fullmatch(args.xp):
            raise Rejected(f'--xp must be an xreq_ id, not {args.xp!r}')
        if args.episode and not EREQ.fullmatch(args.episode):
            raise Rejected(f'--episode must be an ereq_ id, not {args.episode!r}')
        research_cli()
        dest = args.dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        episodes = fetch_xp(args.xp, dest) if args.xp else [fetch_episode(args.episode, dest)]
        return {'dest': dest, 'episodes': episodes,
                'with_logs': sum(1 for episode in episodes if episode['our_slots'])}

    return cli_contract.run(parser, body, argv, engine_check='cached')


if __name__ == '__main__':
    raise SystemExit(main())
