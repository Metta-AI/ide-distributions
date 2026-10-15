# GotA globals budget and submitted baseline

Plan 02 Task 0.1, 2026-10-07. **Verdict: FITS WITH PACKING** for storage,
under the concrete Phase C assumptions below. The unchanged PW runtime **DOES NOT FIT**.
This is a storage estimate, not a compiled-policy or full G3 pass.

## Submitted artifact identity

The baseline is `tests/fixtures/gota/submitted-v13.bas`, downloaded from policy version
`40d72eea-ac61-44b0-aaf4-7828fbf30170` (`james-botts-gota:v13`), submission
`sub_38be3038-d8d1-4275-91da-557ee52ba109`.

- SHA-256: `d0fd3a8210bbf754e699cfd7022ea18d7d416d8d8f9861eb0f1f10ff40b978a1`.
- Size: **54,718 bytes**. Both match the API metadata exactly.
- Inventory: **251 scalar globals, 32 arrays, 3,464 array cells**.
- Limits: 256 globals, 32 arrays, 4,096 cells. The baseline leaves five globals,
  no array names, and 632 cells. The README's 252-global diagnostic v20 is not this artifact.

The lab's `WORKING_CONTEXT.md:196` and the v13 report sample manifest identify this
policy version. Route a inspected `policy/dist/`, policy-version references in report
JSON and Markdown, and the shared `.claude/skills/coworld-policy-lifecycle/scripts/policy_lifecycle.py`.
No original upload hash/bytes record was established; the current dist is diagnostic v20.
The lifecycle script assumes an existing upload and does not itself upload v13.
The verdict's lab-local `tools/policy_lifecycle.py` path does not exist.

Route b used an isolated **User** credential copy under VERDICT-A's authorization.
Read-only source in Metta identifies `GET /v2/policy-files/download?policy_ref=<version>`:
`packages/observatory-api/src/observatory_api/routes/app/v2/policy_files/router.py:14-44`
and `schemas.py:6-13`. The endpoint requires User/team-member access and returns
`content_hash`, `size_bytes`, and a private download URL. No URL is retained.

Observed commands and results in the temporary subprocess environment:

| Operation | Result |
| --- | --- |
| `fetch_cogames_whoami` with isolated user token | `subject_type user` |
| GET policy-files/download without elevation | HTTP 403; `User is not a softmax team member` |
| Same GET with `X-Use-Elevated-Privileges: true` | HTTP 200 |
| GET returned artifact URL | HTTP 200; exact hash and size verified |

The original credential file was compared byte-for-byte before/after: unchanged.
Only its `tokens` mapping was copied to a mode-0600 file. `HOME` was set only in
child subprocess environment dictionaries. Temporary credential directories were deleted.
No upload, submit, retire, credential switch, or API mutation was performed on this User route.
The earlier isolated Games Bond list omitted this James Botts submission (six entries,
`next_cursor: null`); VERDICT-A expressly authorized the User download route.

The raw BASIC fixture is the sole provenance-header exception approved by the parent:
an added header would invalidate byte identity. Its adjacent provenance record and
`games/gota/PROVENANCE.md` identify the immutable original bytes.

## Source matching

No byte-identical `policy/build.py` output was found in the three commits that contain
policy history. Each was extracted with `git archive` to a temporary directory,
then its own `policy/build.py --out <temporary-path>` ran without modifying the lab.
All committed BASIC snapshots under `policy/` in these commits were also checked; none matched.

| Commit | Rebuilt bytes | Rebuilt SHA-256 | Exact match |
| --- | ---: | --- | --- |
| `7a1d1bfc` | 57,834 | `e7eec9463074b9513fd0baf3b3c652ba01657b993f5f8c7b3d3c20f58487ce6c` | no |
| `ab737a16` | 59,293 | `244dac833ceb478820b6c6999c1c291c5396136fac3df3e9f631cc0c78c14577` | no |
| `7df778f7` | 59,293 | `244dac833ceb478820b6c6999c1c291c5396136fac3df3e9f631cc0c78c14577` | no |

`7a1d1bfc` is the closest chronological source candidate. Its reconstructed artifact
has different gameplay knobs and modules, including `cfgKsHeroChaseKu` and
`cfgKsStructPrepTicks`, so **gameplay-equivalent only, unverified** is the strongest
allowed local-source label. Do not use this candidate as the semantic truth of v13.
The exact downloaded policy is the baseline for future compiler equivalence testing.

## Counter scope and compiler evidence

