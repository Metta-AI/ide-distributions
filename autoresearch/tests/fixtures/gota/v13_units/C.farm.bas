' TEST ONLY: mechanically namespaced exact v13-live; never a production compiler input.
' ===== module 10_scan.bas =====
' Module: scan (prefix sc). One pass over the object list into typed arrays.
' The VM allows 32 arrays in total; this module uses 24 and the planner 8.

' c_farm__rsslot = ring slot for any object id (footman, hero or structure).
sub c_farm__lhringslot(id)
  if id >= 1000 then
    c_farm__rsslot = (id - 1000) mod 448
  else
    if id >= 100 then
      c_farm__rsslot = 448 + id - 100
    else
      c_farm__rsslot = 458 + id
    end if
  end if
end sub
' Positions are stored in ku (tile * 60). Enemy objects are only those visible
' to my team this decision; allied objects are always present. Footmen are kept
' only within k_config__cfgscanradius of me (budget).
'
' Outputs (valid for this decision):
'   c_farm__efn, c_farm__efid(i), c_farm__efx(i), c_farm__efy(i), c_farm__efhp(i)     living enemy footmen
'   c_farm__afn, c_farm__afx(i), c_farm__afy(i), c_farm__aftgt(i)              living allied footmen and their targets
'   c_farm__atn, c_farm__atx(i), c_farm__aty(i), c_farm__attgt(i), c_farm__atdmg(i)    allied towers (any), their target and hit damage
'   c_farm__etn, c_farm__etx(i), c_farm__ety(i), c_farm__ettgt(i), c_farm__ethp(i)     visible living enemy towers and barracks:
'                                              c_farm__etx = x * 64 + id, c_farm__ettgt = target * 8 + kind * 2 + exposed
'   c_farm__ahn, c_farm__ahx(i), c_farm__ahy(i), c_farm__ahtgt(i), c_farm__ahdmg(i), c_farm__ahper(i)  living allied heroes (not me)
'   c_farm__ehn, c_farm__ehpos(i), c_farm__ehmeta(i), c_farm__ehhp(i)          visible living enemy heroes: position packed as
'                                              x * 8192 + y (ku), id * 65536 + target id, HP * 16 + class
'   c_farm__scfortx, c_farm__scforty                           own god (fort) position
'   c_farm__scefortx, c_farm__sceforty                         enemy god position (mirror of own)
'   c_farm__fmslot(ring)                               id ring -> index into ef arrays (footmen), or -1;
'                                              ring slot: footman (id-1000) mod 448, hero 448+id-100,
'                                              structure 458+id (see c_farm__lhringslot)
'   c_farm__scselfx, c_farm__scselfy                           my position in ku

dim c_farm__efid(63)
dim c_farm__efx(63)
dim c_farm__efy(63)
dim c_farm__efhp(63)
dim c_farm__afx(63)
dim c_farm__afy(63)
dim c_farm__aftgt(63)
dim c_farm__atx(15)
dim c_farm__aty(15)
dim c_farm__attgt(15)
dim c_farm__atdmg(15)
dim c_farm__etx(23)
dim c_farm__ety(23)
dim c_farm__ettgt(23)
dim c_farm__ethp(23)
dim c_farm__ahtgt(4)
dim c_farm__ahx(4)
dim c_farm__ahy(4)
dim c_farm__ahdmg(4)
dim c_farm__ahper(4)
dim c_farm__ehpos(4)
dim c_farm__ehmeta(4)
dim c_farm__ehhp(4)
dim c_farm__fmslot(511)

' Sets c_farm__hddmg and c_farm__hdper (basic damage and swing ticks) for a hero class and level,
' ignoring items. Table from content.nim at the deployed commit.
sub c_farm__herodmgfor(cls, lvl)
  c_farm__hddmg = 25
  c_farm__hdper = 24
  if cls = 0 then
    c_farm__hddmg = 25 + (lvl - 1) * 5
    c_farm__hdper = 24
  end if
  if cls = 1 then
    c_farm__hddmg = 25 + (lvl - 1) * 6
    c_farm__hdper = 18
  end if
  if cls = 2 then
    c_farm__hddmg = 38 + (lvl - 1) * 8
    c_farm__hdper = 30
  end if
  if cls = 3 then
    c_farm__hddmg = 22 + (lvl - 1) * 4
    c_farm__hdper = 26
  end if
  if cls = 4 then
    c_farm__hddmg = 32 + (lvl - 1) * 7
    c_farm__hdper = 16
  end if
  if cls = 5 then
    c_farm__hddmg = 30 + (lvl - 1) * 6
    c_farm__hdper = 28
  end if
  if cls = 6 then
    c_farm__hddmg = 46 + (lvl - 1) * 9
    c_farm__hdper = 36
  end if
  if cls = 7 then
    c_farm__hddmg = 36 + (lvl - 1) * 8
    c_farm__hdper = 32
  end if
  if cls = 8 then
    c_farm__hddmg = 26 + (lvl - 1) * 5
    c_farm__hdper = 28
  end if
  if cls = 9 then
    c_farm__hddmg = 38 + (lvl - 1) * 8
    c_farm__hdper = 20
  end if
end sub

' c_farm__hsmove = base move speed in ku per tick for a class (level growth ignored; a
' conservative estimate for "can I catch it").
sub c_farm__herospeedfor(cls)
  c_farm__hsmove = 6
  if cls = 4 then
    c_farm__hsmove = 7
  end if
  if cls = 0 or cls = 5 then
    c_farm__hsmove = 5
  end if
end sub

' Tower hit damage from its stable id: lane towers are ids 10-27 in
' outer/inner/gate triples; god guards (28-31) hit like a gate tower.
sub c_farm__towerdmgfor(tid)
  c_farm__tddmg = 30
  if tid >= 10 and tid <= 27 then
    c_farm__tdtier = (tid - 10) mod 3
    if c_farm__tdtier = 0 then
      c_farm__tddmg = 18
    end if
    if c_farm__tdtier = 1 then
      c_farm__tddmg = 24
    end if
  end if
end sub

sub c_farm__scanobjects()
  ' Clear the id ring entries used last decision.
  c_farm__oi = 0
  while c_farm__oi < c_farm__efn
    c_farm__fmslot((c_farm__efid(c_farm__oi) - 1000) mod 448) = -1
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__efn = 0
  c_farm__afn = 0
  c_farm__atn = 0
  c_farm__etn = 0
  c_farm__ahn = 0
  c_farm__ehn = 0
  c_farm__scselfx = selfX * 60
  c_farm__scselfy = selfY * 60
  c_farm__sccount = objectCount()
  c_farm__oi = 0
  while c_farm__oi < c_farm__sccount
    c_farm__sckind = objectKind(c_farm__oi)
    c_farm__scteam = objectTeam(c_farm__oi)
    if c_farm__sckind = 3 then
      if objectAlive(c_farm__oi) = 1 then
        ' Footmen beyond k_config__cfgscanradius are irrelevant to this hero's decisions and
        ' would cost instruction budget in every planner loop; skip them.
        c_farm__odx = objectX(c_farm__oi) * 60 - c_farm__scselfx
        c_farm__ody = objectY(c_farm__oi) * 60 - c_farm__scselfy
        if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody > k_config__cfgscanradius * k_config__cfgscanradius then
          c_farm__sckind = 0
        end if
      else
        c_farm__sckind = 0
      end if
    end if
    if c_farm__sckind = 3 then
      if objectAlive(c_farm__oi) = 1 then
        if c_farm__scteam = selfTeam then
          if c_farm__afn < 64 then
            c_farm__afx(c_farm__afn) = objectX(c_farm__oi) * 60
            c_farm__afy(c_farm__afn) = objectY(c_farm__oi) * 60
            c_farm__aftgt(c_farm__afn) = objectTarget(c_farm__oi)
            c_farm__afn = c_farm__afn + 1
          end if
        else
          if c_farm__efn < 64 then
            c_farm__efid(c_farm__efn) = objectId(c_farm__oi)
            c_farm__efx(c_farm__efn) = objectX(c_farm__oi) * 60
            c_farm__efy(c_farm__efn) = objectY(c_farm__oi) * 60
            c_farm__efhp(c_farm__efn) = objectHp(c_farm__oi)
            c_farm__fmslot((c_farm__efid(c_farm__efn) - 1000) mod 448) = c_farm__efn
            c_farm__efn = c_farm__efn + 1
          end if
        end if
      end if
    end if
    if c_farm__sckind = 4 and c_farm__scteam = selfTeam then
      if c_farm__atn < 16 then
        c_farm__atx(c_farm__atn) = objectX(c_farm__oi) * 60
        c_farm__aty(c_farm__atn) = objectY(c_farm__oi) * 60
        c_farm__attgt(c_farm__atn) = objectTarget(c_farm__oi)
        c_farm__towerdmgfor(objectId(c_farm__oi))
        c_farm__atdmg(c_farm__atn) = c_farm__tddmg
        c_farm__atn = c_farm__atn + 1
      end if
    end if
    if c_farm__scteam <> selfTeam and (c_farm__sckind = 4 or c_farm__sckind = 5) then
      ' Enemy towers and barracks. c_farm__etx packs id (below 64) into the low bits;
      ' c_farm__ettgt packs target * 8 + kind * 2 + exposed.
      if objectHp(c_farm__oi) > 0 then
        if c_farm__etn < 24 then
          c_farm__etx(c_farm__etn) = objectX(c_farm__oi) * 60 * 64 + objectId(c_farm__oi)
          c_farm__ety(c_farm__etn) = objectY(c_farm__oi) * 60
          c_farm__ettgt(c_farm__etn) = objectTarget(c_farm__oi) * 8 + c_farm__sckind * 2 + objectAlive(c_farm__oi)
          c_farm__etn = c_farm__etn + 1
        end if
      end if
    end if
    if c_farm__sckind = 2 then
      if objectId(c_farm__oi) <> selfId then
        if objectAlive(c_farm__oi) = 1 then
          if c_farm__scteam = selfTeam then
            if c_farm__ahn < 5 then
              c_farm__ahtgt(c_farm__ahn) = objectTarget(c_farm__oi)
              c_farm__ahx(c_farm__ahn) = objectX(c_farm__oi) * 60
              c_farm__ahy(c_farm__ahn) = objectY(c_farm__oi) * 60
              c_farm__herodmgfor(objectClass(c_farm__oi), objectLevel(c_farm__oi))
              c_farm__ahdmg(c_farm__ahn) = c_farm__hddmg
              c_farm__ahper(c_farm__ahn) = c_farm__hdper
              c_farm__ahn = c_farm__ahn + 1
            end if
          else
            if c_farm__ehn < 5 then
              c_farm__ehpos(c_farm__ehn) = objectX(c_farm__oi) * 60 * 8192 + objectY(c_farm__oi) * 60
              c_farm__ehmeta(c_farm__ehn) = objectId(c_farm__oi) * 65536 + objectTarget(c_farm__oi)
              c_farm__ehhp(c_farm__ehn) = objectHp(c_farm__oi) * 16 + objectClass(c_farm__oi)
              c_farm__ehn = c_farm__ehn + 1
            end if
          end if
        end if
      end if
    end if
    if c_farm__sckind = 1 then
      if c_farm__scteam = selfTeam then
        c_farm__scfortx = objectX(c_farm__oi) * 60
        c_farm__scforty = objectY(c_farm__oi) * 60
        c_farm__scefortx = (mapWidth - 1) * 60 - c_farm__scfortx
        c_farm__sceforty = (mapHeight - 1) * 60 - c_farm__scforty
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' ===== module 20_lasthit.bas =====
' Module: lasthit (prefix lh). Plans the footman last hit for this decision.
'
' Inputs: scan arrays (10_scan.bas), self data, cfg knobs, c_farm__shpoisonslot (40_shop.bas).
' Outputs, rewritten every decision:
'   c_farm__lhstate      0 no enemy footmen in play, 1 waiting for a window, 2 striking now
'   c_farm__lhtargetid   footman to order attackTarget on now, or 0
'   c_farm__lhsecureslot ability slot to cast on c_farm__lhsecureid to secure a kill, or -1
'   c_farm__lhsecureid   footman id for the secure cast
'   c_farm__lhpoisonid   footman id to poison this decision (attackTarget c_farm__it, then useItem), or 0
'   c_farm__lhholdx, c_farm__lhholdy  tile to walk toward while waiting
' Telemetry counters: c_farm__lhorders, c_farm__lhsecures, c_farm__lhpoisons, lhLost.
'
' Method: for every visible enemy footman, predict its HP at the tick my next basic
' hit would land. Incoming damage comes from recorded HP-drop events (each repeats
' with its attacker's period: 32 ticks for footmen, 24 for towers, the class swing
' for heroes) plus an expected-value term for attached attackers that have not hit
' yet. A footman whose predicted HP is in (0, selfAttackDamage] is a window: order
' the attack now. One that would die first is a secure candidate: poison (instant,
' 35) or the class primary strike. Otherwise hold a walk target at the standoff
' point so the engine never auto-acquires and wastes hits.

