"""Run and read local Pudge Wars Basic episodes (library, no CLI).

Two runners, both the live engine's own code (native.py):

  league    (default) the league host, pudge_league, driven by the game's own
            scripts/run_episode.py exactly as the hosted runner stages seats. Writes the hosted
            accounting: players/<slot>/log.txt (the seat's PRINT output plus the host's
            "Player slot N started." / "BASIC error: ..." lines), players/<slot>/annotations.jsonl
            (ANNOTATE), player_status.json (exit_code 1 = "BASIC VM disabled"), and
            player_failure.json when a policy does not load. The replay is then re-simulated by
            pudge_headless for summary.json and events.jsonl (verifying every tick).
  headless  pudge_headless --bot: about 25% faster, but no seat logs, annotations or
            player_status; a VM failure is read from its `pudge N BASIC error:` line, and a
            policy that does not load is named by loading each policy alone.

Both give identical results.json, replays and state hashes for the same seats and seed.
One episode directory holds results.json (hosted schema), replay.pwr (raw tape; hosted copies
are the same bytes gzipped), summary.json and events.jsonl (headless match log), episode.json
(our provenance and VM status: runner, binaries and sha256s, engine commit, variant, seed,
ticks, kill goal, each seat's policy sha256, vm_failed, vm_error), and the runner's own files.
Neither runner reports per-decision instruction or work-unit use: the VM keeps it
(examples/pudge_wars/bots.nim:832-833 vm.lastWork/lastInstructions) but no output carries it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cli_contract
import evaluate
import native
from cli_contract import Errored, Rejected

GAME = cli_contract.GAME
BASELINES = GAME / 'baselines'
MANIFEST = cli_contract.TOOLS / 'coworld_manifest.json'  # last-known; engine.manifest() is live
RUNNERS = ('league', 'headless')
STARTERS = {'base': 'base.bas', 'aggressive': 'aggressive.bas', 'sniper': 'sniper.bas',
            'neural': 'neural.zip'}
EPISODE_SCHEMA = 'pudge-local-episode/1'
VM_ERROR = re.compile(r'^pudge (\d+) BASIC error: (.*)$', re.MULTILINE)
SCRIPTS = re.compile(r'^scripts: (\d+)/(\d+) active, (\d+) decisions$', re.MULTILINE)
HASH = re.compile(r'^hash: ([0-9A-F]{16}) map ([0-9A-F]{16})$', re.MULTILINE)
SCORE = re.compile(r'^score: west (\d+) / east (\d+)\s+goal (\d+)$', re.MULTILINE)
REPLAY_VERIFIED = 'replay verified: every tick matched'


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def variants() -> dict[str, dict]:
    """The live manifest's variants: seats, seed, max_ticks, kill_goal (the league's config)."""
    import engine
    manifest = engine.manifest(engine.live())
    return {variant['id']: {'seats': len(variant['game_config']['players']),
                            'seed': variant['game_config']['seed'],
                            'max_ticks': variant['game_config']['max_ticks'],
                            'kill_goal': variant['game_config']['kill_goal']}
            for variant in manifest['variants']}


def variant_config(name: str) -> dict:
    found = variants()
    if name not in found:
        raise Rejected(f'unknown variant {name!r}; valid: {", ".join(sorted(found))}')
    return found[name]


def starter(name: str) -> Path:
    return BASELINES / STARTERS[name]


def resolve_policy(text: str) -> Path:
    path = Path(text).expanduser()
    if path.is_file():
        return path.resolve()
    if text in STARTERS:
        return starter(text)
    raise Rejected(f'policy not found: {text!r} (a file path or one of {", ".join(STARTERS)})')


def parse_seats(specs: list[str], seats: int) -> list[Path]:
    """SPEC = PATH[:COUNT] or a starter name, filled in slot order (first half west)."""
    policies: list[Path] = []
    for spec in specs:
        name, count = spec, 1
        if not Path(spec).expanduser().is_file() and ':' in spec:
            name, _, text = spec.rpartition(':')
            if not text.isdigit() or int(text) < 1:
                raise Rejected(f'seat count in {spec!r} must be a positive integer')
            count = int(text)
        policies.extend([resolve_policy(name)] * count)
    if len(policies) != seats:
        raise Rejected(f'this variant has {seats} seats; the --seat specs fill {len(policies)}')
    return policies


def parse_seeds(seed: int | None, seeds: str | None, default: int) -> list[int]:
    if seed is not None and seeds is not None:
        raise Rejected('give --seed or --seeds, not both')
    if seeds is None:
        return [default if seed is None else seed]
    match = re.fullmatch(r'(-?\d+)\.\.(-?\d+)', seeds)
    if not match or int(match[1]) > int(match[2]):
        raise Rejected('--seeds must be A..B with A <= B (inclusive)')
    return list(range(int(match[1]), int(match[2]) + 1))


def mode_flags(seats: int) -> list[str]:
    # Two seats is the ladder duel (`--duel`, middle lane); the league host's duel runs the
    # same setup (state hashes match). Other sizes are N-vs-N teams.
    return ['--duel'] if seats == 2 else ['--team-size', str(seats // 2)]


def bot_flags(policies: list[Path]) -> list[str]:
    """Consecutive identical seats become one `--bot PATH:N` group, in slot order."""
    flags: list[str] = []
    index = 0
    while index < len(policies):
        end = index
        while end < len(policies) and policies[end] == policies[index]:
            end += 1
        flags += ['--bot', f'{policies[index]}:{end - index}']
        index = end
    return flags


def stage_policies(policies: list[Path], directory: Path) -> list[Path]:
    """Copy each distinct policy into the episode so the bytes that ran are kept."""
    directory.mkdir(parents=True)
    staged: dict[Path, Path] = {}
    for policy in policies:
        if policy not in staged:
            target = directory / f'{sha256(policy)[:16]}{policy.suffix or ".bas"}'
            if not target.exists():
                shutil.copyfile(policy, target)
            staged[policy] = target
    return [staged[policy] for policy in policies]


def run_binary(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        raise Errored(f'pudge_headless exceeded {timeout}s: {" ".join(command[:6])} ...',
                      reason='timeout') from None
    except OSError as error:
        raise Errored(f'cannot run pudge_headless: {error}', reason='environment_missing') from None


def policy_loads(binary: Path, policy: Path, timeout: int = 60) -> str | None:
    """None if the policy compiles and runs one tick alone, else the binary's error text."""
    with tempfile.TemporaryDirectory(prefix='pudge-load-') as scratch:
        completed = run_binary([str(binary), '--duel', '--bot', f'{policy}:2', '--ticks', '1',
                                '--duellog', str(Path(scratch) / 'log')],
                               cwd=Path(scratch), timeout=timeout)
    if completed.returncode == 0:
        return None
    return (completed.stderr.strip() or completed.stdout.strip())[-1000:]


