#!/usr/bin/env python3
"""Fail closed for game bindings whose authoritative backend is not implemented."""
import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binding', choices=('build', 'upload'))
    args, _ = parser.parse_known_args()
    gap = ('PW compiler build backend is deferred to Phase C Task 2.5; '
           'implement games/gota/tools/build.py before building candidates.'
           if args.binding == 'build' else
           'Guarded upload backend is deferred to Plan 03 Tasks 3.3/3.4; '
           'use the shared softmax tool when implemented, with C6 identity checks '
           'on the exact credential immediately before mutation. Direct upload is unavailable.')
    print(f'gota {args.binding}: {gap}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