dim c_farm__fmhp(511)
dim c_farm__fmseen(511)
dim c_farm__fmev1(511)
dim c_farm__fmev2(511)
dim c_farm__pvid(63)
dim c_farm__efatt(63)
dim c_farm__eftw(63)
dim c_farm__efhero(63)

' c_farm__add = approximate planar distance of (dx, dy) in ku, within about 7 percent.
sub c_farm__approxdist(dx, dy)
  c_farm__adax = dx
  if c_farm__adax < 0 then
    c_farm__adax = 0 - c_farm__adax
  end if
  c_farm__aday = dy
  if c_farm__aday < 0 then
    c_farm__aday = 0 - c_farm__aday
  end if
  if c_farm__adax >= c_farm__aday then
    c_farm__add = (c_farm__adax * 10 + c_farm__aday * 4) \ 10
  else
    c_farm__add = (c_farm__aday * 10 + c_farm__adax * 4) \ 10
  end if
end sub

' Primary-strike parameters for my class (slot 1). c_farm__spdmg = 0 means no instant strike.
' c_farm__spproj = 1 for projectiles (45 ku per tick of travel).
sub c_farm__lhstrikespec()
  c_farm__spdmg = 0
  c_farm__sprange = 0
  c_farm__spproj = 0
  c_farm__spmana = 0
  if selfClass = 0 then
    c_farm__spdmg = 40
    c_farm__sprange = 90
    c_farm__spmana = 20
  end if
  if selfClass = 1 then
    c_farm__spdmg = 32
    c_farm__sprange = 360
    c_farm__spproj = 1
    c_farm__spmana = 18
  end if
  if selfClass = 2 then
    c_farm__spdmg = 42
    c_farm__sprange = 330
    c_farm__spproj = 1
    c_farm__spmana = 28
  end if
  if selfClass = 4 then
    c_farm__spdmg = 38
    c_farm__sprange = 90
    c_farm__spmana = 16
  end if
  if selfClass = 5 then
    c_farm__spdmg = 42
    c_farm__sprange = 90
    c_farm__spmana = 18
  end if
  if selfClass = 6 then
    c_farm__spdmg = 50
    c_farm__sprange = 400
    c_farm__spproj = 1
    c_farm__spmana = 22
  end if
  if selfClass = 7 then
    c_farm__spdmg = 48
    c_farm__sprange = 400
    c_farm__spproj = 1
    c_farm__spmana = 30
  end if
  if selfClass = 8 then
    c_farm__spdmg = 36
    c_farm__sprange = 280
    c_farm__spproj = 1
    c_farm__spmana = 24
  end if
  if selfClass = 9 then
    c_farm__spdmg = 45
    c_farm__sprange = 90
    c_farm__spmana = 0
  end if
end sub

' Records one object's HP into its ring slot as a repeating drop event
' (same attacker, same phase mod period, same amount: refresh; else shift).
sub c_farm__lhrecorddrop(slot, hp, per)
  if c_farm__fmseen(slot) = 0 or worldTick - c_farm__fmseen(slot) > k_config__cfgseenstale then
    c_farm__fmev1(slot) = 0
    c_farm__fmev2(slot) = 0
  else
    c_farm__od = c_farm__fmhp(slot) - hp
    if c_farm__od > 0 and c_farm__od < 256 then
      c_farm__onew = (worldTick - 1) * 256 + c_farm__od
      c_farm__osame = 0
      if c_farm__fmev1(slot) > 0 then
        if c_farm__fmev1(slot) mod 256 = c_farm__od and ((worldTick - 1) - c_farm__fmev1(slot) \ 256) mod per = 0 then
          c_farm__osame = 1
        end if
      end if
      if c_farm__osame = 1 then
        c_farm__fmev1(slot) = c_farm__onew
      else
        c_farm__fmev2(slot) = c_farm__fmev1(slot)
        c_farm__fmev1(slot) = c_farm__onew
      end if
    end if
  end if
  c_farm__fmhp(slot) = hp
  c_farm__fmseen(slot) = worldTick
end sub

' Records HP drops of visible enemy footmen as repeating events. Also counts
' footmen that were within chase distance last decision and are gone now
' (c_farm__lhvanished), for the missed-opportunity telemetry.
sub c_farm__lhupdatememory()
  c_farm__lhvanished = 0
  c_farm__oi = 0
  while c_farm__oi < c_farm__pvn
    if c_farm__pvid(c_farm__oi) > 0 then
      c_farm__oj = c_farm__fmslot((c_farm__pvid(c_farm__oi) - 1000) mod 448)
      c_farm__ogone = 1
      if c_farm__oj >= 0 then
        if c_farm__efid(c_farm__oj) = c_farm__pvid(c_farm__oi) then
          c_farm__ogone = 0
        end if
      end if
      c_farm__lhvanished = c_farm__lhvanished + c_farm__ogone
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__pvn = 0
  c_farm__ochase = selfAttackRange \ 1000 + k_config__cfgchaseku
  c_farm__oi = 0
  while c_farm__oi < c_farm__efn
    c_farm__lhrecorddrop((c_farm__efid(c_farm__oi) - 1000) mod 448, c_farm__efhp(c_farm__oi), k_config__cfgfootmanperiod)
    c_farm__odx = c_farm__efx(c_farm__oi) - c_farm__scselfx
    c_farm__ody = c_farm__efy(c_farm__oi) - c_farm__scselfy
    c_farm__pvid(c_farm__pvn) = 0
    if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= c_farm__ochase * c_farm__ochase then
      c_farm__pvid(c_farm__pvn) = c_farm__efid(c_farm__oi)
    end if
    c_farm__pvn = c_farm__pvn + 1
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' Counts attackers attached to each enemy footman this decision.
sub c_farm__lhattachers()
  c_farm__oi = 0
  while c_farm__oi < c_farm__efn
    c_farm__efatt(c_farm__oi) = 0
    c_farm__eftw(c_farm__oi) = 0
    c_farm__efhero(c_farm__oi) = 32
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__oi = 0
  while c_farm__oi < c_farm__afn
    if c_farm__aftgt(c_farm__oi) >= 1000 then
      c_farm__oj = c_farm__fmslot((c_farm__aftgt(c_farm__oi) - 1000) mod 448)
      if c_farm__oj >= 0 then
        if c_farm__efid(c_farm__oj) = c_farm__aftgt(c_farm__oi) then
          c_farm__odx = c_farm__afx(c_farm__oi) - c_farm__efx(c_farm__oj)
          c_farm__ody = c_farm__afy(c_farm__oi) - c_farm__efy(c_farm__oj)
          if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= 8100 then
            c_farm__efatt(c_farm__oj) = c_farm__efatt(c_farm__oj) + 1
          end if
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__oi = 0
  while c_farm__oi < c_farm__atn
    if c_farm__attgt(c_farm__oi) >= 1000 then
      c_farm__oj = c_farm__fmslot((c_farm__attgt(c_farm__oi) - 1000) mod 448)
      if c_farm__oj >= 0 then
        if c_farm__efid(c_farm__oj) = c_farm__attgt(c_farm__oi) then
          c_farm__eftw(c_farm__oj) = c_farm__atdmg(c_farm__oi)
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__oi = 0
  while c_farm__oi < c_farm__ahn
    if c_farm__ahtgt(c_farm__oi) >= 1000 then
      c_farm__oj = c_farm__fmslot((c_farm__ahtgt(c_farm__oi) - 1000) mod 448)
      if c_farm__oj >= 0 then
        if c_farm__efid(c_farm__oj) = c_farm__ahtgt(c_farm__oi) then
          c_farm__efhero(c_farm__oj) = c_farm__ahdmg(c_farm__oi) * 256 + c_farm__ahper(c_farm__oi)
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' Number of repeats of an event at tick evT with period per landing in
' [worldTick, worldTick + h]. Result in c_farm__evhits.
sub c_farm__lheventhits(evT, per, h)
  c_farm__evhits = (worldTick + h - evT) \ per - (worldTick - 1 - evT) \ per