def match_log(directory: Path) -> Path:
    found = sorted(path for path in directory.glob('match_*') if path.is_dir())
    if len(found) != 1:
        raise Errored(f'expected one headless match log under {directory}, found {len(found)}',
                      reason='missing_output')
    return found[0]


def results_from_log(summary: dict, end: dict, seed: int) -> dict:
    """The hosted results.json, rebuilt with coworld_host.nim:1059-1100@cd8111a5's formulas.

    Inputs are the headless match log: summary.json players (the same pudge counters the
    host reads) and the match_end event (final tick, outcome and reason).
    """
    players = sorted(summary['players'], key=lambda player: player['seat'])
    if [player['seat'] for player in players] != list(range(len(players))):
        raise Errored('headless summary seats are not 0..N-1', reason='bad_output')
    west, east = summary['score']['west'], summary['score']['east']
    reason = end['reason']
    if reason not in evaluate.END_REASONS:
        raise Errored(f'headless match ended without a result ({reason!r})', reason='bad_output')
    if reason == 'kill_goal':
        winner = end['outcome']
    else:
        winner = 'west' if west > east else 'east' if east > west else 'draw'
    per_seat = [{'slot': player['seat'], 'team': evaluate.TEAMS.index(player['team']),
                 'kills': player['kills'], 'deaths': player['deaths'],
                 'hooks_thrown': player['hooksThrown'], 'hooks_landed': player['hooksLanded']}
                for player in players]
    scores = [1.0 if (west, east)[row['team']] > (east, west)[row['team']] else 0.0 for row in per_seat]
    results = {'scores': scores, 'winner_team': winner, 'west_kills': west, 'east_kills': east,
               'end_tick': end['t'], 'end_reason': reason, 'seed': seed,
               'kill_goal': summary['killGoal'], 'per_seat': per_seat}
    evaluate.validate(results)
    return results


