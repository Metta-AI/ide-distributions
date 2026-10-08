# GotA compiler and M1 readiness

The existing Paintbot pipeline now targets GotA at Polyworld
`801749e509e888b423ba44ee2188946c22e3ba85`, version `2026.10.7.2`, with Bassy
`a02e6dd5889310b13d21725c891bde8d01539f08`. The primary GotA reference is
`personal_labs_gota@09d3ed28`; [PROVENANCE](../PROVENANCE.md) inventories adopted
files and retained contracts. The live refresh is recorded in
[the pin finding](../../../../docs/plans/findings/gota-live-refresh-2026-10-07.md).

## Source and storage

The default source is `games/gota/seed/strategy/STRATEGY.md`. Its components are
specified, not proven. `K.config` owns the 46 configuration outputs and executes
before `C.farm` every decision. `C.farm` owns the baseline's remaining shared
mutable globals, all 32 arrays, and helper routines. Its empty start routine and
absence of an init routine preserve baseline persistence. Runtime death handling
uses the rising edge of `selfRespawnTicks > 0`; it resets selection state only.

One always-selected rule carries `G.survive`, `G.farm`, and `G.finish`. These are
coarse declared labels. They do not distinguish the selected gameplay branch and
do not establish survival, farming quality, or finishing success. This ownership
split preserves cross-routine writes without duplicating mutable state. Finer
capabilities require a later state-ownership design.

Assembly prefixes `' @gota-structures` and retains namespaced state. The structured
limits are 512 globals, 128 arrays, 262,144 cells, 64 routines and 20,000 static
instructions. Host declarations consume 78 globals, 57 arrays and 153,920 cells,
including unused fields. The policy cap is 65,535 bytes; native compiled input,
including host structures, may use 69,191 bytes. Legacy v13 retains its unmodified
256-global, 32-array, 4,096-cell, 65,536-byte mode. Both use 80,000 instruction and
200,000 work soft gates per decision.

The earlier Task 2.3 pvId packing path is retired. `packing.json` is rejected;
there is no implicit binding or baseline-copy fallback. Its historical tests live
in `tests/unit/gota/compiler/legacy_packing.py`. Original generated unit texts
remain available separately from comment/whitespace-compacted `policy.bas`.

## Compiler and Hermes

From `autoresearch/`, with the locked dependencies installed:

```sh
uv run --locked --offline python games/gota/compiler/tools/pw_strategy.py lint --json
uv run --locked --offline python games/gota/compiler/tools/pw_strategy.py compile \
  --agent hermes --model z-ai/glm-5.3-flash --milestone m1 --full \
  --agent-timeout 3600 --manifest /path/to/coworld_manifest.json --json
```

The second command invokes a real model and requires separately authorized
credentials and credit. Task 2.4 qualified real Hermes/Kimi build
`00a0bdcc-2` locally; the flash attempts remained unqualified. See
[the M1 execution finding](../../../../docs/plans/findings/compiler-task-2.4-m1.md). No claim of real M1
completion follows from the local fixture results below. Hermes is the default
backend; explicit `--agent claude` and `--agent codex` remain supported. The default
model is configured in `strategy/compiler/config.json`; explicit `--model` wins,
followed by `OPENROUTER_MODEL` for Hermes. Use the explicitly selected dry-run model first,
then the approved `OPENROUTER_MODEL` value. Prepare checks the selected CLI version;
tests only use local fakes and never probe a real agent executable.

Hermes v0.21.5 dispatch uses `hermes chat --oneshot --format stream-json
--query-file prompt.txt -t file --max-turns 40 --model MODEL --provider openrouter`.
`chat` enforces the turn limit; its top-level `--usage-file` flag is parsed but
not forwarded to the chat runner. Top-level `-z` owns native usage-file writing
but does not forward a turn limit, so it cannot meet this compiler contract.

The driver retains each original stdout JSONL stream and derives a labeled
`hermes-chat-stream-json` receipt after validating exactly one initial `init`,
one terminal successful `result`, the requested model, a matching nonempty
session ID and nonnegative integer token counts. The receipt retains every
record and the raw stream SHA256. These are genuine cumulative main-session
input/output/total/cache counts from Hermes, not a native `--usage-file` ledger.
Cost, API-call, reasoning-token and auxiliary-call coverage are absent; reconcile
provider balances and conservative token-price estimates separately. Failed or
interrupted streams remain retained evidence and cannot qualify generation.