end sub

' c_farm__lhinc = predicted damage to the object in ring slot s over the next h ticks
' from its recorded drop events alone (each repeating with period per).
sub c_farm__lheventinc(s, h, per)
  c_farm__lhinc = 0
  c_farm__ie = 0
  while c_farm__ie < 2
    if c_farm__ie = 0 then
      c_farm__iev = c_farm__fmev1(s)
    else
      c_farm__iev = c_farm__fmev2(s)
    end if
    if c_farm__iev > 0 then
      c_farm__it = c_farm__iev \ 256
      c_farm__ia = c_farm__iev mod 256
      if worldTick - c_farm__it <= k_config__cfgeventstale then
        c_farm__lheventhits(c_farm__it, per, h)
        c_farm__lhinc = c_farm__lhinc + c_farm__ia * c_farm__evhits
      end if
    end if
    c_farm__ie = c_farm__ie + 1
  wend
end sub

' c_farm__lhinc = predicted damage to enemy footman i over the next h ticks (inclusive
' of this tick's footman and tower updates, exclusive of my own hit).
sub c_farm__lhincoming(i, h)
  c_farm__lhinc = 0
  c_farm__irep = 0
  c_farm__v13_item_slot = (c_farm__efid(i) - 1000) mod 448
  c_farm__ie = 0
  while c_farm__ie < 2
    if c_farm__ie = 0 then
      c_farm__iev = c_farm__fmev1(c_farm__v13_item_slot)
    else
      c_farm__iev = c_farm__fmev2(c_farm__v13_item_slot)
    end if
    if c_farm__iev > 0 then
      c_farm__it = c_farm__iev \ 256
      c_farm__ia = c_farm__iev mod 256
      if worldTick - c_farm__it <= k_config__cfgeventstale then
        c_farm__iper = k_config__cfgfootmanperiod
        if c_farm__eftw(i) > 0 and c_farm__ia = c_farm__eftw(i) then
          c_farm__iper = k_config__cfgtowerperiod
        else
          if c_farm__efhero(i) \ 256 > 0 and c_farm__ia = c_farm__efhero(i) \ 256 then
            c_farm__iper = c_farm__efhero(i) mod 256
          else
            c_farm__irep = c_farm__irep + c_farm__ia \ k_config__cfgfootmanhit
          end if
        end if
        c_farm__lheventhits(c_farm__it, c_farm__iper, h)
        c_farm__lhinc = c_farm__lhinc + c_farm__ia * c_farm__evhits
      end if
    end if
    c_farm__ie = c_farm__ie + 1
  wend
  ' Attached footmen without a recorded hit yet: expected value.
  c_farm__iextra = c_farm__efatt(i) - c_farm__irep
  if c_farm__iextra > 0 then
    c_farm__lhinc = c_farm__lhinc + c_farm__iextra * k_config__cfgfootmanhit * (h + 8) \ k_config__cfgfootmanperiod
  end if
end sub

' Standoff point behind an anchor position (ax, ay) toward my fort, at distance
' back ku. Writes c_farm__lhholdx, c_farm__lhholdy in tiles.
sub c_farm__lhholdbehind(ax, ay, back)
  c_farm__hbdx = c_farm__scfortx - ax
  c_farm__hbdy = c_farm__scforty - ay
  c_farm__approxdist(c_farm__hbdx, c_farm__hbdy)
  if c_farm__add < 1 then
    c_farm__add = 1
  end if
  c_farm__lhholdx = (ax + c_farm__hbdx * back \ c_farm__add) \ 60
  c_farm__lhholdy = (ay + c_farm__hbdy * back \ c_farm__add) \ 60
end sub

' 1 if (x, y) in ku is within k_config__cfgtoweravoid of a visible living enemy tower that
' is not busy with a footman. Towers shoot footmen before heroes, so a tower whose
' target is a footman is safe to farm beside for now; the survive module handles
' the moment c_farm__it switches to me.
sub c_farm__lhnearenemytower(x, y)
  c_farm__ntnear = 0
  c_farm__ii = 0
  while c_farm__ii < c_farm__etn
    c_farm__idx = c_farm__etx(c_farm__ii) \ 64 - x
    c_farm__idy = c_farm__ety(c_farm__ii) - y
    c_farm__itg = c_farm__ettgt(c_farm__ii) \ 8
    if (c_farm__ettgt(c_farm__ii) \ 2) mod 4 = 4 and c_farm__idx * c_farm__idx + c_farm__idy * c_farm__idy <= k_config__cfgtoweravoid * k_config__cfgtoweravoid then
      ' Safe beside the tower when c_farm__it is busy with a footman, or idle with
      ' allied footmen inside its reach to absorb the first shots; the survive
      ' module leaves the moment c_farm__it targets me.
      if c_farm__itg = selfId then
        c_farm__ntnear = 1
      end if
      if c_farm__itg = 0 then
        c_farm__iallies = 0
        c_farm__ij = 0
        while c_farm__ij < c_farm__afn
          c_farm__iax = c_farm__afx(c_farm__ij) - c_farm__etx(c_farm__ii) \ 64
          c_farm__iay = c_farm__afy(c_farm__ij) - c_farm__ety(c_farm__ii)
          if c_farm__iax * c_farm__iax + c_farm__iay * c_farm__iay <= k_config__cfgtowerfarmalliesku * k_config__cfgtowerfarmalliesku then
            c_farm__iallies = c_farm__iallies + 1
          end if
          c_farm__ij = c_farm__ij + 1
        wend
        if c_farm__iallies < k_config__cfgtowerfarmallies then
          c_farm__ntnear = 1
        end if
      end if
    end if
    c_farm__ii = c_farm__ii + 1
  wend
end sub

