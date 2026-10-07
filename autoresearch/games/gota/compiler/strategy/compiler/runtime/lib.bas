' Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/runtime/lib.bas
' runtime.lib: strategy runtime library, telemetry v2 print routines. Hand-written source,
' copied verbatim into every build (compilation design §4.1). Do not edit a build's copy.
'
' Contract with generated.tables (strategy_basic.py writes these; this file only reads them):
'   rt__n_rules, rt__def(r), rt__prio(r), rt__rcap(r)    rules 1..n: default and current
'                                                        priority, capability code
'   rt__ok(r)                                            1 when rule r's condition and role hold
'   rt__n_sits, rt__n_words, rt__flag(s)                 situation flags (code s, 0/1)
'   rt__n_adapt, rt__a_rule(a), rt__a_op(a), rt__a_amt(a), rt__a_dur(a)
'                                                        adaptation effects (op 1 add, 2 set;
'                                                        dur -1 = forever)
'   rt__min_hold, rt__margin, rt__interrupt              commitment (design §4.4)
'   rt__status, rt__cond                                 set by st__tick for the running cap
'   rt__in0, rt__in1, rt__in2                            bound inputs (st__bind)
'   rt__pe, rt__pb                                       this tick's print events / bytes
'                                                        (static worst per line)
'   st__pwd_print()                                      prints the PWD line and adds its cost
' Read-only exports for units: rt__rule, rt__cap, rt__since, rt__pver.
' Public kill switch: telemetryOff = 1 silences every telemetry line.
'
' Line formats and the event/byte counts below: API.md §4 / the compilation design.

DIM rt__def(63)
DIM rt__prio(63)
DIM rt__rcap(63)
DIM rt__ok(63)
DIM rt__flag(124)
DIM rt__fw(3)
DIM rt__a_on(31)
DIM rt__a_until(31)
DIM rt__a_prev(31)
DIM rt__a_rule(31)
DIM rt__a_op(31)
DIM rt__a_amt(31)
DIM rt__a_dur(31)

' Capability event: e = 1 start, 4 preempted, 5 died (condition code 0). 6 events, 64 bytes.
SUB rt__pwe(rt__pwe_c, rt__pwe_e)
  IF telemetryOff = 0 THEN
    IF rt__pwe_e = 1 THEN
      PRINT "PWE v=2 t="; worldTick; " c="; rt__pwe_c; " e=1 k=0"
    END IF
    IF rt__pwe_e = 4 THEN
      PRINT "PWE v=2 t="; worldTick; " c="; rt__pwe_c; " e=4 k=0"
    END IF
    IF rt__pwe_e = 5 THEN
      PRINT "PWE v=2 t="; worldTick; " c="; rt__pwe_c; " e=5 k=0"
    END IF
    rt__pe = rt__pe + 6
    rt__pb = rt__pb + 64
  END IF
END SUB

' Capability end: status 1 done (e=2), 2 abort (e=3), with its condition code. 7 events, 64 bytes.
SUB rt__pwe_end(rt__end_c, rt__end_s, rt__end_k)
  IF telemetryOff = 0 THEN
    IF rt__end_s = 1 THEN
      PRINT "PWE v=2 t="; worldTick; " c="; rt__end_c; " e=2 k="; rt__end_k
    ELSE
      PRINT "PWE v=2 t="; worldTick; " c="; rt__end_c; " e=3 k="; rt__end_k
    END IF
    rt__pe = rt__pe + 7
    rt__pb = rt__pb + 64
  END IF
END SUB

' A tick gap means the cog was dead: end the activation before anything else runs.
SUB rt__died()
  IF rt__cap > 0 AND rt__ended = 0 THEN
    rt__pwe(rt__cap, 5)
  END IF
  rt__rule = 0
  rt__cap = 0
  rt__ended = 0
END SUB