The process receives `TERMINAL_CWD` for its isolated scratch directory and the
selected `OPENROUTER_MODEL`. The scratch snapshot contains the work order, exact baseline,
and every component's byte-identical full specification in `context/specs/<ID>.md`.
The prompt directs the model to these ordinary multiline files because Hermes
file tools truncate the long escaped compiled_text lines in JSON. Specs stay
read-only under the snapshot guard; probe files remain prohibited. It also contains
compiler ABI/lessons, live host JSON and GotA/Bassy/structures context before
execution. The model may change only requested units and report_draft.json; the driver
writes the validated usage.json receipt afterward.
Symlink/missing outputs, unexpected writes, Git index/ref changes and nonzero exit
fail closed. Usage receipts are retained for successfully validated generation
attempts, including when later assembly or gates fail. Rejected or failed
generation attempts do not retain a receipt; their totals require provider usage
reconciliation. Retained receipts alone are not complete spend accounting. Unknown
backend names never fall through to another executable.

One compile invokes the agent 1–3 times, with up to 40 agent turns per invocation.
Only explicit `failed` G1–G3 policy gate results and expected structural assembly
`BuildError` diagnostics can request another generation. Unexpected gate,
generation, import or harness exceptions stop immediately with build status
`errored`. Reports retain their type, message and traceback summary; they never
enter the model's `repair_errors`. Later gates are omitted when not run.
G4/G5 failures are advisory warnings and never trigger model repair.
Provider requests/retries and tokens are not a hard spend cap. The 3600-second
operational allowance is informed by the primary lab's observed 3–25 minute
28-unit generations and 2–4 minute gates; this two-unit source has no promised
latency. Local fake fixtures do not predict model reliability or cost.

Inputs must be committed. Source hashes cover the strategy, reference baseline,
compiler context/host/runtime/config, all compiler and gate Python, GotA Python
and Nim helpers, both build scripts and engine.env. Finalization hashes immutable
artifacts. G1–G5 use the shared gate modules, with fresh evidence per repair round.
`--manifest` supplies the downloaded live manifest to G2; null config does not
skip the gate. G2 and `tools/lasthit_eval.py` use the project lockfile through
`uv run --project PROJECT --locked --offline --python 3.12`; they do not override
the locked Coworld or softmax-cli versions. Dependencies and images must already exist.
Pandas is now a locked project dependency; no pandas overlay is needed.

## Offline native tools

Native probe receipts carry the same `passed`/`failed`/`errored` outcomes.
Only known BASIC compile rejection and explicit VM failure/activity measurements
are policy failures. Unexpected native exceptions retain their type, message and
stack trace; incomplete or inconsistent receipts stop verification as tool errors.
Missing counters never become resource violations or model repair requests.

Build and receipt-validation helpers never fetch or install dependencies. The
explicit setup command fetches pinned Git sources (see [gate setup](../../gates/README.md)).
Establish the exact source/dependency caches first, retaining the real Polyworld config.nims and Nim
2.2.10. Both builders use `tools/native_build.py`. Compilation and cached acceptance check
every dependencies.lock Git pin, tracked cleanliness, tracked file content hashes,
additional source files, and the actual configuration chain reported by Nim. They
also check the canonical/copied helper bytes, Nim executable/version, options,
selected environment values,
and the output executable SHA-256. Receipt files use `.receipt.json`; the older
three-file `.fingerprint` sidecar is no longer authoritative:

```sh
uv run --locked python games/gota/tools/native_build.py prepare 801749e509e888b423ba44ee2188946c22e3ba85
sh games/gota/tools/build_nim.sh 801749e509e888b423ba44ee2188946c22e3ba85 gate_probe
sh games/gota/tools/build_gota_match.sh
```

