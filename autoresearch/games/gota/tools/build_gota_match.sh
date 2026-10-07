#!/bin/sh
# Source: personal_labs_gota@09d3ed28:gods_of_the_arena_lab/tools/build_gota_match.sh
# Build at engine.env's pin without changing the shared clone's checkout.
set -eu
export GIT_NO_LAZY_FETCH=1
TOOLS=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$TOOLS/engine.env"
CLONE="$TOOLS/.cache/polyworld"
SHORT=$(printf '%s' "$POLYWORLD_COMMIT" | cut -c1-8)
OUT="$TOOLS/bin/gota_match-$SHORT"
# The same implementation validates the binary before every Python invocation.
fingerprint() {
  uv run --locked --offline --project "$TOOLS/../../.." python - "$TOOLS" <<'PYCODE'
import sys
sys.path.insert(0, sys.argv[1])
from gota_local import build_fingerprint
print(build_fingerprint())
PYCODE
}
FINGERPRINT=$(fingerprint)
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$POLYWORLD_COMMIT" ] || {
  echo "polyworld must already be at $POLYWORLD_COMMIT" >&2; exit 1;
}
export POLYWORLD_DEPS="$CLONE/tmp/coworld/deps"
if [ -x "$OUT" ] && [ -f "$OUT.fingerprint" ] &&
   [ "$(cat "$OUT.fingerprint")" = "$FINGERPRINT" ] && [ "$OUT" -nt "$TOOLS/gota_match.nim" ] &&
   [ "$OUT" -nt "$TOOLS/engine.env" ] && [ "$OUT" -nt "$TOOLS/build_gota_match.sh" ] &&
   [ "$OUT" -nt "$CLONE/config.nims" ] &&
   [ "$OUT" -nt "$CLONE/coworld/dependencies.lock" ] &&
   [ "$OUT" -nt "$CLONE/coworld/gota/coworld_manifest_template.json" ] &&
   [ -d "$POLYWORLD_DEPS/bassy/.git" ] &&
   [ "$(git -C "$POLYWORLD_DEPS/bassy" rev-parse HEAD)" = "$BASSY_COMMIT" ] &&
   [ -z "$(find "$CLONE/src" "$CLONE/examples/gods_of_the_arena" "$POLYWORLD_DEPS" -type f \( -name '*.nim' -o -name '*.nims' -o -name '*.bas' \) -newer "$OUT" -print -quit)" ]; then
  echo "$OUT"
  exit 0
fi
# Dependencies must already be populated. Never fetch or synchronize implicitly.
while read -r name version repository commit; do
  [ -n "$name" ] || continue
  [ "$(git -C "$POLYWORLD_DEPS/$name" rev-parse HEAD)" = "$commit" ] || {
    echo "offline dependency pin mismatch: $name" >&2; exit 1;
  }
  git -C "$POLYWORLD_DEPS/$name" diff --quiet
  git -C "$POLYWORLD_DEPS/$name" diff --cached --quiet
done < "$CLONE/coworld/dependencies.lock"
git -C "$CLONE" diff --quiet
git -C "$CLONE" diff --cached --quiet
[ "$(git -C "$POLYWORLD_DEPS/bassy" rev-parse HEAD)" = "$BASSY_COMMIT" ] || {
  echo 'Bassy pin does not match engine.env' >&2; exit 1;
}
mkdir -p "$TOOLS/bin"
# Do not leave a trusted receipt if compilation fails or inputs change mid-build.
rm -f "$OUT.fingerprint"
TEMP_OUT="$OUT.tmp.$$"
TEMP_FINGERPRINT="$OUT.fingerprint.tmp.$$"
trap 'rm -f "$TEMP_OUT" "$TEMP_FINGERPRINT"' EXIT HUP INT TERM
cp "$TOOLS/gota_match.nim" "$CLONE/examples/gods_of_the_arena/tools/lab_gota_match.nim"
(cd "$CLONE" && nim c --hints:off -d:release -d:replayEvents \
  -d:PolyworldCommit="$POLYWORLD_COMMIT" -d:BassyCommit="$BASSY_COMMIT" \
  --nimcache:"$TOOLS/.cache/nimcache-gota-match-$SHORT" \
  -o:"$TEMP_OUT" examples/gods_of_the_arena/tools/lab_gota_match.nim >&2)
[ "$(fingerprint)" = "$FINGERPRINT" ] || {
  echo 'build inputs changed during compilation; rebuild the harness' >&2; exit 1;
}
printf '%s\n' "$FINGERPRINT" > "$TEMP_FINGERPRINT"
mv "$TEMP_OUT" "$OUT"
mv "$TEMP_FINGERPRINT" "$OUT.fingerprint"
echo "$OUT"
