"""G1 static: the upload rules the league host enforces, checked without running anything.

Raw BASIC (src/polyworld/policies.nim:65-70, examples/pudge_wars/bots.nim:101-121 @cd8111a5):
non-empty UTF-8 text, at most 64 KiB (maxSourceBytes), and not ZIP bytes.
ZIP package (policies.nim:5-7, 22-63): at most 16 MiB and 256 entries; every entry name a
canonical relative path (no leading '/', no '\\', ':', NUL, empty, '.' or '..' parts);
exactly one `.bas` entry (case-insensitive), itself valid raw BASIC as above.
tensors.json (src/polyworld/tensors.nim:256-290, coworld/TENSORS.md): required when the
program calls tensorLoad; at most 64 KiB; {"tensors": [1..256 entries]} with unique names
(1..128 chars), dtype int32|fixed|float32, shape of 1..8 nonnegative dims with at most
4,194,304 elements, and resource/offset inside a package entry (4 bytes per element). The
host checks these lazily at the first tensorLoad, where a failure disables the seat's VM.
"""
from __future__ import annotations

import json
import math
import re
import zipfile
from pathlib import Path

from gates_common import GateContext, GateResult, PolicyFailure, gate_run, is_zip

MAX_SOURCE_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 256
MAX_TENSORS_JSON = 64 * 1024
MAX_TENSOR_ELEMENTS = 4_194_304
DTYPES = ('int32', 'fixed', 'float32')
TENSOR_LOAD = re.compile(r'\btensorLoad\s*\(', re.IGNORECASE)


def calls_tensor_load(source: str) -> bool:
    """A tensorLoad call outside whole-line comments (' or REM)."""
    code = [line for line in source.splitlines()
            if not line.lstrip().startswith("'") and not re.match(r'\s*rem\b', line, re.IGNORECASE)]
    return bool(TENSOR_LOAD.search('\n'.join(code)))


def check_source(data: bytes, name: str) -> str:
    if not data:
        raise PolicyFailure(f'{name} is empty')
    if len(data) > MAX_SOURCE_BYTES:
        raise PolicyFailure(f'{name} is {len(data)} bytes; the BASIC source limit is {MAX_SOURCE_BYTES}')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise PolicyFailure(f'{name} is not UTF-8: {error}') from None


def check_entry_name(name: str) -> None:
    if (not name or name.startswith('/') or any(c in name for c in '\\:\0')
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise PolicyFailure(f'ZIP entry name is not a canonical relative path: {name!r}')


def check_tensors(manifest_bytes: bytes, entries: dict[str, int]) -> int:
    if len(manifest_bytes) > MAX_TENSORS_JSON:
        raise PolicyFailure('tensors.json exceeds 64 KiB')
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError as error:
        raise PolicyFailure(f'tensors.json is not JSON: {error}') from None
    tensors = manifest.get('tensors') if isinstance(manifest, dict) else None
    if not isinstance(tensors, list) or not 1 <= len(tensors) <= 256:
        raise PolicyFailure('tensors.json needs {"tensors": [...]} with 1..256 entries')
    names = set()
    for index, entry in enumerate(tensors):
        where = f'tensors.json entry {index}'
        if not isinstance(entry, dict):
            raise PolicyFailure(f'{where} is not an object')
        name = entry.get('name')
        if not isinstance(name, str) or not 1 <= len(name) <= 128 or name in names:
            raise PolicyFailure(f'{where}: names must be unique, 1..128 characters')
        names.add(name)
        if entry.get('dtype') not in DTYPES:
            raise PolicyFailure(f'{where}: dtype must be one of {", ".join(DTYPES)}')
        shape = entry.get('shape')
        if (not isinstance(shape, list) or not 1 <= len(shape) <= 8
                or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 for d in shape)):
            raise PolicyFailure(f'{where}: shape must have 1..8 nonnegative integer dimensions')
        count = math.prod(shape)
        if count > MAX_TENSOR_ELEMENTS:
            raise PolicyFailure(f'{where}: {count} elements exceeds {MAX_TENSOR_ELEMENTS}')
        resource, offset = entry.get('resource'), entry.get('offset')
        if resource not in entries:
            raise PolicyFailure(f'{where}: resource {resource!r} is not in the package')
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset + 4 * count > entries[resource]:
            raise PolicyFailure(f'{where}: offset/size falls outside {resource}')
    return len(tensors)


def check_package(path: Path) -> dict:
    size = path.stat().st_size
    if size > MAX_PACKAGE_BYTES:
        raise PolicyFailure(f'package is {size} bytes; the limit is {MAX_PACKAGE_BYTES}')
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise PolicyFailure(f'cannot read policy ZIP: {error}') from None
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_PACKAGE_ENTRIES:
            raise PolicyFailure(f'package has {len(infos)} files; the limit is {MAX_PACKAGE_ENTRIES}')
        for info in infos:
            check_entry_name(info.filename)
        programs = [info.filename for info in infos if info.filename.lower().endswith('.bas')]
        if len(programs) != 1:
            raise PolicyFailure(f'package must contain exactly one .bas file; found {programs}')
        source = check_source(archive.read(programs[0]), programs[0])
        entries = {info.filename: info.file_size for info in infos}
        tensors = None
        if 'tensors.json' in entries:
            tensors = check_tensors(archive.read('tensors.json'), entries)
        elif calls_tensor_load(source):
            raise PolicyFailure('the program calls tensorLoad but the package has no tensors.json')
    return {'kind': 'zip', 'bytes': size, 'entries': len(infos), 'program': programs[0],
            'source_bytes': len(source.encode()), 'tensors': tensors}


@gate_run
def run(artifact: Path, context: GateContext) -> GateResult:
    if is_zip(artifact):
        return GateResult('passed', check_package(artifact))
    source = check_source(artifact.read_bytes(), artifact.name)
    if calls_tensor_load(source):
        raise PolicyFailure('tensorLoad needs a ZIP package with tensors.json; raw BASIC has no resources')
    return GateResult('passed', {'kind': 'basic', 'source_bytes': len(source.encode())})