sub c_farm__lhplan()
  c_farm__lhstate = 0
  c_farm__lhtargetid = 0
  c_farm__lhsecureslot = -1
  c_farm__lhsecureid = 0
  c_farm__lhpoisonid = 0
  c_farm__lhupdatememory()
  c_farm__lhattachers()
  c_farm__lhstrikespec()
  c_farm__lhrange = selfAttackRange \ 1000
  c_farm__lhreach = c_farm__lhrange - k_config__cfgrangeslack
  if c_farm__lhreach < 30 then
    c_farm__lhreach = 30
  end if
  c_farm__lhspeed = selfMoveSpeed \ 1000
  if c_farm__lhspeed < 1 then
    c_farm__lhspeed = 1
  end if
  c_farm__herodmgfor(selfClass, 1)
  c_farm__lhwindup = c_farm__hdper * 45 \ 100
  c_farm__lhmelee = 0
  if c_farm__lhrange < 120 then
    c_farm__lhmelee = 1
  end if
  c_farm__lhstrikeready = 0
  if k_config__cfgspellsecure = 1 and c_farm__spdmg > 0 then
    if abilityCharges(1) > 0 and abilityCooldown(1) = 0 and selfMana >= c_farm__spmana then
      c_farm__lhstrikeready = 1
    end if
  end if
  c_farm__lhpoisonready = 0
  if k_config__cfgpoisonsecure = 1 and c_farm__shpoisonslot >= 0 then
    c_farm__lhpoisonready = 1
  end if

  ' My lane and whether any enemy footman is visible in c_farm__it.
  c_farm__lhmylane = 1
  c_farm__lhslot = (selfId - 100) mod 5
  if c_farm__lhslot < 2 then
    c_farm__lhmylane = 0
  end if
  if c_farm__lhslot > 2 then
    c_farm__lhmylane = 2
  end if
  c_farm__lhdiag = (mapWidth - 1) * 60
  c_farm__lhlanecount = 0
  c_farm__lhlocalcount = 0
  c_farm__lhchase = c_farm__lhreach + k_config__cfgchaseku
  c_farm__oi = 0
  while c_farm__oi < c_farm__efn
    c_farm__odx = c_farm__efx(c_farm__oi) - c_farm__scselfx
    c_farm__ody = c_farm__efy(c_farm__oi) - c_farm__scselfy
    if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= c_farm__lhchase * c_farm__lhchase then
      c_farm__lhlocalcount = c_farm__lhlocalcount + 1
    end if
    c_farm__lhs = c_farm__efx(c_farm__oi) + c_farm__efy(c_farm__oi) - c_farm__lhdiag
    c_farm__lhl = 1
    if c_farm__lhs <= -900 then
      c_farm__lhl = 0
    end if
    if c_farm__lhs >= 900 then
      c_farm__lhl = 2
    end if
    if c_farm__lhl = c_farm__lhmylane then
      c_farm__lhlanecount = c_farm__lhlanecount + 1
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  c_farm__lhbest = -1
  c_farm__lhbestscore = 2147483647
  c_farm__lhanchor = -1
  c_farm__lhanchorhp = 2147483647
  c_farm__lhanchorpos = 0
  c_farm__lhanchord2 = 2147483647
  c_farm__oi = 0
  while c_farm__oi < c_farm__efn
    c_farm__odx = c_farm__efx(c_farm__oi) - c_farm__scselfx
    c_farm__ody = c_farm__efy(c_farm__oi) - c_farm__scselfy
    c_farm__od2 = c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody
    c_farm__lhinr = 0
    if c_farm__od2 <= c_farm__lhreach * c_farm__lhreach then
      c_farm__lhinr = 1
    end if
    c_farm__lhnearenemytower(c_farm__efx(c_farm__oi), c_farm__efy(c_farm__oi))
    ' Anchor tiers: footmen already within chase distance (any lane) come first,
    ' then footmen in my lane, then anything visible. This keeps the hero on
    ' local opportunities without wandering into another lane for a distant one.
    c_farm__lhchase = c_farm__lhreach + k_config__cfgchaseku
    c_farm__lhlocal = 0
    if c_farm__od2 <= c_farm__lhchase * c_farm__lhchase then
      c_farm__lhlocal = 1
    end if
    c_farm__lhlaneok = 1
    if c_farm__lhlocal = 0 then
      if c_farm__lhlocalcount > 0 then
        c_farm__lhlaneok = 0
      else
        if c_farm__lhlanecount > 0 then
          c_farm__lhs = c_farm__efx(c_farm__oi) + c_farm__efy(c_farm__oi) - c_farm__lhdiag
          c_farm__lhl = 1
          if c_farm__lhs <= -900 then
            c_farm__lhl = 0
          end if
          if c_farm__lhs >= 900 then
            c_farm__lhl = 2
          end if
          if c_farm__lhl <> c_farm__lhmylane then
            c_farm__lhlaneok = 0
          end if
        end if
      end if
    end if
    ' Only footmen I can reach quickly may be ordered; chasing across the map
    ' abandons the lane and never converts. Footmen an ally is already hitting
    ' are theirs.
    c_farm__lhorderok = c_farm__lhlocal
    if k_config__cfgavoidallytarget = 1 then
      c_farm__oj = 0
      while c_farm__oj < c_farm__ahn
        if c_farm__ahtgt(c_farm__oj) = c_farm__efid(c_farm__oi) then
          c_farm__lhorderok = 0
        end if
        c_farm__oj = c_farm__oj + 1
      wend
    end if
    if c_farm__ntnear = 0 and c_farm__lhlaneok = 1 then
      ' Horizon for my basic hit on this footman.
      if c_farm__lhinr = 1 then
        ' A reported cooldown of C lands during hero update C - 1 ticks from now.
        c_farm__lhh = selfAttackCooldown - 1
        if c_farm__lhh < 0 then
          c_farm__lhh = 0
        end if
      else
        c_farm__approxdist(c_farm__odx, c_farm__ody)
        c_farm__lhh = (c_farm__add - c_farm__lhreach) \ c_farm__lhspeed + c_farm__lhwindup
      end if
      c_farm__lhincoming(c_farm__oi, c_farm__lhh)
      c_farm__lhpred = c_farm__efhp(c_farm__oi) - c_farm__lhinc
      c_farm__lhwindow = selfAttackDamage
      if c_farm__efid(c_farm__oi) = c_farm__lhstickid then
        c_farm__lhwindow = c_farm__lhwindow + k_config__cfgstickslack
      end if
      if c_farm__lhorderok = 1 and c_farm__lhpred > 0 and c_farm__lhpred <= c_farm__lhwindow then
        c_farm__lhscore = c_farm__lhh * 10 + c_farm__lhpred
        if c_farm__efid(c_farm__oi) = c_farm__lhstickid then
          c_farm__lhscore = c_farm__lhscore - k_config__cfgstickbonus
        end if
        if c_farm__lhscore < c_farm__lhbestscore then
          c_farm__lhbestscore = c_farm__lhscore
          c_farm__lhbest = c_farm__oi
        end if
      end if
      if c_farm__lhorderok = 1 and c_farm__lhpred <= 0 then
        ' The basic attack is too slow: try an instant source.
        if c_farm__lhpoisonid = 0 and c_farm__lhpoisonready = 1 and c_farm__lhinr = 1 and c_farm__efhp(c_farm__oi) <= 35 then
          c_farm__lhpoisonid = c_farm__efid(c_farm__oi)
        else
          if c_farm__lhsecureid = 0 and c_farm__lhstrikeready = 1 and c_farm__od2 <= c_farm__sprange * c_farm__sprange then
            if c_farm__spproj = 1 then
              ' Projectile: at least one tick of travel, resolves after this tick's units.
              c_farm__approxdist(c_farm__odx, c_farm__ody)
              c_farm__lhsh = (c_farm__add + 44) \ 45
              if c_farm__lhsh < 1 then
                c_farm__lhsh = 1
              end if
              c_farm__lhincoming(c_farm__oi, c_farm__lhsh)
              c_farm__lhpreds = c_farm__efhp(c_farm__oi) - c_farm__lhinc
            else
              ' Melee cast resolves inside this decision, before any creep swings.
              c_farm__lhpreds = c_farm__efhp(c_farm__oi)
            end if
            if c_farm__lhpreds > 0 and c_farm__lhpreds <= c_farm__spdmg then
              c_farm__lhsecureid = c_farm__efid(c_farm__oi)
              c_farm__lhsecureslot = 1
            end if
          end if
        end if
      end if
      ' Anchor: the footman with the smallest positive predicted HP, else the nearest.
      if c_farm__lhpred > 0 then
        if c_farm__lhanchorpos = 0 or c_farm__lhpred < c_farm__lhanchorhp then
          c_farm__lhanchorpos = 1
          c_farm__lhanchorhp = c_farm__lhpred
          c_farm__lhanchor = c_farm__oi
        end if
      else
        if c_farm__lhanchorpos = 0 and c_farm__od2 < c_farm__lhanchord2 then
          c_farm__lhanchord2 = c_farm__od2
          c_farm__lhanchor = c_farm__oi
          c_farm__lhanchorhp = c_farm__lhpred
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend

  if c_farm__lhanchor >= 0 then
    c_farm__lhstate = 1
    if c_farm__lhmelee = 1 then
      c_farm__lhholdbehind(c_farm__efx(c_farm__lhanchor), c_farm__efy(c_farm__lhanchor), k_config__cfgmeleestandoff)
    else
      c_farm__lhholdbehind(c_farm__efx(c_farm__lhanchor), c_farm__efy(c_farm__lhanchor), c_farm__lhrange - k_config__cfgstandoffback)
    end if
  end if
  if c_farm__lhbest >= 0 then
    c_farm__lhstate = 2
    c_farm__lhtargetid = c_farm__efid(c_farm__lhbest)
  end if
  ' Never poison the footman the basic attack is about to take.
  if c_farm__lhpoisonid <> 0 and c_farm__lhpoisonid = c_farm__lhtargetid then
    c_farm__lhpoisonid = 0
  end if
  if c_farm__lhsecureid <> 0 and c_farm__lhsecureid = c_farm__lhtargetid then
    c_farm__lhsecureid = 0
    c_farm__lhsecureslot = -1
  end if
end sub

' ===== module 22_ksteal.bas =====
' Module: ksteal (prefix ks). Take the killing blow on enemy heroes and structures
' that teammates are already fighting; never start a fight alone.
'
' Inputs: scan arrays (eh* enemy heroes, et* enemy structures, ah* allied heroes,
' af* allied footmen), the drop-event rings (c_farm__lhrecorddrop, c_farm__lheventinc), self data,
' cfg knobs. Outputs, rewritten every decision:
'   c_farm__kstargetid  object to hit now (enemy hero or structure), or 0
'   c_farm__ksslot      -1 basic attack, else the ability slot to castTarget on c_farm__kstargetid
'   c_farm__kshold      1 while an enemy hero is within k_config__cfgksholdku: farm by windows only,
'               so the engine cannot auto-cast the kit on footmen
' Telemetry: c_farm__ksherowin, c_farm__kscasts, c_farm__ksstructwin.
'
' Method: the footman last-hit prediction on hero and structure targets. A hero is
' "engaged" when an allied hero within k_config__cfgksallyku of c_farm__it targets c_farm__it or its HP
' dropped within the last two seconds. For an engaged target in reach, predict HP
' at the landing tick of my basic hit and of each ready strike (melee: now;
' projectile: 45 ku per tick; short area casts: their delay). A window is
' 0 < predicted <= damage; prefer the spell with the earliest landing, then the
' basic hit. Structures: engaged when their HP is dropping and a footman shields
' me from the tower; basic hit only.

