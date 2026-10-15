"""Scanning ``cards/requests`` for the knowledge base's own maintenance requests.

Requests the KB writes carry a marker in their ``why``, wrapped in square brackets (for example
``[hub:HUB_DRIFT:HUB-...]`` or ``[reverify <old>..<new>]``). Matching is exact on the bracketed
form, so one marker is never found inside another.
"""

import json
from collections.abc import Iterator

from autoresearch.paths import Layout


def iter_request_specs(layout: Layout) -> Iterator[dict]:
    """The ``spec`` of each create request in ``cards/requests``, in file-name order."""
    for path in sorted(layout.card_requests.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8")).get("spec")
        if spec:
            yield spec


def bracketed(marker: str) -> str:
    return f"[{marker}]"


def has_marker(spec: dict, marker: str) -> bool:
    return bracketed(marker) in spec.get("why", "")
