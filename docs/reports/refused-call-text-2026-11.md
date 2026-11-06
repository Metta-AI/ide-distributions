# What the text around a refused call changes

Measured on Kimi K3.1 Pro in November 2026, with `core/tests/model/test_mid_turn_recovery.py` and direct
calls. Moved from the README, which keeps the rates that decide what ships.

Hermes's loop warnings stay on. On three different refused calls, where its "Do not switch to
text-only replies; keep using tools" line fires, the next call was valid 59/60 with its warnings and
58/62 with `warnings_enabled: false`, so nothing showed the text prolonging a loop.

Rewording the repeat refusal did not help: a full refusal, a filled-in example from the model's own
arguments, naming the field, the field first, and no way out all fell between 46% and 60%, with
overlapping intervals. A schema that lacks the required field stays at 0/60 whatever the text says,
and the loop guard ends nearly all of those turns (52 to 58 of 60).
