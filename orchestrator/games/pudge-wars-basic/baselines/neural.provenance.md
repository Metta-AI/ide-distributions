# neural.zip provenance

Byte-identical copy of a starter player file shipped in the Pudge Wars Basic Coworld package.
The file carries no header of its own because adding one would change its identity.

| Field | Value |
| --- | --- |
| Coworld | `pudge-wars-basic` 0.1.8, `cow_c23845f0-444f-4d2f-864f-5764c58eda4a` |
| Manifest player id | `neural` ("Neural": Illustrative tensor MLP policy (hand-authored weights).) |
| Manifest file | `player-files/60533fa6caeb948378edcf4cee35d3bc2ffe4180f91fc35d21b38e3cc36891ad` |
| sha256 | `60533fa6caeb948378edcf4cee35d3bc2ffe4180f91fc35d21b38e3cc36891ad` |
| Size | 1101 bytes |
| source_url | https://github.com/Metta-AI/polyworld-pudge-wars/tree/cd8111a5ee4f2fdc38c027615b33421f6c7d2536/examples/pudge_wars/players |
| Same bytes in the game repo | `coworld/players/neural.zip` at `cd8111a5ee4f2fdc38c027615b33421f6c7d2536` (checked with sha256 on 2026-10-26) |
| Fetched | 2026-10-26 (PDT), from the downloaded Coworld package |

Illustrative neural ZIP (policy.bas + tensors.json + weights.bin, hand-authored weights; not a trained competitive policy). Example of the package format and tensor host functions.

`tools/match.py`, `tools/local_check.py --baseline` and `tools/branch.py --bot` accept the
starter name `neural` for this file; `tools/build.py --starter neural` builds it.
