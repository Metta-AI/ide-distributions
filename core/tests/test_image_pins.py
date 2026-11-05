"""The Hermes image is pinned in three places, which must name one digest: a drift here means the
local stack or CI tests a Hermes that hosting does not run."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PINNED_IN = ("image/Dockerfile", ".github/workflows/ci.yml", "tools/local_stack.py")
HERMES_DIGEST = re.compile(r'hermes-agent"?\s*"?@sha256:([0-9a-f]{64})')


def test_every_pin_of_the_hermes_image_names_one_digest():
    digests = {path: HERMES_DIGEST.findall((REPO / path).read_text()) for path in PINNED_IN}
    assert all(digests.values()), digests
    assert len({digest for found in digests.values() for digest in found}) == 1, digests
