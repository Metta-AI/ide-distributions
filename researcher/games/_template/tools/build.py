#!/usr/bin/env python3
"""research-template-stub: the {{GAME}} `build` binding is not implemented yet.

Replace this file with the binding (see games/_template/CONTRACT.md in the researcher
distribution) and drop the `research-template-stub` marker. Until then `research game check`
reports the binding as a gap, and every call fails with `not_implemented`.
"""

import json
import sys

print(
    json.dumps(
        {
            "ok": False,
            "error": {
                "reason": "not_implemented",
                "message": "the {{GAME}} build binding is not implemented yet",
            },
        }
    )
)
sys.exit(1)
