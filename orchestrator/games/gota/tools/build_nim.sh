#!/bin/sh
# Source: Metta-AI/personal_labs@07dc6f94760fae3cf2de36877e0b6186cfd1dc63:gods_of_the_arena_lab/tools/build_expand_replay.sh
# Local adaptation: shared authoritative offline native receipt; never fetch.
set -eu
TOOLS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMMIT=${1:?pass the exact recording Polyworld commit}
TARGET=${2:-expand_replay}
# native_build.py needs only the standard library. Run it with the profile's python3 (first
# on PATH in a profile shell), not `uv run --project`, which would make a venv inside the
# installed profile: read-only on hosted workspaces.
exec python3 "$TOOLS/native_build.py" "$TARGET" "$COMMIT"
