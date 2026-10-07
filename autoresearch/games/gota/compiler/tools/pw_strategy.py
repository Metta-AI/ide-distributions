#!/usr/bin/env python3
# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/pw_strategy.py
"""Compile committed strategy Markdown into a locally verified, immutable BASIC build."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pw_cli
import strategy_build as builds

DEFAULT_SOURCE = builds.LAB / 'strategy/STRATEGY.md'


def parser():
    root = pw_cli.ArgumentParser('pw_strategy', __doc__, examples=[
        'pw.py strategy lint games/gota/compiler/strategy/STRATEGY.md --json',
        'pw.py strategy compile --agent claude --milestone m1 --json',
        'pw.py strategy trace <build-id> --json'])
    commands = root.add_subparsers(dest='action', required=True)
    lint = commands.add_parser('lint', help='validate source format and references')
    lint.add_argument('source', type=Path, nargs='?', default=DEFAULT_SOURCE)
    for action in ('prepare', 'compile'):
        child = commands.add_parser(action, help='prepare a work order' if action == 'prepare' else 'run the full compiler')
        child.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
        child.add_argument('--agent', choices=['claude', 'codex'])
        child.add_argument('--model')
        child.add_argument('--full', action='store_true')
        child.add_argument('--milestone', choices=['m0', 'm1'])
        child.add_argument('--from', dest='previous', help='explicit previous passed build ID')
        if action == 'compile':
            child.add_argument('--agent-timeout', type=int, default=900)
    for action in ('assemble', 'verify', 'trace'):
        child = commands.add_parser(action)
        child.add_argument('build', help='staged build ID (assemble/verify), finalized ID (trace)')
    audit = commands.add_parser('audit', help='join strategy beliefs and decisions to verified replay evidence')
    audit.add_argument('roots', nargs='+')
    audit.add_argument('--build', required=True)
    audit.add_argument('--check', help='component ID or COMPONENT.N (one-based check index)')
    audit.add_argument('--level', choices=['True', 'Believed', 'Acted', 'Acted properly', 'Result'])
    audit.add_argument('--out', type=Path)
    audit.add_argument('--refresh', action='store_true')
    return root


def run(args, report):
    from strategy_format import parse_strategy, lint_strategy
    from gota_gates import verify
    from strategy_basic import BuildError
    if args.action == 'audit':
        from strategy_audit import run as audit
        return audit(args, report)
    if args.action == 'lint':
        if not args.source.is_file():
            raise pw_cli.UsageError(f'strategy does not exist: {args.source}')
        strategy = parse_strategy(args.source.resolve(), backend='gota')
        diagnostics = [d.to_dict() for d in lint_strategy(strategy)]
        for diagnostic in diagnostics:
            if diagnostic['level'] == 'error':
                report.fail(diagnostic.get('component', 'source'), 'lint_error', diagnostic['message'])
        report.counts['processed'] = len(strategy.components)
        return {'diagnostics': diagnostics, 'components': list(strategy.components)}
    if args.action == 'trace':
        path = builds.build_path(args.build)
        version = builds.read(path / 'version.json')
        source = builds.REPO / version['source_path']
        current = builds.component_map(parse_strategy(source, backend='gota'))
        previous = builds.read(path / 'map.json')['components']
        statuses = builds.changes(current, previous)
        return {'build_id': args.build, 'source_commit': version['source_commit'], 'units': statuses}
    if args.action in ('prepare', 'compile'):
        if not args.source.is_file():
            raise pw_cli.UsageError(f'strategy does not exist: {args.source}')
        if args.action == 'compile' and args.agent_timeout < 1:
            raise pw_cli.UsageError('--agent-timeout must be positive')
        order = builds.prepare(args.source.resolve(), agent=args.agent, model=args.model,
                               full=args.full, milestone=args.milestone, previous=args.previous)
        stage = builds.build_path(order['build_id'], staged=True)
        if args.action == 'prepare':
            report.output(stage / 'work_order.json')
            report.suggest(f'run the compiler agent for {order["build_id"]}, then strategy assemble {order["build_id"]}')
            return order
        gates, errors, failure = {}, [], None
        for attempt in range(1, 4):
            try:
                print(f'{order["build_id"]}: generation/gates round {attempt}/3', flush=True)
                builds.generate(order, errors=errors, timeout=args.agent_timeout)
                builds.assemble_build(order['build_id'])
                gates = verify(order['build_id'])
                errors = [{'gate': name, **gate} for name, gate in gates.items() if not gate['passed']]
                if not errors:
                    break
                # A bad screen is never sent to an LLM as a gameplay-improvement request.
                if not gates['G4']['passed'] and gates['G2']['passed']:
                    break
                if not any(not gates[name]['passed'] for name in ('G2', 'G3', 'G5')):
                    break
            except (ValueError, RuntimeError, BuildError) as error:
                failure = str(error)
                errors = [{'stage': 'generate/assemble', 'message': failure}]
                if isinstance(error, BuildError):
                    errors = [d.to_dict() for d in error.diagnostics]
                    for diagnostic in error.diagnostics:
                        key = diagnostic.component
                        if key in order['components'] and order['components'][key]['llm']:
                            order['units'][key] = 'changed'
                    builds.dump(stage / 'work_order.json', order)
                # Scope violations are not repairable compiler mistakes.
                if 'write-scope violation' in failure:
                    break
                if attempt < 3:
                    failure = None
        if errors and not failure:
            failure = f'build failed after round {attempt}: {errors}'
        target = builds.finalize(order, gates, error=failure)
        report.output(target)
        result = builds.read(target / 'report.json')
        if result['status'] != 'passed':
            report.fail(order['build_id'], 'build_failed', failure or 'one or more local gates failed')
        report.counts['processed'] = len(order['components'])
        return {'build': str(target), **result}
    stage = builds.build_path(args.build, staged=True)
    if args.action == 'assemble':
        result = builds.assemble_build(args.build)
        report.output(stage / 'policy.bas')
        report.output(stage / 'map.json')
        return {'build_id': args.build, 'budget': result['budget']}
    order = builds.read(stage / 'work_order.json')
    gates = verify(args.build)
    target = builds.finalize(order, gates)
    report.output(target)
    if not all(g['passed'] for g in gates.values()):
        report.fail(args.build, 'gates_failed', 'one or more local gates failed')
    return {'build': str(target), 'gates': gates}


def main(argv=None):
    return pw_cli.run(parser(), run, argv)


if __name__ == '__main__':
    raise SystemExit(main())
