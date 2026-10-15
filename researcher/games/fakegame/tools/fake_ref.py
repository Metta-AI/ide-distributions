#!/usr/bin/env python3
"""Fake resolver for the ``replay`` reference type: ``ar://replay/<seed>:<tick>``. Stdlib only.

Called as ``fake_ref.py <id>`` with ``AUTORESEARCH_WORKSPACE`` and ``AUTORESEARCH_REV`` set
(see ``ide_research_core.comms.resolver``). Prints one deterministic tick summary as JSON; exits 1
for an id that is not ``<seed>:<tick>``.
"""

import json
import os
import sys


def main(argv: list[str]) -> int:
    seed, separator, tick = argv[-1].partition(":") if argv else ("", "", "")
    if not separator or not seed.isdigit() or not tick.isdigit():
        print(f"not a replay segment id: {argv[-1] if argv else ''!r}", file=sys.stderr)
        return 1
    segment = {
        "seed": int(seed),
        "tick": int(tick),
        "rev": os.environ.get("AUTORESEARCH_REV"),
        "events": [f"tick {tick} of seed {seed}"],
    }
    print(json.dumps(segment, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
