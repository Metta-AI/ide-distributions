#!/usr/bin/env bash
# Protect startup and retained-volume recovery under the hosting filesystem restrictions.
set -euo pipefail
image=${1:?immutable or locally built image reference required}
name="distribution-smoke-${GITHUB_RUN_ID:-local}"
volume="$name"
scratch=$(mktemp -d)
cleanup() {
  docker logs "$name" > "$scratch/container.log" 2>&1 || true
  cat "$scratch/container.log"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT
printf '%s\n' '{"workspace_id":"00000000-0000-4000-8000-000000000149","platform_url":"http://127.0.0.1:1","credential":"fixture-no-platform-access"}' > "$scratch/bootstrap.json"
chmod 644 "$scratch/bootstrap.json"
docker volume create "$volume" >/dev/null
# Kubernetes supplies this volume group ownership through fsGroup.
docker run --rm --user 0 --entrypoint /bin/sh -v "$volume:/workspace" "$image" -c 'chown 10000:10000 /workspace'
sentinel="retained-$name"
for cycle in 1 2; do
  docker run -d --name "$name" --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,size=1g --mount "type=volume,src=$volume,dst=/workspace" \
    --mount "type=bind,src=$scratch/bootstrap.json,dst=/run/workspace/bootstrap.json,readonly" "$image"
  ready=false
  for attempt in $(seq 1 600); do
    if docker exec "$name" /opt/hermes/.venv/bin/python -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8650/health", timeout=2)' >/dev/null 2>&1; then
      ready=true
      break
    fi
    test "$(docker inspect --format '{{.State.Running}}' "$name")" = true
    sleep 2
  done
  test "$ready" = true
  docker exec "$name" /opt/hermes/.venv/bin/python /opt/distribution/image/system_tests/check-contract.py
  if [[ "$cycle" == 1 ]]; then
    docker exec "$name" sh -c 'printf %s "$1" > /workspace/.state-proof' sh "$sentinel"
  else
    test "$(docker exec "$name" cat /workspace/.state-proof)" = "$sentinel"
  fi
  docker exec "$name" /opt/hermes/.venv/bin/python -c \
    'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8650/status")); assert not s["sleep_started"] and not s["paused"], s'
  docker exec -e HERMES_HOME=/workspace/host/profiles/orchestrator "$name" \
    /opt/workspace-env/bin/research workspace sleep
  docker exec "$name" /opt/hermes/.venv/bin/python -c \
    'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8650/status")); assert s["sleep_started"] and s["paused"], s'
  docker stop --time 30 "$name" >/dev/null
  docker logs "$name"
  docker rm "$name" >/dev/null
done
