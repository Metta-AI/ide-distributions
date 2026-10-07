# GotA files: provenance

| File | Source | Identity |
| --- | --- | --- |
| `tools/count_globals.py` token/comment handling | `personal_labs_paintbot_pw/paintbot_pw_lab/tools/strategy_basic.py:91-130` | commit `6b0bffa5085ffc4bf94ecaa8baee463e55599c84`; adapted |
| `tools/count_globals.py` host-data names | `Metta-AI/polyworld/examples/gods_of_the_arena/bots.nim:45-64,195-201` | commit `7365e4e9390e97bbc8b7722b41a2ae88ae008c2b`; historical baseline host |
| `../../tests/fixtures/gota/submitted-v13.bas` | Softmax policy version `40d72eea-ac61-44b0-aaf4-7828fbf30170` | SHA-256 `d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1`; 54,718 bytes |

The submitted policy stays byte-identical. Its adjacent
`submitted-v13.provenance.md` replaces an in-file header under the Phase A parent's
explicit ruling, because a header would change the policy identity.
The remainder of the counter and its small acceptance fixture are new Task 0.1 code.
The counter is a lexical inventory for the historical GotA structured BASIC surface,
not the game compiler or a syntax-validation gate. Its host inputs are historically
pinned; a future host change requires updating the list and checking it against the deployed host.

Run from the repository root:

```sh
python3 autoresearch/games/gota/tools/count_globals.py autoresearch/tests/fixtures/gota/submitted-v13.bas
python3 autoresearch/games/gota/tools/count_globals.py autoresearch/tests/fixtures/gota/submitted-v13.bas --inventory
```

See `../../docs/plans/findings/gota-globals-budget.md` for submitted identity,
source-match limits, the pinned PW runtime inventory, and the packing requirement.
