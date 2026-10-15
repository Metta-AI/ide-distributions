<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/LESSONS.md -->
<!-- Adapted from Paintbot compiler lessons at 6b0bffa5; original hash is in PROVENANCE.md. -->
# GotA compiler lessons

Verified against Polyworld `801749e5` and Bassy `a02e6dd5`.

- Host hero IDs are 100–109; strategy seats are 0–9. Generated role tests add 100.
- Names are case-insensitive; `is` and `as` are reserved. Every component variable,
  array and SUB must retain its declared prefix through assembly.
- Only SUB parameters are local. Other scratch consumes shared policy storage.
- This compiler uses the integer subset: use `\` for division. Live Bassy also
  supports Q16.16 decimals and `/`; substituting decimal division changes behavior.
- Comparisons return -1/0. Boolean operators are bitwise and evaluate both sides.
  Guard array reads and division using nested IF statements, not short circuit assumptions.
- DIM bounds are inclusive. Write one DIM or SUB declaration per line.
  Preserve baseline array bounds, including pvId indices 0–63.
- Units do not PRINT; generated telemetry accounts for the emitted callbacks.
  Generated semicolon-separated PRINT costs one event per item plus its final
  newline. Comma-separated PRINT costs two events per item.
- Structured host declarations consume storage even when unused: 78 globals,
  57 arrays, 153,920 cells. The complete policy must fit live limits. There is no
  truncation, physical packing or baseline-source fallback.
- Dead heroes still receive live BASIC decisions. Do not copy Paintbot dead-seat
  assumptions or action calls. Read the GotA policy surface for host behavior.
- Preserve the exact v13-live action and arithmetic behavior. Full action-stream
  equivalence is a separate required validation, not established by storage tests.
