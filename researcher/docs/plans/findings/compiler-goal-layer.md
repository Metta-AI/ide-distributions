# Compiler goal layer: Task 2.2 findings and rulings

Base: `03bba60` (Task 2.1); compiler source pin remains
`personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84`.

1. The task plan's two named parser/generator modules are insufficient to make
   goal labels usable. The actual pinned telemetry reader is
   `games/gota/compiler/tools/pw_intent.py`, and audit attribution requires
   `strategy_audit.py`, `strategy_audit_runtime.py`, and the staged
   `strategy_audit_baseline.py`. These owned destination modules were extended;
   the original source lab and runtime BASIC were not changed. C1 authorizes
   this smallest faithful module-ownership correction.
2. The existing rule selector is `FOR set=role,...`, rather than a bare role.
   Task 2.2 renames it to `AS set=role,...`, preserving role semantics and
   migrating the owned RICH fixture. Adaptation Effect FOR duration is unchanged.
3. The parent approved PWD v3 for annotated strategies. Rule-specific goal IDs
   merge into the first PRINT literal as `g=G.id,...`; unannotated rules and idle
   use `g=-`. Other telemetry formats and unannotated strategies' v2 bytes remain
   unchanged. The compile-time branches allocate no globals or arrays and add no
   PRINT events. Labels add bytes and instructions; the original budget rejects
   overflow. Exact ordered rule/map labels are mandatory on the reader.
4. Goals use required Claim, Priority, Provenance, and Status fields; Evidence,
   Rationale, and Checks follow the existing optional structured conventions.
   Priority is a descriptive integer 0..1000. Goal metadata is bound to the
   committed source/map, excluded from gameplay compilation, and never used to
   rank rules or research Topics.
5. A traced goal is not a satisfied goal. Audit traces attribute decision evidence
   and report unsupported goal claims/Checks as unmeasurable. No new gameplay
   semantic evaluator or qualification is claimed.

Canonical implementation reference:
[`games/gota/compiler/docs/goal-layer.md`](../../../games/gota/compiler/docs/goal-layer.md).
Pure/local tests and limitations are recorded in compiler PROVENANCE and
`/tmp/ide-mvp-codex-task-2.2-report.md`. Task 2.3 is held for independent review.
