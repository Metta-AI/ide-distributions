#!/bin/sh
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/build_expand_replay.sh
# Build the Gods of the Arena replay expander against a polyworld commit.
#
# Usage: build_nim.sh POLYWORLD_COMMIT [TARGET]
# The recording Polyworld commit is required. Replays only re-simulate at that commit.
#
# Output: games/gota/tools/bin/<target>-<sha> (gitignored).
# Needs: Nim 2.x, Git, and a prepopulated pinned offline dependency cache. The polyworld clone and its
# dependency cache live under games/gota/tools/.cache/.
set -eu
export GIT_NO_LAZY_FETCH=1
LAB=$(cd "$(dirname "$0")/.." && pwd)
TOOLS="$LAB/tools"
CACHE="$TOOLS/.cache"
COMMIT=${1:?pass the exact recording Polyworld commit}
TARGET=${2:-expand_replay}
case "$TARGET" in
  expand_replay|raw_spell_counts|gota_verify|gota_audit|gota_policy_replay|gate_probe) ;;
  *) echo "unsupported Nim tool: $TARGET" >&2; exit 2 ;;
esac
SHORT=$(printf '%s' "$COMMIT" | cut -c1-8)
OUT="$TOOLS/bin/$TARGET-$SHORT"
if [ -x "$OUT" ] && [ "$OUT" -nt "$TOOLS/$TARGET.nim" ] &&
    [ "$OUT" -nt "$0" ] && [ "$OUT" -nt "$TOOLS/replay_outcome.nim" ]; then
  echo "$OUT"
  exit 0
fi
mkdir -p "$CACHE" "$TOOLS/bin"
CLONE="$CACHE/polyworld"
if [ "$(git -C "$CLONE" rev-parse HEAD)" != "$COMMIT" ]; then
  CLONE="$CACHE/polyworld-$SHORT"
fi
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$COMMIT" ] || {
  echo "offline source cache must already be at $COMMIT" >&2; exit 1;
}
while read -r name version repository pin; do
  [ -n "$name" ] || continue
  [ "$(git -C "$CLONE/tmp/coworld/deps/$name" rev-parse HEAD)" = "$pin" ] || {
    echo "offline dependency pin mismatch: $name" >&2; exit 1;
  }
  git -C "$CLONE/tmp/coworld/deps/$name" diff --quiet
  git -C "$CLONE/tmp/coworld/deps/$name" diff --cached --quiet
done < "$CLONE/coworld/dependencies.lock"
git -C "$CLONE" diff --quiet
git -C "$CLONE" diff --cached --quiet
export POLYWORLD_DEPS="$CLONE/tmp/coworld/deps"
# Compile from inside the clone so its config.nims applies (dependency paths),
# with the game modules and our source on the path.
cp "$TOOLS/replay_outcome.nim" "$CLONE/examples/gods_of_the_arena/tools/replay_outcome.nim"
cp "$TOOLS/$TARGET.nim" "$CLONE/examples/gods_of_the_arena/tools/lab_$TARGET.nim"
(cd "$CLONE" && nim c --hints:off --path:. -d:release -d:replayEvents --nimcache:"$CACHE/nimcache-$TARGET-$SHORT" \
  -o:"$OUT" examples/gods_of_the_arena/tools/lab_$TARGET.nim)
echo "$OUT"
