' Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/strategy/compiler/runtime/comms.bas
' Comms-v1 arithmetic. This is scrambling, not authentication.
' Generated initialization supplies cm__keys(0..5). No string handle survives a tick.
DIM cm__keys(5)
DIM cm__batch_a(8)
DIM cm__batch_b(8)

SUB cm__mask(cm__mt, cm__mb)
  cm__h1 = ((cm__mt MOD 30011) * cm__keys(0) + cm__keys(1) + cm__mb * cm__keys(2)) MOD 30011
  cm__h2 = (cm__h1 * cm__h1 + cm__keys(3)) MOD 32749
  cm__mask_value = cm__h1 * 32768 + cm__h2
END SUB

' Validate before packing, and after unpacking. Catalogue bounds also keep
' the telemetry send marker 2000000000 + pA inside signed int32.
SUB cm__validate()
  cm__ok = 0
  IF cm__type < 0 OR cm__type > 8 OR cm__speaker < 0 OR cm__speaker > 15 THEN
    EXIT SUB
  END IF
  IF cm__fa < 0 OR cm__fb < 0 OR cm__cell < 0 OR cm__cell > 65535 THEN
    EXIT SUB
  END IF
  cm__limit_a = 0
  cm__limit_b = 0
  IF cm__type = 0 THEN
    cm__limit_a = 2303
    cm__limit_b = 255
  END IF
  IF cm__type = 1 THEN
    cm__limit_a = 255
  END IF
  IF cm__type = 2 THEN
    cm__limit_a = 24
  END IF
  IF cm__type = 3 THEN
    cm__limit_a = 143
    cm__limit_b = 3
  END IF
  IF cm__type = 4 THEN
    cm__limit_a = 720
  END IF
  IF cm__type = 5 THEN
    cm__limit_a = 255
    cm__limit_b = 720
  END IF
  IF cm__type = 6 THEN
    cm__limit_a = 4095
  END IF
  IF cm__type = 7 THEN
    cm__limit_a = 15
    cm__limit_b = 255
  END IF
  IF cm__type = 8 THEN
    cm__limit_a = 15
  END IF
  IF cm__fa > cm__limit_a OR cm__fb > cm__limit_b THEN
    EXIT SUB
  END IF
  IF cm__type = 3 OR cm__type = 8 THEN
    IF cm__cell <> 0 THEN
      EXIT SUB
    END IF
  END IF
  cm__ok = 1
END SUB

SUB cm__check()
  cm__check_value = ((cm__core MOD 97) + 7 * (cm__pb MOD 97) + ((cm__time MOD 9973) * cm__keys(4)) MOD 97 + cm__keys(5)) MOD 97
END SUB

SUB cm__encode(cm__et, cm__es, cm__ea, cm__eb, cm__ec, cm__etime)
  cm__type = cm__et
  cm__speaker = cm__es
  cm__fa = cm__ea
  cm__fb = cm__eb
  cm__cell = cm__ec
  cm__time = cm__etime
  cm__validate()
  IF cm__time < 0 THEN
    cm__ok = 0
  END IF
  IF cm__ok = 0 THEN
    EXIT SUB
  END IF
  cm__core = cm__type + 16 * (cm__speaker + 16 * cm__fa)
  cm__pb = cm__cell + 65536 * cm__fb
  cm__check()
  cm__pa = cm__check_value + 100 * cm__core
  cm__mask(cm__time, 0)
  cm__block_a = 1000000000 + (cm__pa + cm__mask_value) MOD 1000000000
  cm__mask(cm__time, 1)
  cm__block_b = 1000000000 + (cm__pb + cm__mask_value) MOD 1000000000
END SUB

SUB cm__decode(cm__handle, cm__dtime)
  cm__ok = 0
  cm__time = cm__dtime
  IF cm__time < 0 THEN
    EXIT SUB
  END IF
  IF strLen(cm__handle) <> 20 THEN
    EXIT SUB
  END IF
  IF strByte(cm__handle, 0) <> 49 OR strByte(cm__handle, 10) <> 49 THEN
    EXIT SUB
  END IF
  cm__sa = 0
  cm__sb = 0
  cm__digit_i = 1
  WHILE cm__digit_i < 20
    IF cm__digit_i <> 10 THEN
      cm__digit = strByte(cm__handle, cm__digit_i) - 48
      IF cm__digit < 0 OR cm__digit > 9 THEN
        EXIT SUB
      END IF
      IF cm__digit_i < 10 THEN
        cm__sa = cm__sa * 10 + cm__digit
      ELSE
        cm__sb = cm__sb * 10 + cm__digit
      END IF
    END IF
    cm__digit_i = cm__digit_i + 1
  WEND
  cm__mask(cm__time, 0)
  cm__pa = (cm__sa - cm__mask_value + 1000000000) MOD 1000000000
  cm__mask(cm__time, 1)
  cm__pb = (cm__sb - cm__mask_value + 1000000000) MOD 1000000000
  cm__core = cm__pa / 100
  cm__type = cm__core MOD 16
  cm__speaker = (cm__core / 16) MOD 16
  cm__fa = cm__core / 256
  cm__cell = cm__pb MOD 65536
  cm__fb = cm__pb / 65536
  cm__validate()
  IF cm__ok = 0 THEN
    EXIT SUB
  END IF
  cm__check()
  IF cm__pa MOD 100 <> cm__check_value THEN
    cm__ok = 0
  END IF
