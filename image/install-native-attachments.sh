#!/usr/bin/env bash
# Shared by image packaging and pinned-runtime CI; no downloads or package installs at runtime.
set -euo pipefail
native_python=/opt/hermes/.venv/bin/python
constraints=$(mktemp)
trap 'rm -f "$constraints"' EXIT
uv pip list --python "$native_python" --format json |
  "$native_python" -c 'import json,sys;print("\n".join(p["name"]+"=="+p["version"] for p in json.load(sys.stdin)))' > "$constraints"
uv pip install --python "$native_python" --compile-bytecode --constraint "$constraints" \
  --requirement "$(dirname "$0")/native-requirements.txt"
"$native_python" - <<'PY'
from faster_whisper import WhisperModel
from huggingface_hub import snapshot_download

snapshot_download(
    "Systran/faster-whisper-base",
    revision="ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    local_dir="/opt/models/whisper-base",
    allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
)
WhisperModel("/opt/models/whisper-base", device="cpu", compute_type="int8", local_files_only=True)
PY
