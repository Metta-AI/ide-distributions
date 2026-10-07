<!-- Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/LESSONS.md -->
# Compiler lessons: BASIC pitfalls

Read on every compile. Each entry is a rule plus its evidence. Add an entry only by a reviewed
commit (a compiler can propose entries in `report_draft.json` `lessons`). Engine arithmetic facts are at
coworld-v0.3.123 (`28030de6`, Bassy `77629c03`); the full dialect is in `policy-surface.md` §2-§4.

## Syntax

- `NOT` binds tighter than `=`: write `NOT (a = b)`, or better `a <> b` (policy-surface §2).
- No `FOR`, `DO`, `SELECT`, `ELSEIF`, or `FUNCTION`. Loops are `WHILE` ... `WEND`. Chain
  conditions with nested `IF`.
- `IF cond THEN` must end the statement: no single-line `IF ... THEN x = 1`.
- A `SUB` returns no value. A SUB call inside an expression is a compile error. Return results
  through a global (the way base.bas `isqrt` sets `root`).
- `DIM` takes a literal bound and is top level only. `DIM a(15)` gives 16 cells (0..15).
- There are no built-in math functions (`ABS`, `SQR`, `MIN`). Write them, or use the skills'
  SUBs.

## Names

- Host functions and host data are reserved: `rnd` became a host function in 0.3.89 and broke
  scripts that used it as a variable. Names are case-insensitive, so `RND` collides too.
- Every unit name carries its prefix (`<prefix>__`). A bare name creates a new global and is
  rejected.
- Scalars are created on first use and start at 0. Only SUB parameters are local.

## Runtime errors (each one disables the seat for the rest of the match)

- Division or `MOD` by zero.
- An array index out of range. `AND`/`OR` evaluate both sides, so
  `IF i < 16 AND a(i) > 0` still reads `a(16)`. Use a nested `IF`.
- More than 50,000 instructions or 125,000 work units in one tick. Bound every `WHILE`.
- More than 1,024 printed bytes or 128 print events in one tick. Units never `PRINT`, because
  telemetry is generated.
- A string handle kept from an earlier tick (the pool resets every tick).
- `rnd(n)` with n < 1.

## Arithmetic

- Values are int32: `+ - *` wrap on overflow, and `\` and `MOD` truncate toward 0. `/` produces fixed point and must not be used in this integer strategy. Squared
  distances over about 46,000 cm overflow, so scale down (base.bas divides by 8) before
  squaring.
- Bassy `TRUE` and comparisons yield -1; FALSE is 0. Boolean operators are bitwise.
  Use `flag = 0` instead of `NOT flag`; assign explicit 0/1 when the unit ABI requires it.

## Engine behavior to remember

- `walkTo` persists: the cog keeps walking to the last goal. Aim (`lookAt`/`shootAt`) persists.
  `shootAt` fires only on the tick it is called. Not calling `chargeGrenade(1)` releases a
  charged grenade.
- Dead cogs do not run. The runtime ends the activation when the cog comes back.
- `shout` reaches every living cog within 12.8 m, enemies included. At most 4 shouts per tick.
  Delivery is on the next tick.
