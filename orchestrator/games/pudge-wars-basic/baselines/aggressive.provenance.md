# aggressive.bas provenance

Byte-identical copy of a starter player file shipped in the Pudge Wars Basic Coworld package.
The file carries no header of its own because adding one would change its identity.

| Field | Value |
| --- | --- |
| Coworld | `pudge-wars-basic` 0.1.8, `cow_c23845f0-444f-4d2f-864f-5764c58eda4a` |
| Manifest player id | `aggressive` ("Aggressive": Editable aggressive BASIC policy.) |
| Manifest file | `player-files/58225fd7bf422fbc618d70247120ebc68c097d57d07823316e43b131aea20b86` |
| sha256 | `58225fd7bf422fbc618d70247120ebc68c097d57d07823316e43b131aea20b86` |
| Size | 11717 bytes |
| source_url | https://github.com/Metta-AI/polyworld-pudge-wars/tree/cd8111a5ee4f2fdc38c027615b33421f6c7d2536/coworld/players |
| Same bytes in the game repo | `coworld/players/aggressive.bas` at `cd8111a5ee4f2fdc38c027615b33421f6c7d2536` (checked with sha256 on 2026-10-26) |
| Fetched | 2026-10-26 (PDT), from the downloaded Coworld package |

Bundled aggressive BASIC personality; the default G4 / local opponent alongside base.

`tools/match.py`, `tools/local_check.py --baseline` and `tools/branch.py --bot` accept the
starter name `aggressive` for this file; `tools/build.py --starter aggressive` builds it.