`games/gota/tools/count_globals.py` provides `count_globals(source)` and
`inventory(source)`, plus a file CLI with `--inventory`. It counts distinct names,
normalizes case, handles comments, PRINT strings, colon statements, literal DIM
bounds, SUB parameters, and implicit variable reads/assignments inside and outside SUB.
Arrays occupy separate storage; inclusive bound 63 means 64 cells.

It adapts the token/comment handling from PW `tools/strategy_basic.py:91-130`
at `6b0bffa5085ffc4bf94ecaa8baee463e55599c84`. A dependency is unnecessary for
this small bounded lexical inventory; the existing PW scanner was researched first.
This is not a syntax validator or a general BASIC parser. Scalar DIM is accepted for
Task 0.1's requested fixture; the real GotA compiler requires DIM arrays. Invalid host
writes, invalid scalar/array syntax, labels, and unsupported language extensions are
not validated. Host-data exclusions are the historical GotA inputs, not a claim of
current live host compatibility.

Historical Polyworld source `f2ab9598d8f8001b6beae3e66404e341770c803f` was read via
GitHub: `examples/gods_of_the_arena/bots.nim` (the lab's September baseline engine pin)
and `src/polyworld/basic.nim`. `globalId` allocates non-parameter scalar names;
`parsePrimary` handles host data, functions, arrays, parameters, then implicit globals.
`declareArray` enforces the total 4,096-cell limit, with no smaller per-array limit.
The same host-data list appears at older documented source `7365e4e9390e97bbc8b7722b41a2ae88ae008c2b`.

A temporary Nim 2.2.10 compile-only harness used `basic.nim@f2ab9598`, registered
the historical host data and no-op host callbacks with arities from policy calls,
and compiled the exact policy with the storage limits. Output:

```text
historical compiler globals=251 arrays=32
661-cell array accepted
exit 0
```

Callbacks were not executed. This independently checks storage allocation, not gameplay
or the current live compiler. The first harness accidentally registered a keyword as
a function and failed; excluding keywords produced the result above.

## PW fixed runtime inventory at the required pin

Source: `personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84`.
Use committed bytes, not the checkout's existing documentation/viewer edits.

| Source | Distinct scalars | Arrays | Cells |
| --- | ---: | ---: | ---: |
| `strategy/compiler/runtime/lib.bas` | 46 | 13 | 609 |
| lib plus `runtime/main.bas` | 48 | 13 | 609 |
| above plus fixed `generate_tables` state | 49 | 13 | 609 |

Main adds only `rt__started`, `rt__last`. `generate_tables` at `tools/strategy_basic.py:730-897`
adds fixed `rt__valid`; its other fixed `rt__*` state is already shared with lib/main.
It also emits strategy-dependent outputs, params, condition codes, roles, capability
inputs/status, and situation/adaptation flags. Those require a separate reserve;
baseline config parameters and outputs count once when renamed, not twice.
COM runtime is excluded by the GotA contract. Goal labels add **zero globals or arrays**.

The 49 fixed scalar names (including telemetry state):

```text
rt__as_expired rt__best rt__bit rt__cap rt__changed rt__cond rt__ended
rt__held rt__i rt__in0 rt__in1 rt__in2 rt__interrupt rt__j rt__last
rt__margin rt__min_hold rt__n_adapt rt__n_rules rt__n_sits rt__n_words
rt__new rt__p_cap rt__p_done rt__p_held rt__p_in0 rt__p_in1 rt__p_in2
rt__p_pver rt__p_rule rt__p_t rt__pb rt__pe rt__pver rt__rc_b
rt__rc_old rt__rc_p rt__rc_r rt__rule rt__s rt__since rt__snap
rt__started rt__status rt__switch rt__valid rt__w rt__word telemetryoff
```

The baseline shares none of these names. Unpacked union is **300 globals, 45 arrays,
4,073 cells**, before additional strategy metadata. It fails globals and array counts.

## Concrete packing estimate for Task 2.3

Preserve all 251 baseline scalars and all 32 baseline arrays. Extend existing
`pvId(63)` from 64 cells to **661 cells**, keeping indices 0-63 untouched.
Use the tail for the runtime arrays and state, with literal offsets:

| Packed field | Cells | `pvId` indices |
| --- | ---: | --- |
| `rt__def` | 64 | 64-127 |
| `rt__prio` | 64 | 128-191 |
| `rt__rcap` | 64 | 192-255 |
| `rt__ok` | 64 | 256-319 |
| `rt__flag` | 125 | 320-444 |
| `rt__fw` | 4 | 445-448 |
| `rt__a_on` | 5 | 449-453 |
| `rt__a_until` | 5 | 454-458 |
| `rt__a_prev` | 5 | 459-463 |
| `rt__a_rule` | 5 | 464-468 |
| `rt__a_op` | 5 | 469-473 |
| `rt__a_amt` | 5 | 474-478 |
| `rt__a_dur` | 5 | 479-483 |
| Fixed runtime scalars, sorted names above | 49 | 484-532 |
| New component metadata/scratch reserve | 128 | 533-660 |

Rule and situation capacity stay 63/124. The seven adaptation arrays shrink from
32 cells each to five, preserving unused index zero and adaptations 1-4. This saves
189 cells: 609 - 189 = **420 runtime array cells**. All array accesses and runtime
scalar accesses become accesses to these distinct tail slots; no offset globals are needed.

Baseline `pvId` reads are bounded by `pvN` (`submitted-v13.bas:464-470`); its write
loop at 477-488 resets `pvN` and advances once per `efN`, and scan admits at most
64 enemy footmen (254-260). There are no whole-array operations. Keeping 0-63
preserves the baseline's data region; the new tail is disjoint.

Estimated decomposition assumptions, to validate in Phase C: at most eight situations,
eight capabilities, one role set, and four adaptations. Generated extras can use:
three commitment params + one role scalar + eight situation-on flags + forty
capability inputs/status/condition scalars (three inputs and two state values per
capability) + sixteen condition constants + four adaptation-fire flags = **72** cells.
The reserve leaves 56 for genuinely new params or scratch. Existing baseline config,
outputs and arrays must be reused/renamed, not duplicated. Four adaptations is an
estimate, not a claim that STRATEGY has already been authored; the baseline has no
PW adaptation state. Do not silently truncate extra adaptations.

Worst reserved storage projection:

```text
script globals = 251 (runtime and 128 new metadata scalars packed)
arrays = 32 (all runtime arrays flattened into existing pvId)
array cells = 3464 + 420 + 49 + 128 = 4061 <= 4096
cell slack = 35; scalar slack = 5; array-name slack = 0
```

FITS WITH PACKING is conditional on those assumptions. Task 2.3 must implement
this mapping or prove an equally bounded mapping, reject reserve/capacity overflow,
test disjointness and unchanged baseline accesses, and count the actual generated
artifact. Task 2.4 must prove action-stream equivalence against the downloaded artifact.
The new accesses may increase instruction/work cost; source size, routine limits,
PRINT and the full G3 budgets remain unproven. The baseline plus unmodified lib/main
is already 63,947 bytes before generated tables and markers (only 1,589 bytes below
64 KiB), so the port must measure source size too. G labels remain compile-time
metadata/PRINT literals, with no new runtime state.

## Validation and audit

Commands from the implementation worktree:

```sh
uv run --no-project --python 3.12 --with pytest --with pyyaml pytest autoresearch/tests/unit/gota -q
uv run --no-project --python 3.12 --with ruff ruff check autoresearch/games/gota/tools/count_globals.py autoresearch/tests/unit/gota/test_count_globals.py
python3 autoresearch/games/gota/tools/count_globals.py autoresearch/tests/fixtures/gota/submitted-v13.bas
```

TDD: first pytest failed collection with missing `count_globals.py` (exit 2).
After implementation five tests passed; the exact submitted artifact regression makes
six tests. Final pytest/ruff output is recorded in the implementation report.
Tests cover the requested 3 DIM scalars + 1 array + 2 top-level implicit globals,
SUB scratch versus local parameters, implicit reads, case/comments/strings/colon
statements, host inputs, inclusive cells, and immutable artifact hash/counts.

The audit-documentation skill was applied before the task commit. This finding,
PROVENANCE and the artifact sidecar cover the new command, allocation assumptions,
source identity, header exception and operational evidence. No other authoritative
documentation was changed. No Phase B or compiler implementation was performed.

Freshness: both source labs fetched/pruned; source working-tree changes preserved.
The implementation is on `jboggs/autoresearch-v0-gota`, base
`038998246a405c1bc62ccbede66a085e136ebbed`.
Unpinned project-independent uv resolution confirmed latest coworld `0.1.57` and
softmax-cli `0.26.38`; the probe still used explicit pinned releases.
The untracked GotA partial compiler port is a future Phase B/C inspection requirement,
not a source for this task's pinned PW runtime inventory. No source lab was edited.