END SUB

' One accepted packet per decoded event. The final entry may be our outgoing
' packet; its leading 2 is the direction marker in compact telemetry.
SUB cm__remember(cm__direction)
  ' Reserve the batch before optional priority snapshots consume spare budget.
  IF cm__batch_count = 0 THEN
    rt__pe = rt__pe + 4
    rt__pb = rt__pb + 25
  END IF
  rt__pe = rt__pe + 2
  rt__pb = rt__pb + 20
  cm__batch_a(cm__batch_count) = cm__pa + 1000000000 * cm__direction
  cm__batch_b(cm__batch_count) = cm__pb + 1000000000
  cm__batch_count = cm__batch_count + 1
END SUB

' The generated dispatcher calls cm__receive once before Knowledge. Cheap
' shape filtering scans all heard texts; at most eight candidates are decoded.
' cm__rx_valid indexes this tick's heard list, for motor's non-team sound cue.
DIM cm__rx_valid(63)
DIM cm__suspect_t(15)
DIM cm__suspect_x(15)
DIM cm__suspect_y(15)
DIM cm__heard_t(15)
DIM cm__heard_x(15)
DIM cm__heard_y(15)
SUB cm__receive()
  cm__batch_count = 0
  cm__sent = 0
  cm__attempts = 0
  cm__rx_index = 0
  WHILE cm__rx_index < heardCount()
    cm__rx_valid(cm__rx_index) = 0
    cm__rx_handle = heardText(cm__rx_index)
    cm__rx_slot = heardSlot(cm__rx_index)
    cm__rx_x = heardX(cm__rx_index)
    cm__rx_y = heardY(cm__rx_index)
    cm__ok = 0
    IF cm__attempts < cm__max_decode THEN
      IF strLen(cm__rx_handle) = 20 THEN
        IF strByte(cm__rx_handle, 0) = 49 AND strByte(cm__rx_handle, 10) = 49 THEN
          cm__attempts = cm__attempts + 1
          cm__decode(cm__rx_handle, worldTick - 1)
          IF cm__ok = 1 THEN
            IF cm__speaker MOD 2 = selfTeam AND cm__speaker <> selfId THEN
              cm__rx_valid(cm__rx_index) = 1
              cm__heard_t(cm__speaker) = worldTick + 1
              cm__heard_x(cm__speaker) = cm__rx_x
              cm__heard_y(cm__speaker) = cm__rx_y
              cm__remember(1)
              st__decoded()
            END IF
          END IF
        END IF
      END IF
      IF cm__ok = 0 THEN
        IF cm__rx_slot MOD 2 = selfTeam THEN
          cm__suspect_t(cm__rx_slot) = worldTick + 1
          cm__suspect_x(cm__rx_slot) = cm__rx_x
          cm__suspect_y(cm__rx_slot) = cm__rx_y
        END IF
      END IF
    END IF
    cm__rx_index = cm__rx_index + 1
  WEND
END SUB

' Called by COM send routines in priority order. A rejected proposal leaves
' cm__sent at zero so the next component can try. Cooldowns advance on success.
SUB cm__send(cm__st, cm__sfa, cm__sfb, cm__scell, cm__quiet)
  IF cm__sent = 1 THEN
    EXIT SUB
  END IF
  cm__urgent = cm__st = 2 OR cm__st = 8 OR cm__st = 7
  IF cm__urgent = 0 THEN
    IF cm__quiet <> 0 THEN
      EXIT SUB
    END IF
    IF cm__last_nonurgent > 0 THEN
      IF worldTick + 1 - cm__last_nonurgent < cm__min_gap THEN
        EXIT SUB
      END IF
    END IF
  END IF
  cm__encode(cm__st, selfId, cm__sfa, cm__sfb, cm__scell, worldTick)
  IF cm__ok = 0 THEN
    EXIT SUB
  END IF
  cm__out_handle = strFromInt(cm__block_a)
  cm__out_handle = strCatInt(cm__out_handle, cm__block_b)
  cm__sent = shout(cm__out_handle)
  IF cm__sent = 1 THEN
    cm__remember(2)
    IF cm__urgent = 0 THEN
      cm__last_nonurgent = worldTick + 1
    END IF
  END IF
END SUB

' Flush after snapshots, at the end of this tick. Fixed-width decimal PRINT
' items avoid strings (PRINT handles are numeric) and preserve every payload.
SUB cm__flush()
  IF cm__batch_count = 0 OR telemetryOff <> 0 THEN
    EXIT SUB
  END IF
  PRINT "PWC v=3 t="; worldTick; " b=";
  cm__batch_i = 0
  WHILE cm__batch_i < cm__batch_count
    PRINT cm__batch_a(cm__batch_i); cm__batch_b(cm__batch_i);
    cm__batch_i = cm__batch_i + 1
  WEND
  PRINT
END SUB
