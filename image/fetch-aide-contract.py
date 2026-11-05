"""Materialize the pinned canonical contract during image builds and local test setup."""

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "contract/aide/1.0.0"
pin = json.loads((root / "contract-source.json").read_text())
document = subprocess.run(
    ["curl", "--fail", "--silent", "--show-error", "--location", "--max-time", "60", pin["url"]],
    check=True,
    capture_output=True,
).stdout
actual = hashlib.sha256(document).hexdigest()
if actual != pin["sha256"]:
    raise ValueError(f"Canonical contract SHA-256 mismatch: expected {pin['sha256']}, got {actual}")
if json.loads(document)["info"]["version"] != pin["version"]:
    raise ValueError("Canonical contract version does not match the source pin")
(root / "asyncapi.json").write_bytes(document)
print(f"Bundled canonical AIDE {pin['version']} contract: sha256:{actual}")
