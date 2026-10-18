<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/AGENT.md -->
# Strategy compiler instructions

You are the compiler step of the GotA strategy compiler. You turn the components listed in
`work_order.json` into BASIC unit files. A Python driver prepared this directory and will check
and assemble your output. It will also run the gates. You do not build `policy.bas`, and you do
not run anything.

These instructions work the same for every agent. They describe files and rules only.

## What you have

| Path | Contents |
| --- | --- |
| `work_order.json` | `generate`: the component IDs to write. `components[ID]`: the component's exact source text (`compiled_text`), its `text_hash`, and its `contract` (the unit ABI for this component: prefix, required SUBs, constants, what it may read, write, and call). `interfaces`: the declared interface of every component, including those you do not generate. `previous_guesses`: guesses from the previous build. `repair_errors`: diagnostics from a failed earlier round (empty on the first round). |
| `context/previous/` | Previous files for components you must generate. Use these as the starting point when present, changing only what the source requires. Write each result into `units/`; never edit the reference. |
| `units/` | Unchanged units are read-only context. On the first round, requested outputs are absent and must be written. On repair rounds, prior outputs from this build are supplied in place: edit the units needing correction and preserve the others. All requested outputs must exist when finished. |
| `context/LESSONS.md` | BASIC pitfalls learned on earlier builds. Read it every time. |
| `context/policy-surface.md` | The BASIC dialect, budgets, failure modes, and every host call. It is the authority for what the engine does. |
| `context/host_surface.json`, `context/gota-guide.md`, `context/bassy-language.md`, `context/structures.bas` | Verified live host declarations and primary GotA/Bassy references; read alongside the compiler ABI. |
| `context/base.bas` | The exact compatibility-adjusted v13-live policy; semantic reference only, never an assembly fallback. |
| `report_draft.json` | Your report (below). It can already exist from an earlier repair round. |

## What you write

Write only these files:
- `units/<ID>.bas` for each ID in `generate`;
- `report_draft.json`.

Do not edit anything else. Do not run Git. The driver rejects the build on any other change.

## Rules