' Strike slot table for my class: ksN entries in ks* scalars via c_farm__ksspec(k):
' c_farm__spdmg, c_farm__sprange (ku), c_farm__spcast (ticks), c_farm__spproj (1 projectile), c_farm__spmana, c_farm__spslot.
' Slot 0 passives that are free projectiles are included (Ranger, Crossbowman,
' Lich). Ring, line and capsule ultimates and long casts are excluded: the
' target moves before impact.
sub c_farm__ksspec(k)
  c_farm__spdmg = 0
  c_farm__spslot = -1
  c_farm__spcast = 0
  c_farm__spproj = 0
  c_farm__spmana = 0
  c_farm__sprange = 0
  if selfClass = 0 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 40
      c_farm__sprange = 90
      c_farm__spmana = 20
    end if
    if k = 1 then
      c_farm__spslot = 3
      c_farm__spdmg = 90
      c_farm__sprange = 110
      c_farm__spcast = 6
      c_farm__spmana = 70
    end if
  end if
  if selfClass = 1 then
    if k = 0 then
      c_farm__spslot = 0
      c_farm__spdmg = 16
      c_farm__sprange = 420
      c_farm__spproj = 1
    end if
    if k = 1 then
      c_farm__spslot = 1
      c_farm__spdmg = 32
      c_farm__sprange = 360
      c_farm__spproj = 1
      c_farm__spmana = 18
    end if
  end if
  if selfClass = 2 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 42
      c_farm__sprange = 330
      c_farm__spproj = 1
      c_farm__spmana = 28
    end if
  end if
  if selfClass = 4 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 38
      c_farm__sprange = 90
      c_farm__spmana = 16
    end if
    if k = 1 then
      c_farm__spslot = 2
      c_farm__spdmg = 52
      c_farm__sprange = 120
      c_farm__spcast = 6
      c_farm__spmana = 28
    end if
    if k = 2 then
      c_farm__spslot = 3
      c_farm__spdmg = 100
      c_farm__sprange = 300
      c_farm__spproj = 1
      c_farm__spmana = 65
    end if
  end if
  if selfClass = 5 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 42
      c_farm__sprange = 90
      c_farm__spmana = 18
    end if
  end if
  if selfClass = 6 then
    if k = 0 then
      c_farm__spslot = 0
      c_farm__spdmg = 20
      c_farm__sprange = 420
      c_farm__spproj = 1
    end if
    if k = 1 then
      c_farm__spslot = 1
      c_farm__spdmg = 50
      c_farm__sprange = 400
      c_farm__spproj = 1
      c_farm__spmana = 22
    end if
    if k = 2 then
      c_farm__spslot = 2
      c_farm__spdmg = 68
      c_farm__sprange = 360
      c_farm__spcast = 12
      c_farm__spmana = 40
    end if
  end if
  if selfClass = 7 then
    if k = 0 then
      c_farm__spslot = 0
      c_farm__spdmg = 14
      c_farm__sprange = 360
      c_farm__spproj = 1
    end if
    if k = 1 then
      c_farm__spslot = 1
      c_farm__spdmg = 48
      c_farm__sprange = 400
      c_farm__spproj = 1
      c_farm__spmana = 30
    end if
  end if
  if selfClass = 8 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 36
      c_farm__sprange = 280
      c_farm__spproj = 1
      c_farm__spmana = 24
    end if
  end if
  if selfClass = 9 then
    if k = 0 then
      c_farm__spslot = 1
      c_farm__spdmg = 45
      c_farm__sprange = 90
    end if
    if k = 1 then
      c_farm__spslot = 2
      c_farm__spdmg = 40
      c_farm__sprange = 150
      c_farm__spcast = 12
      c_farm__spmana = 12
    end if
  end if
end sub

' Evaluates one target: (c_farm__ix, c_farm__iy) ku, ring slot iS, HP c_farm__ihp, attacker period c_farm__iper,
' structure flag c_farm__istruct. Writes c_farm__kscandslot (-1 basic, -2 none) and c_farm__kscandh
' (landing ticks) for the earliest converting window.
sub c_farm__kswindow()
  c_farm__kscandslot = -2
  c_farm__kscandh = 100000
  c_farm__idx = c_farm__ix - c_farm__scselfx
  c_farm__idy = c_farm__iy - c_farm__scselfy
  c_farm__id2 = c_farm__idx * c_farm__idx + c_farm__idy * c_farm__idy
  c_farm__ireach = selfAttackRange \ 1000 - k_config__cfgrangeslack
  ' Basic hit: in range now, or within chase distance (walk in, then windup).
  c_farm__ih = -1
  if c_farm__id2 <= c_farm__ireach * c_farm__ireach then
    c_farm__ih = selfAttackCooldown - 1
    if c_farm__ih < 0 then
      c_farm__ih = 0
    end if
  else
    if c_farm__istruct = 0 and c_farm__id2 <= (c_farm__ireach + k_config__cfgchaseku) * (c_farm__ireach + k_config__cfgchaseku) then
      c_farm__approxdist(c_farm__idx, c_farm__idy)
      c_farm__herodmgfor(selfClass, 1)
      c_farm__ih = (c_farm__add - c_farm__ireach) \ (selfMoveSpeed \ 1000 + 1) + c_farm__hdper * 45 \ 100
    end if
  end if
  if c_farm__ih >= 0 then
    c_farm__lheventinc(c_farm__v13_item_slot, c_farm__ih, c_farm__iper)
    if c_farm__ihp - c_farm__lhinc > 0 and c_farm__ihp - c_farm__lhinc <= selfAttackDamage then
      c_farm__kscandslot = -1
      c_farm__kscandh = c_farm__ih
    end if
  end if
  if c_farm__istruct = 1 then
    exit sub
  end if
  ' Spells: each ready strike whose predicted damage window is open.
  c_farm__ik = 0
  while c_farm__ik < 3
    c_farm__ksspec(c_farm__ik)
    if c_farm__spslot >= 0 then
      if c_farm__id2 <= c_farm__sprange * c_farm__sprange and abilityCharges(c_farm__spslot) > 0 and abilityCooldown(c_farm__spslot) = 0 and selfMana >= c_farm__spmana then
        c_farm__ish = c_farm__spcast
        if c_farm__spproj = 1 then
          c_farm__approxdist(c_farm__idx, c_farm__idy)
          c_farm__ish = (c_farm__add + 44) \ 45
          if c_farm__ish < 1 then
            c_farm__ish = 1
          end if
        end if
        c_farm__lheventinc(c_farm__v13_item_slot, c_farm__ish, c_farm__iper)
        if c_farm__ihp - c_farm__lhinc > 0 and c_farm__ihp - c_farm__lhinc <= c_farm__spdmg and c_farm__ish < c_farm__kscandh then
          c_farm__kscandslot = c_farm__spslot
          c_farm__kscandh = c_farm__ish
        end if
      end if
    end if
    c_farm__ik = c_farm__ik + 1
  wend
end sub

sub c_farm__ksplan()
  c_farm__kstargetid = 0
  c_farm__ksslot = -1
  c_farm__kshold = 0
  if k_config__cfgksteal = 0 then
    exit sub
  end if
  c_farm__ksbesth = 100000
  ' Enemy heroes: record drops, hold spells when one is near, find windows.
  c_farm__oi = 0
  while c_farm__oi < c_farm__ehn
    c_farm__oid = c_farm__ehmeta(c_farm__oi) \ 65536
    c_farm__lhringslot(c_farm__oid)
    c_farm__lhrecorddrop(c_farm__rsslot, c_farm__ehhp(c_farm__oi) \ 16, k_config__cfgheroperiod)
    c_farm__ix = c_farm__ehpos(c_farm__oi) \ 8192
    c_farm__iy = c_farm__ehpos(c_farm__oi) mod 8192
    c_farm__odx = c_farm__ix - c_farm__scselfx
    c_farm__ody = c_farm__iy - c_farm__scselfy
    if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= k_config__cfgksholdku * k_config__cfgksholdku then
      c_farm__kshold = 1
    end if
    ' Engaged: an allied hero near c_farm__it is attacking c_farm__it, or c_farm__it took a hit lately.
    c_farm__oeng = 0
    c_farm__oj = 0
    while c_farm__oj < c_farm__ahn
      if c_farm__ahtgt(c_farm__oj) = c_farm__oid then
        c_farm__oax = c_farm__ahx(c_farm__oj) - c_farm__ix
        c_farm__oay = c_farm__ahy(c_farm__oj) - c_farm__iy
        if c_farm__oax * c_farm__oax + c_farm__oay * c_farm__oay <= k_config__cfgksallyku * k_config__cfgksallyku then
          c_farm__oeng = 1
        end if
      end if
      c_farm__oj = c_farm__oj + 1
    wend
    if c_farm__fmev1(c_farm__rsslot) > 0 then
      if worldTick - c_farm__fmev1(c_farm__rsslot) \ 256 <= k_config__cfgksrecentticks then
        c_farm__oeng = 1
      end if
    end if
    if c_farm__oeng = 1 and selfHp * 100 >= selfMaxHp * k_config__cfgksminhppct then
      c_farm__v13_item_slot = c_farm__rsslot
      c_farm__ihp = c_farm__ehhp(c_farm__oi) \ 16
      c_farm__iper = k_config__cfgheroperiod
      c_farm__istruct = 0
      c_farm__kswindow()
      if c_farm__kscandslot > -2 and c_farm__kscandh < c_farm__ksbesth then
        c_farm__ksbesth = c_farm__kscandh
        c_farm__kstargetid = c_farm__oid
        c_farm__ksslot = c_farm__kscandslot
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  if c_farm__kstargetid <> 0 then
    c_farm__ksherowin = c_farm__ksherowin + 1
    exit sub
  end if
  ' Structures: only the finishing hit, only with a footman shield, only while
  ' someone else is already bringing c_farm__it down.
  c_farm__oi = 0
  while c_farm__oi < c_farm__etn
    if c_farm__ettgt(c_farm__oi) mod 2 = 1 then
      c_farm__oid = c_farm__etx(c_farm__oi) mod 64
      c_farm__lhringslot(c_farm__oid)
      c_farm__lhrecorddrop(c_farm__rsslot, c_farm__ethp(c_farm__oi), k_config__cfgfootmanperiod)
      if c_farm__fmev1(c_farm__rsslot) > 0 and c_farm__ettgt(c_farm__oi) \ 8 <> selfId then
        if worldTick - c_farm__fmev1(c_farm__rsslot) \ 256 <= k_config__cfgksrecentticks then
          c_farm__ix = c_farm__etx(c_farm__oi) \ 64
          c_farm__iy = c_farm__ety(c_farm__oi)
          c_farm__v13_item_slot = c_farm__rsslot
          c_farm__ihp = c_farm__ethp(c_farm__oi)
          c_farm__iper = k_config__cfgfootmanperiod
          c_farm__istruct = 1
          c_farm__kswindow()
          if c_farm__kscandslot = -1 then
            c_farm__kstargetid = c_farm__oid
            c_farm__ksslot = -1
            c_farm__ksstructwin = c_farm__ksstructwin + 1
            exit sub
          end if
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' ===== module 25_punish.bas =====
' Module: punish (prefix pn). Attack an enemy hero that farms inside our tower's
' reach. Towers shoot footmen first, so an enemy hero standing beside our tower
' with the wave dying is about to take tower fire; joining in converts that into a
' hero kill (150 XP, 100 gold, six footmen's worth) and the engine auto-casts the
' full kit on the target once attackTarget is set.
'
' Inputs: scan arrays (at* allied towers, eh* enemy heroes), self data, cfg knobs.
' Output: c_farm__pntargetid (enemy hero id to attack now, or 0). State: c_farm__pntargetid
' persists with hysteresis: enter when an allied tower is firing at the hero within
' k_config__cfgpunishenterku of c_farm__it, exit once c_farm__it is farther than k_config__cfgpunishexitku from the tower.
' Telemetry (main): c_farm__pnstarts, c_farm__pnticks.