' Recompute one rule's priority from its default and every active effect on it, in adaptation
' code order: op 1 adds, op 2 sets; the result is clamped to 0..1000. Prints one PWP line
' (13 events, 92 bytes) for the adaptation that changed state.
SUB rt__recompute(rt__rc_a)
  rt__rc_r = rt__a_rule(rt__rc_a)
  rt__rc_old = rt__prio(rt__rc_r)
  rt__rc_p = rt__def(rt__rc_r)
  rt__rc_b = 1
  WHILE rt__rc_b <= rt__n_adapt
    IF rt__a_on(rt__rc_b) <> 0 THEN
      IF rt__a_rule(rt__rc_b) = rt__rc_r THEN
        IF rt__a_op(rt__rc_b) = 1 THEN
          rt__rc_p = rt__rc_p + rt__a_amt(rt__rc_b)
        ELSE
          rt__rc_p = rt__a_amt(rt__rc_b)
        END IF
      END IF
    END IF
    rt__rc_b = rt__rc_b + 1
  WEND
  IF rt__rc_p < 0 THEN
    rt__rc_p = 0
  END IF
  IF rt__rc_p > 1000 THEN
    rt__rc_p = 1000
  END IF
  rt__prio(rt__rc_r) = rt__rc_p
  rt__pver = rt__pver + 1
  IF telemetryOff = 0 THEN
    PRINT "PWP v=2 t="; worldTick; " a="; rt__rc_a; " r="; rt__rc_r; " o="; rt__rc_old; " n="; rt__rc_p; " p="; rt__pver
    rt__pe = rt__pe + 13
    rt__pb = rt__pb + 92
  END IF
END SUB

' One adaptation per tick: expire it when its window is over (a fire on that tick is ignored),
' otherwise apply it on the rising edge of its fire flag while it is not active.
SUB rt__adapt_step(rt__as_a, rt__as_fire)
  rt__as_expired = 0
  IF rt__a_on(rt__as_a) <> 0 THEN
    IF rt__a_until(rt__as_a) >= 0 THEN
      IF worldTick >= rt__a_until(rt__as_a) THEN
        rt__a_on(rt__as_a) = 0
        rt__as_expired = 1
        rt__recompute(rt__as_a)
      END IF
    END IF
  END IF
  IF rt__as_fire <> 0 AND rt__a_prev(rt__as_a) = 0 AND rt__a_on(rt__as_a) = 0 AND rt__as_expired = 0 THEN
    rt__a_on(rt__as_a) = 1
    rt__a_until(rt__as_a) = -1
    IF rt__a_dur(rt__as_a) >= 0 THEN
      rt__a_until(rt__as_a) = worldTick + rt__a_dur(rt__as_a)
    END IF
    rt__recompute(rt__as_a)
  END IF
  rt__a_prev(rt__as_a) = 0
  IF rt__as_fire <> 0 THEN
    rt__a_prev(rt__as_a) = 1
  END IF
END SUB

' Rule selection with commitment (design §4.4; strategy_basic.reference_select mirrors it).
SUB rt__select()
  rt__best = 0
  rt__i = 1
  WHILE rt__i <= rt__n_rules
    IF rt__ok(rt__i) <> 0 THEN
      IF rt__best = 0 THEN
        rt__best = rt__i
      ELSE
        IF rt__prio(rt__i) > rt__prio(rt__best) THEN
          rt__best = rt__i
        END IF
      END IF
    END IF
    rt__i = rt__i + 1
  WEND
  rt__switch = 0
  IF rt__rule = 0 THEN
    rt__switch = 1
  ELSE
    IF rt__ended <> 0 THEN
      rt__switch = 1
    END IF
    IF rt__ok(rt__rule) = 0 THEN
      rt__switch = 1
    END IF
    IF rt__best > 0 AND rt__best <> rt__rule THEN
      IF rt__prio(rt__best) >= rt__interrupt THEN
        rt__switch = 1
      END IF
      IF worldTick - rt__since >= rt__min_hold AND rt__prio(rt__best) >= rt__prio(rt__rule) + rt__margin THEN
        rt__switch = 1
      END IF
    END IF
  END IF
  rt__new = 0
  IF rt__switch = 0 THEN
    rt__held = 1
  ELSE
    rt__held = 0
    IF rt__rule > 0 AND rt__ended = 0 THEN
      rt__pwe(rt__cap, 4)
    END IF
    rt__rule = rt__best
    rt__cap = rt__rcap(rt__best)
    rt__since = worldTick
    rt__ended = 0
    IF rt__rule > 0 THEN
      rt__new = 1
    END IF
  END IF
