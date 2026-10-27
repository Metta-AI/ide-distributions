"""Create the authorized mechanical live compatibility copy of submitted v13."""
from __future__ import annotations

import argparse
import json
import re
import sys
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
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--offline', action='store_true', help='explicit historical engine; no live lookup')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from games.gota.tools.engine_identity import resolve
    from games.gota.tools.prebuilt import ENGINE_FIELDS, committed_files, digest, dump
    original, output = args.original.resolve(), args.output.resolve()
    sidecar = output.with_suffix('.provenance.json')
    if output.exists() or sidecar.exists():
        raise ValueError('output artifact and provenance must not exist')
    committed_files(output.parent, [original])
    identity = resolve(args.manifest, offline=args.offline)
    # Resolution only: native preparation and all execution belong to build.
    output.write_bytes(make_live(original.read_bytes().decode()).encode())
    provenance = {'schema': 'gota-prebuilt/1', 'artifact_sha256': digest(output),
                  'input': {'path': original.relative_to(output.parent).as_posix(),
                            'sha256': digest(original)},
                  'transform': {'name': 'make_v13_live.py', 'sha256': digest(Path(__file__))},
                  'engine': {key: identity[key] for key in ENGINE_FIELDS}}
    dump(sidecar, provenance)
    print(json.dumps({'artifact': str(output), 'provenance': str(sidecar),
                      'artifact_sha256': provenance['artifact_sha256']}))


if __name__ == '__main__':
    main()