' pnD2 = squared distance (ku) from hero index i to the nearest living allied tower;
' c_farm__pnshot = 1 if an allied tower is currently firing at that hero (its target id).
sub c_farm__pntowerdist(i)
  c_farm__id2 = 2147483647
  c_farm__pnshot = 0
  c_farm__ix = c_farm__ehpos(i) \ 8192
  c_farm__iy = c_farm__ehpos(i) mod 8192
  c_farm__iid = c_farm__ehmeta(i) \ 65536
  c_farm__ij = 0
  while c_farm__ij < c_farm__atn
    c_farm__idx = c_farm__atx(c_farm__ij) - c_farm__ix
    c_farm__idy = c_farm__aty(c_farm__ij) - c_farm__iy
    if c_farm__idx * c_farm__idx + c_farm__idy * c_farm__idy < c_farm__id2 then
      c_farm__id2 = c_farm__idx * c_farm__idx + c_farm__idy * c_farm__idy
    end if
    if c_farm__attgt(c_farm__ij) = c_farm__iid then
      c_farm__pnshot = 1
    end if
    c_farm__ij = c_farm__ij + 1
  wend
end sub

' c_farm__pnchaseok = 1 if chasing enemy hero index i beyond tower cover is worth c_farm__it.
sub c_farm__pnchasecheck(i)
  c_farm__pnchaseok = 0
  c_farm__ix = c_farm__ehpos(i) \ 8192
  c_farm__iy = c_farm__ehpos(i) mod 8192
  c_farm__idx = c_farm__ix - c_farm__scselfx
  c_farm__idy = c_farm__iy - c_farm__scselfy
  c_farm__id2 = c_farm__idx * c_farm__idx + c_farm__idy * c_farm__idy
  if c_farm__id2 > k_config__cfgpunishchasemaxku * k_config__cfgpunishchasemaxku then
    exit sub
  end if
  ' DPS in HP per tick times 100: mine plus any allied hero adjacent to the target.
  c_farm__herodmgfor(selfClass, selfLevel)
  c_farm__idps = selfAttackDamage * 100 \ c_farm__hdper
  c_farm__ially = 0
  c_farm__ij = 0
  while c_farm__ij < c_farm__ahn
    c_farm__iax = c_farm__ahx(c_farm__ij) - c_farm__ix
    c_farm__iay = c_farm__ahy(c_farm__ij) - c_farm__iy
    if c_farm__iax * c_farm__iax + c_farm__iay * c_farm__iay <= k_config__cfgpunishallyku * k_config__cfgpunishallyku then
      c_farm__idps = c_farm__idps + c_farm__ahdmg(c_farm__ij) * 100 \ c_farm__ahper(c_farm__ij)
      c_farm__ially = 1
    end if
    c_farm__ij = c_farm__ij + 1
  wend
  if c_farm__idps < 1 then
    exit sub
  end if
  c_farm__ihp = c_farm__ehhp(i) \ 16
  c_farm__iticks = c_farm__ihp * 100 \ c_farm__idps
  ' Closing time: distance beyond my range at my speed, only if I am faster.
  c_farm__herospeedfor(c_farm__ehhp(i) mod 16)
  c_farm__ispeed = selfMoveSpeed \ 1000
  c_farm__ireach = selfAttackRange \ 1000
  if c_farm__id2 > c_farm__ireach * c_farm__ireach then
    if c_farm__ispeed <= c_farm__hsmove and c_farm__ially = 0 then
      exit sub
    end if
    c_farm__approxdist(c_farm__idx, c_farm__idy)
    c_farm__igap = c_farm__add - c_farm__ireach
    c_farm__iclose = c_farm__ispeed - c_farm__hsmove
    if c_farm__iclose < 1 then
      c_farm__iclose = 1
    end if
    c_farm__iticks = c_farm__iticks + c_farm__igap \ c_farm__iclose
  end if
  if c_farm__iticks <= k_config__cfgpunishchaseticks then
    c_farm__pnchaseok = 1
  end if
end sub

sub c_farm__pnplan()
  if k_config__cfgpunish = 0 then
    c_farm__pntargetid = 0
    exit sub
  end if
  ' Keep the current target while c_farm__it stays near our tower and I am healthy, or
  ' while chasing c_farm__it is cheap: expected time to kill from its HP over my DPS
  ' plus an adjacent teammate's, and c_farm__it cannot outrun me unless already in range.
  if c_farm__pntargetid <> 0 then
    c_farm__pnkeep = 0
    c_farm__oi = 0
    while c_farm__oi < c_farm__ehn
      if c_farm__ehmeta(c_farm__oi) \ 65536 = c_farm__pntargetid then
        c_farm__pntowerdist(c_farm__oi)
        if c_farm__od2 <= k_config__cfgpunishexitku * k_config__cfgpunishexitku then
          c_farm__pnkeep = 1
        else
          if k_config__cfgpunish = 1 and k_config__cfgpunishchase = 1 then
            c_farm__pnchasecheck(c_farm__oi)
            if c_farm__pnchaseok = 1 then
              c_farm__pnkeep = 1
              c_farm__pnchaseticks = c_farm__pnchaseticks + 1
            end if
          end if
        end if
      end if
      c_farm__oi = c_farm__oi + 1
    wend
    if selfHp * 100 < selfMaxHp * k_config__cfgpunishstophppct then
      c_farm__pnkeep = 0
    end if
    if c_farm__pnkeep = 0 then
      c_farm__pntargetid = 0
    end if
  end if
  if c_farm__pntargetid <> 0 then
    exit sub
  end if
  if selfHp * 100 < selfMaxHp * k_config__cfgpunishminhppct then
    exit sub
  end if
  ' Start on the lowest-HP enemy hero inside our tower's reach and within engage range.
  c_farm__pnbesthp = 2147483647
  c_farm__oi = 0
  while c_farm__oi < c_farm__ehn
    c_farm__pntowerdist(c_farm__oi)
    ' A tower firing at the hero is the signal: c_farm__it stands inside tower range with
    ' no footman left to absorb the shots. Distance alone is not enough (heroes
    ' farm at the edge of tower range untouched).
    if c_farm__pnshot = 1 and c_farm__od2 <= k_config__cfgpunishenterku * k_config__cfgpunishenterku then
      c_farm__odx = c_farm__ehpos(c_farm__oi) \ 8192 - c_farm__scselfx
      c_farm__ody = c_farm__ehpos(c_farm__oi) mod 8192 - c_farm__scselfy
      if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= k_config__cfgpunishengageku * k_config__cfgpunishengageku then
        if c_farm__ehhp(c_farm__oi) < c_farm__pnbesthp then
          c_farm__pnbesthp = c_farm__ehhp(c_farm__oi)
          c_farm__pntargetid = c_farm__ehmeta(c_farm__oi) \ 65536
        end if
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' ===== module 30_survive.bas =====
' Module: survive (prefix sv). Minimal self-preservation with priority over farming.
'
' Outputs, rewritten every decision:
'   c_farm__svretreat  1 when the hero should walk back toward its fort instead of farming
'   c_farm__svretx, c_farm__svrety  retreat tile
'   c_farm__svhealslot  inventory slot of a healing consumable to use now, or -1
' State: c_farm__svlow (hysteresis flag). Telemetry: svRetreats (orders issued).

sub c_farm__svplan()
  c_farm__svretreat = 0
  c_farm__svhealslot = -1
  if selfHp * 100 < selfMaxHp * k_config__cfgretreatpct then
    c_farm__svlow = 1
  end if
  if selfHp * 100 >= selfMaxHp * k_config__cfgrecoverpct then
    c_farm__svlow = 0
  end if
  ' An enemy hero attacking me, or one close while I am not healthy, is a fight
  ' this module does not take: a larger policy decides fights, this one farms.
  c_farm__svthreat = 0
  c_farm__oi = 0
  while c_farm__oi < c_farm__ehn
    c_farm__odx = c_farm__ehpos(c_farm__oi) \ 8192 - c_farm__scselfx
    c_farm__ody = c_farm__ehpos(c_farm__oi) mod 8192 - c_farm__scselfy
    c_farm__od2 = c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody
    if c_farm__ehmeta(c_farm__oi) mod 65536 = selfId and c_farm__od2 <= k_config__cfgherohuntrange * k_config__cfgherohuntrange then
      c_farm__svthreat = 1
    end if
    if selfHp * 100 < selfMaxHp * 60 and c_farm__od2 <= k_config__cfgherofearrange * k_config__cfgherofearrange then
      c_farm__svthreat = 1
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  ' An enemy tower shooting me: leave its range now (30 damage per second).
  c_farm__svtower = 0
  c_farm__oi = 0
  while c_farm__oi < c_farm__etn
    if c_farm__ettgt(c_farm__oi) \ 8 = selfId then
      c_farm__svtower = 1
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  ' HP does not regenerate, so retreating without a heal only forfeits farm:
  ' retreat for a hero or tower threat, or when low with a heal in hand to drink.
  ' While punishing an overextended enemy under our tower, the tower is on our
  ' side: only HP decides.
  if c_farm__pntargetid <> 0 and c_farm__svtower = 0 then
    c_farm__svthreat = 0
  end if
  if c_farm__svthreat = 1 or c_farm__svtower = 1 or (c_farm__svlow = 1 and c_farm__shhealslot >= 0) then
    c_farm__svretreat = 1
    c_farm__odx = c_farm__scfortx - c_farm__scselfx
    c_farm__ody = c_farm__scforty - c_farm__scselfy
    c_farm__approxdist(c_farm__odx, c_farm__ody)
    if c_farm__add < 1 then
      c_farm__add = 1
    end if
    c_farm__svstep = k_config__cfgretreatstep
    if c_farm__svtower = 1 then
      c_farm__svstep = k_config__cfgtowerescape
    end if
    if c_farm__svstep > c_farm__add then
      c_farm__svstep = c_farm__add
    end if
    c_farm__svretx = (c_farm__scselfx + c_farm__odx * c_farm__svstep \ c_farm__add) \ 60
    c_farm__svrety = (c_farm__scselfy + c_farm__ody * c_farm__svstep \ c_farm__add) \ 60
  end if
  if selfHp * 100 < selfMaxHp * 55 then
    if c_farm__shhealslot >= 0 then
      c_farm__svhealslot = c_farm__shhealslot
    end if
  end if