def run_headless_episode(binary: Path, policies: list[Path], *, seed: int, max_ticks: int, kill_goal: int,
                out: Path, variant: str | None = None, timeout: int = 900) -> dict:
    """Run one episode into a new directory ``out``; return its episode.json record.

    The record's ``status`` is ``completed`` or ``policy_load_failed`` (a seat's policy did
    not compile or unpack; ``failed_slots`` names them). A seat whose VM failed at runtime
    still completes the episode; its ``vm_failed`` is true. Tool faults raise Errored.
    """
    if out.exists():
        raise Rejected(f'episode output already exists: {out}')
    out.mkdir(parents=True)
    staged = stage_policies(policies, out / 'policies')
    seats = len(policies)
    record = {'schema': EPISODE_SCHEMA, 'runner': 'headless', 'binary': str(binary),
              'binary_sha256': sha256(binary),
              'variant': variant, 'seed': seed, 'max_ticks': max_ticks, 'kill_goal': kill_goal,
              'seats': [{'slot': slot, 'team': 'west' if slot < seats // 2 else 'east',
                         'source': str(policy), 'policy': str(staged[slot].relative_to(out)),
                         'sha256': sha256(staged[slot])} for slot, policy in enumerate(policies)]}
    with tempfile.TemporaryDirectory(prefix='.duellog-', dir=out) as scratch:
        command = [str(binary), *mode_flags(seats), *bot_flags(staged), '--seed', str(seed),
                   '--ticks', str(max_ticks), '--killGoal', str(kill_goal),
                   '--record', str(out / 'replay.pwr'), '--duellog', scratch]
        record['command'] = command
        started = time.monotonic()
        completed = run_binary(command, cwd=out, timeout=timeout)
        record['wall_seconds'] = round(time.monotonic() - started, 3)
        (out / 'stdout.log').write_text(completed.stdout)
        (out / 'stderr.log').write_text(completed.stderr)
        if completed.returncode != 0:
            failures = {}
            for policy in dict.fromkeys(staged):
                error = policy_loads(binary, policy)
                if error is not None:
                    failures[policy] = error
            if not failures:
                raise Errored(f'pudge_headless exited {completed.returncode}: '
                              f'{completed.stderr.strip()[-1000:]}', reason='binary_failed')
            record['status'] = 'policy_load_failed'
            record['failed_slots'] = [slot for slot, policy in enumerate(staged) if policy in failures]
            record['errors'] = {str(policy.relative_to(out)): error for policy, error in failures.items()}
            write_json(out / 'episode.json', record)
            return record
        log = match_log(Path(scratch))
        summary = json.loads((log / 'summary.json').read_text())
        events_text = (log / 'events.jsonl').read_text()
    events = [json.loads(line) for line in events_text.splitlines() if line.strip()]
    ends = [event for event in events if event.get('ev') == 'match_end']
    if len(ends) != 1:
        raise Errored('headless match log has no single match_end event', reason='bad_output')
    start = next(event for event in events if event.get('ev') == 'match_start')
    if (start.get('botEvery'), start.get('botAimNoise')) != (1, 0):
        raise Errored('headless match did not run at league cadence (botEvery 1, aim noise 0)',
                      reason='bad_output')
    results = results_from_log(summary, ends[0], seed)
    text = completed.stdout
    score, scripts, state = SCORE.search(text), SCRIPTS.search(text), HASH.search(text)
    if score is None or scripts is None or state is None:
        raise Errored('pudge_headless printed no score/scripts/hash summary', reason='bad_output')
    if (int(score[1]), int(score[2]), int(score[3])) != (
            results['west_kills'], results['east_kills'], results['kill_goal']):
        raise Errored('match log score differs from the binary summary', reason='bad_output')
    vm_errors = {int(match[1]) - 1: match[2] for match in VM_ERROR.finditer(text)}
    for seat in record['seats']:
        seat['vm_failed'] = seat['slot'] in vm_errors
        seat['vm_error'] = vm_errors.get(seat['slot'])
    if int(scripts[2]) != seats or int(scripts[1]) != seats - len(vm_errors):
        raise Errored('active script count disagrees with the BASIC error lines', reason='bad_output')
    summary['dir'] = str(out)
    write_json(out / 'summary.json', summary)
    (out / 'events.jsonl').write_text(events_text)
    # Compact, like the league host writes it, so both runners give the same bytes.
    (out / 'results.json').write_text(json.dumps(results, separators=(',', ':')))
    record.update(status='completed', scripts_active=int(scripts[1]), decisions=int(scripts[3]),
                  state_hash=state[1], map_hash=state[2], end_tick=results['end_tick'],
                  replay_sha256=sha256(out / 'replay.pwr'))
    write_json(out / 'episode.json', record)
    return record


