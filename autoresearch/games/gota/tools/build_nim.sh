#!/bin/sh
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/build_expand_replay.sh
# Build the Gods of the Arena replay expander against a polyworld commit.
#
# Usage: build_nim.sh POLYWORLD_COMMIT [TARGET]
# The recording Polyworld commit is required. Replays only re-simulate at that commit.
#
# Output: games/gota/tools/bin/<target>-<sha> (gitignored).
# Needs: nim 2.x on PATH, git, network on first use. The polyworld clone and its
# dependency cache live under games/gota/tools/.cache/.
set -eu
LAB=$(cd "$(dirname "$0")/.." && pwd)
TOOLS="$LAB/tools"
CACHE="$TOOLS/.cache"
COMMIT=${1:?pass the exact recording Polyworld commit}
TARGET=${2:-expand_replay}
case "$TARGET" in
  expand_replay|raw_spell_counts|gota_verify|gota_audit|gota_policy_replay) ;;
  *) echo "unsupported Nim tool: $TARGET" >&2; exit 2 ;;
esac
SHORT=$(printf '%s' "$COMMIT" | cut -c1-8)
OUT="$TOOLS/bin/$TARGET-$SHORT"
if [ -x "$OUT" ] && [ "$OUT" -nt "$TOOLS/$TARGET.nim" ] && [ "$OUT" -nt "$0" ]; then
  echo "$OUT"
  exit 0
fi
mkdir -p "$CACHE" "$TOOLS/bin"
CLONE="$CACHE/polyworld"
if [ ! -d "$CLONE/.git" ]; then
  git clone -q https://github.com/Metta-AI/polyworld.git "$CLONE"
fi
git -C "$CLONE" fetch -q origin "$COMMIT" 2>/dev/null || git -C "$CLONE" fetch -q origin
git -C "$CLONE" checkout -q "$COMMIT"
# polyworld's own dependency sync: pins every Nim package the sim needs.
(cd "$CLONE" && nim r --hints:off coworld/tools/sync_dependencies.nim >/dev/null)
export POLYWORLD_DEPS="$CLONE/tmp/coworld/deps"
# Compile from inside the clone so its config.nims applies (dependency paths),
# with the game modules and our source on the path.
cp "$TOOLS/$TARGET.nim" "$CLONE/examples/gods_of_the_arena/tools/lab_$TARGET.nim"
(cd "$CLONE" && nim c --hints:off --path:. -d:release -d:replayEvents --nimcache:"$CACHE/nimcache-$TARGET-$SHORT" \
  -o:"$OUT" examples/gods_of_the_arena/tools/lab_$TARGET.nim)
echo "$OUT"
