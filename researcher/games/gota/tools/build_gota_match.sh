#!/bin/sh
# Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/build_gota_match.sh
# Local adaptation: shared authoritative offline native receipt.
set -eu
TOOLS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$TOOLS/engine.env"
exec uv run --locked --offline --project "$TOOLS/../../.." python "$TOOLS/native_build.py" gota_match "$POLYWORLD_COMMIT"