class Runner:
    """Which binaries run local episodes: the league host (default) or the headless simulator."""

    def __init__(self, kind: str, headless: Path, league: Path | None = None, commit: str | None = None):
        if kind not in RUNNERS:
            raise Rejected(f'unknown runner {kind!r}; valid: {", ".join(RUNNERS)}')
        if kind == 'league' and league is None:
            raise Rejected('the league runner needs the pudge_league binary')
        self.kind, self.headless, self.league, self.commit = kind, headless, league, commit

    @classmethod
    def build(cls, kind: str = 'league', commit: str | None = None) -> Runner:
        """Build (or reuse) the binaries for the commit (default: the live league's)."""
        commit = commit or native.default_commit()
        headless, _ = native.ensure(commit, 'pudge_headless')
        league = native.ensure(commit, 'pudge_league')[0] if kind == 'league' else None
        return cls(kind, headless, league, commit)

    @classmethod
    def from_binaries(cls, kind: str, headless: Path, league: Path | None = None) -> Runner:
        receipt = native.read_receipt(headless) or {}
        return cls(kind, headless, league, (receipt.get('inputs') or {}).get('engine', {}).get('commit'))

    def describe(self) -> dict:
        return {'runner': self.kind, 'engine_commit': self.commit,
                'binaries': {name: {'path': str(path), 'sha256': sha256(path)}
                             for name, path in (('pudge_headless', self.headless), ('pudge_league', self.league))
                             if path is not None}}

    def run(self, policies: list[Path], *, seed: int, max_ticks: int, kill_goal: int, out: Path,
            variant: str | None = None, timeout: int = 900) -> dict:
        if self.kind == 'headless':
            record = run_headless_episode(self.headless, policies, seed=seed, max_ticks=max_ticks,
                                          kill_goal=kill_goal, out=out, variant=variant, timeout=timeout)
        else:
            record = run_league_episode(self, policies, seed=seed, max_ticks=max_ticks,
                                        kill_goal=kill_goal, out=out, variant=variant, timeout=timeout)
        record.update(self.describe())
        write_json(out / 'episode.json', record)
        return record


def seat_error(log: Path) -> str | None:
    """The last `BASIC error:` / `BASIC loading error:` line the host wrote to a seat log."""
    if not log.is_file():
        return None
    found = None
    with log.open(errors='replace') as stream:
        for line in stream:
            if 'BASIC error:' in line or 'BASIC loading error:' in line:
                found = line.strip()
    return found


