# GotA toolkit drift: Task 1.2 adaptation

Checked on 2026-10-07 with coworld 0.1.57 and softmax-cli 0.26.38.
The toolkit was copied from `personal_labs@07dc6f94760fae3cf2de36877e0b6186cfd1dc63`
and `co-gas@6742feb` using committed files only.

## Verified live game

The league `league_3c60897b-25cf-4b37-9d1a-8554c1198f28` resolves to
`cow_162aa34f-f447-4c5d-8db3-480ec83e98f4`, version `2026.10.6.1`.
The downloaded manifest cites Polyworld
`5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd`.
The readback and download used Games Bond in a temporary HOME copied from only the
user token. The exact player subject was checked. The temporary HOME was removed;
the original credential file bytes were unchanged.

## Failed acceptance check

Run from `autoresearch/`:

```sh
sh games/gota/tools/build_nim.sh 5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd
```

The initial Nim build reported:

```text
lab_expand_replay.nim(195, 50) Error: undeclared identifier: 'ActionManualSpells'
```

The copied expander uses `ActionManualSpells` to bound the spell-action range in
its inventory/action reconstruction. The live source does not define this name.
This was a source API incompatibility, not a missing Nim installation. The exact
live build could not compile the unadapted pinned expander. No successful episode
or probe parse was claimed at that initial stop. The initial phase brief required stopping. VERDICT-B authorized adapting the
replay reader to the verified live contract; the resolution below records proof.

## Partial checks and required dependency

Five `--help` checks first failed because the tools were absent. After copying
and adapting local import/build-script paths, the provisional command passed:

```sh
uv run --locked --python 3.12 --with scipy pytest tests/unit/gota/test_tools.py -q
# 5 passed in 12.08s
```

`compare.py` imports the pinned `ab_stats.py`, which imports `scipy.stats`.
SciPy is missing from the project dependency manifest. The normal locked test
therefore remains incomplete. `features.py` is an import-only adapter, not a CLI;
its unchanged module exits zero when invoked with `--help` but emits no help text.
`gota_episodes.py`, `ab_stats.py`, and `variance_miner.py` were copied as required
local helper dependencies, each with a source header and manifest entry.

## Live limits differ from plan and untracked port notes

Polyworld `examples/gods_of_the_arena/bots.nim:114-138`, at the exact live commit,
sets globals 256, arrays 32, array cells 4096, source bytes 65536, routines 64,
PRINT bytes 1024, and PRINT events 128. Static code instructions are 20000;
per-decision instructions are 100000 and work units are 250000. The plan's
20000/50000 per-decision values are historical. The untracked port's
512/128/262144 static limits apply only to policies with the first-line
`@gota-structures` marker (`structures.nim:161-175`, selected by
`bots.nim:1074-1081`). The immutable legacy v13 uses 256/32/4096.

See `gota-partial-compiler-assessment.md` for the separate parent assessment.


## Resolution under VERDICT-B

The action reader now uses `action.slot`. Reward and inventory extraction uses
live engine events; no historical coefficients or inferred inventory effects
remain in the replay expander. Teleport scrolls are consumed at channel start. Scores are verified with the live Glory
function. Both the 28,877-tick hosted probe and a 28,909-tick local competition with
ten unchanged official starter policies parse with every hash, reward total, and
results XP/Glory verified. Independent raw cast-request counts match all heroes;
hero 100 has 98 recorded requests in both readers. Requests include rejected
attempts and are not successful-release or damage counts.

The project now locks SciPy. Eight toolkit tests pass using the locked environment;
all five Nim helpers compile against the live source. The 1,500-tick replay fixture
preserves the original prefix hashes and tests recorded statistics, cast slots,
independent raw request counts, and rejection of changed results.

## Separate submitted-v13 loader blocker

An initial local competition attempt with ten byte-identical submitted-v13 files
failed before gameplay. The live runner reported `Policy loading failed for player
slot 0`; the policy log states:

```text
BASIC error: line 578, column 3: name 'is' is not a scalar variable
```

The downloaded fixture remains unchanged. This does not block Task 1.2's official
starter episode, but it prevents claiming Task 1.4's v13 gate acceptance until the
baseline compatibility ruling is supplied. See the separate parent assessment.
