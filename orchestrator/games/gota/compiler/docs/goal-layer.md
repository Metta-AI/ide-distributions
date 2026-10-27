# Goals in strategy source and decision traces

Task 2.2 extends the pinned Paintbot compiler with descriptive goals. This is
compiler and reader support, not a GotA runtime or engine qualification. Goals
label gameplay decisions; they never change rule priority, selection, commitment,
capability inputs, or adaptation effects. Research Topic priorities are separate.

## Source grammar

Declare goals under `## Goals`, using the existing structured field list:

```markdown
## Goals

### G.survive
- Claim: Stay alive.
- Priority: 100
- Provenance: Local strategy specification.
- Status: specified (2026-10-07)
- Evidence: No gameplay evidence yet.
- Checks:
  - Result: Survive the episode. Reads: replay
```

IDs use `G.snake_case`. `Claim`, `Priority`, `Provenance`, and `Status` are
required and nonempty. Priority is a decimal integer from 0 through 1000,
descriptive only. Status uses the existing `idea`, `specified`, `compiled`,
`tested`, or `proven` vocabulary with an optional `(YYYY-MM-DD)` date. `Evidence`,
`Rationale`, and `Checks` are optional. Checks use the existing levels and Reads
grammar. Other fields, including Params, Uses, Inputs, and Outputs, are rejected.
Goals generate no component units, constants, globals, or arrays.

Rules accept:

```text
- `R.id` [priority]: WHEN <condition>|ALWAYS DO `C.id`(input=arg, ...)
  [AS role_set=role,...] [FOR G.id, G.id]
```

The actual rule occupies one source line. Capability arguments, role selection,
and goal annotations are optional. Goal IDs can be bare or backticked; commas
separate them. IDs must resolve to declared G components, and duplicates, empty
items, trailing commas, and non-G references fail lint. Source order is preserved
in the map and wire labels. When both selectors are present, AS precedes FOR:

```markdown
- `R.retreat` [500]: WHEN `S.danger` DO `C.retreat` AS half=low FOR G.survive, `G.farm`
```

The old rule role spelling `FOR half=low` is rejected; use `AS half=low`.
Adaptation `Effect: ... FOR duration` retains its original meaning and grammar.

## Decision wire contract

If any rule has goal annotations, every PWD line uses this exact ordered grammar:

```text
PWD v=3 g=G.survive,G.farm t=0 r=1 c=1 i=12,0,0 h=0 p=0 f=0
```

`g=-` means no goals, used for an unannotated selected rule or idle rule 0.
Goal lists contain literal IDs without backticks or spaces. The reader requires
the exact ordered list declared for that selected rule. Missing, extra,
reordered, duplicate, unknown, and malformed labels fail parsing. v2 lines are
rejected by a goal-aware build; v3 lines are rejected by a v2 build. Numeric
field validation is unchanged. `parse_v2_line` retains its public name and now
also validates goal-aware PWD v3; `parse_telemetry_line` delegates to it.

Strategies with no annotated rules retain byte-identical PWD v2 generation,
including strategies that declare unused goals. PWP, PWE, PWB, and PWC formats
are unchanged, including the existing compact PWC v3 codec.

Each generated `st__pwd_print` branch merges the rule's goal IDs into the initial
PRINT literal and exits the SUB after printing. The idle branch prints `g=-`.
No scalar or string storage holds labels between ticks. Each branch increments
`rt__pe` and `rt__pb` for its actual PRINT items and worst-case bytes; the static
per-tick budget takes the longest list across all rules and idle. Labels add
source text, branch instructions, and printed bytes, but no PRINT items/events.
The existing 512-byte and 64-event per-tick half-budget remains enforced by lint;
an oversized label list is a `telemetry-budget` error.

## Build maps and audit evidence

`strategy_basic.build_map` keeps schema `pw-strategy-map/1` with additive fields.
`goals` records each goal's claim, descriptive integer priority, provenance,
status/date, evidence, and rationale. Goal Checks live in their component entry.
Goal metadata is excluded from compiled component text; committed source and
immutable map hashes bind it instead. Changing goal priority or prose does not
change generated policy. Rule annotations change the generated telemetry.

Goal-aware maps declare `telemetry.decision_version: 3`, include `g` first in
`telemetry.lines.PWD`, and record ordered `rules[].goal_refs`, including an empty
list for unannotated rules. `telemetry.version: 2` remains the base numeric wire
family; `list_keys` describes the existing numeric vectors. `g` is a list of
IDs decoded into `goal_refs`, not a numeric vector. A map without
`decision_version` declares PWD v2. Audit loading verifies goal metadata,
decision version, line fields, rules, and Checks against committed source.

`strategy_audit.read_log` uses the strict wire reader. Reconstruction carries
`goal_refs` through decision ticks within the existing 24-tick heartbeat and
records the activation's initial labels in its window. Missing decision coverage
has no attributed goals. `Evidence.attach_runtime` adds traced labels to
tick-bound check evidence; G component checks carry their own goal ID. Seat audit
summaries include goal decision counts, rule codes, and trace coverage status.
Malformed telemetry or incomplete evidence makes trace coverage unmeasurable.

Attribution does not prove a goal claim. Every goal claim currently has
`claim_status: unmeasurable` and reason `no_evaluator_for_goal_claim`, and G Checks
remain unmeasurable even with identical reference metadata. Existing semantic
evaluators and engine qualification requirements remain in force.

## Local validation

From `researcher/`:

```bash
uv run --locked --offline pytest tests/unit/gota/compiler/test_goal_layer.py -q
uv run --locked --offline pytest tests/unit/gota -q
uv run --locked --offline ruff check tests/unit/gota/compiler/test_goal_layer.py
```

Pandas is a locked project dependency. The full owned suite includes local native
fixtures and a mandatory session guard against real agent execution. Goal tests
use synthetic telemetry; native oracle tests use mechanically derived test units.
See [GotA backend](gota-backend.md) for the supported build-bound goal-audit entry
point. The older Paintbot semantic evaluators are not GotA predicates. Real M1
is still blocked on model credit; local fixture equivalence is not real M1 proof.