end sub

' ===== module 40_shop.bas =====
' Module: shop (prefix sh). Inventory scan and damage-first purchasing.
'
' c_farm__shscaninventory: c_farm__shpoisonslot, c_farm__shhealslot (-1 if none), c_farm__shpoisoncount, c_farm__shempty
' c_farm__shbuy: spends gold; records c_farm__shspent (gold spent this decision, for the
'   telemetry gold accounting in main).
'
' Order: gauntlets (+4) early, then the biggest damage items, then HP; poison is
' stocked only while a footman takes two hits. The goal is a level and item
' advantage over the enemy heroes: every 15 gold of farm becomes stats.

sub c_farm__shscaninventory()
  c_farm__shpoisonslot = -1
  c_farm__shhealslot = -1
  c_farm__shpoisoncount = 0
  c_farm__shempty = 0
  c_farm__shhasgauntlets = 0
  c_farm__shhasdagger = 0
  c_farm__shhassword = 0
  c_farm__shhasaxe = 0
  c_farm__shhasbow = 0
  c_farm__shhascrossbow = 0
  c_farm__shhaswand = 0
  c_farm__shhasbook = 0
  c_farm__shhasstaff = 0
  c_farm__shhasarmor = 0
  c_farm__shhaspauldrons = 0
  c_farm__oi = 0
  while c_farm__oi < 6
    c_farm__oid = itemId(c_farm__oi)
    if c_farm__oid = 0 then
      c_farm__shempty = c_farm__shempty + 1
    end if
    if c_farm__oid = 4 then
      c_farm__shpoisonslot = c_farm__oi
      c_farm__shpoisoncount = itemCount(c_farm__oi)
    end if
    if c_farm__oid = 1 or c_farm__oid = 2 then
      c_farm__shhealslot = c_farm__oi
    end if
    if c_farm__oid = 7 then
      c_farm__shhasgauntlets = 1
    end if
    if c_farm__oid = 11 then
      c_farm__shhasdagger = 1
    end if
    if c_farm__oid = 13 then
      c_farm__shhassword = 1
    end if
    if c_farm__oid = 14 then
      c_farm__shhasbow = 1
    end if
    if c_farm__oid = 18 then
      c_farm__shhasaxe = 1
    end if
    if c_farm__oid = 19 then
      c_farm__shhascrossbow = 1
    end if
    if c_farm__oid = 12 then
      c_farm__shhaswand = 1
    end if
    if c_farm__oid = 20 then
      c_farm__shhasbook = 1
    end if
    if c_farm__oid = 17 then
      c_farm__shhasstaff = 1
    end if
    if c_farm__oid = 16 then
      c_farm__shhasarmor = 1
    end if
    if c_farm__oid = 15 then
      c_farm__shhaspauldrons = 1
    end if
    c_farm__oi = c_farm__oi + 1
  wend
end sub

' Buys one item id if affordable with the gold left this decision.
sub c_farm__shtrybuy(id, cost)
  if c_farm__shgold >= cost then
    if buyItem(id) = 1 then
      c_farm__shgold = c_farm__shgold - cost
      c_farm__shspent = c_farm__shspent + cost
    end if
  end if
end sub

sub c_farm__shbuy()
  c_farm__shspent = 0
  c_farm__shgold = selfGold
  ' Poison while a footman still needs two hits: a stocked poison converts a lost
  ' last hit into 25 XP. Once basic damage one-shots footmen the slot is worth
  ' more as equipment.
  if selfAttackDamage < 60 and c_farm__shpoisoncount < k_config__cfgpoisonstock and c_farm__shgold >= k_config__cfgpoisonmingold then
    c_farm__shtrybuy(4, 40)
  end if
  ' A heal when hurt, before gear: HP never regenerates.
  if selfHp * 100 < selfMaxHp * 55 and c_farm__shhealslot < 0 then
    c_farm__shtrybuy(2, 50)
  end if
  ' Equipment fills every slot but one, which stays free for a heal (or the poison
  ' while c_farm__it is still stocked). Income must become stats, not a gold balance: the
  ' best-farming seats used to end games with 380 gold unspent.
  c_farm__shfree = c_farm__shempty
  if c_farm__shhealslot >= 0 then
    c_farm__shfree = c_farm__shfree + 1
  end if
  if c_farm__shfree <= 1 then
    exit sub
  end if
  if selfAttackDamage < 60 then
    c_farm__shgold = c_farm__shgold - k_config__cfgpoisonmingold
  end if
  ' Cheap early damage first: +4 is a sixth of level-1 damage and widens every
  ' last-hit window. Then the big damage items, then HP for the fights.
  if c_farm__shhasgauntlets = 0 and selfLevel < 4 then
    c_farm__shtrybuy(7, 70)
  end if
  if c_farm__shhasaxe = 0 then
    c_farm__shtrybuy(18, 180)
  end if
  if c_farm__shhascrossbow = 0 then
    c_farm__shtrybuy(19, 180)
  end if
  if c_farm__shhasbook = 0 then
    c_farm__shtrybuy(20, 190)
  end if
  if c_farm__shhassword = 0 then
    c_farm__shtrybuy(13, 150)
  end if
  if c_farm__shhasarmor = 0 then
    c_farm__shtrybuy(16, 160)
  end if
  if c_farm__shhasbow = 0 then
    c_farm__shtrybuy(14, 150)
  end if
  if c_farm__shhaspauldrons = 0 then
    c_farm__shtrybuy(15, 140)
  end if
  if c_farm__shhaswand = 0 then
    c_farm__shtrybuy(12, 140)
  end if
  if c_farm__shhasdagger = 0 then
    c_farm__shtrybuy(11, 110)
  end if
  if c_farm__shhasstaff = 0 then
    c_farm__shtrybuy(17, 170)
  end if
end sub

' ===== module 90_main.bas =====
' Module: main (prefix mn). Per-decision top-level flow and telemetry.
'
' Order: config, scan, inventory, bookkeeping, survive, last-hit plan, shop, act.
' Priority when acting: retreat > punish > kill steal > secure cast > poison > attack order > hold/walk.
' Telemetry line every k_config__cfgreportevery ticks:
'   LH tick lastHits heroKills towerKills level orders secures poisons lost deaths gold early missed punishStarts punishTicks punishChaseTicks chaseTicks ksHeroWindows c_farm__kscasts ksStructWindows
sub c_farm__mnlanefront()
  c_farm__mnseat = selfId - 100
  c_farm__mnslot = c_farm__mnseat mod 5
  c_farm__mnlane = 1
  if c_farm__mnslot < 2 then
    c_farm__mnlane = 0
  end if
  if c_farm__mnslot > 2 then
    c_farm__mnlane = 2
  end if
  c_farm__mndiag = (mapWidth - 1) * 60
  c_farm__mnbest = -1
  c_farm__mnbestd = -1
  c_farm__mnbestfight = 0
  c_farm__oi = 0
  while c_farm__oi < c_farm__afn
    c_farm__os = c_farm__afx(c_farm__oi) + c_farm__afy(c_farm__oi) - c_farm__mndiag
    c_farm__ol = 1
    if c_farm__os <= -900 then
      c_farm__ol = 0
    end if
    if c_farm__os >= 900 then
      c_farm__ol = 2
    end if
    if c_farm__ol = c_farm__mnlane then
      c_farm__odx = c_farm__afx(c_farm__oi) - c_farm__scfortx
      c_farm__ody = c_farm__afy(c_farm__oi) - c_farm__scforty
      c_farm__od = c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody
      c_farm__mnfight = 0
      if c_farm__aftgt(c_farm__oi) <> 0 then
        c_farm__mnfight = 1
      end if
      if c_farm__mnfight > c_farm__mnbestfight or (c_farm__mnfight = c_farm__mnbestfight and c_farm__od > c_farm__mnbestd) then
        c_farm__mnbestfight = c_farm__mnfight
        c_farm__mnbestd = c_farm__od
        c_farm__mnbest = c_farm__oi
      end if
    end if
    c_farm__oi = c_farm__oi + 1
  wend
  if c_farm__mnbest >= 0 then
    c_farm__mnfrontx = c_farm__afx(c_farm__mnbest) \ 60
    c_farm__mnfronty = c_farm__afy(c_farm__mnbest) \ 60
    ' A front with an enemy hero on c_farm__it and no allied hero near c_farm__it is a place
    ' to die, not to farm: wait where I am instead.
    c_farm__mnunsafe = 0
    c_farm__oi = 0
    while c_farm__oi < c_farm__ehn
      c_farm__odx = c_farm__ehpos(c_farm__oi) \ 8192 - c_farm__afx(c_farm__mnbest)
      c_farm__ody = c_farm__ehpos(c_farm__oi) mod 8192 - c_farm__afy(c_farm__mnbest)
      if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= k_config__cfgherofearrange * k_config__cfgherofearrange then
        c_farm__mnunsafe = 1
      end if
      c_farm__oi = c_farm__oi + 1
    wend
    if c_farm__mnunsafe = 1 then
      c_farm__oi = 0
      while c_farm__oi < c_farm__ahn
        c_farm__odx = c_farm__ahx(c_farm__oi) - c_farm__afx(c_farm__mnbest)
        c_farm__ody = c_farm__ahy(c_farm__oi) - c_farm__afy(c_farm__mnbest)
        if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= k_config__cfgherofearrange * k_config__cfgherofearrange then
          c_farm__mnunsafe = 0
        end if
        c_farm__oi = c_farm__oi + 1
      wend
    end if
    if c_farm__mnunsafe = 1 then
      c_farm__mnfrontx = selfX
      c_farm__mnfronty = selfY
    end if
  else
    c_farm__mnfrontx = mapWidth \ 2
    c_farm__mnfronty = mapHeight \ 2
    if c_farm__mnlane = 0 then
      c_farm__mnfrontx = 14
      c_farm__mnfronty = 14
    end if
    if c_farm__mnlane = 2 then
      c_farm__mnfrontx = mapWidth - 15
      c_farm__mnfronty = mapHeight - 15
    end if
  end if
