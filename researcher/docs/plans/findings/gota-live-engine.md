# GotA live engine following (2026-10-08)

Online build and direct gate commands resolve the shared `softmax game-version`
envelope, then use public `PlatformCli` league/show reads to obtain the canonical
Coworld row. Versions must agree. The runnable URL must carry a complete Polyworld
SHA under the supported GotA path. The fetched manifest is copied into immutable
build output or gate evidence; optional supplied manifests must hash identically.
Only explicit `--offline` selects the historical engine.env pin, and requires its
manifest. Authentication, lookup and format failures do not select old sources.

The binding passes an explicit identity to compiler prepare and GateContext.
Engine changes invalidate component reuse. Native prepare/build/validate retain
source, lockfile, full Bassy, toolchain and binary receipts. The compiler records
Coworld version/id, source URL, manifest hash, full commits, receipt fingerprints
and supported host contract hash. Verification rechecks manifest and native
fingerprints before gates. Existing source/hash/clean-input guards remain active.

The host contract digest comes from the historical 801749e5 native `host` output:
host scalars, functions, records, limits and injected structured storage. The new
engine's native host output matches exactly. A changed contract is unsupported
until the compiler/gate assumptions are reviewed; native runtime success is not
semantic qualification.

Live isolated Games Bond lookup returned canonical
`cow_3e536e3c-3f1c-4029-b006-b3d3ef3393b3`, version `2026.10.8.1`, full engine
`02eda3d34d0e7cc3ae1986d2ab57482a04cbe08a`, Bassy
`a02e6dd5889310b13d21725c891bde8d01539f08`. Original credentials were hash checked
unchanged and the temporary copy was removed. Native gate_probe and gota_match
built and validated; legacy baseline compilation passed and the prior structured
artifact compiled and ran one complete 31200-tick native seed-1 mirror match
with no VM errors. All ten seats made 28820 decisions; total 288200. Max
instructions/work were 25530/40465, PRINT events/bytes 28/148. This is single-match
VM/battle compatibility evidence, not new-engine G2/G3 or M1 qualification.

The prior paid M1 receipt remains 2026.10.7.2 / 801749e5. Historical equivalence and
immutable goal-audit checks were deliberately preserved; no new paid compilation,
upload, experience request, EC2 deployment or competitive qualification occurred.

CLI freshness: sanctioned `uv pip install --dry-run --upgrade coworld softmax-cli`
found coworld 0.1.58 and unchanged softmax-cli 0.26.38. Shared pyproject/lock update
is outside this patch's ownership and has been requested. The live readback used
locked coworld 0.1.57; current locked-release verification remains pending. G2 now
uses the shared project lock directly rather than overriding it with --with pins.

Commands and host/player prerequisites are in [GAME.md](../../../games/gota/GAME.md)
and [gate reference](../../../games/gota/gates/README.md).
