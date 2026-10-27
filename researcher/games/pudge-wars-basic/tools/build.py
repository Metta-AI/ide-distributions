#!/usr/bin/env python3
"""Build a Pudge Wars Basic policy into an immutable, gated build directory (the build binding).

  build.py --source FILE_OR_PACKAGE_DIR --strategy STRATEGY_MD --out BUILD_DIR
  build.py --starter NAME --out BUILD_DIR            (base, aggressive, sniper or neural)

There is no compiler (`compiler: basic`): the candidate is the hand-written BASIC file (or a
neural package directory, packed deterministically into policy.zip, or a ready .zip) and the
build freezes its exact bytes. --starter builds games/pudge-wars-basic/baselines/NAME and is
exclusive with --source/--strategy. For `--starter base` the strategy is the shipped
seed/strategy/STRATEGY.md: the shipped seed/strategy/policy.bas must be byte-identical to
baselines/base.bas (checked), so strategy_sha256 is that STRATEGY.md's hash. Other starters
have no STRATEGY.md; their source hash stands in.

Gates: G1 (static upload rules) and G2 (compiles and plays a league-length episode in all
six seats on the league host; replay re-verifies) by default (validation_scope "build", a few
seconds once the binaries are built). --full-evaluation adds G3 (robustness, seeds 1..10 both
sides vs the base starter) and the advisory G4 screen. Upload requires every non-advisory gate
passed.

Engine: the build gates on the engine the league runs now (tools/engine.py: public API,
hourly cache), building its binaries on first use, and records it in version.json `engine`
(version, coworld_id, commit, source_url, manifest_sha256, live, knowledge, the binaries).
If the live lookup fails, or with --offline, it gates on the cached or last-known engine and
records live=false, stale_risk=true and the warning.

Writes BUILD_DIR/{policy.bas | policy.zip, version.json, report.json, report.md, gates/}.
BUILD_DIR must not exist. On a rejected or errored build the same files are written with
the error and no artifact. Exit 0 passed; 1 a gate failed; 2 rejected input; 3 errored.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
import engine
import episodes
import gates_common
import local_check
from build_input import InputRejected
from cli_contract import Errored, Rejected

GAME = cli_contract.GAME
SHIPPED_STRATEGY = GAME / 'seed/strategy/STRATEGY.md'
SHIPPED_POLICY = GAME / 'seed/strategy/policy.bas'
BUILD_GATES = ['G1', 'G2']
FULL_GATES = ['G1', 'G2', 'G3', 'G4']


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def engine_identity(offline: bool) -> dict:
    """The engine this build is gated on: the live league's (tools/engine.py), recorded in full.

    When the live lookup fails (or --offline), the build still runs on the cached or
    last-known engine, and version.json says so (live false, stale_risk true, warning).
    """
    record = engine.live(offline=offline)
    identity = {'schema': 'pudge-engine/2', **engine.summary(record), 'league_id': record.get('league_id'),
                'knowledge': engine.knowledge(record), 'starters': engine.starters(record)}
    identity['coworld_version'] = record.get('version')
    return identity


def resolve_inputs(args) -> dict:
    """The artifact source, the strategy it implements, and the hashes that tie them."""
    if args.starter:
        if args.source or args.strategy:
            raise Rejected('--starter builds baselines/NAME; it cannot be combined with --source or --strategy')
        if args.starter not in episodes.STARTERS:
            raise Rejected(f'unknown starter {args.starter!r}; valid: {", ".join(episodes.STARTERS)}')
        source = episodes.starter(args.starter)
        strategy = None
        if args.starter == 'base':
            if not SHIPPED_STRATEGY.is_file() or not SHIPPED_POLICY.is_file():
                raise Errored('the shipped seed/strategy/{STRATEGY.md,policy.bas} is missing', reason='bad_install')
            if SHIPPED_POLICY.read_bytes() != source.read_bytes():
                raise Errored('seed/strategy/policy.bas differs from baselines/base.bas', reason='bad_install')
            strategy = SHIPPED_STRATEGY
        return {'source': source, 'strategy': strategy, 'starter': args.starter}
    if not args.source or not args.strategy:
        raise Rejected('give --source and --strategy, or --starter NAME')
    source, strategy = args.source.resolve(), args.strategy.resolve()
    if not source.exists():
        raise InputRejected(f'source does not exist: {source}', path=source, action='Point --source at the candidate policy.')
    if not strategy.is_file():
        raise InputRejected(f'strategy does not exist: {strategy}', path=strategy,
                            action='Point --strategy at the candidate STRATEGY.md.')
    return {'source': source, 'strategy': strategy, 'starter': None}


def freeze(source: Path, scratch: Path) -> tuple[Path, dict]:
    """Copy (or pack) the source into the build as policy.bas or policy.zip; record source hashes."""
    if source.is_dir():
        artifact = gates_common.pack_directory(source, scratch / 'policy.zip')
        hashes = {str(path): episodes.sha256(path) for path in sorted(source.rglob('*')) if path.is_file()}
    else:
        name = 'policy.zip' if gates_common.is_zip(source) else 'policy.bas'
        artifact = scratch / name
        shutil.copyfile(source, artifact)
        hashes = {str(source): episodes.sha256(source)}
    return artifact, hashes


def report_markdown(version: dict) -> str:
    lines = [f'# Build {version["build_id"]}', '',
             (f'Status: **{version["status"]}**. Validation: {version["validation_scope"]}; '
              f'evaluation: {version["evaluation"]["status"]}. Artifact: `{version.get("artifact")}`.'), '',
             '| Gate | Outcome | Advisory | Seconds |', '| --- | --- | --- | --- |']
    lines += [f'| {name} | {gate["outcome"]} | {gate["advisory"]} | {gate["seconds"]} |'
              for name, gate in version['gates'].items()]
    failing = {name: gate['summary'] for name, gate in version['gates'].items() if gate['outcome'] != 'passed'}
    if failing:
        lines += ['', '## Not passed', '', '```json', json.dumps(failing, indent=2)[:20000], '```']
    return '\n'.join(lines) + '\n'


def build(args) -> dict:
    started = now()
    out = args.out.resolve()
    if out.exists():
        raise InputRejected(f'output already exists (builds are immutable): {out}', path=out,
                            action='Keep the previous build and choose a new --out directory.')
    inputs = resolve_inputs(args)
    identity = engine_identity(args.offline)
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f'.{out.name}.building-', dir=out.parent))
    try:
        artifact, source_hashes = freeze(inputs['source'], scratch)
        strategy = inputs['strategy']
        if strategy is not None:
            source_hashes[str(strategy)] = episodes.sha256(strategy)
        names = FULL_GATES if args.full_evaluation else BUILD_GATES
        context = gates_common.GateContext(scratch / 'gates', baseline=episodes.starter('base'),
                                           commit=identity['commit'])
        context.work_dir.mkdir()
        gates, status = local_check.run_gates(artifact, context, names)
        policy_sha256 = episodes.sha256(artifact)
        source_path = str(strategy) if strategy is not None else str(inputs['source'])
        runner = context.runner.describe() if context.runner else None
        version = {
            'build_id': out.name, 'status': status, 'artifact': artifact.name, 'policy_sha256': policy_sha256,
            'gates': gates, 'validation_scope': 'full' if args.full_evaluation else 'build',
            'evaluation': {'status': status if args.full_evaluation else 'not_run'},
            'strategy_sha256': source_hashes[source_path], 'source_path': source_path,
            'source_hashes': source_hashes, 'starter': inputs['starter'],
            'compiler': {'mode': 'basic', 'transform': 'pack-directory' if inputs['source'].is_dir() else 'identity'},
            'engine': {**identity, 'native_build': runner},
            'created': now(), 'started': started, 'finished': now(),
        }
        if inputs['starter'] == 'base':
            version['starter_note'] = ('seed/strategy/policy.bas is byte-identical to baselines/base.bas; '
                                       'strategy_sha256 is the shipped seed/strategy/STRATEGY.md')
        report = {key: version[key] for key in ('build_id', 'status', 'gates', 'validation_scope', 'evaluation',
                                                'artifact', 'policy_sha256', 'strategy_sha256')}
        report['warnings'] = [{'gate': name, 'summary': gate['summary']} for name, gate in gates.items()
                              if gate['advisory'] and gate['summary'].get('regression')]
        # Gate evidence paths point into the scratch directory until the rename below.
        def relocate(text: str) -> str:
            return text.replace(str(scratch), str(out))

        (scratch / 'version.json').write_text(relocate(json.dumps(version, indent=2)) + '\n')
        (scratch / 'report.json').write_text(relocate(json.dumps(report, indent=2)) + '\n')
        (scratch / 'report.md').write_text(relocate(report_markdown(version)))
        scratch.rename(out)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return {'build': str(out), 'build_id': out.name, 'status': status, 'artifact': str(out / artifact.name),
            'policy_sha256': policy_sha256, 'validation_scope': version['validation_scope'],
            'evaluation': version['evaluation'], 'gates': {name: gate['outcome'] for name, gate in gates.items()}}


def write_error(out: Path, status: str, error: dict) -> None:
    """Record a rejected/errored build attempt in a new directory (never over an existing build)."""
    if out.exists():
        return
    out.mkdir(parents=True)
    record = {'build_id': out.name, 'status': status, 'error': error, 'gates': {}}
    for name in ('version.json', 'report.json'):
        episodes.write_json(out / name, record)
    (out / 'report.md').write_text(f'# Build error\n\nStatus: **{status}**.\n\n```json\n'
                                   f'{json.dumps(error, indent=2)}\n```\n')


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='build.py',
        examples=['python3 games/pudge-wars-basic/tools/build.py --source cand/policy.bas --strategy cand/STRATEGY.md --out B --json',
                  'python3 games/pudge-wars-basic/tools/build.py --starter base --out .scratch/build/starter --json'])
    parser.add_argument('--source', type=Path, help='candidate .bas file, .zip package, or package directory')
    parser.add_argument('--strategy', type=Path, help="the candidate's STRATEGY.md")
    parser.add_argument('--starter', help='build a shipped starter instead: base, aggressive, sniper or neural')
    parser.add_argument('--out', type=Path, required=True, help='new build directory (must not exist)')
    parser.add_argument('--full-evaluation', action='store_true', help='also run G3 and the advisory G4 (minutes)')
    parser.add_argument('--offline', action='store_true',
                        help='do not contact the API: build on the cached or last-known engine (recorded as not live)')

    def body(args):
        try:
            result = build(args)
        except InputRejected as error:
            write_error(args.out.resolve(), 'rejected', error.to_dict())
            raise Rejected(str(error)) from None
        except cli_contract.ToolError as error:
            write_error(args.out.resolve(), 'rejected' if isinstance(error, Rejected) else 'errored',
                        {'reason': error.reason, 'message': str(error)})
            raise
        except Exception as error:
            write_error(args.out.resolve(), 'errored', gates_common.exception_summary(error))
            raise
        if result['status'] == 'errored':
            raise Errored('a gate errored (tool or environment fault); see report.md', reason='gate_errored', data=result)
        if result['status'] != 'passed':
            raise cli_contract.Failed('a gate failed; see report.md', reason='gates_failed', data=result)
        return result

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
