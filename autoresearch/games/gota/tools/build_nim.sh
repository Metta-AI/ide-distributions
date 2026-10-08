#!/bin/sh
# Source: Metta-AI/personal_labs@07dc6f94760fae3cf2de36877e0b6186cfd1dc63:gods_of_the_arena_lab/tools/build_expand_replay.sh
# Local adaptation: shared authoritative offline native receipt; never fetch.
set -eu
TOOLS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMMIT=${1:?pass the exact recording Polyworld commit}
TARGET=${2:-expand_replay}
exec uv run --locked --offline --project "$TOOLS/../../.." python "$TOOLS/native_build.py" "$TARGET" "$COMMIT"
