"""Create the authorized mechanical live compatibility copy of submitted v13."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

TOKENS = re.compile(r'"[^"\n]*"|\x27[^\n]*|\bREM\b[^\n]*|[A-Za-z_][A-Za-z0-9_]*|\s+|.', re.IGNORECASE)
RENAMES = {"is": "v13_item_slot"}


def make_live(source: str) -> str:
    identifiers = {m.group().lower() for m in TOKENS.finditer(source)
                   if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', m.group())}
    if identifiers & set(RENAMES.values()):
        raise ValueError("Compatibility identifier collides with original source")
    return ''.join(RENAMES.get(m.group().lower(), m.group())
                   if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', m.group()) else ('\\' if m.group() == '/' else m.group())
                   for m in TOKENS.finditer(source))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_bytes(make_live(args.original.read_bytes().decode()).encode())


if __name__ == '__main__':
    main()