end sub


SUB c_farm__start()
END SUB
SUB c_farm__tick()

c_farm__scanobjects()
c_farm__shscaninventory()

' ---- bookkeeping: income from gold deltas, deaths from skipped decisions ----
if c_farm__mninit = 0 then
  c_farm__mninit = 1
  c_farm__mnexpectedgold = selfGold
  c_farm__mnlasttick = worldTick
end if
if worldTick - c_farm__mnlasttick > 1 then
  c_farm__mndeaths = c_farm__mndeaths + 1
  c_farm__svlow = 0
  c_farm__mnorderedid = 0
end if
c_farm__mnlasttick = worldTick
c_farm__mndelta = selfGold - c_farm__mnexpectedgold
c_farm__mngained = 0
' Income is footmen (15), heroes (100) and finishing blows on structures (75).
' Take hero kills only until the remainder is a multiple of 15, so an area spell
' paying 105 reads as seven footmen, not a hero. An estimate; the replay expander
' has the exact counts.
while c_farm__mndelta >= 100 and c_farm__mndelta mod 15 <> 0
  c_farm__mnherokills = c_farm__mnherokills + 1
  c_farm__mndelta = c_farm__mndelta - 100
  c_farm__mngained = 1
wend
' 75 gold is a structure kill when my ordered target was a structure that is gone,
' else five footmen from an area spell (exact counts come from the replay expander).
if c_farm__mnorderedid > 0 and c_farm__mnorderedid < 100 and c_farm__mndelta >= 75 then
  c_farm__ogone = 1
  c_farm__oj = 0
  while c_farm__oj < c_farm__etn
    if c_farm__etx(c_farm__oj) mod 64 = c_farm__mnorderedid then
      c_farm__ogone = 0
    end if
    c_farm__oj = c_farm__oj + 1
  wend
  if c_farm__ogone = 1 then
    c_farm__mntowerkills = c_farm__mntowerkills + 1
    c_farm__mndelta = c_farm__mndelta - 75
    c_farm__mngained = 1
    c_farm__mnorderedid = 0
  end if
end if
while c_farm__mndelta >= 15
  c_farm__mnlasthits = c_farm__mnlasthits + 1
  c_farm__mndelta = c_farm__mndelta - 15
  c_farm__mngained = 1
wend
' A basic hit landed on the ordered target and c_farm__it survived: the window was early.
if c_farm__mnorderedid >= 1000 and selfAttacksLanded > c_farm__mnlanded and c_farm__mngained = 0 then
  c_farm__oj = c_farm__fmslot((c_farm__mnorderedid - 1000) mod 448)
  if c_farm__oj >= 0 then
    if c_farm__efid(c_farm__oj) = c_farm__mnorderedid then
      c_farm__mnearly = c_farm__mnearly + 1
    end if
  end if
end if
c_farm__mnlanded = selfAttacksLanded
' Enemy footmen that vanished within chase distance while I was not on them.
if c_farm__mngained = 0 then
  c_farm__mnmissed = c_farm__mnmissed + c_farm__lhvanished
end if
' An ordered target that vanished without paying us was taken by someone else
' (footman orders only; hero orders are cleared by the punish module).
if c_farm__mnorderedid >= 1000 then
  c_farm__oj = c_farm__fmslot((c_farm__mnorderedid - 1000) mod 448)
  c_farm__mnstill = 0
  if c_farm__oj >= 0 then
    if c_farm__efid(c_farm__oj) = c_farm__mnorderedid then
      c_farm__mnstill = 1
    end if
  end if
  if c_farm__mnstill = 0 then
    if c_farm__mngained = 0 then
      c_farm__mnlost = c_farm__mnlost + 1
    end if
    c_farm__mnorderedid = 0
  end if
end if

' ---- plans ----
c_farm__pnplan()
c_farm__svplan()
c_farm__ksplan()
c_farm__lhstickid = c_farm__mnorderedid
c_farm__lhplan()

' ---- act ----
if c_farm__svhealslot >= 0 then
  useItem(c_farm__svhealslot)
end if
c_farm__mnacted = 0
if c_farm__svretreat = 1 then
  walkTo(c_farm__svretx, c_farm__svrety)
  c_farm__mnorderedid = 0
  c_farm__mnacted = 1
end if
if c_farm__mnacted = 0 and c_farm__pntargetid <> 0 then
  ' Punish: an enemy hero farming under our tower is the best income on the map.
  if attackTarget(c_farm__pntargetid) = 1 then
    c_farm__pnticks = c_farm__pnticks + 1
    if c_farm__mnorderedid <> c_farm__pntargetid then
      c_farm__pnstarts = c_farm__pnstarts + 1
    end if
    c_farm__mnorderedid = c_farm__pntargetid
    c_farm__mnacted = 1
  end if
end if
if c_farm__mnacted = 0 and c_farm__kstargetid <> 0 then
  ' Kill steal: the finishing blow on a hero or structure teammates are fighting.
  if c_farm__ksslot >= 0 then
    if castTarget(c_farm__ksslot, c_farm__kstargetid) = 1 then
      c_farm__kscasts = c_farm__kscasts + 1
    end if
    if c_farm__kstargetid >= 100 and c_farm__kstargetid < 1000 then
      attackTarget(c_farm__kstargetid)
    end if
    c_farm__mnorderedid = c_farm__kstargetid
    c_farm__mnacted = 1
  else
    if attackTarget(c_farm__kstargetid) = 1 then
      c_farm__mnorderedid = c_farm__kstargetid
      c_farm__mnacted = 1
    end if
  end if
end if
if c_farm__mnacted = 0 then
  if c_farm__lhsecureslot >= 0 then
    if castTarget(c_farm__lhsecureslot, c_farm__lhsecureid) = 1 then
      c_farm__lhsecures = c_farm__lhsecures + 1
    end if
  end if
  if c_farm__lhpoisonid <> 0 then
    if attackTarget(c_farm__lhpoisonid) = 1 then
      if useItem(c_farm__shpoisonslot) = 1 then
        c_farm__lhpoisons = c_farm__lhpoisons + 1
      end if
    end if
  end if
  if c_farm__lhtargetid <> 0 then
    if attackTarget(c_farm__lhtargetid) = 1 then
      if c_farm__mnorderedid <> c_farm__lhtargetid then
        c_farm__lhorders = c_farm__lhorders + 1
      end if
      c_farm__mnorderedid = c_farm__lhtargetid
      c_farm__mnacted = 1
    end if
  else
    if c_farm__lhpoisonid <> 0 then
      ' The poison target was already ordered; keep c_farm__it rather than walking away.
      c_farm__mnorderedid = c_farm__lhpoisonid
      c_farm__mnacted = 1
    end if
  end if
end if
if c_farm__mnacted = 0 then
  if c_farm__lhstate = 1 then
    c_farm__mnwalkx = c_farm__lhholdx
    c_farm__mnwalky = c_farm__lhholdy
  else
    c_farm__mnlanefront()
    c_farm__mnwalkx = c_farm__mnfrontx
    c_farm__mnwalky = c_farm__mnfronty
  end if
  c_farm__mnchase = 0
  if k_config__cfgchaseclass = 1 and c_farm__kshold = 0 and c_farm__lhstate = 1 and c_farm__lhanchor >= 0 then
    c_farm__odx = c_farm__efx(c_farm__lhanchor) - c_farm__scselfx
    c_farm__ody = c_farm__efy(c_farm__lhanchor) - c_farm__scselfy
    if c_farm__odx * c_farm__odx + c_farm__ody * c_farm__ody <= c_farm__lhchase * c_farm__lhchase then
      c_farm__mnchase = 1
    end if
  end if
  c_farm__mnidledx = c_farm__mnwalkx - selfX
  c_farm__mnidledy = c_farm__mnwalky - selfY
  if c_farm__mnchase = 1 then
    ' Chase classes: keep the engine attacking the anchor footman instead of
    ' walking to a standoff point.
    if selfTarget = 0 then
      attackTarget(c_farm__efid(c_farm__lhanchor))
    end if
    c_farm__mnchases = c_farm__mnchases + 1
  else
  if k_config__cfgidlehere = 1 and c_farm__kshold = 0 and c_farm__lhstate = 1 and c_farm__mnidledx * c_farm__mnidledx + c_farm__mnidledy * c_farm__mnidledy <= 1 then
    ' Idle classes: at the standoff point, stop walking and let the engine
    ' auto-acquire and attack instead of holding for windows.
    attackTarget(0)
    c_farm__mnidles = c_farm__mnidles + 1
  else
    if c_farm__mnwalkx = selfX and c_farm__mnwalky = selfY then
      ' Already there: keep a live walk target so the engine does not auto-acquire.
      if c_farm__mnnudge = 0 then
        c_farm__mnnudge = 1
      else
        c_farm__mnnudge = 0
      end if
      if c_farm__scfortx > c_farm__scselfx then
        c_farm__mnwalkx = selfX + c_farm__mnnudge
      else
        c_farm__mnwalkx = selfX - c_farm__mnnudge
      end if
    end if
    if walkTo(c_farm__mnwalkx, c_farm__mnwalky) = 0 then
      ' No path to the standoff tile (terrain or a building): aim at the anchor itself.
      if c_farm__lhstate = 1 then
        walkTo(c_farm__efx(c_farm__lhanchor) \ 60, c_farm__efy(c_farm__lhanchor) \ 60)
      end if
    end if
  end if
  end if
  c_farm__mnorderedid = 0
end if

' ---- shopping (works anywhere) ----
c_farm__shbuy()
c_farm__mnexpectedgold = selfGold - c_farm__shspent


c_farm__status=0
END SUB
