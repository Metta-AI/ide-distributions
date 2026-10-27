' Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/runtime/main.bas
' runtime.main: the per-tick order (strategy file format §3). Hand-written source, copied
' as the last block. Structured GotA keeps namespaced runtime state separate from units.
' This is the only top-level executable code in policy.bas.
IF rt__started = 0 THEN
  rt__started = 1
  rt__snap = 1
  st__init()
END IF
rt__pe = 0
rt__pb = 0
IF selfRespawnTicks > 0 THEN
  IF rt__dead = 0 THEN
    rt__dead = 1
    rt__died()
  END IF
ELSE
  rt__dead = 0
END IF
st__receive()
st__knowledge()
st__situations()
st__adapt()
st__conditions()
rt__select()
st__bind()
rt__run()
st__send()
rt__pwd()
st__beliefs()
rt__snapshot()
st__flush()