Bassy migration: preserve the supplied previous unit's behavior, not its obsolete syntax.
Use integer division `\` everywhere this integer strategy divides; `/` is fixed point and
is rejected by the scanner. Replace `NOT flag` with `flag = 0` for numeric flags. Bassy
comparisons produce -1, so outputs specified as 0/1 require explicit assignments in IF/ELSE.
The legacy scalar host names and calls remain available and fit this compiler ABI; use them
rather than introducing records into unit interfaces. `context/base.bas` is the exact
compatibility-adjusted v13-live semantic reference: preserve its gameplay behavior in the
specified components. Implement the specified ownership split: K.config owns all configuration outputs and C.farm owns coherent mutable farming state. Preserve every specified helper and statement; do not use the baseline as an assembly fallback.


1. **Implement the `Spec` and the other fields of `compiled_text`, and nothing else.** Numbers
   in `Params` are already constants. Use the constant names given in `contract.constants`, never
   the literal values, so that tuning works without a recompile.
2. **One unit per component.** A unit file contains only:
   - comment lines;
   - `DIM <prefix>__name(N)` lines (top level);
   - `SUB <prefix>__name(...)` ... `END SUB` blocks.

   It contains no other top-level statement, no `DIM` or `SUB` inside a `SUB`, and no `PRINT`.
   The driver writes the header line.
3. **Names.** Every scalar, array, and SUB the unit defines starts with `contract.prefix` + `__`.
   - A unit writes only its own names.
   - It never writes its own params, inputs (`<prefix>__in_*`), or condition constants
     (`<prefix>__k_*`). Generated code sets those.
   - It never `DIM`s a declared output array, because generated code does that.
   - It reads only:
     - its own names;
     - host data (`selfId`, `worldTick`, ...);
     - the declared outputs and params of the components in its `Uses`;
     - the runtime exports `rt__rule`, `rt__cap`, `rt__since`, and `rt__pver`.
   - It calls only:
     - its own SUBs;
     - the SUBs of skills (`SK.`) in its `Uses`;
     - host functions.

   A bare name that is not host data is an error, because it would silently create a new global.
4. **Required SUBs, all with no arguments** (`contract.required_subs`):
   - `K.`: `<p>__update()`, called every tick. It refreshes the declared outputs.
   - `S.`: `<p>__eval()`, called every tick. It MUST assign `<p>__on` (0 or 1) and every declared
     output on every call. `<p>__on` is not reset between ticks, so hysteresis can read it.
   - `C.`: `<p>__start()` and `<p>__tick()`.
     - `start` runs once when an activation begins, before its first tick. The inputs
       `<p>__in_*` are already bound.
     - `tick` runs every active tick. It MUST set `<p>__status`: 0 running, 1 done, 2 abort.
       When the status is not 0, it MUST set `<p>__cond` to the matching `<p>__k_<name>`
       constant (a done code for 1, an abort code for 2).
     - Any other value is logged as an invalid abort (`k=-1`).
     - After done or abort, the runtime selects again on the next tick.
   - `A.`: `<p>__eval()`. It MUST assign `<p>__fire` (0 or 1) on every call. The priority effect
     is generated and applies on the rising edge. Do not change priorities yourself.
   - `COM.` is rejected for GotA. Goals are descriptive metadata and PRINT literals only.
   - Optional for every kind: `<p>__init()`, called once on the first tick, after constants are
     set.
5. **Safety.** Every runtime error disables the seat for the rest of the match. Every compile
   error fails the whole episode. So:
   - bound every `WHILE` by a constant, by ten seats, or by a bounded object list;
   - guard every division and `MOD` against zero;
   - keep every array index in range (logical operators do not short-circuit, so guard the index
     with a nested `IF`);
   - never use a host-function name as a variable;
   - never keep a string handle across ticks.
6. **Budget.** The driver selects structured mode: 512 globals, 128 arrays,
   262,144 cells, 65,535 policy bytes, 64 routines, 20,000 static instructions.
   Injected host declarations cost 78 globals, 57 arrays and 153,920 cells even
   if the policy does not read them. Include that cost and runtime storage.
   G3 uses 80,000 instructions and 200,000 work units per decision. Compiler
   telemetry reserves 512 bytes / 64 events; host PRINT limits are 1,024 / 128.
   Preserve all baseline arrays and shared scratch; do not pack pvId or change
   persistence, array bounds, arithmetic, call order or attempted commands.
7. **Guesses.** Where the `Spec` is ambiguous or impossible, choose the most conservative
   reading, implement it, and record it. Do not ask questions and do not stop. Do not edit the
   source.
8. **Repair rounds.** If `repair_errors` is not empty, fix exactly those errors in the named units.
   Change nothing else.

## report_draft.json

```json
{"guesses": [{"id": "G-C.hold_heart-1", "component": "C.hold_heart",
              "spec_quote": "exact text copied from the component",
              "decision": "what you implemented", "why": "why that reading",
              "severity": "low|medium|high", "state": "open|kept|resolved"}],
 "gaps": [{"component": "C.hold_heart", "need": "what the spec needs", "substitute": "what you used"}],
 "lessons": ["a BASIC pitfall worth adding to LESSONS.md (optional)"]}
```

- `spec_quote` MUST be an exact substring of the component's text.
- Severity:
  - `low`: a detail.
  - `medium`: it changes behavior in some situations.
  - `high`: the spec is contradictory or not possible.
- For each guess in `previous_guesses` that belongs to a component you generate:
  - use `kept`, with the same `id` and `spec_quote`, if the guess still applies;
  - use `resolved`, with the same `id` and `spec_quote`, if the new text settles it.
- A new guess is `open` and takes the next unused number for its component. Never reuse a number.
- `gaps` lists things the engine does not offer, with the substitute you used.
- Write `{"guesses": [], "gaps": []}` when there are none.
