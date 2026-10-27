"""Where this game's tools write downloads, compiler caches and binaries.

A hosted workspace installs the profile read-only, so the tools cannot always write beside
themselves. ``build_root()`` is, in order:

1. ``$GAME_BUILD_ROOT/pudge-wars-basic`` when ``GAME_BUILD_ROOT`` is set;
2. this tools folder, when its ``.cache`` and ``bin`` can be written (a development checkout);
3. ``$HERMES_HOME/local/game-builds/pudge-wars-basic``: the profile's own state, which is
   writable and kept across restarts.

The build receipts record absolute paths, so a build is reused only from the folder that
made it; never link these folders elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path

GAME = 'pudge-wars-basic'
TOOLS = Path(__file__).resolve().parent
ROOT_ENV = 'GAME_BUILD_ROOT'


def writable(path: Path) -> bool:
    """Whether ``path`` can be written, or created inside its nearest existing parent."""
    while not path.exists():
        path = path.parent
    return os.access(path, os.W_OK)


def build_root(tools: Path = TOOLS) -> Path:
    if os.environ.get(ROOT_ENV):
        return Path(os.environ[ROOT_ENV]) / GAME
    if writable(tools / '.cache') and writable(tools / 'bin'):
        return tools
    if os.environ.get('HERMES_HOME'):
        return Path(os.environ['HERMES_HOME']) / 'local' / 'game-builds' / GAME
    raise OSError(f'{tools} is read-only and HERMES_HOME is not set; '
                  f'set {ROOT_ENV} to a writable folder for builds')
