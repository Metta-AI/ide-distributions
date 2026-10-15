#!/usr/bin/env python3
"""Resolve a local replay reference PATH#ticks=START:END (half-open interval)."""
import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference')
    args = parser.parse_args()
    match = re.fullmatch(r'(.+)#ticks=([0-9]+):([0-9]+)', args.reference)
    if not match:
        parser.error('expected local PATH#ticks=START:END with nonnegative integer ticks')
    path = Path(match[1])
    start, end = int(match[2]), int(match[3])
    if start >= end:
        parser.error('start tick must be less than end tick')
    if not path.is_file():
        parser.error(f'replay file does not exist: {path}')
    print(json.dumps({'replay': str(path.resolve()), 'start_tick': start, 'end_tick': end}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
