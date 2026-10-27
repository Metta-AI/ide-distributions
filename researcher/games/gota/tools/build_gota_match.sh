#!/bin/sh
# Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/build_gota_match.sh
# Local adaptation: shared authoritative offline native receipt.
set -eu
TOOLS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$TOOLS/engine.env"
# native_build.py needs only the standard library. Run it with the profile's python3 (first
# on PATH in a profile shell), not `uv run --project`, which would make a venv inside the
# installed profile: read-only on hosted workspaces.
exec python3 "$TOOLS/native_build.py" gota_match "$POLYWORLD_COMMIT"