def run_league_episode(runner: Runner, policies: list[Path], *, seed: int, max_ticks: int, kill_goal: int,
                       out: Path, variant: str | None = None, timeout: int = 900) -> dict:
    """One episode on the league host via the game's scripts/run_episode.py (module docstring)."""
    if out.exists():
        raise Rejected(f'episode output already exists: {out}')
    out.mkdir(parents=True)
    source = native.source_dir(runner.commit)
    script = source / 'scripts/run_episode.py'
    if not script.is_file():
        raise Errored(f'the game source at {runner.commit[:8]} has no scripts/run_episode.py; use --runner headless',
                      reason='runner_missing')
    command = [sys.executable, '-I', str(script), *map(str, policies), '--seed', str(seed),
               '--ticks', str(max_ticks), '--kill-goal', str(kill_goal), '--binary', str(runner.league),
               '--output', str(out), '--timeout', str(timeout)]
    started = time.monotonic()
    completed = run_binary(command, cwd=source, timeout=timeout + 60)
    wall = round(time.monotonic() - started, 3)
    (out / 'runner.log').write_text(completed.stdout + completed.stderr)
    if (out / 'episode.json').is_file():
        (out / 'episode.json').rename(out / 'host_episode.json')
    seats = len(policies)
    record = {'schema': EPISODE_SCHEMA, 'variant': variant, 'seed': seed, 'max_ticks': max_ticks,
              'kill_goal': kill_goal, 'command': command, 'wall_seconds': wall,
              'seats': [{'slot': slot, 'team': 'west' if slot < seats // 2 else 'east', 'source': str(policy),
                         'policy': f'players/{slot}/file', 'sha256': sha256(policy),
                         'log': f'players/{slot}/log.txt', 'annotations': f'players/{slot}/annotations.jsonl'}
                        for slot, policy in enumerate(policies)]}
    if completed.returncode == 2 and (out / 'player_failure.json').is_file():
        failure = json.loads((out / 'player_failure.json').read_text())
        slot = failure['failed_policy_index']
        record.update(status='policy_load_failed', failed_slots=[slot],
                      errors={f'players/{slot}/file': seat_error(out / f'players/{slot}/log.txt') or failure['message']})
        return record
    if completed.returncode not in (0, 3) or not (out / 'results.json').is_file():
        tail = (out / 'game.log').read_text()[-1000:] if (out / 'game.log').is_file() else ''
        raise Errored(f'the league host run failed (exit {completed.returncode}): '
                      f'{(completed.stderr.strip() or tail)[-1000:]}', reason='binary_failed')
    results = json.loads((out / 'results.json').read_text())
    evaluate.validate(results)
    status = {row['slot']: row for row in json.loads((out / 'player_status.json').read_text())['players']}
    for seat in record['seats']:
        row = status[seat['slot']]
        seat['vm_failed'] = row['exit_code'] != 0
        seat['vm_error'] = seat_error(out / seat['log']) if seat['vm_failed'] else None
        seat['status'] = row['reason']
    decoded = resimulate(runner.headless, out / 'replay.pwr', out / '.decode')
    for name in ('summary.json', 'events.jsonl'):
        shutil.move(str(out / '.decode' / name), str(out / name))
    shutil.rmtree(out / '.decode')
    state = HASH.search(decoded['stdout'])
    actions = re.search(r'^replay: (\d+)/(\d+) actions$', decoded['stdout'], re.MULTILINE)
    record.update(status='completed', scripts_active=sum(1 for seat in record['seats'] if not seat['vm_failed']),
                  decisions=None, actions=int(actions[2]) if actions else None, state_hash=state[1] if state else None,
                  map_hash=state[2] if state else None, end_tick=results['end_tick'],
                  replay_sha256=sha256(out / 'replay.pwr'), replay_verified=True)
    return record


def enemy_hook_hits(events_path: Path, team_of: dict[int, str]) -> dict[int, int]:
    """Per player id: hooks that latched an enemy butcher, from the re-simulated events.

    Counts `hook_landed` events whose victim is on the other team, plus `kill` events with
    cause `headshot` (a hook onto an enemy already hooked by someone else kills at once and
    never emits hook_landed: duellog.nim:447-460, sim.nim:4429-4443 @cd8111a5). The host's
    hooks_landed counter instead also counts latching an ally or an allied mine
    (sim.nim:4371-4373, 4458-4460 @cd8111a5).
    """
    hits = dict.fromkeys(team_of, 0)
    with events_path.open() as stream:
        for line in stream:
            if '"hook_landed"' not in line and '"headshot"' not in line:
                continue
            event = json.loads(line)
            if event.get('ev') == 'hook_landed':
                if team_of.get(event.get('victim')) not in (None, team_of[event['id']]):
                    hits[event['id']] += 1
            elif event.get('ev') == 'kill' and event.get('cause') == 'headshot' and event.get('killer') in hits:
                hits[event['killer']] += 1
    return hits


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + '\n')


def resimulate(binary: Path, replay: Path, out: Path, *, timeout: int = 900) -> dict:
    """Re-simulate a raw replay with --replay --duellog into ``out`` (new); verify every tick.

    Returns {summary, events_path, stdout}. A tape from another game version, or one whose
    hashes do not match, is an error: the binary refuses it.
    """
    if out.exists():
        raise Rejected(f'decode output already exists: {out}')
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='.duellog-', dir=out) as scratch:
        completed = run_binary([str(binary), '--replay', str(replay), '--duellog', scratch],
                               cwd=out, timeout=timeout)
        (out / 'stdout.log').write_text(completed.stdout)
        (out / 'stderr.log').write_text(completed.stderr)
        if completed.returncode != 0 or REPLAY_VERIFIED not in completed.stdout:
            raise cli_contract.Failed(
                f'replay did not verify: {(completed.stderr.strip() or completed.stdout.strip())[-800:]}',
                reason='replay_unverified')
        log = match_log(Path(scratch))
        shutil.copyfile(log / 'summary.json', out / 'summary.json')
        shutil.copyfile(log / 'events.jsonl', out / 'events.jsonl')
    summary = json.loads((out / 'summary.json').read_text())
    summary['dir'] = str(out)
    write_json(out / 'summary.json', summary)
    return {'summary': summary, 'events_path': out / 'events.jsonl', 'stdout': completed.stdout}