END SUB

' Start (on a new activation) and tick the selected capability; report done/abort.
SUB rt__run()
  IF rt__cap > 0 THEN
    IF rt__new <> 0 THEN
      st__start()
      rt__pwe(rt__cap, 1)
    END IF
    st__tick()
    IF rt__status <> 0 THEN
      rt__pwe_end(rt__cap, rt__status, rt__cond)
      rt__ended = 1
    END IF
  END IF
END SUB

' Decision line: on any change of r c i h p f, and at least every 24 ticks. The PRINT is
' generated (st__pwd_print): it folds inputs and the priority version to literals when the
' source makes them constant, prints the same text, and adds its exact static cost.
SUB rt__pwd()
  rt__changed = 0
  rt__w = 0
  WHILE rt__w < rt__n_words
    rt__word = 0
    rt__bit = 1
    rt__j = 0
    WHILE rt__j < 31
      rt__s = rt__w * 31 + rt__j + 1
      IF rt__s <= rt__n_sits THEN
        IF rt__flag(rt__s) <> 0 THEN
          rt__word = rt__word + rt__bit
        END IF
      END IF
      IF rt__j < 30 THEN
        rt__bit = rt__bit * 2
      END IF
      rt__j = rt__j + 1
    WEND
    IF rt__word <> rt__fw(rt__w) THEN
      rt__changed = 1
    END IF
    rt__fw(rt__w) = rt__word
    rt__w = rt__w + 1
  WEND
  IF rt__rule <> rt__p_rule OR rt__cap <> rt__p_cap OR rt__held <> rt__p_held OR rt__pver <> rt__p_pver THEN
    rt__changed = 1
  END IF
  IF rt__in0 <> rt__p_in0 OR rt__in1 <> rt__p_in1 OR rt__in2 <> rt__p_in2 THEN
    rt__changed = 1
  END IF
  IF rt__p_done = 0 OR worldTick - rt__p_t >= 24 THEN
    rt__changed = 1
  END IF
  IF rt__changed <> 0 AND telemetryOff = 0 THEN
    st__pwd_print()
    rt__p_rule = rt__rule
    rt__p_cap = rt__cap
    rt__p_held = rt__held
    rt__p_pver = rt__pver
    rt__p_in0 = rt__in0
    rt__p_in1 = rt__in1
    rt__p_in2 = rt__in2
    rt__p_t = worldTick
    rt__p_done = 1
  END IF
END SUB

' Initial priority snapshot: one `PWP ... a=0` line per rule (o = n = default, p=0), printed at
' the end of a tick only while this tick's static print use leaves room (13 events, 92 bytes).
' Deferred to later ticks when there is no room, never skipped.
SUB rt__snapshot()
  IF telemetryOff = 0 AND rt__snap <= rt__n_rules THEN
    IF rt__pe + 13 <= 64 AND rt__pb + 92 <= 512 THEN
      PRINT "PWP v=2 t="; worldTick; " a=0 r="; rt__snap; " o="; rt__def(rt__snap); " n="; rt__def(rt__snap); " p=0"
      rt__pe = rt__pe + 13
      rt__pb = rt__pb + 92
      rt__snap = rt__snap + 1
    END IF
  END IF
END SUB