The enumerated input set is compared before and after compilation; changes to it
cannot receive a trusted receipt. Gate and match reads validate the same receipt
contract,
including executable bytes, before accepting a cache. Gates retain their actual
native receipts in measurement files. Verification captures distinct gate-probe
and match receipts in `native_provenance.json`, checks the gate actually used that
identity, and retains both receipts in immutable finalization. These receipts do
not attest the external C compiler, Nim library/runtime source tree, or system/SDK.
They bind the exact executable used, without claiming reproducible-build
completeness. A check of the enumerated inputs measured about 1.4 seconds locally;
checks run before/after native invocations. Historical replay tools may use a
separately populated `.cache/polyworld-<sha8>` checkout and matching dependency
cache; the builder never changes a checkout or fetches missing objects. Historical replay
fixtures retain their original recording pin.

## Exact action oracle and honest goal audit

After a finalized compiler build, use its immutable policy artifact:

```sh
uv run --locked --offline python games/gota/tools/equivalence.py \
  --build BUILD-ID --out /path/to/new-oracle-dir
uv run --locked --offline python games/gota/tools/goal_audit.py --build BUILD-ID \
  --logs /path/to/new-oracle-dir/seed-1-candidate --out /path/to/goal-audit-seed-1.json
```

The audit command above covers seed 1 only; run it for each of the 20 candidate
directories to report all M1 decision evidence.

The oracle uses seeds 1–20, full Open Draft and 28,800 battle ticks. Candidate
seats 0–4 (hero IDs 100–104) face the exact baseline in seats 5–9; the paired
reference substitutes baseline on the candidate seats with identical setup.
The baseline SHA-256 must be
`1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c`.
No roster is forced. The actual engine determines decision order; command capture
records attempted commands, including rejected actions, with arguments and order.
All five candidate action streams must match at every tick. PRINT is excluded;
missing ticks, missing decisions, malformed data, VM failure or truncated
runs fail. Config seed/mode/budget must agree with the requested setup; phase
progression and observed draft/battle totals must agree with the completion
record. Timeout requires the full requested battle duration. Receipt files bind setup, binary fingerprint, engine/Bassy, opponent
hashes, loaded policy source hashes, logs and traces. Policies are snapshotted
before loading and native source SHA1 is checked against snapshot SHA1/SHA256.
This is trajectory identity against fixed opponents, not competitive qualification.

Oracle `--build` verifies the immutable build and requires its retained match
receipt; positional candidate paths are only the explicit fixture exploration
mode. The supported GotA audit compares the recorded executable hash and full
native receipt to that same immutable expected identity, then verifies build artifacts and reconstructs the
map from committed source using the existing load_build path. It binds the native
receipt's first five policies to that build, verifies evidence hashes and engine,
and attributes valid PWD labels within the 24-tick heartbeat to actual decisions.
Every True/Believed/Acted/Acted properly/Result predicate and goal claim remains
unmeasurable until a reviewed GotA evaluator exists. Analysis exit zero is not a
policy pass. `--map` supports exploration but always leaves coverage unmeasurable.
The vendored Paintbot semantic audit is not a GotA evidence entry point.

## Local evidence and remaining gate

Test-only mechanical units reproduce the reference statements and exercise fake
Hermes generation plus actual assembly. Full native strict compilation used 384
globals, 102 arrays, 157,993 cells, 54 routines and 5,056 instructions; policy size
54,452 bytes (58,108 with host). The complete fake seed-1 oracle matched all 31,200
ticks. Adding a rejected attackTarget(0) caused the oracle to detect its first
changed attempt at draft tick 241. These are harness/fixture proofs, not generated
M1 results. Real generation, all G gates, the twenty-seed oracle, and the bound
audit remain required after OpenRouter credit is available.

The earlier seven-launch containment incident is preserved in
[the incident finding](../../../../docs/plans/findings/compiler-task-2.4-containment.md).
C2 accepted containment and authorized local resumption; all owned tests now have
a session guard against real codex/claude/hermes execution, and dispatch tests use
strict fake-only PATH/process allowlists.

Only G1–G3 policy failures block a build and may enter compiler repair rounds.
G4 and G5 still run and carry `advisory: true` in build receipts. Their `failed`
outcomes appear in `report.json` warnings and the Markdown report, while a build
with passing G1–G3 remains passed. An `errored` outcome from any gate stops the
build immediately and makes it errored, including advisory gates. Local check
returns 0 for advisory failures, 1 for blocking failures, and 3 for tool errors.
